"""Every view switcher keeps its place in the address bar (theme/url_state.py).

The ``check_view_urls`` hook catches an unbound ``ui.tabs``/``ui.toggle`` while a
file is edited; the first test is the same rule where it cannot be bypassed. The
browser half (Back, Forward, reload, deep links) is scripts/ui_history.js in CI.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

from theme.anchors import anchor_id, match_anchor, shift_anchor
from theme.url_state import UrlState, iso_day

REPO = Path(__file__).resolve().parents[2]


def _hook():
    scripts = REPO / '.claude' / 'scripts'
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location('check_view_urls', scripts / 'check_view_urls.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts))


def test_every_switcher_in_the_app_is_bound_or_exempt():
    check = _hook().check
    hits = []
    for root in (REPO / 'pages', REPO / 'theme'):
        for path in root.rglob('*.py'):
            hits += check(path.read_text(encoding='utf-8'), str(path.relative_to(REPO)))
    assert not hits, '\n\n'.join(hits)


def test_the_hook_flags_an_unbound_toggle_and_accepts_the_three_ways_out():
    check = _hook().check
    unbound = "t = ui.toggle({'a': 'A', 'b': 'B'}, value='a').props('dense')\n"
    assert check(unbound, 'pages/x.py')
    assert not check(unbound + "bind_url('view', t, default='a')\n", 'pages/x.py')
    assert not check(
        "with ui.tabs() as tabs:\n    pass\nurl_param('tab', tabs, default='a')\n", 'pages/x.py')
    assert not check('# url-state: exempt — a form input\n' + unbound, 'pages/x.py')


def test_a_day_in_the_address_is_read_leniently():
    assert iso_day('2026-10-23') == date(2026, 10, 23)
    assert iso_day('tomorrow') is None
    assert iso_day(None) is None


def test_anchors_are_stable_slugs():
    assert anchor_id('Your matches') == 'your-matches'
    assert anchor_id("When you can't play") == 'when-you-can-t-play'
    assert match_anchor(12) == 'match-12'
    assert shift_anchor(5) == 'shift-5'


def test_the_anchor_script_knows_the_markers_rows_carry():
    script = (REPO / 'static/js/anchors.js').read_text()
    assert 'data-wiz-row' in script and 'data-wiz-shift' in script
    assert 'data-wiz-shift' in (REPO / 'theme/volunteer_grid.py').read_text()


def test_every_page_shell_loads_the_anchor_script():
    assert 'js/anchors.js' in (REPO / 'theme/base.py').read_text()
    assert 'js/anchors.js' in (REPO / 'theme/chrome.py').read_text()


def test_copy_link_controls_are_wired_to_the_click_listener():
    script = (REPO / 'static/js/anchors.js').read_text()
    assert 'data-wiz-copy-link' in script and 'navigator.clipboard' in script
    for path in ('theme/anchors.py', 'theme/tables/match_player_run_sheet.py'):
        assert 'data-wiz-copy-link' in (REPO / path).read_text(), path
    assert 'copy_link_button(' in (REPO / 'theme/section.py').read_text()
    assert 'copy_link_item(' in (REPO / 'pages/home_tabs/my_volunteer_shifts.py').read_text()


def test_hub_tabs_push_their_address_inside_the_click():
    from theme.url_state import _LISTENER_JS, PUSH_ATTR
    assert PUSH_ATTR in _LISTENER_JS and 'pushState' in _LISTENER_JS
    assert 'mark_tab_push(' in (REPO / 'theme/drawer_nav.py').read_text()
    assert 'mark_tab_push(bottom_nav_tab(' in (REPO / 'theme/base.py').read_text()


def test_a_late_replace_cannot_rewrite_the_entry_back_landed_on():
    from unittest.mock import MagicMock, patch

    from theme.url_state import UrlState

    state = UrlState.__new__(UrlState)
    state.client = MagicMock()
    state.path, state.tail, state.query, state._params = '/t/x/home/tournaments', '', {}, []
    state._at, state.tab, state.base_title = '/t/x/home/tournaments', None, 'X'
    with patch('theme.url_state.ui.run_javascript') as run:
        state.sync()
    script = run.call_args[0][0]
    assert script.startswith('if(location.pathname==="/t/x/home/tournaments")')
    assert 'replaceState' in script and 'pushState' not in script


def _state(query: dict) -> UrlState:
    from unittest.mock import MagicMock

    state = UrlState.__new__(UrlState)
    state.client = MagicMock()
    state.path, state.tail, state.query, state._params = '/t/x/admin/match-schedule', '', dict(query), []
    state._at, state.tab, state.base_title = '/t/x/admin/match-schedule', None, 'X'
    state.restoring, state.busy = False, 0
    return state


def test_a_handled_deep_link_leaves_the_address_so_a_reload_cannot_replay_it():
    from unittest.mock import patch

    state = _state({'reschedule_request': '33', 'match_id': '7'})
    with patch('theme.url_state.ui.run_javascript') as run:
        state.consume('reschedule_request')
    script = run.call_args[0][0]
    assert 'reschedule_request' not in script
    assert '"/t/x/admin/match-schedule?match_id=7"' in script
    assert 'replaceState' in script and 'pushState' not in script
    assert state.query == {'match_id': '7'}


def test_a_consumed_link_can_leave_the_filter_it_settled_into():
    from unittest.mock import patch

    state = _state({'comments': '12'})
    with patch('theme.url_state.ui.run_javascript') as run:
        state.consume('comments', match_id=12)
    assert '"/t/x/admin/match-schedule?match_id=12"' in run.call_args[0][0]


def test_consuming_a_link_that_is_not_there_writes_nothing():
    from unittest.mock import patch

    state = _state({'view': 'unbooked'})
    with patch('theme.url_state.ui.run_javascript') as run:
        state.consume('reschedule_request', 'agree')
    run.assert_not_called()


_PERSISTENT_LINK_PARAMS = {'match', 'qualifier', 'tab'}


def test_every_one_shot_dm_link_parameter_is_consumed():
    import re

    links = (REPO / 'application/utils/app_links.py').read_text()
    built = {
        key for call in re.findall(r'(?:admin|home)_url\(([^)]*)\)', links)
        for key in re.findall(r'\b([a-z_]+)=', call) if key != 'params'
    }
    assert {'reschedule_request', 'comments', 'agree'} <= built

    consumed: set[str] = set()
    for root in (REPO / 'pages', REPO / 'theme'):
        for path in root.rglob('*.py'):
            source = path.read_text(encoding='utf-8')
            for call in re.findall(r'consume_deep_link\(([^)]*)\)', source):
                consumed |= set(re.findall(r"'([a-z_]+)'", call))
                consumed |= set(re.findall(r'\b([a-z_]+)=', call)) - {'client'}
            for group in re.findall(r'_ONE_SHOT_LINKS = \(([^)]*)\)', source):
                consumed |= set(re.findall(r"'([a-z_]+)'", group))
    missing = built - consumed - _PERSISTENT_LINK_PARAMS
    assert not missing, f'DM link parameters a reload would replay: {sorted(missing)}'
