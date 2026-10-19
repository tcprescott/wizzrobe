"""Admin → Notifications: who gets each message, and whether it pings.

One row per message, one column per kind of recipient (layout B, picked by
Thomas 2026-10-19). Tapping a cell offers Off, Silent and Ping, and back to the
default; the change saves at once and redraws only that cell. On a phone each
row is a card of the same cells as chips. The catalogue and defaults are code
(``application/services/notification_catalog.py``); only overrides are stored.
"""

from typing import Callable, Dict, List

from nicegui import app, ui

from application.services import NotificationSettingsService, get_user_from_session
from application.services.notification_catalog import GROUPS
from application.services.notification_settings_service import NotificationSettingRow
from application.utils.app_links import REPORTS, admin_url
from models import NotificationMode, NotificationRecipient
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header

_ICONS = {
    NotificationMode.PING: 'notifications_active',
    NotificationMode.SILENT: 'notifications_off',
    NotificationMode.OFF: 'block',
}
_LABELS = {
    NotificationMode.PING: 'Ping',
    NotificationMode.SILENT: 'Silent',
    NotificationMode.OFF: 'Off',
}
_COLUMNS = list(NotificationRecipient)


def _cell_text(row: NotificationSettingRow, *, with_recipient: bool) -> str:
    label = _LABELS[row.mode]
    return f'{row.recipient.label}: {label}' if with_recipient else label


class _Cell:
    """One recipient's setting for one message, drawn on desktop and on a phone."""

    def __init__(self, row: NotificationSettingRow) -> None:
        self.row = row
        self.buttons: List[tuple[ui.button, bool]] = []

    def draw(self, on_pick: Callable, *, with_recipient: bool) -> None:
        button = ui.button(on_click=lambda: None, color=None).props('flat dense no-caps')
        self.buttons.append((button, with_recipient))
        with button:
            with ui.menu():
                for mode in (NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING):
                    menu_action(_ICONS[mode], _LABELS[mode], lambda m=mode: on_pick(self, m))
                ui.separator()
                menu_action('undo', f'Default: {_LABELS[self.row.default]}',
                            lambda: on_pick(self, self.row.default))
        self.paint()

    def paint(self) -> None:
        row = self.row
        who = row.notice.who.get(row.recipient, '')
        for button, with_recipient in self.buttons:
            button.props(f'icon={_ICONS[row.mode]}')
            button.set_text(_cell_text(row, with_recipient=with_recipient))
            button.classes(
                replace=f'wiz-notify-cell wiz-notify-cell--{row.mode.value}'
                + (' wiz-notify-cell--changed' if row.changed else ''),
            )
            state = f'{_LABELS[row.mode]}' + ('' if not row.changed else f', default {_LABELS[row.default]}')
            button.props(f'aria-label="{row.notice.title}, {row.recipient.label}: {state}"')
            button.tooltip(who or row.recipient.label)


async def admin_notifications_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    if actor is None:
        return
    try:
        rows = await NotificationSettingsService.list_settings(actor)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return

    cells: Dict[tuple[str, NotificationRecipient], _Cell] = {
        (r.notice.key, r.recipient): _Cell(r) for r in rows
    }
    notices = []
    for r in rows:
        if r.notice not in notices:
            notices.append(r.notice)
    filters = {'text': '', 'changed': False}
    row_elements: Dict[str, List[ui.element]] = {}
    group_elements: Dict[str, List[ui.element]] = {}

    def visible(notice) -> bool:
        text = str(filters['text']).strip().lower()
        if text and text not in f'{notice.title} {notice.when} {notice.group}'.lower():
            return False
        if filters['changed'] and not any(
            cells[(notice.key, r)].row.changed for r in notice.defaults
        ):
            return False
        return True

    def apply_filters() -> None:
        shown_groups = set()
        for notice in notices:
            show = visible(notice)
            if show:
                shown_groups.add(notice.group)
            for element in row_elements.get(notice.key, []):
                element.set_visibility(show)
        for group, elements in group_elements.items():
            for element in elements:
                element.set_visibility(group in shown_groups)
        changed = sum(1 for c in cells.values() if c.row.changed)
        changed_chip.set_text(f'Changed ({changed})')
        empty.set_visibility(not shown_groups)

    async def pick(cell: _Cell, mode: NotificationMode) -> None:
        if mode == cell.row.mode:
            return
        try:
            cell.row = await NotificationSettingsService.set_mode(
                actor, cell.row.notice.key, cell.row.recipient, mode,
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        cell.paint()
        apply_filters()
        ui.notify(
            f'{cell.row.notice.title}: {cell.row.recipient.label} {_LABELS[mode].lower()}',
            color='positive',
        )

    async def reset_all() -> None:
        try:
            count = await NotificationSettingsService.reset_all(actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        for cell in cells.values():
            if cell.row.changed:
                cell.row = NotificationSettingRow(cell.row.notice, cell.row.recipient, cell.row.default)
                cell.paint()
        apply_filters()
        ui.notify(f'{count} setting{"" if count == 1 else "s"} back to default', color='positive')

    def confirm_reset() -> None:
        changed = sum(1 for c in cells.values() if c.row.changed)
        if not changed:
            ui.notify('Everything is already on its default.', color='info')
            return
        ConfirmationDialog(
            message=(
                f'{changed} setting{"" if changed == 1 else "s"} will go back to the '
                'default. Messages already sent are not affected.'
            ),
            on_confirm=reset_all, confirm_text='Reset', title='Reset notifications',
        ).open()

    def toggle_changed() -> None:
        filters['changed'] = not filters['changed']
        changed_chip.classes(replace='wiz-notify-filter' + (' wiz-notify-filter--on' if filters['changed'] else ''))
        changed_chip.props(f'aria-pressed={"true" if filters["changed"] else "false"}')
        apply_filters()

    def on_search(e) -> None:
        filters['text'] = e.value or ''
        apply_filters()

    with ui.column().classes('page-container w-full wiz-notify'):
        page_header(
            'Notifications',
            'Who each message goes to, and whether it pings. Silent messages still '
            'arrive, without a sound or buzz; Off sends nothing on any channel. Email '
            "can't be silent.",
        )
        with ui.row().classes('wiz-toolbar w-full items-center'):
            with ui.row().classes('wiz-toolbar__search items-center no-wrap'):
                ui.input(placeholder='Search notifications', on_change=on_search) \
                    .props('outlined dense clearable aria-label="Search notifications"') \
                    .classes('wiz-toolbar__grow')
                changed_chip = ui.button('Changed', on_click=toggle_changed) \
                    .props('flat dense no-caps aria-pressed=false').classes('wiz-notify-filter')
            with ui.row().classes('wiz-toolbar__actions items-center no-wrap'):
                with ui.button('Actions').props('flat color=primary icon-right=expand_more'):
                    with ui.menu():
                        menu_action(
                            'history', 'Sent messages',
                            lambda: ui.navigate.to(admin_url(REPORTS, report='messages')),
                        )
                        ui.separator()
                        menu_action('restart_alt', 'Reset all to defaults', confirm_reset, negative=True)

        empty = ui.label('No notifications match.').classes('text-caption st-neutral q-mt-md')

        # Desktop: the grid.
        with ui.element('div').classes('wiz-notify-grid gt-sm w-full'):
            with ui.element('table').classes('wiz-notify-table'):
                with ui.element('thead'), ui.element('tr'):
                    with ui.element('th').classes('wiz-notify-table__name'):
                        ui.label('Notification')
                    for column in _COLUMNS:
                        with ui.element('th').classes('wiz-notify-table__col'):
                            ui.label(column.label)
                with ui.element('tbody'):
                    for group in GROUPS:
                        group_notices = [n for n in notices if n.group == group]
                        if not group_notices:
                            continue
                        with ui.element('tr').classes('wiz-notify-table__group') as group_row:
                            with ui.element('td').props(f'colspan={len(_COLUMNS) + 1}'):
                                ui.label(group)
                        group_elements.setdefault(group, []).append(group_row)
                        for notice in group_notices:
                            with ui.element('tr') as tr:
                                with ui.element('td').classes('wiz-notify-table__name'):
                                    ui.label(notice.title).classes('text-weight-bold')
                                    ui.label(notice.when).classes('text-caption st-neutral')
                                for column in _COLUMNS:
                                    with ui.element('td').classes('wiz-notify-table__col'):
                                        cell = cells.get((notice.key, column))
                                        if cell is None:
                                            ui.label('–').classes('st-neutral').props('aria-hidden=true')
                                        else:
                                            cell.draw(pick, with_recipient=False)
                            row_elements.setdefault(notice.key, []).append(tr)

        # Phone: one card per message, its cells as chips.
        with ui.column().classes('lt-md w-full gap-3'):
            for group in GROUPS:
                group_notices = [n for n in notices if n.group == group]
                if not group_notices:
                    continue
                with ui.card().classes('w-full wiz-notify-card') as card:
                    ui.label(group).classes('text-subtitle1 text-weight-bold')
                    for notice in group_notices:
                        with ui.column().classes('wiz-notify-card__row w-full gap-1') as block:
                            ui.label(notice.title).classes('text-weight-bold')
                            ui.label(notice.when).classes('text-caption st-neutral')
                            with ui.row().classes('gap-2'):
                                for recipient in notice.defaults:
                                    cells[(notice.key, recipient)].draw(pick, with_recipient=True)
                        row_elements.setdefault(notice.key, []).append(block)
                group_elements.setdefault(group, []).append(card)

    apply_filters()
