"""Cross-tenant isolation (leak) tests for the repository layer.

Creates data under two tenants and asserts each tenant's repository reads see
only its own rows — the core guarantee of logical multitenancy. Global identity
(``User``) is shared across tenants, so the reverse-relation reads keyed on a
user are the sharpest leak points and are covered explicitly.

The ``db`` fixture already created the default tenant (id 1); these tests add a
second and drive both via explicit ``tenant_scope`` blocks. Direct ``Model.create``
calls are stamped with the active scope by the test harness (see conftest).
"""

import pytest

from application.repositories.commentary_invite_repository import CommentaryInviteRepository
from application.repositories.equipment_repository import EquipmentRepository
from application.repositories.feedback_repository import FeedbackRepository
from application.repositories.match_hard_preset_repository import (
    MatchHardPresetRepository,
)
from application.repositories.match_repository import MatchRepository
from application.repositories.match_stream_volunteer_repository import (
    MatchStreamVolunteerRepository,
)
from application.repositories.match_watcher_repository import MatchWatcherRepository
from application.repositories.preset_repository import PresetRepository
from application.repositories.reschedule_request_repository import (
    RescheduleRequestRepository,
)
from application.repositories.stage_repository import StageRepository
from application.repositories.tournament_repository import TournamentRepository
from application.repositories.volunteer_position_repository import VolunteerPositionRepository
from application.tenant_context import tenant_scope
from models import (
    CommentaryInvite,
    Equipment,
    Feedback,
    Match,
    MatchHardPresetOptIn,
    MatchRescheduleRequest,
    MatchStreamVolunteer,
    MatchWatcher,
    Preset,
    Stage,
    Tenant,
    Tournament,
    TournamentPlayers,
    User,
    VolunteerPosition,
)


@pytest.fixture
async def tenants(db):
    """(tenant_a, tenant_b). Tenant A is the default (id 1) from the db fixture."""
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='Tenant B', slug='tenant-b')
    return a, b


async def test_tournament_reads_are_isolated(tenants):
    a, b = tenants
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')

    with tenant_scope(a.id):
        assert [t.id for t in await TournamentRepository.get_all()] == [ta.id]
        assert await TournamentRepository.get_by_id(tb.id) is None  # B's row invisible to A
    with tenant_scope(b.id):
        assert [t.id for t in await TournamentRepository.get_all()] == [tb.id]
        assert await TournamentRepository.get_by_id(ta.id) is None


async def test_preset_reads_are_isolated(tenants):
    a, b = tenants
    with tenant_scope(a.id):
        pa = await Preset.create(name='Open', randomizer='alttpr', settings={'mode': 'open'})
    with tenant_scope(b.id):
        pb = await Preset.create(name='Open', randomizer='alttpr', settings={'mode': 'standard'})

    repo = PresetRepository()
    with tenant_scope(a.id):
        assert [p.id for p in await repo.list_all()] == [pa.id]
        assert await repo.get_by_id(pb.id) is None  # B's preset invisible to A
        # Same (randomizer, name) exists in both tenants; the natural-key lookup
        # resolves only A's row.
        assert (await repo.get_by_natural_key('alttpr', 'Open')).id == pa.id
    with tenant_scope(b.id):
        assert [p.id for p in await repo.list_all()] == [pb.id]
        assert await repo.get_by_id(pa.id) is None
        assert (await repo.get_by_natural_key('alttpr', 'Open')).id == pb.id


async def test_match_reads_are_isolated(tenants):
    a, b = tenants
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        ma = await Match.create(tournament=ta)
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        mb = await Match.create(tournament=tb)

    with tenant_scope(a.id):
        assert [m.id for m in await MatchRepository.get_all_for_schedule()] == [ma.id]
    with tenant_scope(b.id):
        assert [m.id for m in await MatchRepository.get_all_for_schedule()] == [mb.id]

    # A deep link carries a raw match id in the URL, so the focused board's
    # filter has to stay inside the tenant that asked for it.
    with tenant_scope(a.id):
        assert [m.id for m in await MatchRepository.get_all(match_ids=[ma.id])] == [ma.id]
        assert await MatchRepository.get_all(match_ids=[mb.id]) == []


async def test_stage_reads_are_isolated(tenants):
    a, b = tenants
    with tenant_scope(a.id):
        ra = await Stage.create(name='Stage A')
    with tenant_scope(b.id):
        # Same name is allowed across tenants now (per-tenant unique).
        rb = await Stage.create(name='Stage A')

    with tenant_scope(a.id):
        assert [r.id for r in await StageRepository.get_all()] == [ra.id]
    with tenant_scope(b.id):
        assert [r.id for r in await StageRepository.get_all()] == [rb.id]


async def test_feedback_reads_are_isolated(tenants):
    a, b = tenants
    user = await User.create(discord_id=901, username='u')
    with tenant_scope(a.id):
        fa = await Feedback.create(user=user, message='A', page_url='/a')
    with tenant_scope(b.id):
        await Feedback.create(user=user, message='B', page_url='/b')

    with tenant_scope(a.id):
        rows = await FeedbackRepository.list_recent()
        assert [f.id for f in rows] == [fa.id]


async def test_volunteer_position_reads_are_isolated(tenants):
    a, b = tenants
    with tenant_scope(a.id):
        pa = await VolunteerPosition.create(name='Desk')
    with tenant_scope(b.id):
        pb = await VolunteerPosition.create(name='Desk')  # same name, different tenant

    with tenant_scope(a.id):
        assert [p.id for p in await VolunteerPositionRepository.list_all()] == [pa.id]
    with tenant_scope(b.id):
        assert [p.id for p in await VolunteerPositionRepository.list_all()] == [pb.id]


async def test_equipment_asset_numbering_is_per_tenant(tenants):
    a, b = tenants
    with tenant_scope(a.id):
        await Equipment.create(asset_number=5, name='A gear')
        assert await EquipmentRepository.next_asset_number() == 6
    with tenant_scope(b.id):
        # B has no equipment, so its numbering starts fresh — not 6.
        assert await EquipmentRepository.next_asset_number() == 1
        assert [e.id for e in await EquipmentRepository.list_all()] == []


async def test_enrolled_players_by_user_does_not_leak_across_tenants(tenants):
    """The sharp edge: a global user enrolled in both tenants must only surface
    the current tenant's enrollment when read back by user."""
    a, b = tenants
    user = await User.create(discord_id=902, username='shared')
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        await TournamentPlayers.create(tournament=ta, user=user)
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        await TournamentPlayers.create(tournament=tb, user=user)

    with tenant_scope(a.id):
        rows = await TournamentRepository.get_enrolled_players_by_user(user)
        assert [r.tournament_id for r in rows] == [ta.id]
    with tenant_scope(b.id):
        rows = await TournamentRepository.get_enrolled_players_by_user(user)
        assert [r.tournament_id for r in rows] == [tb.id]


async def test_match_watcher_by_user_does_not_leak(tenants):
    a, b = tenants
    user = await User.create(discord_id=903, username='watcher')
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        ma = await Match.create(tournament=ta)
        await MatchWatcher.create(match=ma, user=user)
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        mb = await Match.create(tournament=tb)
        await MatchWatcher.create(match=mb, user=user)

    with tenant_scope(a.id):
        rows = await MatchWatcherRepository.get_by_user(user)
        assert [w.match_id for w in rows] == [ma.id]
    with tenant_scope(b.id):
        rows = await MatchWatcherRepository.get_by_user(user)
        assert [w.match_id for w in rows] == [mb.id]


async def test_match_hard_preset_opt_in_does_not_leak(tenants):
    """One community's opt-in must not count toward another's agreement.

    The sharper failure than a listing leak: unanimity is computed from these
    ids, so a cross-tenant read would make a match roll the harder preset
    because its players agreed to a *different* community's match.
    """
    a, b = tenants
    user = await User.create(discord_id=905, username='opter')
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        ma = await Match.create(tournament=ta)
        await MatchHardPresetOptIn.create(match=ma, user=user)
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        mb = await Match.create(tournament=tb)
        await MatchHardPresetOptIn.create(match=mb, user=user)

    with tenant_scope(a.id):
        assert await MatchHardPresetRepository.opted_in_match_ids(
            user, [ma.id, mb.id],
        ) == {ma.id}
        assert await MatchHardPresetRepository.user_ids_for_match(mb.id) == set()
        assert await MatchHardPresetRepository.user_ids_by_match([ma.id, mb.id]) == {
            ma.id: {user.id},
        }
        assert await MatchHardPresetRepository.has_opted_in(mb.id, user.id) is False
    with tenant_scope(b.id):
        assert await MatchHardPresetRepository.opted_in_match_ids(
            user, [ma.id, mb.id],
        ) == {mb.id}
        assert await MatchHardPresetRepository.user_ids_for_match(ma.id) == set()
        assert await MatchHardPresetRepository.user_ids_by_match([ma.id, mb.id]) == {
            mb.id: {user.id},
        }
        assert await MatchHardPresetRepository.has_opted_in(ma.id, user.id) is False


async def test_match_stream_volunteer_does_not_leak(tenants):
    a, b = tenants
    user = await User.create(discord_id=904, username='offerer')
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        ma = await Match.create(tournament=ta)
        await MatchStreamVolunteer.create(match=ma, user=user)
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        mb = await Match.create(tournament=tb)
        await MatchStreamVolunteer.create(match=mb, user=user)

    with tenant_scope(a.id):
        assert await MatchStreamVolunteerRepository.get_match_ids_for_user(user) == [ma.id]
        assert await MatchStreamVolunteerRepository.names_by_match([ma.id, mb.id]) == {
            ma.id: ['offerer'],
        }
    with tenant_scope(b.id):
        assert await MatchStreamVolunteerRepository.get_match_ids_for_user(user) == [mb.id]
        assert await MatchStreamVolunteerRepository.names_by_match([ma.id, mb.id]) == {
            mb.id: ['offerer'],
        }


async def test_commentary_invite_does_not_leak(tenants):
    a, b = tenants
    inviter = await User.create(discord_id=905, username='inviter')
    invitee = await User.create(discord_id=906, username='invitee')
    with tenant_scope(a.id):
        ma = await Match.create(tournament=await Tournament.create(name='A Cup'))
        ia = await CommentaryInvite.create(match=ma, inviter=inviter, invitee=invitee)
    with tenant_scope(b.id):
        mb = await Match.create(tournament=await Tournament.create(name='B Cup'))
        ib = await CommentaryInvite.create(match=mb, inviter=inviter, invitee=invitee)

    with tenant_scope(a.id):
        assert [i.id for i in await CommentaryInviteRepository.pending_for_invitee(invitee.id)] == [ia.id]
        assert set(await CommentaryInviteRepository.pending_by_match([ma.id, mb.id])) == {ma.id}
        assert await CommentaryInviteRepository.get_by_id(ib.id) is None
    with tenant_scope(b.id):
        assert [i.id for i in await CommentaryInviteRepository.pending_for_invitee(invitee.id)] == [ib.id]
        assert await CommentaryInviteRepository.get_by_id(ia.id) is None


async def test_reschedule_request_does_not_leak(tenants):
    a, b = tenants
    user = await User.create(discord_id=905, username='asker')
    with tenant_scope(a.id):
        ta = await Tournament.create(name='A Cup')
        ma = await Match.create(tournament=ta)
        ra = await MatchRescheduleRequest.create(
            match=ma, requested_by=user, reason='clash',
        )
    with tenant_scope(b.id):
        tb = await Tournament.create(name='B Cup')
        mb = await Match.create(tournament=tb)
        rb = await MatchRescheduleRequest.create(
            match=mb, requested_by=user, reason='clash',
        )

    with tenant_scope(a.id):
        assert [r.id for r in await RescheduleRequestRepository.list_pending()] == [ra.id]
        assert [r.id for r in await RescheduleRequestRepository.list_for_user(user)] == [ra.id]
        assert await RescheduleRequestRepository.pending_count() == 1
        assert await RescheduleRequestRepository.pending_match_ids() == [ma.id]
        # The other tenant's match id must not resolve a count, even when named.
        assert await RescheduleRequestRepository.pending_by_match([ma.id, mb.id]) == {ma.id: 1}
        # get_by_id is the sharpest leak point: a forwarded request id.
        assert await RescheduleRequestRepository.get_by_id(rb.id) is None
        assert await RescheduleRequestRepository.get_pending_for(mb.id, user.id) is None

    with tenant_scope(b.id):
        assert [r.id for r in await RescheduleRequestRepository.list_pending()] == [rb.id]
        assert [r.id for r in await RescheduleRequestRepository.list_for_user(user)] == [rb.id]
        assert await RescheduleRequestRepository.pending_by_match([ma.id, mb.id]) == {mb.id: 1}
        assert await RescheduleRequestRepository.get_by_id(ra.id) is None


async def test_challonge_connection_is_per_tenant(tenants):
    from application.repositories.challonge_repository import ChallongeRepository
    a, b = tenants
    with tenant_scope(a.id):
        await ChallongeRepository.save_connection('tok-A', None, None, None, 'acct-a', None)
        conn = await ChallongeRepository.get_connection()
        assert conn is not None and conn.access_token == 'tok-A'
    with tenant_scope(b.id):
        # B has its own (absent) connection; A's must not be visible.
        assert await ChallongeRepository.get_connection() is None
        await ChallongeRepository.save_connection('tok-B', None, None, None, 'acct-b', None)
    with tenant_scope(a.id):
        assert (await ChallongeRepository.get_connection()).access_token == 'tok-A'


async def test_challonge_api_usage_is_per_tenant(tenants):
    from application.repositories.challonge_repository import ChallongeRepository
    a, b = tenants
    with tenant_scope(a.id):
        await ChallongeRepository.increment_api_usage(5)
        assert await ChallongeRepository.get_monthly_usage() == 5
    with tenant_scope(b.id):
        assert await ChallongeRepository.get_monthly_usage() == 0


async def test_volunteer_pool_is_per_tenant(tenants):
    """The pool is role-derived and roles are per tenant: a VOLUNTEER in one
    community is not assignable in another."""
    from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
    from models import Role, UserRole

    a, b = tenants
    user = await User.create(discord_id=904, username='vol')
    await UserRole.create(user=user, role=Role.VOLUNTEER, tenant_id=a.id)

    with tenant_scope(a.id):
        assert await VolunteerPoolService().assignable_user_ids() == {user.id}
    with tenant_scope(b.id):
        assert await VolunteerPoolService().assignable_user_ids() == set()


async def test_match_cannot_reference_another_tenants_stage(tenants):
    """``stage_id`` arrives as a bare integer and the FK spans tenants.

    Unguarded, a caller in tenant A could attach tenant B's stage to their match
    and surface its name on their schedule, Discord embed and API responses.
    """
    from application.errors import NotFoundError
    from application.services.match.match_service import MatchService
    from models import Role, UserRole

    a, b = tenants
    with tenant_scope(b.id):
        foreign = await Stage.create(name='B-only stage')

    staff = await User.create(discord_id=905, username='staff')
    with tenant_scope(a.id):
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=a.id)
        ta = await Tournament.create(name='A Cup')
        with pytest.raises(NotFoundError):
            await MatchService().create_match(
                tournament_id=ta.id,
                scheduled_date='2030-01-01',
                scheduled_time='12:00',
                player_ids=[staff.id],
                stage_id=foreign.id,
                actor=staff,
            )

        match = await Match.create(tournament=ta)
        with pytest.raises(NotFoundError):
            await MatchService().assign_stage(match.id, foreign.id, actor=staff)
