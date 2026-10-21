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
from theme.url_state import iso_day

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
    # The copy must run inside the click (Safari refuses a later clipboard
    # write), so every control is a data attribute anchors.js listens for.
    script = (REPO / 'static/js/anchors.js').read_text()
    assert 'data-wiz-copy-link' in script and 'navigator.clipboard' in script
    for path in ('theme/anchors.py', 'theme/tables/match_player_run_sheet.py'):
        assert 'data-wiz-copy-link' in (REPO / path).read_text(), path
    assert 'copy_link_button(' in (REPO / 'theme/section.py').read_text()
    assert 'copy_link_item(' in (REPO / 'pages/home_tabs/my_volunteer_shifts.py').read_text()


def test_hub_tabs_push_their_address_inside_the_click():
    # A push that waits on the server lands outside the click, and Safari then
    # skips that Back entry; the hub tabs' rows push in the click instead.
    from theme.url_state import _LISTENER_JS, PUSH_ATTR
    assert PUSH_ATTR in _LISTENER_JS and 'pushState' in _LISTENER_JS
    assert 'mark_tab_push(' in (REPO / 'theme/drawer_nav.py').read_text()
    assert 'mark_tab_push(bottom_nav_tab(' in (REPO / 'theme/base.py').read_text()
