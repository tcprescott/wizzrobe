from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from theme.tables.tournament import (
    _match_length,
    _signup_detail,
    _tournament_row,
    tournament_columns,
)


def _tournament(**overrides):
    base = dict(
        id=1, name='Cup', description=None, seed_generator=None, is_active=True, is_test=False,
        players_per_match=2, player_count=0, average_match_duration=None,
        max_match_duration=None, signups_open_at=None, signups_close_at=None,
        tournament_format=None, bracket_url=None, rules_url=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_match_length_combines_average_and_maximum():
    assert _match_length(_tournament(average_match_duration=90, max_match_duration=120)) == '90 min (max 120)'
    assert _match_length(_tournament(average_match_duration=90)) == '90 min'
    assert _match_length(_tournament(max_match_duration=120)) == 'max 120 min'
    assert _match_length(_tournament()) == ''


def test_signup_detail_names_the_next_boundary():
    now = datetime.now(timezone.utc)
    assert _signup_detail(_tournament()) == 'No close date'
    assert _signup_detail(_tournament(signups_close_at=now + timedelta(days=2))).startswith('Closes ')
    assert _signup_detail(_tournament(signups_close_at=now - timedelta(days=2))).startswith('Closed ')
    assert _signup_detail(_tournament(signups_open_at=now + timedelta(days=2))).startswith('Opens ')


def test_row_drops_links_that_are_not_http():
    row = _tournament_row(_tournament(bracket_url='javascript:alert(1)', rules_url='https://example.com/rules'))
    assert row['bracket_url'] == ''
    assert row['rules_url'] == 'https://example.com/rules'


def test_board_shows_seven_columns_and_hides_the_description():
    visible = [c['name'] for c in tournament_columns() if not c.get('hidden')]
    assert visible == ['name', 'signups', 'bracket', 'seed_generator', 'match_length',
                       'player_count', 'actions']
