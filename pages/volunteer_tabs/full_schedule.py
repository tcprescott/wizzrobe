"""Volunteer Full Schedule tab: every role's shifts for one day, read-only."""

from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple

from nicegui import app, ui

from application.services import (
    SystemConfigService,
    TablePreferenceService,
    get_user_from_discord_id,
)
from application.services.volunteer import (
    GridRole,
    GridShift,
    VolunteerScheduleService,
    apply_roles,
    arrange_roles,
)
from application.utils.tenant_session import tenant_session_get, tenant_session_set
from application.utils.timezone import timezone_label, today_local
from theme.dialog._helpers import form_dialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.preferences import TableKeys
from theme.timer_teardown import PageTimer
from theme.url_state import iso_day, url_param, url_state
from theme.volunteer_grid import (  # noqa: F401 — re-exported for the grid tests
    HOUR_PX,
    day_label,
    event_days,
    is_customized,
    names_that_fit,
    open_columns_dialog,
    opening_day,
    person_label,
    render_day_bar,
    render_day_grid,
    shift_range,
)

_DAY_KEY = 'volunteer:schedule_day'


@dataclass
class _ViewState:
    day: Optional[date]
    roles: Tuple[GridRole, ...]
    saved: list


def _event_day(days, value: Optional[str]) -> Optional[str]:
    """``value`` when it names one of ``days``, as the ISO string ``opening_day`` takes."""
    d = iso_day(value)
    return d.isoformat() if d is not None and d in days else None


async def full_schedule_tab() -> None:
    user = await get_user_from_discord_id(app.storage.user.get('discord_id'))
    service = VolunteerScheduleService()
    start, end = await SystemConfigService.get_event_window()
    days = event_days(start, end)
    prefs = TablePreferenceService()
    try:
        positions = await service.grid_roles(user)
    except (ValueError, PermissionError):
        positions = []
    stored = await prefs.get(user, TableKeys.VOLUNTEER_FULL_SCHEDULE) if user else None
    saved = (stored or {}).get('columns') or []
    state = _ViewState(
        day=opening_day(days, _event_day(days, url_state().requested('day')) or tenant_session_get(_DAY_KEY),
                        today_local()),
        roles=arrange_roles(positions, saved),
        saved=saved,
    )

    def open_names(shift: GridShift, position: str) -> None:
        with form_dialog(position, card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md gap-1 full-width'):
                ui.label(shift_range(shift)).classes('text-caption')
                for person in shift.people:
                    ui.label(person_label(person.name, person.is_viewer)) \
                        .classes('text-weight-bold' if person.is_viewer else '')
        dialog.open()

    def columns_saved(roles: tuple, saved: list) -> None:
        state.roles = roles
        state.saved = saved
        day_bar.refresh()
        grid_view.refresh()

    def open_columns() -> None:
        open_columns_dialog(
            user=user, key=TableKeys.VOLUNTEER_FULL_SCHEDULE, positions=positions,
            roles=state.roles, saved=state.saved, on_saved=columns_saved,
        )

    @ui.refreshable
    def day_bar() -> None:
        render_day_bar(
            days, state.day, pick_day,
            on_columns=open_columns if positions else None,
            customized=is_customized(state.roles, positions),
        )

    @steady_refreshable
    async def grid_view() -> None:
        if state.day is None:
            empty_panel('No event dates are set yet.', icon='event_busy')
            return
        try:
            everyone = await service.day_grid(user, state.day)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        if not everyone.columns:
            empty_panel(f"No shifts on {day_label(state.day)}.", icon='event_busy')
            return
        grid = apply_roles(everyone, state.roles)
        if not grid.columns:
            empty_panel(
                f"Every role with shifts on {day_label(state.day)} is hidden. "
                'Use Columns to show them.',
                icon='visibility_off',
            )
            return
        render_day_grid(grid, state.day, on_shift=open_names)

    async def show_day(d: Optional[date]) -> None:
        state.day = d
        day_bar.refresh()
        await grid_view.refresh()

    async def pick_day(d: date) -> None:
        tenant_session_set(_DAY_KEY, d.isoformat())
        await show_day(d)
        day_param.set(d.isoformat())

    async def restore_day(value: str) -> None:
        await show_day(opening_day(days, _event_day(days, value), today_local()))

    with ui.column().classes('page-container-wide') as page:
        with page_header(
            'Volunteer schedule',
            f"Who's at which station, hour by hour. Times are {timezone_label()}.",
        ):
            await help_icon(('full-schedule', 'The full schedule'), user=user)
        day_param = url_param(
            'day', page, default='', value=state.day.isoformat() if state.day else '',
            label=lambda v: day_label(d) if (d := iso_day(v)) else None, on_restore=restore_day,
        )
        day_bar()
        await grid_view()
    PageTimer(300, grid_view.refresh)
