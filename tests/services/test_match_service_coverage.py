"""DB-backed coverage tests for MatchService: the reads and the smaller writes.

``create_match`` and ``update_match`` moved to ``test_match_write_coverage.py``
when this module approached the 800-line guideline; the row builders both use
live in ``_match_service_setup``.

Exercises the large untested regions of ``application/services/match/match_service.py``
against the in-memory SQLite ``db`` fixture with a *real* ``MatchService`` (real
repositories, real ``MatchScheduleService``). Permission gates are satisfied by
granting real roles / tournament-admin membership rather than monkeypatching, so
the service's own auth branches are exercised end-to-end. Discord fan-out is
captured (never awaited) by the autouse ``stub_discord_queue`` fixture in
``tests/services/conftest.py``.

The happy-path event fan-out for a handful of methods is already covered with
mocks in ``test_match_service.py``; here we focus on the untested method bodies,
error paths, enrollment, and the player/crew sync helpers.
"""

from datetime import datetime, timezone

import pytest

from application.events import EventType, event_bus
from application.services.match.match_service import MatchService
from application.services.system_config_service import KEY_STATION_FORMAT
from models import (
    Match,
    MatchAcknowledgment,
    MatchPlayers,
    Stage,
    TournamentPlayers,
)
from tests.factories import set_setting, utc
from tests.services._match_service_setup import (
    make_player,
    make_staff,
    make_tournament,
)

UTC = timezone.utc




@pytest.fixture
async def service():
    # Constructed inside the running loop: MatchService -> MatchScheduleService ->
    # DiscordService lazily builds the singleton commands.Bot.
    return MatchService()


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


class TestReadMethods:
    async def test_get_match_by_id_and_get_by_id(self, service, db):
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        assert (await service.get_match_by_id(match.id)).id == match.id
        assert (await service.get_by_id(match.id, prefetch_relations=False)).id == match.id
        assert await service.get_match_by_id(999999) is None

    async def test_get_match_players_and_player_names(self, service, db):
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        alice = await make_player("alice", "Alice")
        bob = await make_player("bob", "Bob")
        await MatchPlayers.create(match=match, user=alice)
        await MatchPlayers.create(match=match, user=bob)

        players = await service.get_match_players(match)
        assert {p.user_id for p in players} == {alice.id, bob.id}

        names = await service.get_player_names(match.id)
        assert "Alice" in names and "Bob" in names

    async def test_get_player_names_empty(self, service, db):
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        assert await service.get_player_names(match.id) == ""

    async def test_list_acknowledgments(self, service, db):
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        user = await make_player()
        await MatchAcknowledgment.create(match=match, user=user)
        acks = await service.list_acknowledgments(match)
        assert len(acks) == 1

    async def test_get_all_matches_for_schedule(self, service, db):
        t = await make_tournament()
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 16, 18))
        assert len(await service.get_all_matches_for_schedule()) == 2

    async def test_get_matches_for_date_and_grouping(self, service, db):
        t = await make_tournament()
        stage = await Stage.create(name="Stage 1")
        await Match.create(tournament=t, stage=stage, scheduled_at=utc(2025, 1, 15, 18))
        # excluded: no stage
        await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19))

        matches = await service.get_matches_for_date(datetime(2025, 1, 15).date())
        assert len(matches) == 1

        grouped = await service.group_matches_by_stage(matches)
        assert stage.id in grouped
        stored_stage, stage_matches = grouped[stage.id]
        assert stored_stage.id == stage.id and len(stage_matches) == 1

    async def test_get_matches_for_player(self, service, db):
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        user = await make_player()
        await MatchPlayers.create(match=match, user=user)
        found = await service.get_matches_for_player(str(user.discord_id))
        assert [m.id for m in found] == [match.id]


class TestSetStreamCandidate:
    async def test_not_found_raises(self, service, db):
        actor = await make_staff()
        with pytest.raises(ValueError, match="Match 999999 not found"):
            await service.set_stream_candidate(match_id=999999, flag=True, actor=actor)

    async def test_set_true_emits_event_and_persists(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        result = await service.set_stream_candidate(match_id=match.id, flag=True, actor=actor)
        assert result.is_stream_candidate is True
        assert (await Match.get(id=match.id)).is_stream_candidate is True
        assert EventType.MATCH_STREAM_CANDIDATE_SET in [e.event_type for e in captured_events]

    async def test_set_false_emits_cleared_event(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 18), is_stream_candidate=True
        )
        await service.set_stream_candidate(match_id=match.id, flag=False, actor=actor)
        assert (await Match.get(id=match.id)).is_stream_candidate is False
        assert EventType.MATCH_STREAM_CANDIDATE_CLEARED in [e.event_type for e in captured_events]

    async def test_set_true_when_already_candidate_skips_notify(self, service, db, captured_events):
        # already a candidate: the `flag and not was_candidate` branch is False, so no
        # stream-candidate notify fan-out happens, but the event still fires.
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(
            tournament=t, scheduled_at=utc(2025, 1, 15, 18), is_stream_candidate=True
        )
        await service.set_stream_candidate(match_id=match.id, flag=True, actor=actor)
        assert (await Match.get(id=match.id)).is_stream_candidate is True
        assert EventType.MATCH_STREAM_CANDIDATE_SET in [e.event_type for e in captured_events]


class TestAssignStage:
    async def test_not_found_raises(self, service, db):
        actor = await make_staff()
        with pytest.raises(ValueError, match="Match 999999 not found"):
            await service.assign_stage(match_id=999999, stage_id=1, actor=actor)

    async def test_assign_then_clear_persists(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        stage = await Stage.create(name="Stage Z")
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))

        await service.assign_stage(match_id=match.id, stage_id=stage.id, actor=actor)
        assert (await Match.get(id=match.id)).stage_id == stage.id

        await service.assign_stage(match_id=match.id, stage_id=None, actor=actor)
        assert (await Match.get(id=match.id)).stage_id is None


class TestAssignStations:
    async def test_not_found_raises(self, service, db):
        actor = await make_staff()
        with pytest.raises(ValueError, match="Match 999999 not found"):
            await service.assign_stations(match_id=999999, assignments={}, actor=actor)

    async def test_invalid_format_raises(self, service, db):
        await set_setting(name=KEY_STATION_FORMAT, value="numeric")
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        player = await make_player()
        mp = await MatchPlayers.create(match=match, user=player)
        with pytest.raises(ValueError, match="does not match the required format"):
            await service.assign_stations(match_id=match.id, assignments={mp.id: "abc"}, actor=actor)

    async def test_valid_assignment_persists(self, service, db, captured_events):
        await set_setting(name=KEY_STATION_FORMAT, value="numeric")
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        p1 = await make_player("a", "A")
        p2 = await make_player("b", "B")
        mp1 = await MatchPlayers.create(match=match, user=p1)
        mp2 = await MatchPlayers.create(match=match, user=p2)

        # only mp1 is assigned; mp2 stays untouched
        await service.assign_stations(match_id=match.id, assignments={mp1.id: "5"}, actor=actor)

        assert (await MatchPlayers.get(id=mp1.id)).assigned_station == "5"
        assert (await MatchPlayers.get(id=mp2.id)).assigned_station is None
        assert EventType.MATCH_STATIONS_ASSIGNED in [e.event_type for e in captured_events]

    async def test_racetime_tournament_rejects_station_assignment(self, service, db):
        from models import RacetimeBot

        await set_setting(name=KEY_STATION_FORMAT, value="numeric")
        actor = await make_staff()
        bot = await RacetimeBot.create(
            category="alttpr", client_id="cid", client_secret="secret", name="Bot",
        )
        t = await make_tournament(racetime_bot=bot)
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        player = await make_player()
        mp = await MatchPlayers.create(match=match, user=player)

        with pytest.raises(ValueError, match=r"racetime\.gg"):
            await service.assign_stations(match_id=match.id, assignments={mp.id: "5"}, actor=actor)

        assert (await MatchPlayers.get(id=mp.id)).assigned_station is None


class TestDeleteMatch:
    async def test_not_found_raises(self, service, db):
        actor = await make_staff()
        with pytest.raises(ValueError, match="Match 999999 not found"):
            await service.delete_match(match_id=999999, actor=actor)

    async def test_delete_removes_row_and_emits_event(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        await service.delete_match(match_id=match.id, actor=actor)
        assert await Match.filter(id=match.id).exists() is False
        assert EventType.MATCH_DELETED in [e.event_type for e in captured_events]


class TestRecordMatchResult:
    async def test_not_found_raises(self, service, db):
        actor = await make_staff()
        with pytest.raises(ValueError, match="Match 999999 not found"):
            await service.record_match_result(match_id=999999, winner_id=1, actor=actor)

    async def test_no_players_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        with pytest.raises(ValueError, match="no players"):
            await service.record_match_result(match_id=match.id, winner_id=1, actor=actor)

    async def test_winner_not_a_player_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        p1 = await make_player("a", "A")
        await MatchPlayers.create(match=match, user=p1)
        with pytest.raises(ValueError, match="isn't part of this match"):
            await service.record_match_result(match_id=match.id, winner_id=424242, actor=actor)

    async def test_records_ranks(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        p1 = await make_player("a", "A")
        p2 = await make_player("b", "B")
        mp1 = await MatchPlayers.create(match=match, user=p1)
        mp2 = await MatchPlayers.create(match=match, user=p2)

        await service.record_match_result(match_id=match.id, winner_id=mp1.id, actor=actor)

        assert (await MatchPlayers.get(id=mp1.id)).finish_rank == 1
        assert (await MatchPlayers.get(id=mp2.id)).finish_rank == 2
        assert EventType.MATCH_RESULT_RECORDED in [e.event_type for e in captured_events]


class TestEnrollmentAndSeedHelpers:
    async def test_ensure_players_enrolled_enrolls_and_skips(self, service, db):
        t = await make_tournament()
        already = await make_player("already")
        fresh = await make_player("fresh")
        await TournamentPlayers.create(tournament=t, user=already)

        actor = await make_player("scheduler")
        await service.ensure_players_enrolled(t.id, [already.id, fresh.id], actor)

        assert await TournamentPlayers.filter(tournament=t, user=already).count() == 1
        assert await TournamentPlayers.filter(tournament=t, user=fresh).exists()

    async def test_ensure_players_enrolled_missing_user_raises(self, service, db):
        t = await make_tournament()
        actor = await make_player("scheduler2")
        with pytest.raises(ValueError, match="User 424242 not found"):
            await service.ensure_players_enrolled(t.id, [424242], actor)

    async def test_seed_acknowledgments_skips_missing_user(self, service, db):
        t = await make_tournament()
        match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 18))
        # id 424242 has no User row -> the loop `continue`s and no ack is created
        await service._seed_acknowledgments(match, [424242], None)
        assert await MatchAcknowledgment.filter(match=match).count() == 0
