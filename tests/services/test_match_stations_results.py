"""The on-site desk, against a real database: where players sit, and who won.

Split from ``test_match_service.py`` when that module approached the 800-line
guideline. The split is along the seam that was already there: everything left
in the sibling drives a ``MatchService`` whose repositories are mocks, and
everything here needs real rows — the station validation ladder reads the
tenant's station pool and derives occupancy from *other* matches, and the result
assertions are about what was persisted.

Both halves cover code that now lives in mixins (``StationAssignmentMixin``,
``match_service``'s result path); they are composed into ``MatchService``, so
the tests still drive the one service class.
"""

import itertools
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from application.services.match.match_service import MatchService
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


@pytest.fixture
def captured_events():
    """Subscribe to the event bus for the test and collect published events."""
    from application.events import event_bus

    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


# ---------------------------------------------------------------------------
# station pool validation (T2.3) — real DB, since the ladder reads the station
# pool and derives occupancy from other matches.


_station_seq = itertools.count(1)


async def _make_onsite_match(*, seated=False, finished=False, stations=()):
    """A two-player on-site match, optionally seated/finished with stations set."""
    from models import Match, MatchPlayers, Tournament

    n = next(_station_seq)
    tournament = await Tournament.create(name=f'Station Cup {n}')
    match = await Match.create(
        tournament=tournament,
        seated_at=datetime(2025, 1, 15, 19, 0) if (seated or finished) else None,
        finished_at=datetime(2025, 1, 15, 20, 0) if finished else None,
    )
    players = []
    for slot in (1, 2):
        user = await make_user(discord_id=n * 10 + slot, username=f'station-{n}-{slot}')
        players.append(await MatchPlayers.create(match=match, user=user))
    for player, station in zip(players, stations, strict=False):
        player.assigned_station = station
        await player.save()
    return match, players


# ---------------------------------------------------------------------------
# record_match_result — real DB, so the ranks are asserted as persisted.


class TestRecordMatchResult:
    """The contract the one-tap winner buttons in ``MatchResultDialog`` rest on:
    the dialog sends a ``MatchPlayers`` row id and nothing else."""

    @pytest.fixture
    async def actor(self, db):
        return await make_user(discord_id=9101, username='result-staff')

    async def test_record_result_sets_rank_one_and_two_for_two_players(self, db, actor):
        match, players = await _make_onsite_match(seated=True)

        await MatchService().record_match_result(
            match.id, winner_id=players[1].id, actor=actor,
        )

        for player in players:
            await player.refresh_from_db()
        assert players[1].finish_rank == 1
        assert players[0].finish_rank == 2

    async def test_record_result_rejects_a_non_participant_winner(self, db, actor):
        match, players = await _make_onsite_match(seated=True)
        _, other_players = await _make_onsite_match()

        with pytest.raises(ValueError, match="isn't part of this match"):
            await MatchService().record_match_result(
                match.id, winner_id=other_players[0].id, actor=actor,
            )

        for player in players:
            await player.refresh_from_db()
        assert all(p.finish_rank is None for p in players)


class TestReRecordMatchResult:
    """An admin correcting a result the proctor already recorded (T4.2).

    The pencil on a Finished row reaches ``record_match_result`` a second time;
    nothing else in the service changes, so what these pin is that a second call
    is *allowed* and fully rewrites the ranks.
    """

    @pytest.fixture
    async def actor(self, db):
        return await make_user(discord_id=9102, username='correcting-staff')

    async def test_result_can_be_re_recorded_for_a_non_bracket_match(self, db, actor):
        match, players = await _make_onsite_match(finished=True)
        service = MatchService()
        await service.record_match_result(match.id, winner_id=players[0].id, actor=actor)

        await service.record_match_result(match.id, winner_id=players[0].id, actor=actor)

        for player in players:
            await player.refresh_from_db()
        assert players[0].finish_rank == 1
        assert players[1].finish_rank == 2

    async def test_re_recording_swaps_the_ranks(self, db, actor):
        match, players = await _make_onsite_match(finished=True)
        service = MatchService()
        await service.record_match_result(match.id, winner_id=players[0].id, actor=actor)

        await service.record_match_result(match.id, winner_id=players[1].id, actor=actor)

        for player in players:
            await player.refresh_from_db()
        assert players[1].finish_rank == 1
        assert players[0].finish_rank == 2

    async def test_re_recording_a_settled_bracket_game_raises(self, db, actor):
        """The amber toast the UI shows instead of changing the winner.

        ``test_bracket_match_integration`` proves the guard fires off a real
        settled series; this pins that ``record_match_result`` still consults it
        and leaves the recorded ranks alone, since the correction pencil is the
        caller that now hits it most.
        """
        from models import BracketMatchGameState

        match, players = await _make_onsite_match(finished=True)
        service = MatchService()
        await service.record_match_result(match.id, winner_id=players[0].id, actor=actor)

        settled = SimpleNamespace(state=BracketMatchGameState.COMPLETE, bracket_match=None)
        with patch(
            'application.services.bracket_service.BracketService.get_game_for_match',
            AsyncMock(return_value=settled),
        ):
            with pytest.raises(ValueError, match='Correct it from the bracket'):
                await service.record_match_result(
                    match.id, winner_id=players[1].id, actor=actor,
                )

        for player in players:
            await player.refresh_from_db()
        assert players[0].finish_rank == 1
        assert players[1].finish_rank == 2


class TestFlagForReview:
    """The dispute flag (T4.4): a proctor's "an admin should look at this".

    A flag plus a note, never a state — the match stays Finished throughout, and
    these pin the two things that make it trustworthy: it refuses a match that
    was never played out, and it keeps the proctor's own words verbatim.
    """

    @pytest.fixture
    async def actor(self, db):
        return await make_user(discord_id=9103, username='flagging-proctor')

    async def test_flag_for_review_requires_a_finished_match(self, db, actor):
        match, _ = await _make_onsite_match(seated=True)

        with pytest.raises(ValueError, match='Only a finished match'):
            await MatchService().flag_for_review(match.id, actor)

        await match.refresh_from_db()
        assert match.needs_review is False

    async def test_flag_for_review_raises_the_flag(self, db, actor):
        match, _ = await _make_onsite_match(finished=True)

        await MatchService().flag_for_review(match.id, actor)

        await match.refresh_from_db()
        assert match.needs_review is True

    async def test_flagging_publishes_the_event(self, db, actor, captured_events):
        from application.events import EventType

        match, _ = await _make_onsite_match(finished=True)

        await MatchService().flag_for_review(match.id, actor)

        flagged = [e for e in captured_events
                   if e.event_type == EventType.MATCH_FLAGGED_FOR_REVIEW]
        assert len(flagged) == 1
        assert flagged[0].payload['match_id'] == match.id
        assert flagged[0].payload['tournament_id'] == match.tournament_id

    async def test_clear_review_drops_the_flag(self, db, actor):
        match, _ = await _make_onsite_match(finished=True)
        service = MatchService()
        await service.flag_for_review(match.id, actor)

        await service.clear_review(match.id, actor)

        await match.refresh_from_db()
        assert match.needs_review is False


class TestStationAssignmentValidation:
    """The ``assign_stations`` ladder: duplicate → format → pool → occupancy."""

    @pytest.fixture
    async def actor(self, db):
        return await make_user(discord_id=9001, username='station-staff')

    async def test_rejects_same_station_twice_in_one_match(self, db, actor):
        match, players = await _make_onsite_match()
        with pytest.raises(ValueError, match='more than one player'):
            await MatchService().assign_stations(
                match.id, {players[0].id: '3', players[1].id: '3'}, actor=actor,
            )

    async def test_rejects_station_outside_the_pool(self, db, actor):
        from models import Station

        await Station.create(name='1')
        match, players = await _make_onsite_match()
        with pytest.raises(ValueError, match='not one of'):
            await MatchService().assign_stations(match.id, {players[0].id: '99'}, actor=actor)

    async def test_allows_free_text_when_pool_is_empty(self, db, actor):
        # No Station rows -> historical behaviour, format regex only.
        match, players = await _make_onsite_match()
        await MatchService().assign_stations(match.id, {players[0].id: 'anything'}, actor=actor)
        await players[0].refresh_from_db()
        assert players[0].assigned_station == 'anything'

    async def test_rejects_station_in_use_by_a_live_match(self, db, actor):
        """And names the match sitting there, rather than quoting its id.

        The proctor's next move is to go and find those players; "match #23"
        is the one thing that does not help them do it.
        """
        from models import Station

        await Station.create(name='4')
        other, other_players = await _make_onsite_match(seated=True, stations=('4',))
        match, players = await _make_onsite_match()
        with pytest.raises(ValueError) as exc:
            await MatchService().assign_stations(match.id, {players[0].id: '4'}, actor=actor)
        message = str(exc.value)
        assert f'#{other.id}' not in message
        for player in other_players:
            assert (await player.user).preferred_name in message

    async def test_frees_the_station_once_the_other_match_finishes(self, db, actor):
        from models import Station

        await Station.create(name='4')
        await _make_onsite_match(seated=True, finished=True, stations=('4',))
        match, players = await _make_onsite_match()
        await MatchService().assign_stations(match.id, {players[0].id: '4'}, actor=actor)
        await players[0].refresh_from_db()
        assert players[0].assigned_station == '4'

    async def test_reassigning_a_match_to_its_own_station_is_allowed(self, db, actor):
        # exclude_match_id must not make a match collide with itself.
        from models import Station

        for name in ('4', '5'):
            await Station.create(name=name)
        match, players = await _make_onsite_match()
        match.seated_at = datetime(2025, 1, 15, 19, 0)
        await match.save()
        await MatchService().assign_stations(match.id, {players[0].id: '4'}, actor=actor)
        await MatchService().assign_stations(
            match.id, {players[0].id: '4', players[1].id: '5'}, actor=actor,
        )
        await players[1].refresh_from_db()
        assert players[1].assigned_station == '5'

    async def test_names_every_clash_at_once(self, db, actor):
        """A 23-player race fixes every clash in one pass, not one per submit."""
        other, _ = await _make_onsite_match(seated=True, stations=('4', '5'))
        match, players = await _make_onsite_match()
        with pytest.raises(ValueError) as exc:
            await MatchService().assign_stations(
                match.id, {players[0].id: '5', players[1].id: '4'}, actor=actor,
            )
        assert 'Stations 4 and 5 are in use by' in str(exc.value)

    async def test_one_shared_station_reads_as_before(self, db, actor):
        match, players = await _make_onsite_match()
        with pytest.raises(ValueError) as exc:
            await MatchService().assign_stations(
                match.id, {players[0].id: '3', players[1].id: '3'}, actor=actor,
            )
        assert str(exc.value) == 'Station 3 is assigned to more than one player in this match.'
