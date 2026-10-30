"""Volunteer Coverage report.

Per-shift filled/needed counts across a date range, highlighting understaffed
shifts.
"""

from datetime import date
from typing import Optional

from nicegui import app, ui

from application.services import AuthService, get_user_from_session
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.timezone import format_local_display, to_local
from pages.admin_tabs.links import VOL_SCHEDULE, admin_url
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    csv_export_button,
    date_range_filter,
    default_date_range,
    display_bounds,
    enable_drill_link,
    event_jump_options,
    is_custom_range,
    navigate_with_params,
    report_filter_bar,
    report_jumps,
    report_page_shell,
)


async def volunteers_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    all_reports_link: bool = True,
    **_unused,
) -> None:
    start_d, end_d = await default_date_range(start, end)
    viewer = await get_user_from_session(app.storage.user)
    can_staff_shifts = await AuthService.can_manage_volunteers(viewer)

    def nav(s: date, e: date) -> None:
        navigate_with_params(report='volunteers', start=s, end=e)

    with report_page_shell(
        'Volunteer Coverage',
        'How many volunteers each shift has against how many it needs.',
        all_reports_link=all_reports_link,
    ):
        with report_filter_bar(int(await is_custom_range(start_d, end_d))):
            date_range_filter(start_d, end_d, on_change=nav)
            report_jumps(await event_jump_options(), start_d, end_d, on_pick=nav)

        bounds_start, bounds_end = display_bounds(start_d, end_d)
        coverage = await VolunteerScheduleService().coverage(bounds_start, bounds_end)

        understaffed = [r for r in coverage if r['understaffed']]
        total_open = sum(r['needed'] - r['filled'] for r in understaffed)

        short_panel = await section_panel('Understaffed shifts', icon='person_off')
        with short_panel.body:
            if not understaffed:
                ui.label('Every shift in this window is fully staffed.').classes('italic-note')
            else:
                with ui.row().classes('items-center full-width'):
                    ui.label(f'{len(understaffed)} shift(s) need {total_open} more volunteer(s).') \
                        .classes('text-body2')
                    if can_staff_shifts:
                        first_day = _local_day(min(r['starts_at'] for r in understaffed))
                        ui.space()
                        ui.button(
                            f'Staff {first_day}', icon='open_in_new',
                            on_click=lambda: ui.navigate.to(
                                admin_url(VOL_SCHEDULE, day=first_day)
                            ),
                        ).props('flat dense no-caps color=primary')

        rows = [
            {
                'position': r['position'],
                'label': r['label'],
                'starts_at': format_local_display(r['starts_at']),
                'day': _local_day(r['starts_at']),
                'understaffed': r['understaffed'],
                'coverage': f"{r['filled']}/{r['needed']}",
                'status': 'Understaffed' if r['understaffed'] else 'OK',
            }
            for r in coverage
        ]
        columns = [
            {'name': 'position', 'label': 'Position', 'field': 'position', 'sortable': True},
            {'name': 'label', 'label': 'Shift', 'field': 'label', 'sortable': True},
            {'name': 'starts_at', 'label': 'Start', 'field': 'starts_at', 'sortable': True},
            {'name': 'coverage', 'label': 'Filled / Needed', 'field': 'coverage'},
            {'name': 'status', 'label': 'Status', 'field': 'status', 'sortable': True},
        ]

        shifts_panel = await section_panel('All shifts', icon='event_note')
        with shifts_panel.aside:
            csv_export_button(
                f'volunteer-coverage-{start_d}-to-{end_d}',
                lambda: columns,
                lambda: rows,
            )
        with shifts_panel.body:
            table = ui.table(columns=columns, rows=rows, pagination=25, row_key='starts_at') \
                .classes('full-width')
            drill_actions = enable_drill_link(
                table, columns, rows,
                lambda r: admin_url(VOL_SCHEDULE, day=r['day']) if r['understaffed'] else None,
                enabled=can_staff_shifts,
                hint='Staff this day',
            )
            enable_mobile_grid(table, columns, actions=drill_actions,
                               table_key=TableKeys.REPORTS_VOLUNTEER_HOURS)


def _local_day(when) -> str:
    """The local calendar day a shift starts on, as the Vol. Schedule names it.

    Never a naive ``.date()``: the stored value is UTC, and a 20:00 ET shift
    lands on the next UTC day.
    """
    return to_local(when).date().isoformat()
