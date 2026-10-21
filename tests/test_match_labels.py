"""How the web copy names a match.

The audit measured every crew string quoting a primary key — *"sign up as a
commentator for match ID 17"* — while the DM built by the same service named the
players, the time and the stage. These cover the shared formatter and keep the
id from coming back into the surfaces that used it.
"""

import re
from pathlib import Path
from types import SimpleNamespace

from application.utils.match_labels import entrant_name, match_label, match_row_label, players_label

REPO = Path(__file__).resolve().parents[1]


class TestPlayersLabel:
    def test_two_players_read_as_a_matchup(self):
        assert players_label(['Alice', 'Bob']) == 'Alice vs Bob'

    def test_other_counts_are_comma_joined(self):
        assert players_label(['Alice', 'Bob', 'Cara']) == 'Alice, Bob, Cara'
        assert players_label(['Alice']) == 'Alice'

    def test_empty_is_empty(self):
        assert players_label([]) == ''
        assert players_label(None) == ''


class TestMatchLabel:
    def test_players_plus_context(self):
        assert match_label(
            player_names=['Alice', 'Bob'],
            tournament='Wizzrobe Cup',
            scheduled_at='2025-08-03 19:00',
        ) == 'Alice vs Bob (Wizzrobe Cup, 2025-08-03 19:00)'

    def test_context_can_be_dropped_for_a_toast(self):
        assert match_label(
            player_names=['Alice', 'Bob'], tournament='Wizzrobe Cup', with_context=False,
        ) == 'Alice vs Bob'

    def test_a_title_leads_when_it_adds_information(self):
        assert match_label(
            player_names=['Alice', 'Bob'], title='Grand Final',
        ) == 'Grand Final'

    def test_a_title_that_repeats_the_matchup_is_suppressed(self):
        assert match_label(
            player_names=['Alice', 'Bob'], title='Alice vs Bob',
        ) == 'Alice vs Bob'

    def test_no_roster_falls_back_to_the_context(self):
        assert match_label(
            tournament='Wizzrobe Cup', scheduled_at='2025-08-03 19:00',
        ) == 'Wizzrobe Cup, 2025-08-03 19:00'

    def test_nothing_identifying_falls_back_to_words_not_an_id(self):
        assert match_label() == 'this match'


class TestMatchRowLabel:
    def test_reads_a_display_row(self):
        row = {
            'id': 17,
            'title': '',
            'tournament': 'Wizzrobe Cup',
            'scheduled_at': '2025-08-03 19:00',
            'players': [{'name': 'Alice'}, {'name': 'Bob'}],
        }
        assert match_row_label(row) == 'Alice vs Bob (Wizzrobe Cup, 2025-08-03 19:00)'
        assert '17' not in match_row_label(row, with_context=False)

    def test_a_sparse_row_does_not_raise(self):
        assert match_row_label({}) == 'this match'


def test_no_crew_or_watch_copy_quotes_a_match_id():
    """The finding, as a guard.

    ``application/utils/discord_messages`` states the rule for Discord; this
    keeps the web surfaces that broke it from breaking it again. Docstrings and
    argument descriptions ("match_id: The match ID") are not user-facing copy,
    so only quoted f-string interpolations count.
    """
    offenders = []
    for path in (
        REPO / 'theme/tables/match_handlers.py',
        REPO / 'theme/dialog/match_dialog_base.py',
    ):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if re.search(r'match ID \{', line):
                offenders.append(f'{path.relative_to(REPO)}:{number}')
    assert not offenders, f"user-facing copy still quotes a match id: {offenders}"


def test_entrant_name_prefers_the_linked_member():
    member = SimpleNamespace(preferred_name='Racer Six')
    assert entrant_name(SimpleNamespace(user=member, name='MockPlayerFour')) == 'Racer Six'
    assert entrant_name(SimpleNamespace(user=None, name='Walk-in')) == 'Walk-in'
    assert entrant_name(SimpleNamespace(user=None, name=None)) == 'TBD'
    assert entrant_name(None) == 'TBD'
