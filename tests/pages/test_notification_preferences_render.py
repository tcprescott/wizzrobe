"""Profile → Notifications stays cheap to build (SGLMAN-1X, SGLMAN-1Y).

Production built every message row in every section, collapsed ones included,
plus a full Off / Silent / Ping menu behind each row's button, then rebuilt the
lot on every change. One player opening Profile blocked the shared event loop
for about 2s. These run the real NiceGUI element tree (no browser).
"""

import asyncio

from nicegui import Client, core, ui
from nicegui.page import page

from application.services.notification_catalog import targets
from application.services.notification_preference_service import (
    SECTIONS,
    MyNotificationRow,
    MyNotifications,
    NotificationPreferenceService,
    preset_mode,
)
from models import NotificationMode, NotificationPreset
from pages.home_tabs import notification_preferences_section as section


def _mine() -> MyNotifications:
    preset = NotificationPreset.EVERYTHING
    rows = [
        MyNotificationRow(t, t.default, preset_mode(preset, t), NotificationMode.OFF if i % 7 == 0 else None)
        for i, t in enumerate(targets())
    ]
    rows.sort(key=lambda r: [s for s, _ in SECTIONS].index(r.section))
    return MyNotifications(preset, rows)


async def _build(monkeypatch):
    mine = _mine()

    async def get_mine(_user):
        return mine

    monkeypatch.setattr(NotificationPreferenceService, 'get_mine', get_mine)
    core.loop = asyncio.get_running_loop()
    client = Client(page('/'), request=None)
    with client:
        with ui.column() as column:
            await section.render_notification_preferences(
                object(), on_saving=lambda: None, on_saved=lambda: None, on_error=lambda _: None,
            )
    return client, column, mine


def _click(client: Client, element: ui.element) -> None:
    with client:
        for listener in element._event_listeners.values():
            if listener.type == 'click':
                listener.handler(None)


async def test_page_stays_small(monkeypatch):
    _, column, mine = await _build(monkeypatch)
    count = len(list(column.descendants()))
    print(f'{len(mine.rows)} rows -> {count} elements')
    assert count < 250, f'{count} elements for {len(mine.rows)} messages'


def _expansions(column: ui.element) -> list[ui.expansion]:
    return [e for e in column.descendants() if isinstance(e, ui.expansion)]


async def test_sections_build_their_rows_on_first_open(monkeypatch):
    client, column, mine = await _build(monkeypatch)
    customize = _expansions(column)[0]
    assert not customize.default_slot.children
    with client:
        customize.set_value(True)
    sections = _expansions(customize)
    assert len(sections) == len({r.section for r in mine.rows})
    assert not any(s.default_slot.children for s in sections), 'collapsed sections stay empty'
    with client:
        sections[0].set_value(True)
    buttons = [e for e in sections[0].descendants() if isinstance(e, ui.button)]
    assert buttons, 'opening a section builds its rows'
    with client:
        sections[0].set_value(False)
        sections[0].set_value(True)
    assert len([e for e in sections[0].descendants() if isinstance(e, ui.button)]) == len(buttons), \
        'reopening a section does not build it twice'


async def test_mode_menus_are_built_on_first_open(monkeypatch):
    client, column, mine = await _build(monkeypatch)
    with client:
        _expansions(column)[0].set_value(True)
        for s in _expansions(column)[1:]:
            s.set_value(True)
    assert not [e for e in column.descendants() if isinstance(e, ui.item)]
    menus = [e for e in column.descendants() if isinstance(e, ui.menu)]
    assert len(menus) == sum(1 for r in mine.rows if r.community is not NotificationMode.OFF)
    _click(client, menus[0].parent_slot.parent)
    labels = [e.text for e in menus[0].descendants() if isinstance(e, ui.item_section) and e.text]
    assert any(label.startswith('Off') for label in labels), labels


async def test_open_sections_stay_open_and_built_after_a_change(monkeypatch):
    client, column, mine = await _build(monkeypatch)

    async def set_mode(_user, *_args):
        return mine.rows[0]

    monkeypatch.setattr(NotificationPreferenceService, 'set_mode', set_mode)
    with client:
        _expansions(column)[0].set_value(True)
        _expansions(column)[1].set_value(True)
    menu = next(e for e in column.descendants() if isinstance(e, ui.menu))
    _click(client, menu.parent_slot.parent)
    old_customize = _expansions(column)[0]
    _click(client, next(e for e in menu.descendants() if isinstance(e, ui.item) and e._event_listeners))
    for _ in range(20):
        await asyncio.sleep(0)
        if _expansions(column)[0] is not old_customize:
            break
    customize, first, *rest = _expansions(column)
    assert customize is not old_customize, 'the change rebuilt the list'
    assert customize.value and first.value
    assert first.default_slot.children, 'a section open before the change is built straight away'
    assert not any(s.default_slot.children for s in rest)
