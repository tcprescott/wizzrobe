"""The event button in the header, and the pieces every event list shares.

The button names the event the page shows and opens the community's events:
live ones first (the default leading), then drafts (staff only), then past
events, and Manage events for staff. Picking one opens the same page in that
event, or that event's home when the page is about one row of another event (a
bracket, a meetup). An archived event turns the button amber.

On a phone the menu is a bottom sheet: the same ``q-menu``, pinned to the
bottom edge by CSS (``.wiz-event-menu``).
"""

import json
from datetime import date
from typing import TYPE_CHECKING, Iterable, Optional

from nicegui import ui

from application.services.event_service import EventRef
from models import EventStatus

if TYPE_CHECKING:
    from theme.base import BaseLayout

#: Status → (chip text, chip tone).
STATUS_CHIPS = {
    EventStatus.LIVE: ('Live', 'ok'),
    EventStatus.DRAFT: ('Draft', 'info'),
    EventStatus.ARCHIVED: ('Archived', 'pending'),
}

#: The admin tab that lists every event.
EVENTS_ADMIN_PATH = '/admin/events'

# Rebuilds the address in the picked event from the browser's own location, so
# it is right after an in-place tab switch too. ``prefix`` is everything before
# the community-local path (forwarded prefix, /t/<slug>, /e/<slug>); a row page
# goes to the event's home because the row belongs to the event it was in.
_SWITCH_JS = '''
window.wizSwitchEvent = function (prefix, community, slug, row) {
    var path = location.pathname.indexOf(prefix) === 0 ? location.pathname.slice(prefix.length) : '/';
    var rest = row ? '/' : (path || '/') + location.search;
    location.assign(community + '/e/' + slug + rest);
};
'''


def event_dates_label(starts_on: Optional[date], ends_on: Optional[date]) -> str:
    """``Oct 22–25, 2026``, ``Oct 30 – Nov 2, 2026``, ``Dec 30, 2026 – Jan 2, 2027``, or ``''``."""
    if starts_on is None and ends_on is None:
        return ''
    if starts_on is None or ends_on is None or starts_on == ends_on:
        day = starts_on or ends_on
        assert day is not None
        return f'{day:%b} {day.day}, {day.year}'
    if starts_on.year != ends_on.year:
        return f'{starts_on:%b} {starts_on.day}, {starts_on.year} – {ends_on:%b} {ends_on.day}, {ends_on.year}'
    if starts_on.month != ends_on.month:
        return f'{starts_on:%b} {starts_on.day} – {ends_on:%b} {ends_on.day}, {ends_on.year}'
    return f'{starts_on:%b} {starts_on.day}–{ends_on.day}, {ends_on.year}'


def event_status_chip(status: EventStatus) -> ui.element:
    text, tone = STATUS_CHIPS[EventStatus(status)]
    with ui.element('span').classes(f'wiz-chip wiz-chip--{tone}') as chip:
        ui.label(text)
    return chip


def default_tag() -> ui.element:
    with ui.element('span').classes('wiz-chip wiz-chip--neutral') as chip:
        ui.label('Default')
    return chip


def switcher_groups(events: Iterable[EventRef]) -> list[tuple[str, list[EventRef]]]:
    """The menu's sections in order, empty ones left out. Pure, for tests."""
    events = list(events)
    groups = [
        ('Live', [e for e in events if e.is_live]),
        ('Coming up · staff only', [e for e in events if e.is_draft]),
        ('Past events', [e for e in events if e.is_archived]),
    ]
    return [(label, rows) for label, rows in groups if rows]


def render_event_switcher(layout: 'BaseLayout') -> None:
    """The header's event button. Draws nothing without an event in scope."""
    current = layout.current_event
    if current is None or not layout.switcher_events:
        return
    ui.add_head_html(f'<script>{_SWITCH_JS}</script>')
    button = ui.button(color=None).props('flat no-caps').classes('wiz-event-switch')
    button.props(f'aria-label="Event: {current.name}. Switch event"')
    if current.is_archived:
        button.classes(add='wiz-event-switch--archived')
    with button:
        if current.is_archived:
            ui.icon('inventory_2').props('size=xs')
        ui.label(current.name).classes('wiz-event-switch__name')
        ui.icon('expand_more').props('size=xs')
        with ui.menu().props('fit=false').classes('wiz-event-menu'):
            ui.label('Switch event').classes('wiz-event-menu__title')
            with ui.list().props('dense'):
                for label, rows in switcher_groups(layout.switcher_events):
                    ui.item_label(label).props('header').classes('wiz-event-menu__group')
                    for event in rows:
                        _event_item(layout, event, current)
                if layout.manages_events:
                    ui.separator()
                    with ui.item(on_click=lambda: ui.navigate.to(EVENTS_ADMIN_PATH)) \
                            .classes('wiz-event-menu__manage'):
                        with ui.item_section().props('avatar'):
                            ui.icon('settings')
                        with ui.item_section():
                            ui.item_label('Manage events')


def _event_item(layout: 'BaseLayout', event: EventRef, current: EventRef) -> None:
    args = json.dumps([layout.switch_prefix, layout.community_root, event.slug, layout.is_row_page])[1:-1]
    item = ui.item(on_click=lambda: ui.run_javascript(f'wizSwitchEvent({args})'))
    item.classes('wiz-event-menu__item')
    if event.id == current.id:
        item.props('active aria-current=true')
    with item:
        with ui.item_section():
            ui.item_label(event.name).classes('wiz-event-menu__name')
            dates = event_dates_label(event.starts_on, event.ends_on)
            if dates:
                ui.item_label(dates).props('caption')
        with ui.item_section().props('side'):
            event_status_chip(event.status)
