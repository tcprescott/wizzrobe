"""Icon-only controls must have an accessible name (WCAG 4.1.2).

The 2026-10 accessibility audit found 500+ buttons a screen reader announced as
just "button": they drew an icon and explained themselves only in a hover
tooltip. Python-built buttons get their name from ``theme.a11y`` (``.tooltip()``
copies into ``aria-label``); Vue slot templates are strings that hook can't see,
so this file reads them.
"""

import pathlib
import re

import pytest
from nicegui import ui
from nicegui.client import Client
from nicegui.page import page

from theme.a11y import install_tooltip_labels, label_field, make_activatable

ROOT = pathlib.Path(__file__).resolve().parent.parent

_Q_BTN = re.compile(r'<q-btn\b((?:[^>"\']|"[^"]*"|\'[^\']*\')*?)(/?)>(.*?)(?:</q-btn>|$)', re.S)
_NAMED = re.compile(r'(?:^|\s):?(?:label|aria-label|title)=')


def _template_buttons():
    for path in sorted([*ROOT.joinpath('pages').rglob('*.py'), *ROOT.joinpath('theme').rglob('*.py')]):
        text = path.read_text()
        for match in _Q_BTN.finditer(text):
            line = text.count('\n', 0, match.start()) + 1
            yield f'{path.relative_to(ROOT)}:{line}', match


def test_every_template_q_btn_has_a_name():
    unnamed = []
    for where, match in _template_buttons():
        attrs, self_closing, body = match.groups()
        if _NAMED.search(attrs):
            continue
        if not self_closing:
            visible = re.sub(r'<q-tooltip.*?</q-tooltip>', '', body, flags=re.S)
            visible = re.sub(r'<[^>]+>', '', visible)
            if visible.strip():
                continue
        unnamed.append(where)
    assert not unnamed, (
        'q-btn with no label, aria-label or title (a tooltip is not a name):\n  '
        + '\n  '.join(unnamed)
    )


@pytest.fixture
def client():
    install_tooltip_labels()
    with Client(page(''), request=None) as c:
        yield c


def test_tooltip_names_an_icon_only_button(client):
    assert ui.button(icon='delete').tooltip('Delete').props['aria-label'] == 'Delete'


def test_tooltip_leaves_a_labelled_button_alone(client):
    assert 'aria-label' not in ui.button('Save').tooltip('Save your changes').props


def test_tooltip_keeps_an_explicit_aria_label(client):
    button = ui.button(icon='menu').props('aria-label="Menu"').tooltip('Open the menu')
    assert button.props['aria-label'] == 'Menu'


def test_make_activatable_puts_an_element_in_the_tab_order(client):
    icon = make_activatable(ui.icon('edit_calendar'), 'Pick a date')
    assert icon.props['tabindex'] == '0'
    assert icon.props['role'] == 'button'
    assert icon.props['aria-label'] == 'Pick a date'


def test_label_field_points_the_caption_at_the_control(client):
    caption = ui.label('Day')
    select = label_field(caption, ui.select(['a', 'b']))
    assert caption.tag == 'label'
    assert caption.props['for'] == select.props['for']
