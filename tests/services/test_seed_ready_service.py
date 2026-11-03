"""Players releasing their own seed by all tapping Ready for seed.

What matters: it only opens on a tournament that turned it on, once the match is
checked in and until a seed exists; only the match's players can tap it; the
last tap rolls and sends the seed (and nothing earlier does); Ready can be taken
back until then; the opponent is nudged once, not on every toggle; and the
roll's own gate refuses a release while anyone isn't ready.
"""

from datetime import datetime, timezone

import pytest

import application.services.discord as discord_pkg
from application.events import EventType, event_bus
from application.services import MatchScheduleService, MatchService, SeedReadyService
from application.services.match.match_display_service import MatchDisplayService
from application.services.seedgen_service import RolledSeed, SeedGenerationService
from application.utils.seed_provider import ProviderCall
from models import Match, MatchPlayers, Role, Tournament, UserRole
from tests.factories import make_user
from theme.tables.match_player_run_sheet import decorate_player_row


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


@pytest.fixture
def dms(monkeypatch):
    pending = []
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', pending.append)
    yield pending
    for coro in pending:
        coro.close()


@pytest.fixture
def rolls(monkeypatch):
    calls = []

    async def fake_call(self, randomizer, preset=None, **kwargs):
        calls.append(randomizer)
        return ProviderCall(
            value=RolledSeed(url='https://example.test/seed/1', settings=None),
            provider=randomizer, operation='generate_seed', attempts=1, latency_ms=1,
        )

    monkeypatch.setattr(SeedGenerationService, 'generate_seed_call', fake_call)
    return calls


async def _match(*, release=True, seated=True, players=2):
    t = await Tournament.create(name='T', seed_generator='test', players_release_seed=release)
    m = await Match.create(
        tournament=t, scheduled_at=datetime.now(timezone.utc),
        seated_at=datetime.now(timezone.utc) if seated else None,
    )
    users = []
    for i in range(players):
        u = await make_user(discord_id=8201 + i, username=f'r{i}')
        await MatchPlayers.create(match=m, user=u)
        users.append(u)
    return m, users


class TestWhenReadyIsOpen:
    async def test_before_check_in_it_says_to_wait(self, db, dms):
        m, (p1, _) = await _match(seated=False)

        with pytest.raises(ValueError, match='checks your match in'):
            await SeedReadyService().set_ready(m.id, p1)

    async def test_a_tournament_without_the_switch_refuses(self, db, dms):
        m, (p1, _) = await _match(release=False)

        with pytest.raises(ValueError, match='proctor rolls the seed'):
            await SeedReadyService().set_ready(m.id, p1)

    async def test_a_non_player_is_refused(self, db, dms):
        m, _ = await _match()
        outsider = await make_user(discord_id=8299, username='nosy')

        with pytest.raises(ValueError, match='players in a match'):
            await SeedReadyService().set_ready(m.id, outsider)


class TestReadyAndRelease:
    async def test_one_player_ready_does_not_roll(self, db, dms, rolls, captured_events):
        m, (p1, p2) = await _match()

        result = await SeedReadyService().set_ready(m.id, p1)

        assert result.ready and not result.released
        assert p2.preferred_name in result.message
        assert rolls == []
        row = await MatchPlayers.get(match=m, user=p1)
        assert row.seed_ready_at is not None
        assert EventType.MATCH_PLAYER_SEED_READY in [e.event_type for e in captured_events]

    async def test_the_last_ready_rolls_and_sends_the_seed(self, db, dms, rolls):
        m, (p1, p2) = await _match()
        service = SeedReadyService()

        await service.set_ready(m.id, p1)
        result = await service.set_ready(m.id, p2)

        assert result.released and result.ok
        assert rolls == ['test']
        await m.refresh_from_db()
        assert m.generated_seed_id is not None

    async def test_ready_again_after_the_seed_is_out_says_so(self, db, dms, rolls):
        m, (p1, p2) = await _match()
        service = SeedReadyService()
        await service.set_ready(m.id, p1)
        await service.set_ready(m.id, p2)

        with pytest.raises(ValueError, match='already out'):
            await service.set_ready(m.id, p1)

    async def test_a_failed_roll_leaves_everyone_ready_to_retry(self, db, dms, monkeypatch):
        async def boom(self, *args, **kwargs):
            raise RuntimeError('upstream down')

        monkeypatch.setattr(SeedGenerationService, 'generate_seed_call', boom)
        m, (p1, p2) = await _match()
        service = SeedReadyService()
        await service.set_ready(m.id, p1)

        result = await service.set_ready(m.id, p2)

        assert not result.ok and not result.released
        assert 'Tap Ready again' in result.message
        assert await MatchPlayers.filter(match=m, seed_ready_at__isnull=True).count() == 0


class TestNotReady:
    async def test_takes_it_back(self, db, dms, captured_events):
        m, (p1, _) = await _match()
        service = SeedReadyService()
        await service.set_ready(m.id, p1)

        result = await service.set_not_ready(m.id, p1)

        assert not result.ready
        assert (await MatchPlayers.get(match=m, user=p1)).seed_ready_at is None
        assert EventType.MATCH_PLAYER_SEED_NOT_READY in [e.event_type for e in captured_events]

    async def test_refused_once_the_seed_is_out(self, db, dms, rolls):
        m, (p1, p2) = await _match()
        service = SeedReadyService()
        await service.set_ready(m.id, p1)
        await service.set_ready(m.id, p2)

        with pytest.raises(ValueError, match='already out'):
            await service.set_not_ready(m.id, p1)


class TestNudge:
    async def test_the_opponent_is_nudged_once_however_often_ready_toggles(self, db, dms, monkeypatch):
        m, (p1, p2) = await _match()
        sent = []

        async def _send(self, discord_id, message, match_id, **kwargs):
            sent.append((discord_id, kwargs.get('notice')))
            return True, 'ok'

        monkeypatch.setattr(
            'application.services.discord.discord_service.DiscordService.send_dm_with_seed_ready_button', _send,
        )
        service = SeedReadyService()
        await service.set_ready(m.id, p1)
        await service.set_not_ready(m.id, p1)
        await service.set_ready(m.id, p1)
        for coro in list(dms):
            dms.remove(coro)
            await coro

        assert [d for d, _ in sent] == [p2.discord_id]
        assert sent[0][1].key == 'opponent_seed_ready'

    def test_the_nudge_escapes_the_players_name(self):
        from application.services.match._seed_ready_notifications import opponent_seed_ready_dm

        text = opponent_seed_ready_dm('@everyone *x*', 'T')

        assert '@​everyone' in text and '\\*x\\*' in text


class TestRollGate:
    async def test_release_is_refused_while_anyone_is_not_ready(self, db, dms, rolls):
        m, (p1, _) = await _match()
        await MatchPlayers.filter(match=m, user=p1).update(seed_ready_at=datetime.now(timezone.utc))

        ok, message, _ = await MatchScheduleService().release_seed_to_ready_players(m.id, p1)

        assert not ok and "can't be released" in message
        assert rolls == []

    async def test_staff_still_roll_whenever(self, db, dms, rolls):
        m, _ = await _match()
        staff = await make_user(discord_id=8290, username='staff')
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)

        ok, _, _ = await MatchScheduleService().generate_seed(m.id, staff)

        assert ok and rolls == ['test']

    async def test_clearing_the_seed_starts_ready_over(self, db, dms, rolls):
        m, (p1, p2) = await _match()
        service = SeedReadyService()
        await service.set_ready(m.id, p1)
        await service.set_ready(m.id, p2)
        staff = await make_user(discord_id=8291, username='staff2')
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)

        await MatchService().clear_match_seed(m.id, actor=staff)

        assert await MatchPlayers.filter(match=m, seed_ready_at__isnull=False).count() == 0


class TestBoardRows:
    async def test_the_players_row_carries_the_ready_panel(self, db, dms):
        m, (p1, p2) = await _match()
        await SeedReadyService().set_ready(m.id, p2)

        row = await MatchDisplayService().get_match_for_display(m.id)
        decorate_player_row(row, p1.discord_id)

        assert row['seed_ready_open'] and row['seed_ready_label'] == '1 of 2 ready'
        assert row['_seed_panel'] and not row['_my_seed_ready']
        assert [p['ready'] for p in row['_seed_who']] == [False, True]
        assert row['_seed_who'][0]['name'].endswith('(you)')

    async def test_waiting_on_the_opponent_after_tapping(self, db, dms):
        m, (p1, p2) = await _match()
        await SeedReadyService().set_ready(m.id, p1)

        row = await MatchDisplayService().get_match_for_display(m.id)
        decorate_player_row(row, p1.discord_id)

        assert row['_my_seed_ready']
        assert row['_seed_waiting'] == f'Waiting on {p2.preferred_name}'
        assert not row['_seed_retry']

    async def test_nobody_else_gets_the_panel(self, db, dms):
        m, _ = await _match()
        outsider = await make_user(discord_id=8298, username='viewer')

        row = await MatchDisplayService().get_match_for_display(m.id)
        decorate_player_row(row, outsider.discord_id)

        assert not row['_seed_panel']


class TestCheckInDM:
    async def test_every_players_copy_asks_for_ready(self, db, dms):
        from application.services.match.match_schedule_service import _checked_in_player_copies

        m, (p1, p2) = await _match()
        await m.fetch_related('tournament', 'players__user', 'stage')

        copies = _checked_in_player_copies(m, 'Community')

        assert set(copies) == {p1.discord_id, p2.discord_id}
        text, embed = copies[p1.discord_id]
        assert 'Ready for seed' in text and 'ready for your seed' in embed.title

    async def test_without_the_switch_only_seated_players_get_their_own(self, db, dms):
        from application.services.match.match_schedule_service import _checked_in_player_copies

        m, _ = await _match(release=False)
        await m.fetch_related('tournament', 'players__user', 'stage')

        assert _checked_in_player_copies(m, 'Community') == {}

    async def test_check_in_sends_players_the_ready_button(self, db, dms, monkeypatch):
        m, (p1, p2) = await _match(seated=False)
        for p in (p1, p2):
            p.dm_notifications = True
            await p.save(update_fields=['dm_notifications'])
        staff = await make_user(discord_id=8292, username='staff3')
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
        with_button = []

        async def _ready(self, discord_id, message, match_id, **kwargs):
            with_button.append(discord_id)
            return True, 'ok'

        async def _plain(self, *args, **kwargs):
            return True, 'ok'

        dds = 'application.services.discord.discord_service.DiscordService'
        monkeypatch.setattr(f'{dds}.send_dm_with_seed_ready_button', _ready)
        monkeypatch.setattr(f'{dds}.send_dm', _plain)

        await MatchScheduleService().seat_match(m, staff)
        for coro in list(dms):
            dms.remove(coro)
            await coro

        assert sorted(with_button) == sorted([p1.discord_id, p2.discord_id])
