"""Cross-tenant isolation for prize payouts.

Money is the worst thing to leak: a split read out of the wrong community would
name another event's winners and their handles.
"""

from decimal import Decimal

from application.repositories.tournament_payout_repository import (
    TournamentPayoutRepository,
)
from application.tenant_context import tenant_scope
from models import Tournament, TournamentPayout, User


async def test_payout_reads_are_isolated(two_tenants):
    a, b = two_tenants
    winner = await User.create(discord_id=1, username='jem')

    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup', prize_pool=Decimal('1000.00'))
        pa = await TournamentPayout.create(
            tournament=ta, place=1, percentage=Decimal('50.00'), entrant=winner,
        )
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup', prize_pool=Decimal('500.00'))
        pb = await TournamentPayout.create(
            tournament=tb, place=1, percentage=Decimal('50.00'), entrant=winner,
        )

    repo = TournamentPayoutRepository()
    with tenant_scope(a.id):
        assert [p.id for p in await repo.list_for_tournament(ta.id)] == [pa.id]
        assert await repo.list_for_tournament(tb.id) == []
        assert await repo.get_by_id(pb.id) is None
    with tenant_scope(b.id):
        assert [p.id for p in await repo.list_for_tournament(tb.id)] == [pb.id]
        assert await repo.get_by_id(pa.id) is None


async def test_payout_writes_are_stamped_with_the_active_tenant(two_tenants):
    a, b = two_tenants
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        created = await TournamentPayoutRepository.create(
            tournament=tb, place=1, percentage=Decimal('100.00'),
        )

    assert created.tenant_id == b.id
    with tenant_scope(a.id):
        assert await TournamentPayoutRepository.get_by_id(created.id) is None


async def test_replacing_a_split_only_clears_its_own_tenant(two_tenants):
    """Delete-then-create must not reach across the tenant boundary."""
    a, b = two_tenants
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        kept = await TournamentPayout.create(
            tournament=ta, place=1, percentage=Decimal('100.00'),
        )
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        await TournamentPayoutRepository.replace_split(
            tb, [{'place': 1, 'percentage': Decimal('100.00')}],
        )

    with tenant_scope(a.id):
        assert [p.id for p in await TournamentPayoutRepository.list_for_tournament(ta.id)] \
            == [kept.id]
