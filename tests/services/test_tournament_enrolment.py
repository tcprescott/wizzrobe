"""Enrolment from the tournament's own screen.

The Tournaments tab has always said "click a player count to manage entrants";
the dialog behind it was a read-only list. These cover the service the writable
version calls — in particular that it refuses a non-member at the service, not
merely by omitting them from the picker. UI-only gating is not gating.
"""

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tournament_service import TournamentService
from models import AuditLog, Role, TenantMembership, Tournament, TournamentPlayers, User


@pytest.fixture
async def staff(db):
    boss = await User.create(discord_id=5200, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


@pytest.fixture
async def tournament(db):
    return await Tournament.create(name='Spring Open')


@pytest.fixture
async def member(staff, db):
    person = await User.create(discord_id=5201, username='entrant')
    await TenantMembershipService().add_member(staff, person)
    return person


async def test_entrants_can_be_added_and_removed(staff, tournament, member):
    service = TournamentService()
    await service.enroll_player(tournament, member, staff)
    assert [tp.user_id for tp in await service.get_enrolled_players(tournament)] == [member.id]

    await service.unenroll_player(tournament, member, staff)
    assert await service.get_enrolled_players(tournament) == []


async def test_the_enrolment_row_is_tenant_stamped(staff, tournament, member):
    await TournamentService().enroll_player(tournament, member, staff)
    row = await TournamentPlayers.get(tournament=tournament, user=member)
    assert row.tenant_id == 1


async def test_adding_a_non_member_is_refused(staff, tournament, db):
    stranger = await User.create(discord_id=5202, username='stranger')
    with pytest.raises(ValueError) as exc:
        await TournamentService().enroll_player(tournament, stranger, staff)
    assert 'not a member' in str(exc.value)
    assert await TournamentPlayers.filter(user=stranger).count() == 0


async def test_enrolling_twice_is_refused_rather_than_silently_duplicated(
    staff, tournament, member,
):
    service = TournamentService()
    await service.enroll_player(tournament, member, staff)
    with pytest.raises(ValueError) as exc:
        await service.enroll_player(tournament, member, staff)
    assert 'already enrolled' in str(exc.value)


async def test_entrant_management_requires_staff_or_the_tournaments_admin(tournament, db):
    nobody = await User.create(discord_id=5203, username='nobody')
    target = await User.create(discord_id=5204, username='target')
    with pytest.raises(PermissionError):
        await TournamentService().enroll_player(tournament, target, nobody)
    with pytest.raises(PermissionError):
        await TournamentService().unenroll_player(tournament, target, nobody)


async def test_a_tournament_admin_manages_entrants_of_their_own_tournament_only(
    staff, tournament, member,
):
    """The players dialog offered Enrol and Remove to tournament admins and the
    service refused every press. Rostering is part of running the tournament."""
    ta = await User.create(discord_id=5205, username='ta')
    await TenantMembership.create(user=ta, tenant_id=1)
    await tournament.admins.add(ta)
    other = await Tournament.create(name='Someone Else\'s')

    service = TournamentService()
    await service.enroll_player(tournament, member, ta)
    assert [tp.user_id for tp in await service.get_enrolled_players(tournament)] == [member.id]
    await service.unenroll_player(tournament, member, ta)
    assert await service.get_enrolled_players(tournament) == []

    with pytest.raises(PermissionError):
        await service.enroll_player(other, member, ta)
    with pytest.raises(PermissionError):
        await service.unenroll_player(other, member, ta)


async def test_entrant_changes_are_audited_under_the_shared_action(
    staff, tournament, member,
):
    """One fact reached from three screens keeps one audit action.

    The per-user edit dialog and the match dialog already write
    ``user.tournament_enrollment_updated``; a parallel action for the same
    change made from the tournament screen is how an audit log stops being
    answerable.
    """
    service = TournamentService()
    await service.enroll_player(tournament, member, staff)
    await service.unenroll_player(tournament, member, staff)

    rows = await AuditLog.filter(action='user.tournament_enrollment_updated')
    assert len(rows) == 2
    import json
    added, removed = (json.loads(r.details) for r in rows)
    assert added['added_tournament_ids'] == [tournament.id]
    assert added['removed_tournament_ids'] == []
    assert removed['removed_tournament_ids'] == [tournament.id]
    assert removed['added_tournament_ids'] == []


async def test_ensure_players_enrolled_reports_who_it_enrolled(staff, tournament, member, db):
    """The dialog can only say "Alice was enrolled" if the service tells it."""
    from application.services import MatchService

    service = MatchService()
    newly = await service.ensure_players_enrolled(tournament.id, [member.id], staff)
    assert [u.id for u in newly] == [member.id]

    # Second time round nobody is new, so the dialog says nothing.
    assert await service.ensure_players_enrolled(tournament.id, [member.id], staff) == []


async def test_ensure_players_enrolled_reports_nothing_for_an_empty_roster(staff, tournament, db):
    from application.services import MatchService

    assert await MatchService().ensure_players_enrolled(tournament.id, [], staff) == []


async def test_an_inactive_tournament_is_not_offered(staff, tournament, db):
    await Tournament.create(name='Last Year', is_active=False)
    offered, _counts = await TournamentService().list_schedulable(staff)
    assert [t.name for t in offered] == ['Spring Open']


async def test_editing_a_match_keeps_its_own_tournament_in_the_list(staff, db):
    """Dropping it would blank the chip on the match being edited."""
    retired = await Tournament.create(name='Last Year', is_active=False)
    offered, _counts = await TournamentService().list_schedulable(staff, keep_id=retired.id)
    assert retired.id in {t.id for t in offered}


async def test_the_kept_tournament_is_not_duplicated_when_it_is_already_live(
    staff, tournament, db,
):
    offered, _counts = await TournamentService().list_schedulable(staff, keep_id=tournament.id)
    assert [t.id for t in offered].count(tournament.id) == 1


async def test_a_missing_keep_id_is_not_an_error(staff, tournament, db):
    offered, _counts = await TournamentService().list_schedulable(staff, keep_id=99999)
    assert [t.name for t in offered] == ['Spring Open']


async def test_entrant_counts_let_the_dialog_predict_an_empty_player_menu(
    staff, tournament, member, db,
):
    empty = await Tournament.create(name='Nobody Yet')
    await TournamentService().enroll_player(tournament, member, staff)

    _offered, counts = await TournamentService().list_schedulable(staff)
    assert counts.get(tournament.id) == 1
    assert counts.get(empty.id, 0) == 0


async def test_a_tournament_admin_is_offered_only_the_tournaments_they_run(staff, tournament, db):
    """create_match refuses any other tournament from them, so New Match must
    not list it."""
    ta = await User.create(discord_id=5206, username='ta2')
    await TenantMembership.create(user=ta, tenant_id=1)
    await Tournament.create(name='Not Theirs')
    await tournament.admins.add(ta)

    offered, _counts = await TournamentService().list_schedulable(ta)
    assert [t.name for t in offered] == ['Spring Open']
    staff_offered, _ = await TournamentService().list_schedulable(staff)
    assert {t.name for t in staff_offered} == {'Spring Open', 'Not Theirs'}
