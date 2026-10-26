"""A side the bracket hasn't decided reads as a placeholder, not a player, on every matchup line."""

import pytest

from application.services.bracket_sources import UnbookedMatch
from application.utils.match_labels import has_undecided, is_undecided_side, matchup_parts
from theme.matchup import TBD_CLASS, WHO_PARTS_VUE, matchup_html
from theme.tables.match_player_run_sheet import _BODY_SLOT
from theme.tables.match_run_sheet import _body_slot, decorate_run_sheet_row
from theme.watch_view import WatchMatch, players_html


@pytest.mark.parametrize('label', ['TBD', 'Winner of N', 'Loser of K', 'z3ph1r or Exodus', ' Winner of 12 '])
def test_bracket_placeholders_are_undecided(label):
    assert is_undecided_side(label)


@pytest.mark.parametrize('label', ['Flee', 'TBDragon', 'Winner', 'Orlando', ''])
def test_names_are_not(label):
    assert not is_undecided_side(label)


def test_players_never_count_as_undecided_whatever_their_name():
    parts = matchup_parts(['Andy or Benny'], ['Winner of C'])
    assert parts == [
        {'text': 'Andy or Benny', 'tbd': False},
        {'text': ' vs ', 'tbd': False},
        {'text': 'Winner of C', 'tbd': True},
    ]


def test_an_accountless_entrant_riding_in_placeholders_reads_as_a_player():
    parts = matchup_parts([], ['wthh or MrMario', 'Flee'])
    assert [p['tbd'] for p in parts] == [True, False, False]


def test_max_names_counts_the_rest():
    parts = matchup_parts(['A', 'B', 'C'], ['TBD'], max_names=2)
    assert ''.join(p['text'] for p in parts) == 'A vs B +2 more'
    assert not has_undecided(parts)


def test_run_sheet_row_carries_parts_only_when_a_side_is_open():
    row = {'id': 1, 'state': 'Scheduled', 'players': [{'name': 'Flee'}], 'placeholders': ['z3ph1r or Exodus'],
           'scheduled_at': '', 'scheduled_ts': None}
    decorate_run_sheet_row(row)
    assert row['_who'] == 'Flee vs z3ph1r or Exodus'
    assert row['_who_parts'][2] == {'text': 'z3ph1r or Exodus', 'tbd': True}

    plain = {'id': 2, 'state': 'Scheduled', 'players': [{'name': 'A'}, {'name': 'B'}],
             'scheduled_at': '', 'scheduled_ts': None}
    assert decorate_run_sheet_row(plain)['_who_parts'] is None


def test_every_run_sheet_draws_the_parts():
    assert WHO_PARTS_VUE in _body_slot(False)
    assert WHO_PARTS_VUE in _BODY_SLOT


def test_unbooked_board_flags_only_open_sides():
    item = UnbookedMatch(
        source='challonge', source_label='Challonge', key=1, tournament=None, round_label='',
        player_names=['Flee', 'Winner of N'], player_ids=[7, None], players_tbd=True,
    )
    assert [p['tbd'] for p in item.matchup_parts] == [False, False, True]
    ready = UnbookedMatch(
        source='challonge', source_label='Challonge', key=1, tournament=None, round_label='',
        player_names=['Andy or Benny', 'Flee'], player_ids=[None, 7],
    )
    assert not has_undecided(ready.matchup_parts)


def test_static_watch_rows_escape_and_mark_the_placeholder():
    match = WatchMatch(time='16:00', players='<b> vs TBD', players_parts=matchup_parts(['<b>'], ['TBD']))
    assert players_html(match) == f'&lt;b&gt; vs <span class="{TBD_CLASS}">TBD</span>'
    assert players_html(WatchMatch(time='', players='A & B')) == 'A &amp; B'
    assert matchup_html([]) == ''
