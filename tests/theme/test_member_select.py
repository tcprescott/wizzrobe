"""The per-row member picker ships matches for what's typed, never the whole community."""

from theme.pickers import MEMBER_MATCH_LIMIT, member_matches

MEMBERS = {i: f'Imported Person {i}' for i in range(2000)} | {5000: 'Pat Example'}


def test_nothing_typed_offers_only_the_current_pick():
    assert member_matches(MEMBERS, '', keep=5000) == {5000: 'Pat Example'}
    assert member_matches(MEMBERS, 'p', keep=None) == {}


def test_typing_narrows_case_insensitively():
    assert member_matches(MEMBERS, 'pat ex') == {5000: 'Pat Example'}


def test_a_broad_search_is_capped_and_keeps_the_current_pick():
    found = member_matches(MEMBERS, 'imported', keep=5000)
    assert 5000 in found
    assert len(found) <= MEMBER_MATCH_LIMIT + 1
