"""Tests for the admin match editor's bracket-matchup picker options.

Regression cover for a bug the browser loop caught: the picker was built with
``options={None: '(None)'}`` and ``value=<linked matchup id>``, and NiceGUI's
``ChoiceElement`` rejects a value that is not among its options — so opening the
editor on an *already-linked* match raised ``ValueError: Invalid value`` and took
the whole Edit Match dialog down with it.

``build_options`` is module-level and ORM-free precisely so that invariant —
**the current link is always among the options** — is testable here rather than
only in a browser.
"""

import pytest

from theme.dialog._match_bracket_link import build_options, matchup_label


class _Entrant:
    def __init__(self, display_name):
        self.display_name = display_name


class _Entry:
    def __init__(self, name):
        self.entrant = _Entrant(name)


class _Matchup:
    def __init__(self, id_, round_, n1, n2):
        self.id = id_
        self.round = round_
        self.entry1 = _Entry(n1) if n1 else None
        self.entry2 = _Entry(n2) if n2 else None


class _Service:
    """Stub with the two methods the picker calls."""

    def __init__(self, linkable=(), linked=None):
        self._linkable = list(linkable)
        self._linked = linked

    async def list_linkable_matches(self, tournament_id):
        return self._linkable

    async def get_bracket_match_for_match(self, match_id):
        return self._linked


class TestMatchupLabel:
    def test_names_the_round_and_both_entrants(self):
        assert matchup_label(_Matchup(1, 2, 'Alice', 'Bob')) == 'Round 2 — Alice vs Bob'

    def test_negative_round_is_the_losers_bracket(self):
        assert matchup_label(_Matchup(1, -1, 'Alice', 'Bob')) == 'Losers round 1 — Alice vs Bob'

    def test_unresolved_slot_reads_tbd(self):
        assert matchup_label(_Matchup(1, 1, 'Alice', None)) == 'Round 1 — Alice vs TBD'


class TestBuildOptions:
    async def test_no_tournament_offers_only_none(self):
        opts = await build_options(_Service(), None, linked_id=None, match_id=None)
        assert opts == {None: '(None)'}

    async def test_lists_the_tournaments_linkable_matchups(self):
        svc = _Service(linkable=[_Matchup(7, 1, 'Alice', 'Bob')])
        opts = await build_options(svc, 3, linked_id=None, match_id=None)
        assert opts == {None: '(None)', 7: 'Round 1 — Alice vs Bob'}

    async def test_current_link_is_always_present(self):
        """The regression: a linked matchup is excluded from list_linkable_matches.

        Its slot is taken, so it never appears there — but the select holds it as
        its value, and a value absent from the options is a hard ValueError.
        """
        linked = _Matchup(9, 2, 'Alice', 'Bob')
        svc = _Service(linkable=[_Matchup(7, 1, 'Cara', 'Dev')], linked=linked)

        opts = await build_options(svc, 3, linked_id=9, match_id=42)

        assert 9 in opts, 'the linked matchup must be selectable'
        assert opts[9] == 'Round 2 — Alice vs Bob (linked)'
        assert 7 in opts and None in opts

    async def test_linked_matchup_not_duplicated_when_already_listed(self):
        both = _Matchup(7, 1, 'Alice', 'Bob')
        svc = _Service(linkable=[both], linked=both)
        opts = await build_options(svc, 3, linked_id=7, match_id=42)
        assert opts[7] == 'Round 1 — Alice vs Bob'

    async def test_vanished_link_degrades_to_none(self):
        """A link deleted underneath us leaves the options valid rather than raising."""
        svc = _Service(linkable=[], linked=None)
        opts = await build_options(svc, 3, linked_id=9, match_id=42)
        assert opts == {None: '(None)'}
        assert 9 not in opts


@pytest.mark.parametrize('linked_id', [None, 9])
async def test_options_always_contain_the_value_the_select_will_hold(linked_id):
    """The exact precondition NiceGUI's ChoiceElement enforces."""
    svc = _Service(linkable=[_Matchup(7, 1, 'A', 'B')], linked=_Matchup(9, 2, 'C', 'D'))
    opts = await build_options(svc, 3, linked_id=linked_id, match_id=42)
    value = linked_id if linked_id in opts else None
    assert value in opts
