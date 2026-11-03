"""B9 tests — the native-bracket scheduling seam and Challonge exclusivity.

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). These
exercise the seam that lets a native bracket reuse the same ``Match`` creation
path the Challonge integration proved out: scheduling an OPEN bracket match into
a real ``Match``, advancing the bracket when that match is confirmed, and the
mutual-exclusion guards that keep a tournament on a native bracket OR a Challonge
link, never both.
"""

import pytest

from application.errors import NotFoundError
from application.services.auth_service import AuthService
from application.services.bracket_service import BracketService
from application.services.challonge_service import ChallongeService
from application.tenant_context import tenant_scope
from models import (
    BracketMatchGame,
    BracketMatchGameState,
    BracketMatchState,
    BracketState,
    Match,
    MatchPlayers,
    Role,
    Stage,
    Tournament,
    User,
    UserRole,
)
from tests.factories import join_community

pytestmark = pytest.mark.usefixtures("db")


async def _staff(discord_id: int = 1234, username: str = 'staff') -> User:
    user = await User.create(discord_id=discord_id, username=username)
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _proctor(discord_id: int = 2222, username: str = 'proctor') -> User:
    user = await User.create(discord_id=discord_id, username=username)
    await UserRole.create(user=user, role=Role.PROCTOR)
    return user


async def _player(discord_id: int, username: str) -> User:
    user = await User.create(discord_id=discord_id, username=username)
    await join_community(user)
    return user


@pytest.fixture
def service() -> BracketService:
    return BracketService()


async def _linked_bracket(service, actor, n=2, link_users=True):
    """Create + start a 2-entrant single-elim bracket with linked entrants.

    Returns ``(tournament, bracket, users, open_match)`` where ``open_match`` is
    the single round-1 OPEN match. When ``link_users`` is False, the second
    entrant is left as a placeholder (no linked ``user``).
    """
    t = await Tournament.create(name='Cup')
    bracket = await service.create_bracket(actor, t.id, 'Main', 'single_elim')
    users = []
    for i in range(1, n + 1):
        linked = link_users or i == 1
        user = await _player(1000 + i, f'p{i}') if linked else None
        users.append(user)
        entrant = await service.add_entrant(
            actor, t.id, f'P{i}', user_id=user.id if user else None
        )
        await service.enroll(actor, bracket.id, entrant.id, seed=i)
    await service.start_bracket(actor, bracket.id)
    open_matches = await service.get_open_matches(bracket.id)
    return t, bracket, users, open_matches[0]


class TestScheduleBracketMatch:
    async def test_creates_match_attaches_players_and_links(self, service):
        actor = await _staff()
        t, bracket, users, bmatch = await _linked_bracket(service, actor)

        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2099-06-12', scheduled_time='14:30',
        )

        assert match.tournament_id == t.id
        player_ids = {p.user_id for p in await MatchPlayers.filter(match=match)}
        assert player_ids == {users[0].id, users[1].id}

        game = await BracketMatchGame.get(bracket_match_id=bmatch.id)
        assert game.game_number == 1
        assert game.match_id == match.id
        assert game.state == BracketMatchGameState.SCHEDULED

    async def test_placeholder_entrant_rejected(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _linked_bracket(service, actor, link_users=False)
        with pytest.raises(ValueError, match='players must be linked'):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
            )

    async def test_non_open_match_rejected(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _linked_bracket(service, actor)
        bmatch.state = BracketMatchState.PENDING
        await bmatch.save()
        with pytest.raises(ValueError, match="isn't ready to schedule"):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
            )

    async def test_cancelled_stage_rejected(self, service):
        """Hiding it from the listings is not the whole gate.

        A stale dialog, the REST route and the admin's link picker all reach
        this method directly, and the matchup's own state is still OPEN.
        """
        actor = await _staff()
        _, bracket, _, bmatch = await _linked_bracket(service, actor)
        await service.cancel_stage(actor, bracket.id)
        with pytest.raises(ValueError, match='cancelled'):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
            )
        assert await BracketMatchGame.filter(bracket_match_id=bmatch.id).count() == 0

    async def test_already_scheduled_rejected(self, service):
        actor = await _staff()
        t, _, _, bmatch = await _linked_bracket(service, actor)
        existing = await Match.create(tournament=t)
        await BracketMatchGame.create(
            bracket_match_id=bmatch.id, game_number=1, match_id=existing.id,
        )
        with pytest.raises(ValueError, match='already scheduled'):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
            )

    async def test_non_staff_rejected(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _linked_bracket(service, actor)
        outsider = await _player(5000, 'outsider')
        with pytest.raises(PermissionError):
            await service.schedule_bracket_match(
                outsider, bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
            )

    async def test_entrant_can_schedule_their_own_matchup(self, service):
        """The bracket is a player's only scheduling route once it's attached."""
        actor = await _staff()
        t, _, users, bmatch = await _linked_bracket(service, actor)

        match = await service.schedule_bracket_match(
            users[0], bmatch.id,
            scheduled_date='2099-06-12', scheduled_time='14:30',
        )

        assert match.tournament_id == t.id
        player_ids = {p.user_id for p in await MatchPlayers.filter(match=match)}
        assert player_ids == {users[0].id, users[1].id}
        game = await BracketMatchGame.get(bracket_match_id=bmatch.id)
        assert game.match_id == match.id

    async def test_a_stale_booking_names_who_booked_it_and_when(self, service):
        """Two players with the dialog open: the loser learns who and when.

        It used to read "All 1 game(s) of this series are already scheduled."
        The error states the time in UTC (REST's clock); a web surface rebuilds
        it on its viewer's clock with ``describe``.
        """
        from application.errors import AlreadyBookedError

        actor = await _staff()
        _, _, users, bmatch = await _linked_bracket(service, actor)
        users[0].display_name = 'Alice'
        await users[0].save()
        await service.schedule_bracket_match(
            users[0], bmatch.id, game_number=1,
            scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        with pytest.raises(AlreadyBookedError) as lost:
            await service.schedule_bracket_match(
                users[1], bmatch.id, game_number=1,
                scheduled_date='2099-06-13', scheduled_time='10:00',
            )
        assert str(lost.value) == 'Alice already booked this match for 2099-06-12 18:30 UTC.'
        assert lost.value.describe('Fri 14:30') == 'Alice already booked this match for Fri 14:30.'
        with pytest.raises(AlreadyBookedError, match=r'^You already booked this match'):
            await service.schedule_bracket_match(
                users[0], bmatch.id, game_number=1,
                scheduled_date='2099-06-13', scheduled_time='10:00',
            )

    async def test_a_staff_booking_says_when_without_a_name(self, service):
        from application.errors import AlreadyBookedError

        actor = await _staff()
        _, _, users, bmatch = await _linked_bracket(service, actor)
        await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        with pytest.raises(AlreadyBookedError, match=r'^This match is already booked for 2099-06-12'):
            await service.schedule_bracket_match(
                users[1], bmatch.id, game_number=1,
                scheduled_date='2099-06-13', scheduled_time='10:00',
            )

    async def test_two_players_racing_for_game_2_do_not_book_game_3(self, service):
        """The dialog says "game 2 of 3"; the loser used to silently get game 3."""
        from application.errors import AlreadyBookedError

        actor = await _staff()
        t, bracket, users, bmatch = await _linked_bracket(service, actor)
        bmatch.best_of = 3
        await bmatch.save()
        await service.schedule_bracket_match(
            users[0], bmatch.id, game_number=1,
            scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        await service.schedule_bracket_match(
            users[0], bmatch.id, game_number=2,
            scheduled_date='2099-06-13', scheduled_time='14:30',
        )
        with pytest.raises(AlreadyBookedError, match=r'already booked game 2 of 3'):
            await service.schedule_bracket_match(
                users[1], bmatch.id, game_number=2,
                scheduled_date='2099-06-14', scheduled_time='10:00',
            )
        assert await BracketMatchGame.filter(bracket_match_id=bmatch.id).count() == 2

    async def test_a_cancelled_or_played_slot_is_not_a_lost_race(self, service):
        """G1 played, G2 booked, G3 cancelled on the clinch: a neutral refusal,
        not "X already booked the last game"."""
        from application.errors import AlreadyBookedError
        from models import BracketMatchGameState

        actor = await _staff()
        t, _, users, bmatch = await _linked_bracket(service, actor)
        bmatch.best_of = 3
        await bmatch.save()
        cancelled = await Match.create(tournament=t)
        await BracketMatchGame.create(
            bracket_match_id=bmatch.id, game_number=3, match_id=cancelled.id,
            state=BracketMatchGameState.CANCELLED,
        )
        with pytest.raises(ValueError, match='can no longer be scheduled') as refused:
            await service.schedule_bracket_match(
                users[1], bmatch.id, game_number=3,
                scheduled_date='2099-06-14', scheduled_time='10:00',
            )
        assert not isinstance(refused.value, AlreadyBookedError)

    async def test_entrant_schedules_despite_the_request_toggle(self, service):
        """create_bracket turns the toggle off; the bracket path must still work."""
        actor = await _staff()
        t, _, users, bmatch = await _linked_bracket(service, actor)
        await t.refresh_from_db()
        assert t.allow_player_match_requests is False

        match = await service.schedule_bracket_match(
            users[1], bmatch.id,
            scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        assert match.id is not None

    async def test_entrant_cannot_set_staff_only_fields(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _linked_bracket(service, actor)
        room = await Stage.create(name='Stage 1')
        with pytest.raises(ValueError, match='Only staff can set stage_id'):
            await service.schedule_bracket_match(
                users[0], bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
                stage_id=room.id,
            )

    async def test_tournament_admin_can_schedule(self, service):
        actor = await _staff()
        t, _, _, bmatch = await _linked_bracket(service, actor)
        ta = await _player(6100, 'ta')
        await t.admins.add(ta)

        match = await service.schedule_bracket_match(
            ta, bmatch.id, scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        assert match.tournament_id == t.id


class TestListOpenMatchesForUser:
    async def test_returns_only_users_open_matches(self, service):
        actor = await _staff()
        _, bracket, users, bmatch = await _linked_bracket(service, actor)

        for user in users:
            got = await service.list_open_matches_for_user(user.id)
            assert [m.id for m in got] == [bmatch.id]

        stranger = await _player(7777, 'stranger')
        assert await service.list_open_matches_for_user(stranger.id) == []

    async def test_scheduled_match_drops_off(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _linked_bracket(service, actor)
        await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        assert await service.list_open_matches_for_user(users[0].id) == []

    async def test_a_cancelled_stages_matchups_drop_off(self, service):
        """Cancelling a stage leaves its matchups OPEN — the listing must not.

        ``cancel_stage`` flips only the bracket's own state, by design: the
        played results stay as history. Without a bracket-state filter here the
        abandoned stage kept inviting both of its players to schedule a game in
        it, from the player dashboard's "Upcoming matches to schedule".
        """
        actor = await _staff()
        _, bracket, users, bmatch = await _linked_bracket(service, actor)
        assert await service.list_open_matches_for_user(users[0].id) == [bmatch]

        await service.cancel_stage(actor, bracket.id)

        assert (await service.get_bracket(bracket.id)).state == BracketState.CANCELLED
        assert await bmatch.__class__.get(id=bmatch.id) is not None
        assert await service.list_open_matches_for_user(users[0].id) == []
        assert await service.list_linkable_matches(bracket.tournament_id) == []


class TestAdvanceIfLinked:
    async def _scheduled(self, service, actor):
        t, bracket, users, bmatch = await _linked_bracket(service, actor)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2099-06-12', scheduled_time='14:30',
        )
        return bracket, users, bmatch, match

    async def test_advances_bracket_from_finish_rank(self, service):
        actor = await _staff()
        bracket, users, bmatch, match = await self._scheduled(service, actor)

        await MatchPlayers.filter(match=match, user=users[0]).update(finish_rank=1)
        await MatchPlayers.filter(match=match, user=users[1]).update(finish_rank=2)

        advanced = await service.advance_if_linked(match, actor)
        assert advanced is True

        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.COMPLETE
        winner = await service.repository.get_entry(bmatch.winner_id)
        entrant = await winner.entrant
        assert entrant.user_id == users[0].id

        await bracket.refresh_from_db()
        assert bracket.state == BracketState.COMPLETE

    async def test_non_staff_confirmation_still_advances(self, service):
        actor = await _staff()
        bracket, users, bmatch, match = await self._scheduled(service, actor)
        proctor = await _proctor()
        assert await AuthService.is_staff(proctor) is False

        await MatchPlayers.filter(match=match, user=users[0]).update(finish_rank=1)
        await MatchPlayers.filter(match=match, user=users[1]).update(finish_rank=2)

        advanced = await service.advance_if_linked(match, proctor)
        assert advanced is True

        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.COMPLETE
        winner = await service.repository.get_entry(bmatch.winner_id)
        entrant = await winner.entrant
        assert entrant.user_id == users[0].id

        await bracket.refresh_from_db()
        assert bracket.state == BracketState.COMPLETE

    async def test_unlinked_match_is_noop(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Standalone')
        match = await Match.create(tournament=t)
        assert await service.advance_if_linked(match, actor) is False

    async def test_none_actor_is_noop(self, service):
        actor = await _staff()
        _, _, _, match = await self._scheduled(service, actor)
        assert await service.advance_if_linked(match, None) is False

    async def test_already_complete_is_noop_returns_true(self, service):
        actor = await _staff()
        bracket, users, bmatch, match = await self._scheduled(service, actor)
        # Refresh so saving doesn't clobber the ``match`` link set by scheduling.
        await bmatch.refresh_from_db()
        bmatch.state = BracketMatchState.COMPLETE
        await bmatch.save()
        assert await service.advance_if_linked(match, actor) is True


class TestExclusivity:
    async def test_native_bracket_rejected_when_challonge_linked(self, service):
        actor = await _staff()
        t = await Tournament.create(name='T', challonge_tournament_id='T1')
        with pytest.raises(ValueError, match='never both'):
            await service.create_bracket(actor, t.id, 'Main', 'single_elim')

    async def test_challonge_link_rejected_when_native_bracket_exists(self, service):
        actor = await _staff()
        t = await Tournament.create(name='T')
        await service.create_bracket(actor, t.id, 'Main', 'single_elim')
        with pytest.raises(ValueError, match='native bracket'):
            await ChallongeService().link_tournament(t.id, 'https://challonge.com/x', actor)


async def test_bracket_match_not_schedulable_across_tenants(service, two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        actor = await _staff()
        _, _, _, bmatch = await _linked_bracket(service, actor)

    with tenant_scope(tenant_b.id):
        b_actor = await _staff(discord_id=4321, username='b-staff')
        with pytest.raises(NotFoundError):
            await service.schedule_bracket_match(
                b_actor, bmatch.id,
                scheduled_date='2099-06-12', scheduled_time='14:30',
            )
