"""Engagement Telemetry report.

Staff-only view of how people actually use the tool: top-line reach KPIs,
leaderboards (most-viewed pages, most-frequent events, most-active users), and
a filterable, server-paginated raw event log. Reads are Staff-gated at the
service boundary; this page also pre-checks so a non-Staff admin gets a clear
message instead of a raw permission error.
"""

from typing import Optional

from nicegui import app, ui

from application.services import TelemetryService, get_user_from_session
from application.services.telemetry_service import TelemetryCategory
from application.utils.timezone import format_local_display, timezone_label
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    date_range_filter,
    default_date_range,
    display_bounds,
    event_jump_options,
    filter_field,
    is_custom_range,
    kpi_card,
    kpi_grid,
    navigate_with_params,
    paginated_event_log,
    parse_details,
    parse_int,
    report_filter_bar,
    report_jumps,
    report_page_shell,
)

PAGE_SIZE = 50

SUBTITLE = 'How people use the app: page views, clicks and the things they change.'

_CATEGORY_OPTIONS = {
    '': 'All categories',
    TelemetryCategory.PAGE: 'Page views',
    TelemetryCategory.INTERACTION: 'Interactions',
    TelemetryCategory.DOMAIN: 'Domain events',
}


async def telemetry_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    category: Optional[str] = None,
    page: Optional[int] = None,
    **_unused,
) -> None:
    actor = await get_user_from_session(app.storage.user)
    service = TelemetryService()
    if not await _is_staff(actor):
        with report_page_shell('Engagement Telemetry', SUBTITLE):
            ui.label('Only Staff can view engagement telemetry.').classes('text-error')
        return

    start_d, end_d = await default_date_range(start, end)
    user_id_int = parse_int(user_id)
    page_int = max(1, parse_int(page) or 1)
    event_filter = (action or '').strip()
    category_filter = (category or '').strip()
    if category_filter not in _CATEGORY_OPTIONS:
        category_filter = ''
    bounds_start, bounds_end = display_bounds(start_d, end_d)

    def _nav(**overrides):
        params = {
            'report': 'telemetry',
            'start': start_d, 'end': end_d,
            'user_id': user_id_int,
            'action': event_filter or None,
            'category': category_filter or None,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(bool(category_filter))
        + int(bool(event_filter)) + int(user_id_int is not None)
    )
    with report_page_shell('Engagement Telemetry', SUBTITLE):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: _nav(start=s, end=e, page=None))
            category_select = filter_field(ui.select(
                options=_CATEGORY_OPTIONS,
                value=category_filter,
                label='Category',
            ))
            category_select.on(
                'update:model-value',
                lambda _e: _nav(category=(category_select.value or None), page=None),
            )
            event_input = filter_field(ui.input('Path contains', value=event_filter))

            def _on_event(_e):
                _nav(action=(event_input.value or '').strip() or None, page=None)
            event_input.on('blur', _on_event)
            event_input.on('keydown.enter', _on_event)

            if user_id_int is not None:
                ui.button(
                    'Clear person', icon='close',
                    on_click=lambda: _nav(user_id=None, page=None),
                ).props('flat dense no-caps color=primary')
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: _nav(start=s, end=e, page=None))

        summary = await service.engagement_summary(actor, start=bounds_start, end=bounds_end)
        with kpi_grid():
            kpi_card('Total events', f"{summary['total_events']:,}", 'captured in window')
            kpi_card('Unique users', f"{summary['unique_users']:,}", 'identified actors')
            kpi_card('Unique sessions', f"{summary['unique_sessions']:,}", 'browser sessions')
            kpi_card('Page views', f"{summary['page_views']:,}", 'authenticated loads')

        top_paths = await service.top_paths(actor, start=bounds_start, end=bounds_end)
        top_events = await service.top_event_types(actor, start=bounds_start, end=bounds_end)
        top_users = await service.top_users(actor, start=bounds_start, end=bounds_end)

        with ui.element('div').classes('wiz-tile-grid'):
            await _leaderboard(
                'Most viewed pages', 'visibility',
                [
                    {'name': 'path', 'label': 'Path', 'field': 'path'},
                    {'name': 'views', 'label': 'Views', 'field': 'views'},
                    {'name': 'users', 'label': 'Users', 'field': 'users'},
                ],
                top_paths,
                row_key='path',
            )
            await _leaderboard(
                'Most frequent events', 'bolt',
                [
                    {'name': 'event_type', 'label': 'Event', 'field': 'event_type'},
                    {'name': 'category', 'label': 'Category', 'field': 'category'},
                    {'name': 'count', 'label': 'Count', 'field': 'count'},
                ],
                top_events,
                row_key='event_type',
            )
            await _leaderboard(
                'Most active users', 'person',
                [
                    {'name': 'user', 'label': 'User', 'field': 'user'},
                    {'name': 'events', 'label': 'Events', 'field': 'events'},
                    {'name': 'sessions', 'label': 'Sessions', 'field': 'sessions'},
                ],
                top_users,
                row_key='user_id',
                on_row_click=lambda uid: _nav(user_id=uid, page=None),
            )

        # ``action`` is a substring filter, applied to path here for the raw log
        # (event_type is exposed via the category + leaderboards). count_events
        # MUST take the same filters as list_events or the total/pagination and
        # the rows disagree.
        total = await service.count_events(
            actor, start=bounds_start, end=bounds_end,
            category=category_filter or None,
            user_id=user_id_int,
            path_contains=event_filter or None,
        )
        events = await service.list_events(
            actor, start=bounds_start, end=bounds_end,
            category=category_filter or None,
            user_id=user_id_int,
            path_contains=event_filter or None,
            limit=PAGE_SIZE, offset=(page_int - 1) * PAGE_SIZE,
        )

        rows = []
        for ev in events:
            _, display = parse_details(ev.details)
            truncated = display[:200] + ('…' if len(display) > 200 else '')
            rows.append({
                'ev_id': ev.id,
                'created_at': format_local_display(ev.created_at),
                'user': ev.user.preferred_name if ev.user else (
                    f'User {ev.user_id}' if ev.user_id else '—'
                ),
                'category': ev.category,
                'event_type': ev.event_type,
                'path': ev.path or '—',
                'details': truncated,
                'full_details': display,
            })
        columns = [
            {'name': 'created_at', 'label': f'When ({timezone_label()})', 'field': 'created_at', 'sortable': False},
            {'name': 'user', 'label': 'User', 'field': 'user', 'sortable': False},
            {'name': 'category', 'label': 'Category', 'field': 'category', 'sortable': False},
            {'name': 'event_type', 'label': 'Event', 'field': 'event_type', 'sortable': False},
            {'name': 'path', 'label': 'Path', 'field': 'path', 'sortable': False},
            {'name': 'details', 'label': 'Details', 'field': 'details', 'sortable': False},
        ]

        await paginated_event_log(
            title='Events',
            icon='list_alt',
            columns=columns,
            rows=rows,
            row_key='ev_id',
            table_key=TableKeys.REPORTS_TELEMETRY_LOG,
            total=total,
            page=page_int,
            page_size=PAGE_SIZE,
            on_page=lambda new_page: _nav(page=new_page),
            csv_filename_prefix=f'telemetry-page-{page_int}-{start_d}-to-{end_d}',
            count_noun='events',
            note=(
                'Click a row to filter by that user. Category filters the log; '
                'the "Path contains" box matches the path.'
            ),
        )


async def _is_staff(actor) -> bool:
    from application.services import AuthService
    return await AuthService.is_staff(actor)


async def _leaderboard(title, icon, columns, rows, *, row_key, on_row_click=None) -> None:
    panel = await section_panel(
        title, icon=icon,
        subtitle='Pick a person to filter the log to them.' if on_row_click is not None and rows else '',
    )
    with panel.body:
        if not rows:
            ui.label('No data in window.').classes('italic-note')
            return
        table = ui.table(columns=columns, rows=rows, row_key=row_key).classes('full-width')
        if on_row_click is not None:
            table.add_slot('body', r'''
                <q-tr :props="props" @click="$parent.$emit('lb-click', props.row)" style="cursor: pointer">
                    <q-td v-for="col in props.cols" :key="col.name" :props="props">{{ col.value }}</q-td>
                </q-tr>
            ''')

            def _handle(e):
                row = e.args if isinstance(e.args, dict) else {}
                uid = row.get(row_key)
                if uid is not None:
                    on_row_click(uid)
            table.on('lb-click', _handle)
        enable_mobile_grid(table, columns, table_key=TableKeys.REPORTS_TELEMETRY_TOP,
                           row_click_event='lb-click' if on_row_click is not None else None)
