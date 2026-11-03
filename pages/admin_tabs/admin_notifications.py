"""Admin → Notifications: who gets each message, and whether it pings.

One card per subheading of the catalogue (layout B, picked by Thomas
2026-10-19), two across on a wide screen, each showing only the recipient
columns its messages use. The on-shift, admins and all-staff columns share one
Staff dropdown (Ping all, Ping on-duty, Silent, Off). Tapping a cell saves at
once and redraws only that cell. On a phone each card lists its messages with
labelled chips instead of columns. The catalogue and defaults are code
(``application/services/notification_catalog.py``); only overrides are stored.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from nicegui import app, ui

from application.services import NotificationSettingsService, get_user_from_session
from application.services.notification_catalog import (
    GROUPS,
    ON_DUTY,
    SECTIONS,
    STAFF_RECIPIENTS,
    Notice,
    StaffChoice,
    staff_choice_for,
    staff_choices,
)
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
_CHOICE_MODE = {
    StaffChoice.PING_ALL: NotificationMode.PING,
    StaffChoice.PING_ON_DUTY: NotificationMode.PING,
    StaffChoice.SILENT: NotificationMode.SILENT,
    StaffChoice.OFF: NotificationMode.OFF,
}
_FALLBACK = "All staff if nobody's on shift."
_CHOICE_HELP = {
    StaffChoice.PING_ALL: f"Whoever's on shift, plus the tournament's own admins or coordinators. {_FALLBACK}",
    StaffChoice.PING_ON_DUTY: f"Only whoever's on shift. {_FALLBACK}",
    StaffChoice.SILENT: 'The same people, without a sound or buzz.',
    StaffChoice.OFF: 'Sends nothing.',
}
_STAFF = 'staff_alerts'
_COLUMN_ORDER: List[Union[NotificationRecipient, str]] = [
    NotificationRecipient.PLAYERS, NotificationRecipient.CREW, NotificationRecipient.WATCHERS,
    NotificationRecipient.FOLLOWERS, NotificationRecipient.VOLUNTEERS, _STAFF,
    NotificationRecipient.MEMBERS,
]


def _column_label(column: Union[NotificationRecipient, str]) -> str:
    return column.label if isinstance(column, NotificationRecipient) else 'Staff'


def _choice_label(notice: Notice, choice: Optional[StaffChoice]) -> str:
    if choice is None:
        return 'Custom'
    if choice is StaffChoice.PING_ALL and ON_DUTY not in notice.staff_recipients:
        return 'Ping'
    return choice.label


def _choice_help(notice: Notice, choice: StaffChoice) -> str:
    if ON_DUTY in notice.staff_recipients:
        return _CHOICE_HELP[choice]
    who = notice.who.get(notice.staff_recipients[0], '')
    if choice is StaffChoice.PING_ALL:
        return who or 'Pings as normal.'
    return _CHOICE_HELP[choice]


class _Cell:
    """One recipient's setting for one message, drawn on desktop and on a phone."""

    def __init__(self, row: NotificationSettingRow) -> None:
        self.row = row
        self.buttons: List[Tuple[ui.button, bool]] = []
        self.items: List[Tuple[NotificationMode, ui.menu_item]] = []

    @property
    def notice(self) -> Notice:
        return self.row.notice

    @property
    def changed(self) -> bool:
        return self.row.changed

    def draw(self, on_pick: Callable, *, with_label: bool) -> None:
        button = _lazy_menu_button(self, lambda: self._fill(on_pick))
        self.buttons.append((button, with_label))
        button.tooltip(self.row.notice.who.get(self.row.recipient, '') or self.row.recipient.label)
        self.paint()

    def _fill(self, on_pick: Callable) -> None:
        for mode in (NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING):
            self.items.append(
                (mode, menu_action(_ICONS[mode], _LABELS[mode], lambda m=mode: on_pick(self, m))),
            )
        ui.separator()
        menu_action('undo', f'Default: {_LABELS[self.row.default]}',
                    lambda: on_pick(self, self.row.default))

    def paint(self) -> None:
        row = self.row
        _mark_current(self.items, row.mode)
        label = _LABELS[row.mode]
        state = label + ('' if not row.changed else f', default {_LABELS[row.default]}')
        _paint(
            self.buttons, mode=row.mode, label=label, changed=row.changed,
            column=row.recipient.label,
            aria=f'{row.notice.title}, {row.recipient.label}: {state}',
        )


class _StaffCell:
    """A message's on-shift, admins and all-staff settings as one Staff dropdown."""

    def __init__(self, rows: List[NotificationSettingRow]) -> None:
        self.rows = {r.recipient: r for r in rows}
        self.buttons: List[Tuple[ui.button, bool]] = []
        self.items: List[Tuple[StaffChoice, ui.menu_item]] = []
        self.mix_notes: List[Tuple[ui.item, ui.item_label]] = []

    @property
    def notice(self) -> Notice:
        return next(iter(self.rows.values())).notice

    @property
    def choice(self) -> Optional[StaffChoice]:
        return staff_choice_for(self.notice, {r: row.mode for r, row in self.rows.items()})

    @property
    def default(self) -> Optional[StaffChoice]:
        return staff_choice_for(self.notice, self.notice.defaults)

    @property
    def changed(self) -> bool:
        return any(r.changed for r in self.rows.values())

    def draw(self, on_pick: Callable, *, with_label: bool) -> None:
        button = _lazy_menu_button(self, lambda: self._fill(on_pick), 'wiz-notify-staff-menu')
        self.buttons.append((button, with_label))
        self.paint()

    def _fill(self, on_pick: Callable) -> None:
        notice = self.notice
        with ui.item() as note:
            with ui.item_section():
                ui.item_label('Custom').props('overline')
                mix_label = ui.item_label().props('caption')
        self.mix_notes.append((note, mix_label))
        for choice in staff_choices(notice):
            with ui.menu_item(on_click=lambda c=choice: on_pick(self, c)) as item:
                self.items.append((choice, item))
                with ui.item_section().props('avatar'):
                    ui.icon(_ICONS[_CHOICE_MODE[choice]])
                with ui.item_section():
                    ui.item_label(_choice_label(notice, choice))
                    ui.item_label(_choice_help(notice, choice)).props('caption')
        if self.default is not None:
            ui.separator()
            menu_action('undo', f'Default: {_choice_label(notice, self.default)}',
                        lambda: on_pick(self, self.default))

    def paint(self) -> None:
        notice, choice = self.notice, self.choice
        _mark_current(self.items, choice)
        mode = _CHOICE_MODE[choice] if choice else NotificationMode.SILENT
        label = _choice_label(notice, choice)
        state = label if not self.changed or self.default is None else (
            f'{label}, default {_choice_label(notice, self.default)}'
        )
        mix = ' · '.join(f'{r.label}: {_LABELS[row.mode]}' for r, row in self.rows.items())
        for note, mix_label in self.mix_notes:
            note.set_visibility(choice is None)
            mix_label.set_text(mix)
        _paint(
            self.buttons, mode=mode, label=label, changed=self.changed, column='Staff',
            aria=f'{notice.title}, Staff: {state}' + ('' if choice else f' ({mix})'),
        )


def _lazy_menu_button(cell: 'AnyCell', fill: Callable[[], None], menu_classes: str = '') -> ui.button:
    """A cell button whose menu is built on first open.

    Building every cell's menu up front put ~10,000 elements on the page and
    blocked the event loop for 2-3.5s per visit (SGLMAN-1F/1G/1H/1K/1M).
    """
    button = ui.button(color=None).props('flat dense no-caps')
    with button:
        menu = ui.menu().props('no-parent-event').classes(menu_classes)

    def open_menu() -> None:
        if not menu.default_slot.children:
            with menu:
                fill()
            cell.paint()
        menu.open()

    button.on_click(open_menu)
    return button


def _mark_current(items: Sequence[Tuple[Any, ui.menu_item]], current: Any) -> None:
    for value, item in items:
        if value == current:
            item.props('active')
        else:
            item.props(remove='active')


def _paint(
    buttons: List[Tuple[ui.button, bool]], *, mode: NotificationMode, label: str, changed: bool,
    column: str, aria: str,
) -> None:
    for button, with_label in buttons:
        button.props(f'icon={_ICONS[mode]}')
        button.set_text(f'{column}: {label}' if with_label else label)
        button.classes(
            replace=f'wiz-notify-cell wiz-notify-cell--{mode.value}'
            + (' wiz-notify-cell--changed' if changed else ''),
        )
        button.props(f'aria-label="{aria}"')


AnyCell = Union[_Cell, _StaffCell]


def _cards(notices: List[Notice]) -> List[Tuple[str, str, List[Notice]]]:
    """(group, section, its notices) per card, in display order."""
    cards: List[Tuple[str, str, List[Notice]]] = []
    for group in GROUPS:
        in_group = [n for n in notices if n.group == group]
        if not in_group:
            continue
        sections = [name for name, _ in SECTIONS.get(group, ())] or ['']
        for section in sections:
            members = [n for n in in_group if n.section == section]
            if members:
                cards.append((group, section, members))
    return cards


async def admin_notifications_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    if actor is None:
        return
    try:
        rows = await NotificationSettingsService.list_settings(actor)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return

    notices: List[Notice] = []
    by_notice: Dict[str, List[NotificationSettingRow]] = {}
    for r in rows:
        if r.notice.key not in by_notice:
            notices.append(r.notice)
        by_notice.setdefault(r.notice.key, []).append(r)

    cells: Dict[Tuple[str, Union[NotificationRecipient, str]], AnyCell] = {}
    for notice in notices:
        staff_rows = [r for r in by_notice[notice.key] if r.recipient in STAFF_RECIPIENTS]
        if staff_rows:
            cells[(notice.key, _STAFF)] = _StaffCell(staff_rows)
        for r in by_notice[notice.key]:
            if r.recipient not in STAFF_RECIPIENTS:
                cells[(notice.key, r.recipient)] = _Cell(r)

    def notice_cells(notice: Notice) -> List[Tuple[Union[NotificationRecipient, str], AnyCell]]:
        return [(c, cells[(notice.key, c)]) for c in _COLUMN_ORDER if (notice.key, c) in cells]

    filters = {'text': '', 'changed': False}
    row_elements: Dict[str, List[ui.element]] = {}
    card_elements: Dict[Tuple[str, str], List[ui.element]] = {}
    cards = _cards(notices)

    def visible(notice: Notice) -> bool:
        text = str(filters['text']).strip().lower()
        if text and text not in f'{notice.title} {notice.when} {notice.group} {notice.section}'.lower():
            return False
        if filters['changed'] and not any(cell.changed for _, cell in notice_cells(notice)):
            return False
        return True

    def apply_filters() -> None:
        shown_cards = set()
        for group, section, members in cards:
            for notice in members:
                show = visible(notice)
                if show:
                    shown_cards.add((group, section))
                for element in row_elements.get(notice.key, []):
                    element.set_visibility(show)
        for key, elements in card_elements.items():
            for element in elements:
                element.set_visibility(key in shown_cards)
        changed = sum(1 for c in cells.values() if c.changed)
        changed_chip.set_text(f'Changed ({changed})')
        empty.set_visibility(not shown_cards)

    async def pick(cell: AnyCell, value: Union[NotificationMode, StaffChoice]) -> None:
        try:
            if isinstance(cell, _StaffCell) and isinstance(value, StaffChoice):
                if value == cell.choice:
                    return
                for row in await NotificationSettingsService.set_staff_choice(actor, cell.notice.key, value):
                    cell.rows[row.recipient] = row
                message = f'{cell.notice.title}: staff {_choice_label(cell.notice, value).lower()}'
            elif isinstance(cell, _Cell) and isinstance(value, NotificationMode):
                if value == cell.row.mode:
                    return
                cell.row = await NotificationSettingsService.set_mode(
                    actor, cell.notice.key, cell.row.recipient, value,
                )
                message = f'{cell.notice.title}: {cell.row.recipient.label} {_LABELS[value].lower()}'
            else:
                return
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        cell.paint()
        apply_filters()
        ui.notify(message, color='positive')

    async def reset_all() -> None:
        try:
            count = await NotificationSettingsService.reset_all(actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        for cell in cells.values():
            if not cell.changed:
                continue
            if isinstance(cell, _StaffCell):
                cell.rows = {
                    r: NotificationSettingRow(row.notice, r, row.default, cell.default)
                    for r, row in cell.rows.items()
                }
            else:
                cell.row = NotificationSettingRow(cell.notice, cell.row.recipient, cell.row.default)
            cell.paint()
        apply_filters()
        ui.notify(f'{count} setting{"" if count == 1 else "s"} back to default', color='positive')

    def confirm_reset() -> None:
        changed = sum(1 for c in cells.values() if c.changed)
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

    def card_heading(group: str, section: str) -> None:
        with ui.row().classes('wiz-notify-card__head items-baseline no-wrap w-full'):
            if section:
                ui.label(group).classes('wiz-notify-card__group')
            ui.label(section or group).classes('wiz-notify-card__title')

    with ui.column().classes('page-container-wide w-full wiz-notify'):
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

        with ui.element('div').classes('wiz-notify-cards gt-sm w-full'):
            for group, section, members in cards:
                columns = [c for c in _COLUMN_ORDER if any((n.key, c) in cells for n in members)]
                with ui.element('section').classes('wiz-notify-card') as card:
                    card_heading(group, section)
                    with ui.element('table').classes('wiz-notify-table'):
                        with ui.element('thead'), ui.element('tr'):
                            with ui.element('th').classes('wiz-notify-table__name'):
                                ui.label('Notification')
                            for column in columns:
                                with ui.element('th').classes('wiz-notify-table__col'):
                                    ui.label(_column_label(column))
                        with ui.element('tbody'):
                            for notice in members:
                                with ui.element('tr') as tr:
                                    with ui.element('td').classes('wiz-notify-table__name'):
                                        ui.label(notice.title).classes('text-weight-bold')
                                        ui.label(notice.when).classes('text-caption st-neutral')
                                    for column in columns:
                                        with ui.element('td').classes('wiz-notify-table__col'):
                                            cell = cells.get((notice.key, column))
                                            if cell is None:
                                                ui.label('–').classes('st-neutral').props('aria-hidden=true')
                                            else:
                                                cell.draw(pick, with_label=False)
                                row_elements.setdefault(notice.key, []).append(tr)
                card_elements.setdefault((group, section), []).append(card)

        with ui.column().classes('lt-md w-full gap-3'):
            for group, section, members in cards:
                with ui.element('section').classes('wiz-notify-card w-full') as card:
                    card_heading(group, section)
                    for notice in members:
                        with ui.column().classes('wiz-notify-card__row w-full gap-1') as block:
                            ui.label(notice.title).classes('text-weight-bold')
                            ui.label(notice.when).classes('text-caption st-neutral')
                            with ui.row().classes('gap-2'):
                                for _, cell in notice_cells(notice):
                                    cell.draw(pick, with_label=True)
                        row_elements.setdefault(notice.key, []).append(block)
                card_elements.setdefault((group, section), []).append(card)

    apply_filters()
