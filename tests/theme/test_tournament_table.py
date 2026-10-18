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


class _Site:
    def __init__(self, key, label, field):
        self.key, self.label, self._field = key, label, field

    def linked(self, t):
        return bool(getattr(t, self._field, None))


_SITES = (_Site('startgg', 'start.gg', 'startgg_event_slug'),
          _Site('challonge', 'Challonge', 'challonge_tournament_id'))


def _linked(**overrides):
    base = dict(startgg_event_slug=None, challonge_tournament_id=None, challonge_state=None,
                challonge_group_stage=False, challonge_last_synced_at=None,
                startgg_last_synced_at=None)
    base.update(overrides)
    return _tournament(**base)


def test_a_challonge_row_carries_its_stage_and_next_step():
    row = _tournament_row(_linked(challonge_tournament_id='123', challonge_state='group_stages_underway',
                                  challonge_group_stage=True), _SITES, can_sync=True, can_link=True)
    assert row['bracket_site'] == 'challonge'
    assert row['bracket_label'] == 'Challonge'
    assert row['bracket_stage'] == 'Group stage underway'
    assert row['bracket_synced'] == 'Never synced'
    assert row['bracket_manage'] is True
    assert row['bracket_unlinkable'] is True
    assert row['bracket_linkable'] is False
    assert row['stage_action'] == 'advance_to_bracket'


def test_a_tournament_admin_syncs_but_cannot_unlink():
    row = _tournament_row(_linked(challonge_tournament_id='123'), _SITES, can_sync=True)
    assert row['bracket_label'] == 'Challonge'
    assert row['bracket_manage'] is True
    assert row['bracket_unlinkable'] is False


def test_an_unlinked_row_offers_link_only_to_staff():
    assert _tournament_row(_linked(), _SITES, can_sync=True, can_link=True)['bracket_linkable'] is True
    assert _tournament_row(_linked(), _SITES, can_sync=True)['bracket_linkable'] is False
    assert _tournament_row(_linked(), (), can_sync=True, can_link=True)['bracket_linkable'] is False
