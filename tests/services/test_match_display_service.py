from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.services.match.match_display_service import MatchDisplayService
from application.utils.timezone import format_local_datetime
from models import Match, RacetimeBot, Tournament


@pytest.fixture
def display_service():
    # _get_match_state / _format_match_for_display are pure formatting helpers
    # (no repository access), so a plain instance is sufficient.
    return MatchDisplayService()


def make_match(**overrides):
    defaults = dict(
        id=1,
        tournament_id=1,
        title=None,
        seated_at=None,
        started_at=None,
        finished_at=None,
        confirmed_at=None,
        created_at=datetime(2025, 1, 1, 12, 0),
        scheduled_at=datetime(2025, 1, 15, 19, 30),
        comment=None,
        tournament=SimpleNamespace(
            name="Test Tournament", short_label="Test", seed_generator=None,
            is_racetime_enabled=False, required_commentators=1, required_trackers=1,
        ),
        stage=None,
        stage_id=None,
        generated_seed=None,
        is_stream_candidate=False,
        needs_review=False,
        players=[],
        commentators=[],
        trackers=[],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_player(name, *, discord_id="111", dm_notifications=True,
                finish_rank=None, station=None, user_id=7, player_id=70):
    """A ``MatchPlayers``-shaped fake, including the two DM-reachability fields.

    ``discord_id=None`` / ``dm_notifications=False`` are the two ways a player
    ends up in ``seed_dm_blocked``.
    """
    return SimpleNamespace(
        user=SimpleNamespace(
            preferred_name=name,
            discord_id=discord_id,
            dm_notifications=dm_notifications,
        ),
        id=player_id,
        user_id=user_id,
        finish_rank=finish_rank,
        assigned_station=station,
    )


# ---------------------------------------------------------------------------
# _get_match_state
# ---------------------------------------------------------------------------


class TestGetMatchState:
    def test_scheduled_when_no_timestamps(self, display_service):
        assert display_service._get_match_state(make_match()) == "Scheduled"

    def test_checked_in_when_only_seated(self, display_service):
        match = make_match(seated_at=datetime.now())
        assert display_service._get_match_state(match) == "Checked In"

    def test_started_when_seated_and_started(self, display_service):
        now = datetime.now()
        match = make_match(seated_at=now, started_at=now)
        assert display_service._get_match_state(match) == "Started"

    def test_finished_when_finished_set(self, display_service):
        now = datetime.now()
        match = make_match(seated_at=now, started_at=now, finished_at=now)
        assert display_service._get_match_state(match) == "Finished"

    def test_confirmed_takes_highest_priority(self, display_service):
        now = datetime.now()
        match = make_match(seated_at=now, started_at=now, finished_at=now, confirmed_at=now)
        assert display_service._get_match_state(match) == "Confirmed"

    def test_confirmed_beats_finished(self, display_service):
        now = datetime.now()
        # Even without started_at, confirmed_at should dominate
        match = make_match(finished_at=now, confirmed_at=now)
        assert display_service._get_match_state(match) == "Confirmed"


# ---------------------------------------------------------------------------
# _format_match_for_display
# ---------------------------------------------------------------------------


class TestHasResult:
    """``has_result`` is what the boards gate the Confirm control on.

    It must answer the same question ``MatchScheduleService.confirm_match``
    enforces (both call ``has_recorded_result``), or a board hides a Confirm the
    service would have accepted.
    """

    def test_false_with_no_players(self, display_service):
        assert display_service._format_match_for_display(make_match())["has_result"] is False

    def test_false_when_nobody_is_ranked(self, display_service):
        players = [make_player("Alice"), make_player("Bob")]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["has_result"] is False

    def test_true_when_a_winner_is_ranked(self, display_service):
        players = [make_player("Alice", finish_rank=1), make_player("Bob", finish_rank=2)]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["has_result"] is True

    def test_true_when_ranks_exist_but_none_is_first(self, display_service):
        """A racetime sync passes ``entrant.place`` straight through.

        An unlinked place-1 entrant leaves the linked player on rank 2, and
        ``confirm_match`` accepts that — so the board must not call it resultless.
        """
        players = [make_player("Alice", finish_rank=2)]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["has_result"] is True


class TestFormatMatchForDisplay:
    def test_returns_id(self, display_service):
        result = display_service._format_match_for_display(make_match(id=77))
        assert result["id"] == 77

    def test_state_is_scheduled_by_default(self, display_service):
        result = display_service._format_match_for_display(make_match())
        assert result["state"] == "Scheduled"

    def test_state_timestamp_falls_back_to_created_at_when_scheduled(self, display_service):
        result = display_service._format_match_for_display(make_match())
        assert result["state_timestamp"] == format_local_datetime(
            datetime(2025, 1, 1, 12, 0)
        )

    def test_stage_empty_when_none(self, display_service):
        result = display_service._format_match_for_display(make_match(stage=None))
        assert result["stage"] == ""

    def test_stage_name_when_set(self, display_service):
        match = make_match(stage=SimpleNamespace(name="Stage 1", stream_url="https://twitch.tv/sglive1"))
        result = display_service._format_match_for_display(match)
        assert result["stage"] == "Stage 1"
        assert result["stage_url"] == "https://twitch.tv/sglive1"

    def test_stage_url_empty_when_none(self, display_service):
        result = display_service._format_match_for_display(make_match(stage=None))
        assert result["stage_url"] == ""

    def test_stage_url_empty_for_invalid_scheme(self, display_service):
        match = make_match(stage=SimpleNamespace(name="Stage 1", stream_url="javascript:alert(1)"))
        result = display_service._format_match_for_display(match)
        assert result["stage_url"] == ""

    def test_no_crew_needed_without_a_stage(self, display_service):
        result = display_service._format_match_for_display(make_match(stage=None, stage_id=None))
        assert result["crew_required"] == {"commentators": 0, "trackers": 0}

    def test_crew_needed_once_on_a_stage(self, display_service):
        match = make_match(stage=SimpleNamespace(name="Stage 1", stream_url=None), stage_id=3)
        result = display_service._format_match_for_display(match)
        assert result["crew_required"] == {"commentators": 1, "trackers": 1}

    def test_seed_empty_when_none(self, display_service):
        result = display_service._format_match_for_display(make_match(generated_seed=None))
        assert result["seed"] == ""

    def test_seed_url_when_set(self, display_service):
        match = make_match(generated_seed=SimpleNamespace(seed_url="https://alttpr.com/h/abc", seed_code=None))
        result = display_service._format_match_for_display(match)
        assert result["seed"] == "https://alttpr.com/h/abc"
        assert result["seed_code"] == ""

    def test_seed_code_is_one_line(self, display_service):
        match = make_match(generated_seed=SimpleNamespace(
            seed_url="https://alttpr.com/h/abc", seed_code=["Bow", "Hookshot", "Bombs", "Lamp", "Cape"],
        ))
        result = display_service._format_match_for_display(match)
        assert result["seed_code"] == "Code: Bow · Hookshot · Bombs · Lamp · Cape"

    def test_tournament_name_in_result(self, display_service):
        result = display_service._format_match_for_display(make_match())
        assert result["tournament"] == "Test Tournament"
        assert result["tournament_short"] == "Test"

    def test_tournament_empty_when_none(self, display_service):
        match = make_match(tournament=None)
        result = display_service._format_match_for_display(match)
        assert result["tournament"] == ""

    def test_players_formatted_as_dicts(self, display_service):
        player = make_player("Alice", discord_id="111", finish_rank=1, station="A")
        result = display_service._format_match_for_display(make_match(players=[player]))
        assert result["players"] == [
            {"name": "Alice", "player_id": 70, "user_id": 7, "finish_rank": 1, "station": "A", "discord_id": "111"}
        ]

    def test_multiple_players_all_included(self, display_service):
        players = [
            make_player("Alice", discord_id="111", finish_rank=1, station="A"),
            make_player("Bob", discord_id="222", finish_rank=2, station="B"),
        ]
        result = display_service._format_match_for_display(make_match(players=players))
        assert len(result["players"]) == 2

    def test_commentators_formatted_as_dicts(self, display_service):
        commentator = SimpleNamespace(
            id=11,
            user_id=7,
            user=SimpleNamespace(preferred_name="Charlie", discord_id="123"),
            approved=True,
            acknowledged_at=None,
            auto_acknowledged=False,
        )
        result = display_service._format_match_for_display(make_match(commentators=[commentator]))
        assert result["commentators"] == [
            {
                "name": "Charlie",
                "approved": True,
                "discord_id": "123",
                "user_id": 7,
                "acknowledged": False,
                "ack_ts": "",
                "id": 11,
            }
        ]

    def test_trackers_formatted_as_dicts(self, display_service):
        tracker = SimpleNamespace(
            id=22,
            user_id=8,
            user=SimpleNamespace(preferred_name="Dana", discord_id="456"),
            approved=False,
            acknowledged_at=None,
            auto_acknowledged=False,
        )
        result = display_service._format_match_for_display(make_match(trackers=[tracker]))
        assert result["trackers"] == [
            {
                "name": "Dana",
                "approved": False,
                "discord_id": "456",
                "user_id": 8,
                "acknowledged": False,
                "ack_ts": "",
                "id": 22,
            }
        ]

    def test_state_timestamp_set_when_seated(self, display_service):
        match = make_match(seated_at=datetime(2025, 1, 15, 19, 30))
        result = display_service._format_match_for_display(match)
        assert result["state_timestamp"] is not None

    def test_state_timestamp_reflects_highest_state(self, display_service):
        now = datetime.now()
        match = make_match(seated_at=now, started_at=now, finished_at=now, confirmed_at=now)
        result = display_service._format_match_for_display(match)
        assert result["state"] == "Confirmed"
        assert result["state_timestamp"] is not None

    def test_scheduled_at_formatted_as_string(self, display_service):
        match = make_match(scheduled_at=datetime(2025, 1, 15, 19, 30))
        result = display_service._format_match_for_display(match)
        assert isinstance(result["scheduled_at"], str)
        assert result["scheduled_at"] != ""

    def test_scheduled_at_empty_when_none(self, display_service):
        match = make_match(scheduled_at=None)
        result = display_service._format_match_for_display(match)
        assert result["scheduled_at"] == ""


# ---------------------------------------------------------------------------
# _format_match_for_display — proctor-board sort key and overdue flag
# ---------------------------------------------------------------------------


class TestFormatMatchOverdue:
    """``is_overdue`` drives the proctor board's top bucket and amber time.

    Overdue means: the scheduled time has passed and nobody has checked the
    match in (and it did not simply finish). The comparison is aware-UTC on
    both sides — ``scheduled_at`` is stored aware in production; a naive value
    is interpreted as UTC by ``to_utc_aware``.
    """

    def test_display_marks_an_unchecked_past_match_overdue(self, display_service):
        match = make_match(scheduled_at=datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc))
        assert display_service._format_match_for_display(match)["is_overdue"] is True

    def test_display_does_not_mark_a_seated_past_match_overdue(self, display_service):
        match = make_match(
            scheduled_at=datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc),
            seated_at=datetime(2020, 1, 1, 12, 5, tzinfo=timezone.utc),
        )
        assert display_service._format_match_for_display(match)["is_overdue"] is False

    def test_display_does_not_mark_a_finished_past_match_overdue(self, display_service):
        match = make_match(
            scheduled_at=datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc),
            finished_at=datetime(2020, 1, 1, 13, 0, tzinfo=timezone.utc),
        )
        assert display_service._format_match_for_display(match)["is_overdue"] is False

    def test_display_does_not_mark_a_future_match_overdue(self, display_service):
        match = make_match(scheduled_at=datetime(2999, 1, 1, 12, 0, tzinfo=timezone.utc))
        assert display_service._format_match_for_display(match)["is_overdue"] is False

    def test_unscheduled_match_is_not_overdue_and_has_no_sort_key(self, display_service):
        result = display_service._format_match_for_display(make_match(scheduled_at=None))
        assert result["is_overdue"] is False
        assert result["scheduled_ts"] is None

    def test_scheduled_ts_is_the_utc_epoch_seconds(self, display_service):
        when = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        match = make_match(scheduled_at=when)
        assert display_service._format_match_for_display(match)["scheduled_ts"] == when.timestamp()

    def test_naive_scheduled_at_is_read_as_utc(self, display_service):
        """A stripped tzinfo must not raise on the comparison."""
        match = make_match(scheduled_at=datetime(2020, 1, 1, 12, 0))
        result = display_service._format_match_for_display(match)
        assert result["is_overdue"] is True
        assert result["scheduled_ts"] == datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# get_matches_for_display — exclude_racetime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExcludeRacetime:
    async def test_exclude_racetime_omits_racetime_tournament_matches(self, db, display_service):
        bot = await RacetimeBot.create(
            name='Bot', category='alttp', client_id='cid', client_secret='sec',
        )
        onsite = await Tournament.create(name='On Site')
        online = await Tournament.create(name='Online', racetime_bot=bot)
        onsite_match = await Match.create(tournament=onsite)
        await Match.create(tournament=online)

        rows = await display_service.get_matches_for_display(exclude_racetime=True)

        assert [r['id'] for r in rows] == [onsite_match.id]

    async def test_default_still_includes_racetime_matches(self, db, display_service):
        bot = await RacetimeBot.create(
            name='Bot', category='alttp', client_id='cid', client_secret='sec',
        )
        onsite = await Tournament.create(name='On Site')
        online = await Tournament.create(name='Online', racetime_bot=bot)
        onsite_match = await Match.create(tournament=onsite)
        online_match = await Match.create(tournament=online)

        rows = await display_service.get_matches_for_display()

        assert {r['id'] for r in rows} == {onsite_match.id, online_match.id}


# ---------------------------------------------------------------------------
# get_matches_for_display — match_ids (the deep-linked board)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMatchIdsFilter:
    async def test_match_ids_narrows_to_those_matches(self, db, display_service):
        tournament = await Tournament.create(name='T')
        wanted = await Match.create(tournament=tournament)
        await Match.create(tournament=tournament)

        rows = await display_service.get_matches_for_display(match_ids=[wanted.id])

        assert [r['id'] for r in rows] == [wanted.id]

    async def test_unknown_match_id_returns_nothing_rather_than_everything(
        self, db, display_service,
    ):
        tournament = await Tournament.create(name='T')
        await Match.create(tournament=tournament)

        rows = await display_service.get_matches_for_display(match_ids=[999999])

        assert rows == []

    async def test_no_match_ids_is_unfiltered(self, db, display_service):
        tournament = await Tournament.create(name='T')
        one = await Match.create(tournament=tournament)
        two = await Match.create(tournament=tournament)

        rows = await display_service.get_matches_for_display(match_ids=None)

        assert {r['id'] for r in rows} == {one.id, two.id}


# ---------------------------------------------------------------------------
# _format_match_for_display — is_stream_candidate field
# ---------------------------------------------------------------------------


class TestFormatMatchIsStreamCandidate:
    def test_is_stream_candidate_false_by_default(self, display_service):
        result = display_service._format_match_for_display(make_match())
        assert result["is_stream_candidate"] is False

    def test_is_stream_candidate_true_when_set(self, display_service):
        result = display_service._format_match_for_display(make_match(is_stream_candidate=True))
        assert result["is_stream_candidate"] is True


class TestFormatMatchIsRacetime:
    def test_is_racetime_false_for_on_site_tournament(self, display_service):
        result = display_service._format_match_for_display(make_match())
        assert result["is_racetime"] is False

    def test_is_racetime_true_when_tournament_racetime_enabled(self, display_service):
        match = make_match(
            tournament=SimpleNamespace(
                name="Online Cup", short_label="Online Cup", seed_generator=None,
                is_racetime_enabled=True,
                required_commentators=1, required_trackers=1,
            )
        )
        result = display_service._format_match_for_display(match)
        assert result["is_racetime"] is True


class TestFormatMatchSeedDmBlocked:
    """``seed_dm_blocked`` names the players a seed DM cannot reach.

    Deliverability, not delivery: it is derived purely from ``User.discord_id``
    and ``User.dm_notifications`` — the same two fields ``_send_seed_dms`` skips
    on — and says nothing about whether a DM was sent or arrived.
    """

    def test_seed_dm_blocked_lists_a_player_with_dms_off(self, display_service):
        players = [
            make_player("Alice", discord_id="111", dm_notifications=True),
            make_player("Bob", discord_id="222", dm_notifications=False),
        ]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["seed_dm_blocked"] == ["Bob"]

    def test_seed_dm_blocked_lists_a_player_with_no_discord_id(self, display_service):
        players = [
            make_player("Alice", discord_id="111"),
            make_player("Carol", discord_id=None),
        ]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["seed_dm_blocked"] == ["Carol"]

    def test_seed_dm_blocked_is_empty_when_everyone_is_reachable(self, display_service):
        players = [
            make_player("Alice", discord_id="111"),
            make_player("Bob", discord_id="222"),
        ]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["seed_dm_blocked"] == []

    def test_seed_dm_blocked_lists_both_players_in_row_order(self, display_service):
        players = [
            make_player("Carol", discord_id=None),
            make_player("Bob", discord_id="222", dm_notifications=False),
        ]
        result = display_service._format_match_for_display(make_match(players=players))
        assert result["seed_dm_blocked"] == ["Carol", "Bob"]

    def test_seed_dm_blocked_is_empty_for_a_match_with_no_players(self, display_service):
        result = display_service._format_match_for_display(make_match(players=[]))
        assert result["seed_dm_blocked"] == []

    def test_an_empty_string_discord_id_counts_as_unreachable(self, display_service):
        """A blank link is as undeliverable as a missing one."""
        result = display_service._format_match_for_display(
            make_match(players=[make_player("Dana", discord_id="")])
        )
        assert result["seed_dm_blocked"] == ["Dana"]


class TestFormatMatchBracket:
    """``bracket`` is None unless a bracket game scheduled the match.

    ``make_match`` has no ``bracket_match_game`` attribute at all, which is also
    what a caller that skipped ``prefetch_relations`` sees — the link degrades to
    absent rather than raising.
    """

    def test_absent_relation_yields_no_link(self, display_service):
        assert display_service._format_match_for_display(make_match())["bracket"] is None

    def test_unlinked_game_row_yields_no_link(self, display_service):
        match = make_match(bracket_match_game=None)
        assert display_service._format_match_for_display(match)["bracket"] is None

    def test_links_to_the_bracket_of_its_game(self, display_service):
        match = make_match(
            bracket_match_game=SimpleNamespace(
                game_number=3,
                bracket_match=SimpleNamespace(
                    bracket=SimpleNamespace(id=9, name="Playoff"),
                ),
            )
        )
        assert display_service._format_match_for_display(match)["bracket"] == {
            "id": 9, "name": "Playoff", "game": 3, "best_of": 1, "standing": "",
        }
