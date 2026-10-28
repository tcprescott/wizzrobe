"""Event > Schedule's friends: the row filter and the underline on the matchup line."""

from theme.matchup import FRIEND_CLASS, WHO_PARTS_VUE
from theme.tables.match_player_run_sheet import arrange_player_run_sheet, has_friend, mark_friends


def _row(*players, placeholders=()):
    return {
        'id': 1, 'state': 'Scheduled', 'scheduled_ts': 1_800_000_000,
        'players': [{'name': name, 'user_id': uid, 'discord_id': None} for uid, name in players],
        'placeholders': list(placeholders),
    }


def test_has_friend_reads_the_raw_row():
    row = _row((1, 'Ash'), (2, 'Bo'))
    assert has_friend(row, {2})
    assert not has_friend(row, {3})
    assert not has_friend(row, frozenset())


def test_mark_friends_flags_only_the_friends_side():
    row = _row((1, 'Ash'), (2, 'Bo'))
    mark_friends(row, {2})
    assert row['_friend'] is True
    assert [(p['text'], p.get('friend', False)) for p in row['_who_parts']] == [
        ('Ash', False), (' vs ', False), ('Bo', True)]


def test_mark_friends_keeps_undecided_sides_muted():
    row = _row((2, 'Bo'), placeholders=['Winner of C'])
    mark_friends(row, {2})
    assert row['_who_parts'][0]['friend'] is True
    assert row['_who_parts'][-1]['tbd'] is True


def test_a_line_without_friends_is_left_alone():
    row = _row((1, 'Ash'), (2, 'Bo'))
    row['_who_parts'] = None
    mark_friends(row, {9})
    assert row['_friend'] is False
    assert row['_who_parts'] is None


def test_arrange_underlines_friends():
    rows = arrange_player_run_sheet([_row((1, 'Ash'), (2, 'Bo'))], None, frozenset({1}))
    assert rows[0]['_who_parts'][0]['friend'] is True


def test_the_template_marks_a_friend_for_screen_readers_too():
    assert FRIEND_CLASS in WHO_PARTS_VUE
    assert '(friend)' in WHO_PARTS_VUE
