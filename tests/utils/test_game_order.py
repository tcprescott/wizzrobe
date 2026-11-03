"""A set's game numbers follow the clock, and what's left to book of it."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from application.services.bracket_service import BracketService
from application.utils.game_order import GameTimes, game_numbers, remaining_slots
from models import Match

AT = datetime(2026, 10, 25, 19, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def test_unplayed_games_number_by_booked_time():
    assert game_numbers([
        GameTimes('c', None, AT + 2 * HOUR), GameTimes('a', None, AT), GameTimes('b', None, AT + HOUR),
    ]) == {'a': 1, 'b': 2, 'c': 3}


def test_started_games_come_first_in_the_order_they_started():
    # 'late' was booked last but started first; 'early' is still waiting.
    assert game_numbers([
        GameTimes('early', None, AT),
        GameTimes('late', AT + HOUR, AT + 2 * HOUR),
        GameTimes('next', AT + 3 * HOUR, AT + HOUR),
    ]) == {'late': 1, 'next': 2, 'early': 3}


def test_a_game_with_no_time_goes_last_by_tiebreak():
    assert game_numbers([
        GameTimes('gone2', None, None, 2), GameTimes('gone1', None, None, 1), GameTimes('x', None, AT),
    ]) == {'x': 1, 'gone1': 2, 'gone2': 3}


def test_naive_times_read_as_utc():
    assert game_numbers([
        GameTimes('a', None, AT.replace(tzinfo=None) + HOUR), GameTimes('b', None, AT),
    ]) == {'b': 1, 'a': 2}


def test_remaining_slots_mark_games_past_a_sweep_as_if_needed():
    assert remaining_slots(3, 0, False) == [False, False, True]
    assert remaining_slots(3, 1, False) == [False, True]
    assert remaining_slots(5, 2, False) == [False, True, True]
    assert remaining_slots(3, 3, False) == []
    assert remaining_slots(3, 2, True) == []
    assert remaining_slots(1, 0, False) == []


def _game(game_id, number, match):
    return SimpleNamespace(id=game_id, game_number=number, match_id=match.id if match else None, match=match)


def test_native_games_number_by_their_matches_times():
    late = Match(id=11, scheduled_at=AT + HOUR)
    early = Match(id=12, scheduled_at=AT)
    games = [_game(1, 1, late), _game(2, 2, early), _game(3, 3, None)]
    assert BracketService.numbers_by_time(games) == {2: 1, 1: 2, 3: 3}


def test_native_games_fall_back_to_their_slot_without_matches_loaded():
    games = [SimpleNamespace(id=1, game_number=2, match_id=5, match=None),
             SimpleNamespace(id=2, game_number=1, match_id=6, match=None)]
    assert BracketService.numbers_by_time(games) == {1: 2, 2: 1}
