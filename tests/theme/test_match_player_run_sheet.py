"""The Event > Schedule run sheet: its groups, and the buttons each line offers the viewer."""

import pathlib

from theme.tables.match_player_run_sheet import (
    _BODY_SLOT,
    _ITEM_SLOT,
    PLAYER_RUN_SHEET_COLUMNS,
    arrange_player_run_sheet,
    player_crew,
    player_status,
)
from theme.tables.run_sheet_meta import crew_columns

ME = '111'
PLAYERS = [{'name': 'Player One', 'discord_id': '900'}, {'name': 'Player Two', 'discord_id': '901'}]


def _row(id_=1, state='Scheduled', ts=None, **extra):
    row = {
        'id': id_, 'state': state, 'players': PLAYERS, 'scheduled_ts': ts, 'scheduled_at': '',
        'crew_signup_open': True,
        'crew_wanted': {'commentators': True, 'trackers': True},
        'crew_required': {'commentators': 2, 'trackers': 1},
        'commentators': [], 'trackers': [],
    }
    row.update(extra)
    return row


def _role(row, key, discord_id=ME):
    return next(r for r in player_crew(row, discord_id) if r['key'] == key)


class TestStatus:
    def test_a_recorded_result_is_not_staffs_to_do_list(self):
        assert player_status(_row(state='Finished', has_result=False)) == ('Finished', 'neutral')
        assert player_status(_row(state='Confirmed')) == ('Confirmed', 'ok')

    def test_live_and_late(self):
        assert player_status(_row(state='Started')) == ('Live', 'live')
        assert player_status(_row(is_overdue=True)) == ('Late', 'pending')


class TestArrange:
    def test_live_then_next_then_waiting_then_finished(self):
        rows = [
            _row(1, 'Finished', 100),
            _row(2, 'Scheduled', 300),
            _row(3, 'Started', 200),
            _row(4, 'Scheduled', 400, players=[]),
            _row(5, 'Confirmed', 200),
        ]
        arranged = arrange_player_run_sheet(rows, ME)
        assert [r['id'] for r in arranged] == [3, 2, 4, 5, 1]
        assert [r['_group'] for r in arranged if r['_group_start']] == [
            'Live now', 'Up next', 'Waiting for players', 'Finished']


class TestCrew:
    def test_an_open_role_offers_sign_up_and_says_it_needs_crew(self):
        role = _role(_row(), 'commentators')
        assert role['can_sign_up'] and not role['can_withdraw']
        assert role['short'] and role['count'] == '0/2'

    def test_a_waiting_signup_is_not_cover_but_can_be_withdrawn(self):
        row = _row(trackers=[{'discord_id': ME, 'approved': False}])
        role = _role(row, 'trackers')
        assert role['mine'] == 'waiting' and role['can_withdraw'] and not role['can_sign_up']
        assert role['short'] and role['count'] == '0/1'

    def test_approved_but_unconfirmed_crew_get_confirm(self):
        row = _row(commentators=[{'discord_id': '5', 'approved': True},
                                 {'discord_id': ME, 'approved': True, 'acknowledged': False}])
        assert _role(row, 'commentators')['ack_idx'] == 1
        row['commentators'][1]['acknowledged'] = True
        assert _role(row, 'commentators')['ack_idx'] is None

    def test_a_player_cannot_crew_their_own_match(self):
        assert not _role(_row(), 'commentators', discord_id='900')['can_sign_up']

    def test_closed_signup_offers_nothing_and_needs_nobody(self):
        role = _role(_row(crew_signup_open=False), 'commentators')
        assert not role['can_sign_up'] and not role['short']
        assert role['unavailable'] == 'Signups closed'

    def test_the_disabled_button_says_why(self):
        assert _role(_row(), 'commentators', discord_id='900')['unavailable'] == "You're playing"
        assert _role(_row(), 'commentators')['unavailable'] == ''
        row = _row(state='Started', crew_signup_open=False, commentators=[{'discord_id': ME, 'approved': True}])
        assert _role(row, 'commentators')['unavailable'] == ''

    def test_withdraw_stays_once_the_match_is_under_way(self):
        row = _row(state='Started', crew_signup_open=False,
                   commentators=[{'discord_id': ME, 'approved': True}])
        role = _role(row, 'commentators')
        assert role['can_withdraw'] and not role['can_sign_up']

    def test_a_live_or_finished_match_drops_the_signup_controls(self):
        for state in ('Started', 'Finished', 'Confirmed'):
            row = _row(state=state, commentators=[{'name': 'SM User', 'discord_id': '5', 'approved': True}])
            roles = player_crew(row, ME)
            assert [r['key'] for r in roles] == ['commentators']
            role = roles[0]
            assert not role['tray'] and not role['can_sign_up'] and not role['empty']
            assert role['unavailable'] == '' and not role['short']
            assert [p['name'] for p in role['people']] == ['SM User']

    def test_a_live_match_keeps_only_the_viewers_own_withdraw(self):
        row = _row(state='Started', commentators=[{'discord_id': ME, 'approved': True, 'acknowledged': False}])
        role = _role(row, 'commentators')
        assert role['tray'] and role['can_withdraw'] and role['ack_idx'] is None

    def test_names_show_with_the_viewer_as_you_and_approved_first(self):
        row = _row(commentators=[{'name': 'Me', 'discord_id': ME, 'approved': False},
                                 {'name': 'SM User', 'discord_id': '5', 'approved': True}])
        people = _role(row, 'commentators')['people']
        assert [(p['name'], p['me'], p['note']) for p in people] == [
            ('SM User', False, ''), ('Me', True, 'waiting for approval')]

    def test_no_signups_yet_only_while_the_role_can_fill(self):
        assert _role(_row(), 'commentators')['empty']
        assert not _role(_row(crew_signup_open=False), 'commentators')['empty']

    def test_a_role_the_tournament_does_not_use_is_left_off(self):
        row = _row(crew_wanted={'commentators': True, 'trackers': False})
        assert [r['key'] for r in player_crew(row, ME)] == ['commentators']


class TestAcknowledge:
    def test_only_the_player_who_has_not_acknowledged_gets_the_button(self):
        row = _row(acknowledgments=[{'discord_id': '900', 'acknowledged': False},
                                    {'discord_id': '901', 'acknowledged': True}])
        assert arrange_player_run_sheet([dict(row)], '900')[0]['_ack']
        assert not arrange_player_run_sheet([dict(row)], '901')[0]['_ack']
        assert not arrange_player_run_sheet([dict(row)], ME)[0]['_ack']


class TestTemplates:
    def test_every_button_on_a_line_stops_the_click_opening_the_sheet(self):
        for slot in (_BODY_SLOT, _ITEM_SLOT):
            emits = slot.count('$parent.$emit(')
            opens = slot.count("$parent.$emit('open_match'")
            assert slot.count('@click.stop="$parent.$emit(') >= emits - opens

    def test_the_phone_card_does_not_nest_buttons(self):
        opener = _ITEM_SLOT.split('<button type="button" class="player-sheet__open"')[1].split('</button>')[0]
        assert '<q-btn' not in opener

    def test_every_role_draws_its_button_between_count_and_names(self):
        for slot in (_BODY_SLOT, _ITEM_SLOT):
            assert slot.index('run-sheet__crew') < slot.index('wiz-crew-action') < slot.index('run-sheet__people')
            assert '<div v-if="c.tray" class="wiz-crew-action">' in slot
            assert ' disable ' in slot
        assert 'player-sheet__crew-cell' in _BODY_SLOT

    def test_withdraw_sits_left_of_confirm(self):
        assert _BODY_SLOT.index('Withdraw') < _BODY_SLOT.index('label="Confirm"')

    def test_the_columns_fit_without_sorting(self):
        assert [c['name'] for c in PLAYER_RUN_SHEET_COLUMNS] == [
            'scheduled_at', 'match', 'stage', 'commentators', 'trackers', 'broadcasters', 'status', 'watch']
        assert not any(c.get('sortable') for c in PLAYER_RUN_SHEET_COLUMNS)

    def test_broadcasters_only_show_on_an_event_that_has_them(self):
        names = lambda on: [c['name'] for c in crew_columns(PLAYER_RUN_SHEET_COLUMNS, on)]
        assert 'broadcasters' in names(True)
        assert 'broadcasters' not in names(False)

    def test_the_schedule_page_uses_it(self):
        src = pathlib.Path('pages/home_tabs/schedule.py').read_text()
        assert "layout='player_run_sheet'" in src
        assert 'on_open_match=sheet.open' in src


class TestMyScheduleMenu:
    """My Schedule's own matches: one ⋮ per line for what only a player can do."""

    def _slots(self, menu):
        from theme.tables.match_player_run_sheet import register_player_run_sheet_slots

        class Table:
            def __init__(self):
                self.slots = {}
                self._props = {}

            def add_slot(self, name, template):
                self.slots[name] = template

        table = Table()
        register_player_run_sheet_slots(table, menu=menu)
        return table.slots

    def test_the_phone_card_carries_the_menu_only_where_the_board_has_the_column(self):
        assert 'more_vert' in self._slots(True)['item']
        assert 'more_vert' not in self._slots(False)['item']
        assert '__MENU__' not in self._slots(False)['item']

    def test_the_menu_is_named_for_its_match_and_only_on_the_viewers_own(self):
        from theme.tables.match_player_run_sheet import _MENU

        assert ":aria-label=\"'More for ' + (" in _MENU
        assert 'v-if="props.row._mine && ' in _MENU

    def test_each_item_keeps_its_own_gate(self):
        from theme.tables.match_player_run_sheet import _MENU
        from theme.tables.match_slots import STREAM_VOLUNTEER_ACTIONABLE

        assert "props.row._can_reschedule && props.row.state === 'Scheduled'" in _MENU
        assert 'v-else-if="props.row._reschedule_pending" disable' in _MENU
        assert f'v-if="{STREAM_VOLUNTEER_ACTIONABLE}"' in _MENU
        for event in ('request_reschedule', 'open_hard_preset', 'toggle_stream_volunteer'):
            assert f"$parent.$emit('{event}'" in _MENU

    def test_my_schedule_uses_the_run_sheet_with_the_menu(self):
        from theme.tables.match_player_run_sheet import PLAYER_MENU_COLUMN
        from theme.tables.match_slots import MY_ACTIONS_COLUMN

        assert PLAYER_MENU_COLUMN['name'] == MY_ACTIONS_COLUMN
        src = pathlib.Path('pages/home_tabs/player.py').read_text()
        assert "layout='player_run_sheet'" in src
        assert 'columns = [*crew_columns(PLAYER_RUN_SHEET_COLUMNS' in src
        assert 'PLAYER_MENU_COLUMN]' in src

    def test_the_crew_section_is_the_run_sheet_narrowed_to_your_slots(self):
        src = pathlib.Path('pages/home_tabs/my_crew.py').read_text()
        assert "layout='player_run_sheet'" in src
        assert 'crew_discord_id=discord_id' in src


def test_no_column_points_at_a_list():
    # NiceGUI patches in its own body-cell slot for any column whose field holds a list.
    row = arrange_player_run_sheet([_row(commentators=[{'discord_id': '5', 'approved': True}])], ME)[0]
    for col in PLAYER_RUN_SHEET_COLUMNS:
        assert not isinstance(row.get(col['field']), (list, set, tuple)), col['name']
