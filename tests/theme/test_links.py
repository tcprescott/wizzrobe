"""Links that open a new tab must be real ``<a href>``s, not a server round trip.

``ui.navigate.to(url, new_tab=True)`` from a click handler reaches the browser as
a ``window.open`` after the websocket round trip, which iPhone Safari blocks as a
pop-up and a Home Screen web app never opens (the event guide's "Open guide").
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from theme import links
from theme.menu import SlotItem, slot_more_menu

ROOT = Path(__file__).resolve().parents[2]

# Opened only after a form is filled in, so there is no URL to link up front.
# Staff-only and printed from a desktop.
_ALLOWED = {'theme/dialog/qr_label_dialog.py'}


def test_no_new_tab_navigate_in_presentation():
    offenders = []
    for folder in ('pages', 'theme'):
        for path in (ROOT / folder).rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if rel in _ALLOWED:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'to'):
                    continue
                for kw in node.keywords:
                    if kw.arg == 'new_tab' and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                        offenders.append(f'{rel}:{node.lineno}')
    assert not offenders, (
        'Use theme.links.link_button / theme.menu.menu_link (or SlotItem(href=...)) '
        f'so the tap itself opens the tab: {offenders}'
    )


def _client(root_path: str = '', forwarded: str = ''):
    headers = {'X-Forwarded-Prefix': forwarded} if forwarded else {}
    return SimpleNamespace(request=SimpleNamespace(headers=headers, scope={'root_path': root_path}))


@pytest.mark.parametrize('path, root, forwarded, expected', [
    ('/event-info/guide.pdf', '/t/sgl26', '', '/t/sgl26/event-info/guide.pdf'),
    ('/brackets/4', '', '', '/brackets/4'),
    ('/help', '/t/x', '/proxy', '/proxy/t/x/help'),
    ('https://start.gg/x', '/t/x', '', 'https://start.gg/x'),
    ('//evil.example/x', '/t/x', '', '//evil.example/x'),
])
def test_app_href_adds_the_page_prefix(monkeypatch, path, root, forwarded, expected):
    monkeypatch.setattr(links, 'context', SimpleNamespace(client=_client(root, forwarded)))
    assert links.app_href(path) == expected


def test_slot_item_with_href_renders_a_link_not_an_emit():
    html = slot_more_menu('props.row.name', [
        SlotItem('open_in_new', 'Open rules', 'open_rules', href='props.row.rules_url'),
        SlotItem('edit', 'Edit…', 'edit'),
    ])
    assert '<q-item clickable tag="a" :href="props.row.rules_url" target="_blank"' in html
    assert "$emit('open_rules'" not in html
    assert "$emit('edit'" in html
