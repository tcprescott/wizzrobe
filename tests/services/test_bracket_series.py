"""Best-of-N series: numbering, clinch, teardown, and the racetime auto-open hold.

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). Two of
these are regression guards for failure modes that are silent in production:

* ``test_slipped_game_still_opens`` — the auto-open poll's scan starts at
  ``now - 15min``, so a game held behind a long-running previous game drops out
  of it permanently. Without the push at game-end, "defer" would mean "never".
* ``test_double_settle_counts_one_win`` — two independent paths settle a game
  (racetime finish, human confirm). Counting one game twice clinches a Bo3 off a
  single game.
"""

from datetime import timedelta

import pytest

from application.services import race_room_worker
from application.services.bracket_service import BracketService
from models import (
    BracketMatchGame,
    BracketMatchGameState,
    BracketMatchState,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeRoom,
    Role,
    Tournament,
    User,
    UserRole,
)
from tests.factories import join_community, utc

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def service() -> BracketService:
    return BracketService()


async def _staff() -> User:
    user = await User.create(discord_id=1234, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _series(service, actor, *, best_of: int):
    """A started 2-entrant single-elim whose round-1 match is a best-of-N."""
    tournament = await Tournament.create(name='Cup')
    bracket = await service.create_bracket(
        actor, tournament.id, 'Main', 'single_elim',
        config={'rounds': {'1': {'best_of': best_of}}},
    )
    users = []
    for i in (1, 2):
        user = await User.create(discord_id=1000 + i, username=f'p{i}')
        await join_community(user)
        users.append(user)
        entrant = await service.add_entrant(actor, tournament.id, f'P{i}', user_id=user.id)
        await service.enroll(actor, bracket.id, entrant.id, seed=i)
    await service.start_bracket(actor, bracket.id)
    bmatch = (await service.get_open_matches(bracket.id))[0]
    return tournament, bracket, users, bmatch


async def _play(service, actor, match: Match, winner: User) -> None:
    """Record ``winner`` as the rank-1 finisher and settle the game."""
    await MatchPlayers.filter(match=match, user=winner).update(finish_rank=1)
    await MatchPlayers.filter(match=match).exclude(user=winner).update(finish_rank=2)
    await service.settle_game_if_linked(match, actor)


class TestBestOfResolution:
    async def test_override_beats_round_config_beats_default(self, service):
        actor = await _staff()
        _, bracket, _, bmatch = await _series(service, actor, best_of=3)
        assert service.resolve_best_of(bracket, bmatch) == 3

        bmatch.best_of = 5
        assert service.resolve_best_of(bracket, bmatch) == 5

        bmatch.best_of = None
        bmatch.round = 99  # a round the config says nothing about
        assert service.resolve_best_of(bracket, bmatch) == 1

    async def test_wins_needed(self, service):
        assert [service.wins_needed(n) for n in (1, 3, 5, 7)] == [1, 2, 3, 4]

    async def test_best_of_locked_once_games_exist(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)
        await service.set_best_of(actor, bmatch.id, 5)  # fine before scheduling

        await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        with pytest.raises(ValueError, match='already has scheduled games'):
            await service.set_best_of(actor, bmatch.id, 3)

    async def test_even_best_of_rejected(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)
        with pytest.raises(ValueError, match='odd'):
            await service.set_best_of(actor, bmatch.id, 2)


class TestGameNumbering:
    async def test_numbers_are_assigned_in_order(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)

        for expected in (1, 2, 3):
            match = await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )
            game = await BracketMatchGame.get(match_id=match.id)
            assert game.game_number == expected

    async def test_rejects_a_fourth_game_of_a_bo3(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)
        for _ in range(3):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )
        with pytest.raises(ValueError, match='already scheduled'):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )

    async def test_series_games_get_a_self_describing_title(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        # Not a bare "Game 1 of 3": Match.title replaces the whole Discord event title.
        assert 'Cup' in match.title
        assert 'P1' in match.title and 'P2' in match.title
        assert 'Game 1 of 3' in match.title

    async def test_best_of_one_keeps_a_null_title(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        assert match.title is None  # existing Bo1 events/rooms render unchanged


class TestClinch:
    async def test_bo3_does_not_advance_on_the_first_game(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )

        await _play(service, actor, match, users[0])

        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.OPEN
        assert await service.series_standing(bmatch) == (1, 0)

    async def test_bo3_clinches_at_two_nil_and_cancels_game_three(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        matches = [
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )
            for _ in range(3)
        ]

        await _play(service, actor, matches[0], users[0])
        await _play(service, actor, matches[1], users[0])

        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.COMPLETE
        assert (bmatch.entry1_score, bmatch.entry2_score) == (2, 0)

        game3 = await BracketMatchGame.get(match_id=None, game_number=3)
        assert game3.state == BracketMatchGameState.CANCELLED
        assert 'clinched 2-0' in game3.cancelled_reason
        assert await Match.filter(id=matches[2].id).first() is None

    async def test_bo3_goes_the_distance(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        matches = [
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )
            for _ in range(3)
        ]

        await _play(service, actor, matches[0], users[0])
        await _play(service, actor, matches[1], users[1])
        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.OPEN  # 1-1, still live

        await _play(service, actor, matches[2], users[1])
        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.COMPLETE
        assert (bmatch.entry1_score, bmatch.entry2_score) == (1, 2)

    async def test_double_settle_counts_one_win(self, service):
        """A racetime finish then a human confirm must not count the game twice."""
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )

        await _play(service, actor, match, users[0])
        await service.settle_game_if_linked(match, actor)  # the second path

        assert await service.series_standing(bmatch) == (1, 0)
        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.OPEN

    async def test_scheduling_a_decided_series_is_rejected(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        for _ in range(2):
            match = await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )
            await _play(service, actor, match, users[0])

        with pytest.raises(ValueError, match='already decided'):
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )


class TestBestOfOneUnchanged:
    async def test_single_game_clinches_and_advances_as_before(self, service):
        """The regression guard: a Bo1 must behave exactly as it did pre-series."""
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )

        await _play(service, actor, match, users[0])

        await bmatch.refresh_from_db()
        assert bmatch.state == BracketMatchState.COMPLETE
        assert bmatch.winner_id == bmatch.entry1_id


class TestStaffOverrideReconciles:
    async def test_report_result_cancels_pending_games(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)
        matches = [
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time='14:30',
            )
            for _ in range(3)
        ]

        await service.report_result(
            actor, bmatch.id, bmatch.entry1_id, entry1_score=2, entry2_score=0,
        )

        games = await service.repository.list_games(bmatch.id)
        assert all(g.state == BracketMatchGameState.CANCELLED for g in games)
        for match in matches:
            assert await Match.filter(id=match.id).first() is None


class TestAutoOpenHold:
    async def _racetime_series(self, service, actor, *, best_of=3):
        tournament, bracket, users, bmatch = await _series(
            service, actor, best_of=best_of
        )
        bot = await RacetimeBot.create(
            category='alttpr', client_id='c', client_secret='s', name='bot',
        )
        tournament.racetime_bot = bot
        tournament.racetime_auto_create_rooms = True
        tournament.racetime_default_goal = "Beat the game"
        tournament.room_open_minutes_before = 30
        await tournament.save()
        for user in users:
            user.racetime_user_id = f'rt-{user.id}'
            await user.save()
        return tournament, users, bmatch

    async def test_game_two_is_held_while_game_one_runs(self, service, monkeypatch):
        actor = await _staff()
        _, _, bmatch = await self._racetime_series(service, actor)
        now = utc(2026, 6, 12, 14, 30)
        monkeypatch.setattr(
            race_room_worker, 'datetime', _FrozenDatetime(now), raising=False,
        )

        m1 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:30',
        )
        m2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:35',
        )

        held = await service.held_match_ids([m1.id, m2.id])
        assert held == {m2.id}

    async def test_game_two_is_released_once_game_one_finishes(self, service):
        actor = await _staff()
        _, _, bmatch = await self._racetime_series(service, actor)
        m1 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:30',
        )
        m2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:35',
        )

        m1.finished_at = utc(2026, 6, 12, 11, 30)
        await m1.save()

        assert await service.held_match_ids([m2.id]) == set()

    async def test_a_non_series_match_is_never_held(self, service):
        tournament = await Tournament.create(name='Solo')
        match = await Match.create(
            tournament=tournament, scheduled_at=utc(2026, 6, 12, 14, 30),
        )
        assert await service.held_match_ids([match.id]) == set()

    async def test_a_prior_game_with_no_match_fails_open(self, service):
        """A deleted prior Match must not deadlock the series forever."""
        actor = await _staff()
        _, _, bmatch = await self._racetime_series(service, actor)
        m1 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:30',
        )
        m2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:35',
        )
        await Match.filter(id=m1.id).delete()  # SET_NULL leaves game 1 unresolved

        assert await service.held_match_ids([m2.id]) == set()

    async def test_a_cancelled_game_is_held(self, service):
        actor = await _staff()
        _, _, bmatch = await self._racetime_series(service, actor)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:30',
        )
        await BracketMatchGame.filter(match_id=match.id).update(
            state=BracketMatchGameState.CANCELLED,
        )
        assert await service.held_match_ids([match.id]) == {match.id}

    async def test_slipped_game_still_opens(self, service):
        """The regression guard for the GRACE_MINUTES hole.

        Game 2's slot is 45 minutes in the past because game 1 ran long, so the
        poll's own ``now - 15min`` scan can no longer see it. The series backstop
        scan must, and ``auto_open_if_eligible`` must then open it.
        """
        actor = await _staff()
        _, _, bmatch = await self._racetime_series(service, actor)
        m1 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='08:00',
        )
        m2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='09:00',
        )
        m1.finished_at = m2.scheduled_at + timedelta(minutes=45)
        await m1.save()

        due = await service.series_matches_due(
            m2.scheduled_at - timedelta(hours=12),
            m2.scheduled_at + timedelta(days=7),
        )
        assert m2.id in {m.id for m in due}

        from application.services.race_room_service import RaceRoomService

        match = await RaceRoomService()._load_match(m2.id)
        room = await RaceRoomService().auto_open_if_eligible(
            match, now=m2.scheduled_at + timedelta(minutes=45), actor=actor,
        )
        assert room is not None
        assert room.status == RaceRoomStatus.OPEN


class _FrozenDatetime:
    """Minimal ``datetime`` stand-in so a worker tick sees a fixed 'now'."""

    def __init__(self, now):
        self._now = now

    def now(self, tz=None):
        return self._now


class TestRoomIsNotOpenedForAHeldGame:
    async def test_worker_tick_skips_the_held_game(self, service, monkeypatch):
        actor = await _staff()
        tournament = await Tournament.create(name='Cup2')
        bot = await RacetimeBot.create(
            category='alttpr', client_id='c', client_secret='s', name='bot',
        )
        tournament.racetime_bot = bot
        tournament.racetime_auto_create_rooms = True
        tournament.racetime_default_goal = "Beat the game"
        await tournament.save()

        bracket = await service.create_bracket(
            actor, tournament.id, 'Main', 'single_elim',
            config={'rounds': {'1': {'best_of': 3}}},
        )
        for i in (1, 2):
            user = await User.create(
                discord_id=2000 + i, username=f'q{i}', racetime_user_id=f'rt{i}',
            )
            await join_community(user)
            entrant = await service.add_entrant(
                actor, tournament.id, f'Q{i}', user_id=user.id
            )
            await service.enroll(actor, bracket.id, entrant.id, seed=i)
        await service.start_bracket(actor, bracket.id)
        bmatch = (await service.get_open_matches(bracket.id))[0]

        m1 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:30',
        )
        m2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='10:35',
        )
        monkeypatch.setattr(
            race_room_worker, 'datetime',
            _FrozenDatetime(m1.scheduled_at), raising=False,
        )

        await race_room_worker._tick()

        assert await RacetimeRoom.filter(match_id=m1.id).exists()
        assert not await RacetimeRoom.filter(match_id=m2.id).exists()


class TestScheduleBracketLink:
    """The schedule's link back into the bracket a game belongs to.

    Exercises the real prefetch (``bracket_match_game__bracket_match__bracket``,
    a reverse OneToOne), which a SimpleNamespace test cannot: the row field is
    only useful if the relation actually hydrates.
    """

    async def test_row_carries_the_bracket_it_settles(self, service):
        from application.services.match.match_display_service import MatchDisplayService

        actor = await _staff()
        _, bracket, _, bmatch = await _series(service, actor, best_of=3)
        await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        game2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-13', scheduled_time='14:30',
        )

        rows = {r['id']: r for r in await MatchDisplayService().get_matches_for_display()}

        assert rows[game2.id]['bracket'] == {
            'id': bracket.id, 'name': 'Main', 'game': 2,
            'best_of': 3, 'standing': '',
        }
        # Game 1 of a best-of-3 still says so, so the run sheet reads "Game 1 of 3".
        first = next(r for r in rows.values() if r['bracket'] and r['id'] != game2.id)
        assert first['bracket']['game'] == 1

    async def test_ordinary_match_has_no_bracket(self, service):
        from application.services.match.match_display_service import MatchDisplayService

        tournament = await Tournament.create(name='Unbracketed')
        match = await Match.create(tournament=tournament, scheduled_at=utc(2026, 6, 12, 18, 0))

        rows = {r['id']: r for r in await MatchDisplayService().get_matches_for_display()}
        assert rows[match.id]['bracket'] is None
