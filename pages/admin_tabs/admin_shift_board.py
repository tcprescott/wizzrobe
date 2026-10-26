"""Admin → Shift Board: the volunteers' day grid, editable.

The same role-column grid volunteers read under Volunteer → Full Schedule,
with drafts, open slots and who has turned up. Tapping a shift opens its sheet
(``VolunteerShiftSheet``); tapping an empty hour adds a shift there. Admin →
Volunteer Shifts stays the list view for building the schedule in bulk.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

from nicegui import app, ui

from application.services import (
    AuthService,
    SystemConfigService,
    TablePreferenceService,
    get_user_from_session,
)
from application.services.volunteer import (
    DayGrid,
    GridRole,
    GridShift,
    VolunteerScheduleService,
    apply_roles,
    arrange_roles,
)
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.utils.app_links import VOL_SCHEDULE, admin_url
from application.utils.tenant_session import tenant_session_get, tenant_session_set
from application.utils.timezone import timezone_label, today_local
from theme.dialog.volunteer_export_dialog import VolunteerExportDialog
from theme.dialog.volunteer_shift_dialog import VolunteerShiftDialog
from theme.dialog.volunteer_shift_sheet import DayShifts, VolunteerShiftSheet
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header
from theme.tables.preferences import TableKeys
from theme.timer_teardown import PageTimer
from theme.url_state import iso_day, url_param, url_state
from theme.volunteer_grid import (
    day_label,
    event_days,
    is_customized,
    open_columns_dialog,
    opening_day,
    render_day_bar,
    render_day_grid,
)

# Shares the Volunteer Shifts tab's remembered day: both are the coordinator's
# "which day am I working on".
_DAY_KEY = 'volunteers:day'


@dataclass
class _BoardState:
    day: Optional[date]
    roles: Tuple[GridRole, ...]
    saved: list
    grid: Optional[DayGrid] = None
    selected: int = 0
    shifts: dict = field(default_factory=dict)


def coverage_chips(grid: DayGrid) -> List[Tuple[str, str]]:
    """The day's coverage as (text, chip tone), worded like Volunteer Shifts' strip."""
    shifts = [s for c in grid.columns for s in c.shifts]
    if not shifts:
        return []
    needed = sum(s.slots_needed for s in shifts)
    filled = sum(min(len(s.people), s.slots_needed) for s in shifts)
    gaps = sum(s.open_slots for s in shifts)
    drafts = sum(1 for s in shifts for p in s.people if p.draft)
    chips = [(f'{filled}/{needed} slots filled', 'neutral')]
    chips.append((f'{gaps} open', 'pending') if gaps else ('Fully staffed', 'ok'))
    if drafts:
        chips.append((f'{drafts} draft', 'live'))
    return chips


def index_shifts(grid: DayGrid) -> dict:
    """shift id → (position name, shift), for the sheet to read from."""
    return {s.shift_id: (c.position, s) for c in grid.columns for s in c.shifts}


async def admin_shift_board_page(day: Optional[str] = None) -> None:
    actor = await get_user_from_session(app.storage.user)
    if not await AuthService.can_manage_volunteers(actor):
        ui.label('You do not have permission to manage volunteers.').classes('text-error')
        return

    service = VolunteerScheduleService()
    prefs = TablePreferenceService()
    start, end = await SystemConfigService.get_event_window()
    days = event_days(start, end)
    try:
        positions = await service.grid_roles(actor)
    except (ValueError, PermissionError):
        positions = []
    stored = await prefs.get(actor, TableKeys.ADMIN_SHIFT_BOARD) if actor else None
    saved = (stored or {}).get('columns') or []
    # The address (?day=: a deep link, a reload, Back) outranks the remembered
    # day, when it is a day of this event. ``day`` is the request's, which only
    # holds for the tab the page opened on; the address keeper tracks switches.
    day = url_state().requested('day')
    wanted = day if day in {d.isoformat() for d in days} else tenant_session_get(_DAY_KEY)
    state = _BoardState(
        day=opening_day(days, wanted, today_local()),
        roles=arrange_roles(positions, saved),
        saved=saved,
    )

    def lookup(shift_id: int):
        return state.shifts.get(shift_id)

    def day_shifts() -> DayShifts:
        if state.grid is None:
            return []
        return [(c.position, s) for c in state.grid.columns
                for s in sorted(c.shifts, key=lambda s: s.starts_at)]

    def sheet_closed() -> None:
        if state.selected:
            state.selected = 0
            grid_view.refresh()

    async def reload() -> None:
        if state.day is not None:
            try:
                state.grid = await service.board_day_grid(actor, state.day)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                state.grid = None
        state.shifts = index_shifts(state.grid) if state.grid else {}
        summary.refresh()
        grid_view.refresh()
        sheet.sync()

    sheet = VolunteerShiftSheet(lookup, day_shifts, reload, sheet_closed)

    def open_shift(shift: GridShift, _position: str) -> None:
        state.selected = shift.shift_id
        grid_view.refresh()
        sheet.open(shift.shift_id)

    async def add_shift(position_id: Optional[int] = None, starts_at: Optional[datetime] = None) -> None:
        position = await VolunteerPositionService().get(position_id) if position_id else None

        async def after(_) -> None:
            await reload()
        await VolunteerShiftDialog(
            position=position,
            default_day=state.day.isoformat() if state.day else None,
            default_start=starts_at,
            on_submit=after,
        ).open()

    def columns_saved(roles: tuple, saved: list) -> None:
        state.roles = roles
        state.saved = saved
        day_bar.refresh()
        grid_view.refresh()

    def open_columns() -> None:
        open_columns_dialog(
            user=actor, key=TableKeys.ADMIN_SHIFT_BOARD, positions=positions,
            roles=state.roles, saved=state.saved, on_saved=columns_saved,
        )

    async def show_day(d: Optional[date]) -> None:
        state.day = d
        state.selected = 0
        day_bar.refresh()
        await reload()

    async def pick_day(d: date) -> None:
        tenant_session_set(_DAY_KEY, d.isoformat())
        await show_day(d)
        day_param.set(d.isoformat())

    async def restore_day(value: str) -> None:
        await show_day(opening_day(days, value, today_local()))

    def toolbar() -> None:
        ui.button('Add shift', icon='add', on_click=lambda: add_shift()) \
            .props('no-caps rounded color=primary').classes('wiz-write')
        with ui.button('Actions').props('flat no-caps color=primary icon-right=expand_more').classes('wiz-write'):
            with ui.menu():
                menu_action('view_list', 'Open the list view',
                            lambda: ui.navigate.to(admin_url(VOL_SCHEDULE, day=state.day)))
                menu_action('download', 'Export data', lambda: VolunteerExportDialog().open())

    @ui.refreshable
    def day_bar() -> None:
        render_day_bar(
            days, state.day, pick_day,
            on_columns=open_columns if positions else None,
            customized=is_customized(state.roles, positions),
            trailing=toolbar,
        )

    @ui.refreshable
    def summary() -> None:
        chips = coverage_chips(state.grid) if state.grid is not None else []
        if chips:
            with ui.row().classes('items-center gap-2').style('flex-wrap: wrap;'):
                for text, tone in chips:
                    ui.label(text).classes(f'wiz-chip wiz-chip--{tone}')

    @ui.refreshable
    def grid_view() -> None:
        if state.day is None:
            empty_panel('No event dates are set yet.', icon='event_busy')
            return
        if state.grid is None:
            return
        if not state.grid.columns:
            empty_panel('No active positions yet. Add one under Volunteer Shifts → Manage positions.',
                        icon='badge')
            return
        grid = apply_roles(state.grid, state.roles)
        if not grid.columns:
            empty_panel(
                f'Every role on {day_label(state.day)} is hidden. Use Columns to show them.',
                icon='visibility_off',
            )
            return
        render_day_grid(
            grid, state.day, staff=True, on_shift=open_shift,
            on_empty=lambda pid, at: add_shift(pid, at),
            selected_shift_id=state.selected,
        )

    with ui.column().classes('page-container-wide') as page:
        day_param = url_param(
            'day', page, default='', value=state.day.isoformat() if state.day else '',
            label=lambda v: day_label(d) if (d := iso_day(v)) else None, on_restore=restore_day,
        )
        with page_header(
            'Shift Board',
            "Every station, hour by hour. Tap a shift to change who's on it, or an "
            f'empty hour to add one. Times are {timezone_label()}.',
        ):
            await help_icon('shift-board', user=actor)
        day_bar()
        summary()
        grid_view()
    await reload()
    # Picks up check-ins, late notes and other coordinators' edits on a screen
    # left open at the desk.
    PageTimer(60, reload)

