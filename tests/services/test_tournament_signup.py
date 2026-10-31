"""Player self-service tournament signup.

The behaviour the Profile-tab checkbox never had: a signup window, a membership
check, and a refusal that says which of the two stopped it. The window cases sit
on the pure helper where they belong; the rest go through the service, because
the point of moving signup out of the UI was that the UI is not the gate.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from application.errors import NotFoundError
from application.events import EventType, event_bus
from application.repositories.user_role_repository import UserRoleRepository
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tournament_service import TournamentService
from application.utils.tournament_signup import SignupWindow, signup_window_state
from models import Role, TenantMembership, Tournament, TournamentPlayers, User

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def staff(db):
    boss = await User.create(discord_id=7300, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


@pytest.fixture
async def player(staff, db):
    person = await User.create(discord_id=7301, username='entrant')
    await TenantMembershipService().add_member(staff, person)
    return person


@pytest.fixture
async def open_tournament(db):
    return await Tournament.create(
        name='Spring Open',
        signups_open_at=datetime.now(timezone.utc) - timedelta(days=1),
        signups_close_at=datetime.now(timezone.utc) + timedelta(days=7),
    )


def test_a_tournament_with_no_window_is_open():
    """Both bounds null is the state every tournament predating these columns
    is in, so it has to keep meaning "signups are open"."""
    assert signup_window_state(None, None, NOW) is SignupWindow.OPEN


def test_an_unset_close_date_leaves_signups_open_indefinitely():
    assert signup_window_state(NOW - timedelta(days=1), None, NOW) is SignupWindow.OPEN


def test_an_unset_open_date_means_open_until_the_close_date():
    assert signup_window_state(None, NOW + timedelta(days=1), NOW) is SignupWindow.OPEN
    assert signup_window_state(None, NOW - timedelta(days=1), NOW) is SignupWindow.CLOSED


def test_the_close_boundary_is_exclusive():
    """A window that closes at noon is shut at noon, not a moment after."""
    assert signup_window_state(None, NOW, NOW) is SignupWindow.CLOSED


def test_a_naive_bound_is_read_as_utc_rather_than_raising():
    """SQLite hands back naive datetimes where Postgres returns aware ones, and
    comparing the two raises. Storage is UTC on both."""
    assert signup_window_state(None, datetime(2026, 7, 1), NOW) is SignupWindow.OPEN


async def test_a_member_can_sign_themselves_up(open_tournament, player):
    await TournamentService().self_enroll(open_tournament.id, player)
    assert await TournamentPlayers.filter(
        tournament=open_tournament, user=player,
    ).count() == 1


async def test_the_signup_row_is_tenant_stamped(open_tournament, player):
    await TournamentService().self_enroll(open_tournament.id, player)
    row = await TournamentPlayers.get(tournament=open_tournament, user=player)
    assert row.tenant_id == 1


async def test_a_non_member_is_refused(open_tournament, db):
    """The check the Profile checkbox never made: it listed every active
    tournament in the tenant and enrolled whoever ticked a box."""
    stranger = await User.create(discord_id=7302, username='stranger')
    with pytest.raises(ValueError) as exc:
        await TournamentService().self_enroll(open_tournament.id, stranger)
    assert 'Join this community' in str(exc.value)
    assert await TournamentPlayers.filter(user=stranger).count() == 0


async def test_signing_up_twice_is_refused(open_tournament, player):
    service = TournamentService()
    await service.self_enroll(open_tournament.id, player)
    with pytest.raises(ValueError) as exc:
        await service.self_enroll(open_tournament.id, player)
    assert 'already signed up' in str(exc.value)


async def test_signup_before_the_window_opens_is_refused(player, db):
    tournament = await Tournament.create(
        name='Next Season',
        signups_open_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    with pytest.raises(ValueError) as exc:
        await TournamentService().self_enroll(tournament.id, player)
    assert 'not opened yet' in str(exc.value)


async def test_signup_after_the_window_closes_is_refused(player, db):
    tournament = await Tournament.create(
        name='Last Season',
        signups_close_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    with pytest.raises(ValueError) as exc:
        await TournamentService().self_enroll(tournament.id, player)
    assert 'closed' in str(exc.value)


async def test_signup_for_an_inactive_tournament_is_refused(player, db):
    tournament = await Tournament.create(name='Archived', is_active=False)
    with pytest.raises(ValueError):
        await TournamentService().self_enroll(tournament.id, player)


async def test_a_player_can_withdraw_while_the_window_is_open(open_tournament, player):
    service = TournamentService()
    await service.self_enroll(open_tournament.id, player)
    await service.self_withdraw(open_tournament.id, player)
    assert await TournamentPlayers.filter(
        tournament=open_tournament, user=player,
    ).count() == 0


async def test_withdrawing_after_the_window_closes_is_refused(open_tournament, player):
    """A roster a bracket was seeded from should not lose an entrant silently."""
    service = TournamentService()
    await service.self_enroll(open_tournament.id, player)
    open_tournament.signups_close_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await open_tournament.save()

    with pytest.raises(ValueError):
        await service.self_withdraw(open_tournament.id, player)
    assert await TournamentPlayers.filter(
        tournament=open_tournament, user=player,
    ).count() == 1


async def test_withdrawing_without_being_signed_up_is_refused(open_tournament, player):
    with pytest.raises(ValueError) as exc:
        await TournamentService().self_withdraw(open_tournament.id, player)
    assert 'not signed up' in str(exc.value)


async def test_withdrawing_before_the_window_opens_is_allowed(player, staff, db):
    """Staff can enter someone before signups open, and there is no roster to
    protect yet — locking them in would be a rule with nothing behind it."""
    tournament = await Tournament.create(
        name='Next Season',
        signups_open_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    service = TournamentService()
    await service.enroll_player(tournament, player, staff)

    await service.self_withdraw(tournament.id, player)
    assert await TournamentPlayers.filter(tournament=tournament, user=player).count() == 0


async def test_a_missing_tournament_is_not_found_rather_than_refused(player, db):
    """`not_found`, not "not accepting signups" — the latter confirms a
    tournament exists when it does not."""
    with pytest.raises(NotFoundError):
        await TournamentService().self_enroll(999, player)
    with pytest.raises(NotFoundError):
        await TournamentService().self_withdraw(999, player)


async def test_a_challonge_linked_tournament_refuses_self_signup(player, db):
    tournament = await Tournament.create(name='Bracket Cup', challonge_tournament_id='T1')
    with pytest.raises(ValueError) as exc:
        await TournamentService().self_enroll(tournament.id, player)
    assert 'Challonge' in str(exc.value)


async def test_a_challonge_card_offers_no_button(player, db):
    tournament = await Tournament.create(name='Bracket Cup', challonge_tournament_id='T1')
    card = next(
        c for c in await TournamentService().list_signup_cards(player)
        if c.tournament.id == tournament.id
    )
    assert card.challonge_managed is True
    assert card.can_sign_up is False


async def test_a_leftover_challonge_link_stops_mattering_with_the_feature_off(
    player, db, monkeypatch,
):
    """A tenant that has since disabled Challonge must not be left with a
    tournament nobody can sign up for."""
    from application.services import feature_flag_service

    monkeypatch.setattr(
        feature_flag_service.FeatureFlagService, 'enabled_flags',
        AsyncMock(return_value=set()),
    )
    tournament = await Tournament.create(name='Ex-Challonge Cup', challonge_tournament_id='T1')
    await TournamentService().self_enroll(tournament.id, player)
    assert await TournamentPlayers.filter(tournament=tournament, user=player).count() == 1


async def test_staff_can_enrol_after_signups_close(staff, player, db):
    """Closing signups stops players adding themselves; it is not meant to stop
    staff fixing a roster."""
    tournament = await Tournament.create(
        name='Closed Cup',
        signups_close_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    await TournamentService().enroll_player(tournament, player, staff)
    assert await TournamentPlayers.filter(tournament=tournament, user=player).count() == 1


async def test_the_card_reports_the_viewers_own_state(open_tournament, player, staff):
    await TournamentService().enroll_player(open_tournament, staff, staff)
    cards = {c.tournament.id: c for c in await TournamentService().list_signup_cards(player)}
    card = cards[open_tournament.id]

    assert card.enrolled is False
    assert card.can_sign_up is True
    assert card.can_withdraw is False
    assert card.entrant_count == 1
    assert [u.id for u in card.entrants] == [staff.id]


async def test_a_closed_card_offers_neither_button(player, db):
    tournament = await Tournament.create(
        name='Shut', signups_close_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    card = next(
        c for c in await TournamentService().list_signup_cards(player)
        if c.tournament.id == tournament.id
    )
    assert card.window is SignupWindow.CLOSED
    assert card.can_sign_up is False
    assert card.can_withdraw is False


async def test_inactive_tournaments_are_not_offered(player, db):
    await Tournament.create(name='Archived', is_active=False)
    names = {c.tournament.name for c in await TournamentService().list_signup_cards(player)}
    assert 'Archived' not in names


async def test_signing_up_and_withdrawing_publish_events(open_tournament, player):
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    try:
        service = TournamentService()
        await service.self_enroll(open_tournament.id, player)
        await service.self_withdraw(open_tournament.id, player)
    finally:
        event_bus.unsubscribe(token)

    types = [e.event_type for e in seen]
    assert EventType.TOURNAMENT_ENROLLED in types
    assert EventType.TOURNAMENT_WITHDRAWN in types
    enrolled = next(e for e in seen if e.event_type == EventType.TOURNAMENT_ENROLLED)
    assert enrolled.payload['tournament_id'] == open_tournament.id
    assert enrolled.payload['user_id'] == player.id
