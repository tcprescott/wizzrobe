"""The side menu and bottom bar draw outlined icons from a subset font.

``static/fonts/material-symbols-outlined-nav.woff2`` holds only the navigation
icons, so a nav icon left out of the subset renders as its ligature text
("view_timeline") instead of a glyph. These keep the name list, the drawer's
sources and the font in step, and check the renamed admin tabs still answer
their old URLs, which DM buttons already sent carry.
"""

import ast
import hashlib
import re
from pathlib import Path

from theme.base import HOME_SECTIONS, BaseLayout
from theme.nav_badges import NAV_ICON_NAMES, nav_icon

ROOT = Path(__file__).resolve().parents[2]
TAB_SOURCES = ('pages/admin.py', 'pages/home.py', 'pages/volunteer.py', 'theme/base.py',
               'theme/drawer_help.py')


def _nav_icon_literals() -> set[str]:
    """Every icon a tab or menu dict names, plus every ``nav_icon('…')`` literal."""
    names: set[str] = set()
    for rel in TAB_SOURCES:
        tree = ast.parse((ROOT / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (isinstance(key, ast.Constant) and key.value == 'icon'
                            and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                        names.add(value.value)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'nav_icon' and node.args
                    and isinstance(node.args[0], ast.Constant)):
                names.add(node.args[0].value)
    return {n for n in names if ':' not in n}


def test_every_nav_icon_is_in_the_subset_font():
    used = _nav_icon_literals() | {icon for _label, icon in HOME_SECTIONS}
    assert used, 'the scan found no nav icons'
    assert used - NAV_ICON_NAMES == set()


def test_the_subset_font_is_shipped_and_declared():
    font = ROOT / 'static/fonts/material-symbols-outlined-nav.woff2'
    assert font.stat().st_size > 0
    css = (ROOT / 'static/css/styles.css').read_text()
    match = re.search(r"url\('/static/fonts/material-symbols-outlined-nav\.woff2\?v=([0-9a-f]+)'\)", css)
    assert match, 'the subset font URL must carry a ?v= stamp'
    assert '.q-drawer .q-item--active .material-symbols-outlined' in css


def test_the_subset_font_stamp_matches_the_file():
    # An unchanged URL let phones keep the subset from before "notifications"
    # joined it, so the active Notifications item drew its name as text.
    font = ROOT / 'static/fonts/material-symbols-outlined-nav.woff2'
    css = (ROOT / 'static/css/styles.css').read_text()
    stamp = re.search(r'material-symbols-outlined-nav\.woff2\?v=([0-9a-f]+)', css).group(1)
    assert stamp == hashlib.sha256(font.read_bytes()).hexdigest()[:12], (
        'static/fonts/material-symbols-outlined-nav.woff2 changed: set its ?v= in styles.css '
        'to the first 12 hex digits of its sha256'
    )


def test_nav_icon_prefixes_plain_names_only():
    assert nav_icon('home') == 'sym_o_home'
    assert nav_icon('sym_o_home') == 'sym_o_home'
    triforce = 'svguse:/static/triforce.svg#triforce|0 0 512 512'
    assert nav_icon(triforce) == triforce


def test_renamed_admin_tabs_answer_their_old_slugs():
    tabs = [
        {'label': 'Match Schedule', 'aliases': ('schedule',), 'content': lambda: None},
        {'label': 'Members', 'aliases': ('users',), 'content': lambda: None},
        {'label': 'Volunteer Shifts', 'aliases': ('vol-schedule',), 'content': lambda: None},
    ]
    for old, label in (('schedule', 'Match Schedule'), ('users', 'Members'),
                       ('vol-schedule', 'Volunteer Shifts'), ('members', 'Members')):
        assert BaseLayout(tabs=tabs, section=old)._default_tab == label


def test_admin_aliases_cover_every_renamed_slug():
    source = (ROOT / 'pages/admin.py').read_text()
    for alias in ('schedule', 'users', 'check-in', 'presets', 'vol-roster', 'vol-schedule'):
        assert f"'aliases': ('{alias}',)" in source, alias


def test_nav_section_matches_its_own_pages_only():
    from theme.nav_section import nav_section_matches

    assert nav_section_matches('/', '/')
    assert nav_section_matches('/', '/home/my-schedule')
    assert not nav_section_matches('/', '/volunteer')
    assert nav_section_matches('/volunteer', '/volunteer')
    assert nav_section_matches('/admin', '/admin/settings/general')
    assert not nav_section_matches('/live', '/liveness')
    assert not nav_section_matches('/admin', '/')


def test_current_section_has_its_own_highlight():
    css = (ROOT / 'static/css/styles.css').read_text()
    assert '.q-drawer .q-item.wiz-nav-section--active {' in css
    assert '.q-drawer .wiz-nav-section--active .material-symbols-outlined' in css
