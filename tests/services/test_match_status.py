"""The derived status vocabulary shared by the schedule, the bracket, and Discord.

Pure — no DB, no NiceGUI. ``resolve`` is a projection of five nullable timestamps
plus (optionally) a bracket game state and a racetime room status, and the whole
point of the module is that three surfaces agree, so the truth table below is the
contract rather than an illustration.
"""

from types import SimpleNamespace

import pytest

from application.services.match.match_status import (
    LEGACY_STATE_LABELS,
    STATUS_LABELS,
    STATUS_TONES,
    MatchStatus,
    label,
    legacy_label,
    resolve,
    resolve_matchup,
    tone,
)
from models import BracketMatchGameState, BracketMatchState, RaceRoomStatus

T = 'a timestamp'


def make_match(**stamps):
    base = {
        'seated_at': None, 'started_at': None,
        'finished_at': None, 'confirmed_at': None,
    }
    return SimpleNamespace(**{**base, **stamps})


class TestTimestampLadder:
    """The five-timestamp ladder, read newest-first."""

    @pytest.mark.parametrize('stamps,expected', [
        ({}, MatchStatus.SCHEDULED),
        ({'seated_at': T}, MatchStatus.CHECKED_IN),
        ({'seated_at': T, 'started_at': T}, MatchStatus.LIVE),
        ({'seated_at': T, 'started_at': T, 'finished_at': T},
         MatchStatus.AWAITING_RESULT),
        ({'seated_at': T, 'started_at': T, 'finished_at': T, 'confirmed_at': T},
         MatchStatus.COMPLETE),
        ({'finished_at': T}, MatchStatus.AWAITING_RESULT),
        ({'confirmed_at': T}, MatchStatus.COMPLETE),
        ({'started_at': T}, MatchStatus.LIVE),
    ])
    def test_ladder(self, stamps, expected):
        assert resolve(match=make_match(**stamps)) == expected


class TestGameStatePrecedence:
    """A settled or cancelled game state outranks the match timestamps."""

    def test_complete_game_wins_over_an_unconfirmed_match(self):
        assert resolve(
            match=make_match(started_at=T, finished_at=T),
            game_state=BracketMatchGameState.COMPLETE,
        ) == MatchStatus.COMPLETE

    def test_cancelled_game_wins_over_a_scheduled_match(self):
        assert resolve(
            match=make_match(),
            game_state=BracketMatchGameState.CANCELLED,
        ) == MatchStatus.CANCELLED

    def test_scheduled_game_defers_to_the_match(self):
        assert resolve(
            match=make_match(started_at=T),
            game_state=BracketMatchGameState.SCHEDULED,
        ) == MatchStatus.LIVE


class TestRoomTiebreaker:
    """The racetime room only breaks ties — it never overrides a later stamp."""

    def test_in_progress_room_makes_an_unstarted_match_live(self):
        assert resolve(
            match=make_match(seated_at=T),
            room_status=RaceRoomStatus.IN_PROGRESS,
        ) == MatchStatus.LIVE

    def test_in_progress_room_does_not_reopen_a_finished_match(self):
        assert resolve(
            match=make_match(started_at=T, finished_at=T),
            room_status=RaceRoomStatus.IN_PROGRESS,
        ) == MatchStatus.AWAITING_RESULT

    @pytest.mark.parametrize('status', [
        RaceRoomStatus.OPEN, RaceRoomStatus.FINISHED, RaceRoomStatus.CANCELLED,
    ])
    def test_other_room_statuses_change_nothing(self, status):
        assert resolve(
            match=make_match(), room_status=status,
        ) == MatchStatus.SCHEDULED


class TestNoMatch:
    """A matchup with nothing booked falls back to the bracket state."""

    @pytest.mark.parametrize('state,expected', [
        (BracketMatchState.PENDING, MatchStatus.PENDING),
        (BracketMatchState.OPEN, MatchStatus.UNSCHEDULED),
        (BracketMatchState.COMPLETE, MatchStatus.COMPLETE),
        (None, MatchStatus.PENDING),
    ])
    def test_bracket_state_fallback(self, state, expected):
        assert resolve(match=None, bracket_match_state=state) == expected


class TestResolveMatchup:
    def test_complete_and_pending_pass_through(self):
        assert resolve_matchup(
            bracket_match_state=BracketMatchState.COMPLETE,
        ) == MatchStatus.COMPLETE
        assert resolve_matchup(
            bracket_match_state=BracketMatchState.PENDING,
        ) == MatchStatus.PENDING

    def test_open_with_no_games_is_unscheduled(self):
        assert resolve_matchup(
            bracket_match_state=BracketMatchState.OPEN,
        ) == MatchStatus.UNSCHEDULED

    def test_the_most_interesting_live_game_wins(self):
        """A Bo3 with game 1 done and game 2 being raced reads Live, not Complete."""
        assert resolve_matchup(
            bracket_match_state=BracketMatchState.OPEN,
            game_statuses=[MatchStatus.COMPLETE, MatchStatus.LIVE],
        ) == MatchStatus.LIVE

    @pytest.mark.parametrize('statuses,expected', [
        ([MatchStatus.SCHEDULED, MatchStatus.CHECKED_IN], MatchStatus.CHECKED_IN),
        ([MatchStatus.SCHEDULED, MatchStatus.AWAITING_RESULT],
         MatchStatus.AWAITING_RESULT),
        ([MatchStatus.SCHEDULED], MatchStatus.SCHEDULED),
    ])
    def test_ranking(self, statuses, expected):
        assert resolve_matchup(
            bracket_match_state=BracketMatchState.OPEN, game_statuses=statuses,
        ) == expected

    def test_underway_series_with_nothing_booked_needs_rescheduling(self):
        assert resolve_matchup(
            bracket_match_state=BracketMatchState.OPEN,
            game_statuses=[MatchStatus.COMPLETE],
            series_underway=True,
        ) == MatchStatus.NEEDS_RESCHEDULE


class TestLabelsAndTones:
    def test_every_status_has_a_label_and_a_tone(self):
        for status in MatchStatus:
            assert STATUS_LABELS[status]
            assert STATUS_TONES[status]
            assert label(status) == STATUS_LABELS[status]
            assert tone(status) == STATUS_TONES[status]

    def test_legacy_labels_are_the_schedule_tables_five_strings(self):
        """The filter chips and the Vue slots compare against these by value."""
        assert set(LEGACY_STATE_LABELS.values()) == {
            'Scheduled', 'Checked In', 'Started', 'Finished', 'Confirmed',
        }

    def test_legacy_label_defaults_to_scheduled(self):
        assert legacy_label(MatchStatus.PENDING) == 'Scheduled'
