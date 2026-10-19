"""Prize pools synced from sglive.speedgaming.org: linking, syncing, the worker."""

from decimal import Decimal

import pytest

from application.services import sglive_pool_worker
from application.services.payout_service import PayoutService
from application.utils.clients import sglive_client
from application.utils.clients.sglive_client import SgliveAPIError, SgliveGame
from models import AuditLog, FeatureFlag, Role, TenantFeatureFlag, Tournament, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

GAMES = [
    SgliveGame(slug='wwr', name='Wind Waker Randomizer', total=Decimal('1701.00'), onsite=True),
    SgliveGame(slug='alttpr', name='A Link to the Past Randomizer Tournament',
               total=Decimal('1054.00'), onsite=True),
]


@pytest.fixture
async def staff(db):
    user = await make_user(discord_id=900, username='staff')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
def sglive(monkeypatch):
    state = {'games': list(GAMES), 'calls': 0, 'error': None}

    async def fake_fetch(url=sglive_client.SGLIVE_URL):
        state['calls'] += 1
        if state['error']:
            raise SgliveAPIError(state['error'])
        return state['games']

    monkeypatch.setattr(sglive_client, 'fetch_leaderboard', fake_fetch)
    return state


async def _reload(tournament):
    return await Tournament.get(id=tournament.id)


async def test_linking_fills_the_pool_straight_away(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer')

    await PayoutService().set_sglive_links({'wwr': ww.id, 'alttpr': None}, staff)

    ww = await _reload(ww)
    assert ww.sglive_slug == 'wwr'
    assert ww.prize_pool == Decimal('1701.00')
    assert ww.prize_pool_synced_at is not None
    assert await AuditLog.filter(action='tournament.sglive_link_updated').count() == 1


async def test_a_linked_pool_cannot_be_typed_over_but_the_bonus_can(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer')
    await PayoutService().set_sglive_links({'wwr': ww.id}, staff)

    with pytest.raises(ValueError, match='Unlink it'):
        await PayoutService().set_pool(ww.id, Decimal('5.00'), None, staff)

    await PayoutService().set_pool(ww.id, Decimal('1701.00'), Decimal('500.00'), staff)
    ww = await _reload(ww)
    assert ww.prize_bonus == Decimal('500.00')


async def test_unlinking_keeps_the_last_amount_and_frees_the_pool(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer')
    service = PayoutService()
    await service.set_sglive_links({'wwr': ww.id}, staff)

    await service.unlink_sglive(ww.id, staff)

    ww = await _reload(ww)
    assert ww.sglive_slug is None
    assert ww.prize_pool == Decimal('1701.00')
    await service.set_pool(ww.id, Decimal('5.00'), None, staff)


async def test_moving_a_slug_unlinks_its_old_tournament(staff, sglive):
    a = await Tournament.create(name='ALTTPR')
    b = await Tournament.create(name='ALTTPR Main')
    service = PayoutService()
    await service.set_sglive_links({'alttpr': a.id}, staff)

    await service.set_sglive_links({'alttpr': b.id}, staff)

    assert (await _reload(a)).sglive_slug is None
    assert (await _reload(b)).sglive_slug == 'alttpr'


async def test_one_tournament_cannot_follow_two_games(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer')
    with pytest.raises(ValueError, match='only follow one'):
        await PayoutService().set_sglive_links({'wwr': ww.id, 'alttpr': ww.id}, staff)


async def test_a_sync_only_audits_a_pool_that_moved(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer')
    service = PayoutService()
    await service.set_sglive_links({'wwr': ww.id}, staff)
    before = await AuditLog.filter(action='tournament.prize_pool_updated').count()

    await service.sync_sglive_pools(staff)
    assert await AuditLog.filter(action='tournament.prize_pool_updated').count() == before

    sglive['games'] = [SgliveGame('wwr', 'Wind Waker Randomizer', Decimal('1800.00'), True)]
    await service.sync_sglive_pools(staff)
    assert (await _reload(ww)).prize_pool == Decimal('1800.00')
    assert await AuditLog.filter(action='tournament.prize_pool_updated').count() == before + 1


async def test_a_game_gone_from_sglive_keeps_its_last_pool(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer')
    service = PayoutService()
    await service.set_sglive_links({'wwr': ww.id}, staff)
    sglive['games'] = [GAMES[1]]

    missing = await service.sync_sglive_pools(staff)

    assert missing == ['wwr']
    assert (await _reload(ww)).prize_pool == Decimal('1701.00')


async def test_a_plain_member_cannot_link(db, sglive):
    member = await make_user(discord_id=901, username='player')
    ww = await Tournament.create(name='Wind Waker Randomizer')
    with pytest.raises(PermissionError):
        await PayoutService().set_sglive_links({'wwr': ww.id}, member)
    with pytest.raises(PermissionError):
        await PayoutService().fetch_sglive_games(member)


async def test_worker_is_idle_with_nothing_linked(db, sglive):
    await sglive_pool_worker._tick()
    assert sglive['calls'] == 0


async def test_worker_updates_linked_pools(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer', sglive_slug='wwr')

    await sglive_pool_worker._tick()

    assert (await _reload(ww)).prize_pool == Decimal('1701.00')


async def test_worker_keeps_pools_when_sglive_is_down(staff, sglive, caplog):
    ww = await Tournament.create(
        name='Wind Waker Randomizer', sglive_slug='wwr', prize_pool=Decimal('900.00'),
    )
    sglive['error'] = 'sglive answered HTTP 502'

    await sglive_pool_worker._tick()
    await sglive_pool_worker._tick()

    assert (await _reload(ww)).prize_pool == Decimal('900.00')
    levels = [r.levelname for r in caplog.records if 'sglive prize pool read failed' in r.message]
    assert levels == ['ERROR', 'WARNING']
    sglive_pool_worker._last_error = None


async def test_worker_skips_a_community_with_payouts_off(staff, sglive):
    ww = await Tournament.create(name='Wind Waker Randomizer', sglive_slug='wwr')
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.PAYOUTS,
    ).update(enabled=False)

    await sglive_pool_worker._tick()

    assert (await _reload(ww)).prize_pool is None
