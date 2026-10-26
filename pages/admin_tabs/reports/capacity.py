"""Capacity Forecast detail report.

Concurrent player counts over a date range, against the configured capacity.
"""

from datetime import datetime
from typing import Any, Optional

from nicegui import app, ui

from application.services import (
    AuthService,
    ReportsService,
    get_user_from_session,
)
from application.utils.timezone import format_local_datetime, format_local_display
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    CHART_GRID,
    CHART_RED,
    CHART_TEXT,
    chart_palette,
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
    reports_url,
    schedule_drill,
    themed_chart_option,
    tournament_filter,
)


async def capacity_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    tournament_id: Optional[int] = None,
    focus: Optional[str] = None,
    **_unused,
) -> None:
    reports_service = ReportsService()

    start_d, end_d = await default_date_range(start, end)
    focus_dt = _parse_focus(focus)
    viewer = await get_user_from_session(app.storage.user)
    board_drill, can_open_board = schedule_drill(await AuthService.schedule_board_scope(viewer))

    def nav(**overrides) -> None:
        params: dict[str, Any] = {'report': 'capacity', 'start': start_d, 'end': end_d, 'tournament_id': tournament_id}
        params.update(overrides)
        navigate_with_params(**params)

    active = int(await is_custom_range(start_d, end_d)) + int(tournament_id is not None)
    with report_page_shell(
        'Capacity Forecast',
        "How many players are at a setup at once, against the venue's capacity.",
    ):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e))
            await tournament_filter(tournament_id, on_change=lambda t_id: nav(tournament_id=t_id))
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e))

        bounds_start, bounds_end = display_bounds(start_d, end_d)
        forecast = await reports_service.generate_capacity_forecast(
            bounds_start, bounds_end, tournament_id=tournament_id, actor=viewer,
        )

        intervals = forecast['intervals']
        player_counts = forecast['player_counts']
        on_stream_counts = forecast['on_stream_player_counts']
        off_stream_counts = forecast['off_stream_player_counts']
        capacity = forecast['max_capacity']

        palette = await chart_palette()
        chart_panel = await section_panel(
            'Concurrent players', icon='show_chart',
            subtitle=(
                f'{forecast["interval_minutes"]}-minute intervals. Capacity {capacity}. '
                'Off-stream and on-stream players stack to the total.'
            ),
        )
        with chart_panel.body:
            time_labels = [format_local_datetime(t, '%m-%d %H:%M %Z') for t in intervals]
            capacity_series = [capacity] * len(intervals)

            # The slider ships in stock ECharts blue; restyle it to the
            # community's colour so it sits with the palette in both modes.
            slider_style = {
                'fillerColor': palette.primary_wash,
                'borderColor': CHART_GRID,
                'handleStyle': {'color': palette.primary},
                'moveHandleStyle': {'color': palette.primary},
                'textStyle': {'color': CHART_TEXT},
            }
            data_zoom_options = [{'type': 'inside'}, {'type': 'slider', **slider_style}]
            if focus_dt and intervals:
                first = intervals[0]
                last = intervals[-1]
                if first <= focus_dt <= last:
                    span = (last - first).total_seconds() or 1
                    center = (focus_dt - first).total_seconds() / span
                    half = 7200 / span  # ±2h window
                    data_zoom_options = [
                        {'type': 'inside', 'start': max(0, (center - half) * 100), 'end': min(100, (center + half) * 100)},
                        {'type': 'slider', **slider_style, 'start': max(0, (center - half) * 100), 'end': min(100, (center + half) * 100)},
                    ]

            echart_option = {
                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
                # The toolbox sits top-right; the legend keeps to the left half so the two
                # never overlap when the legend wraps on a phone.
                'legend': {'data': ['Off-stream players', 'On-stream players', 'Capacity'], 'top': 0, 'left': 0, 'width': '50%'},
                'grid': {'left': '3%', 'right': '4%', 'top': 96, 'bottom': '14%', 'containLabel': True},
                'toolbox': {'feature': {'saveAsImage': {}, 'dataZoom': {}, 'restore': {}}},
                'dataZoom': data_zoom_options,
                'xAxis': {'type': 'category', 'boundaryGap': False, 'data': time_labels},
                'yAxis': [{'type': 'value', 'name': 'Players'}],
                'series': [
                    # Stage matches have their own setups, so the floor load
                    # against capacity is the lower band; the top edge is the total.
                    {
                        'name': 'Off-stream players',
                        'type': 'line',
                        'stack': 'players',
                        'data': off_stream_counts,
                        'smooth': True,
                        'itemStyle': {'color': palette.primary},
                        'areaStyle': {'color': palette.primary_area},
                        'lineStyle': {'width': 2, 'color': palette.primary},
                    },
                    {
                        'name': 'On-stream players',
                        'type': 'line',
                        'stack': 'players',
                        'data': on_stream_counts,
                        'smooth': True,
                        'itemStyle': {'color': palette.secondary},
                        'areaStyle': {'color': palette.secondary, 'opacity': 0.18},
                        'lineStyle': {'width': 2, 'color': palette.secondary},
                    },
                    {
                        'name': 'Capacity',
                        'type': 'line',
                        'data': capacity_series,
                        'itemStyle': {'color': CHART_RED},
                        'lineStyle': {'width': 2, 'color': CHART_RED, 'type': 'dashed'},
                        'symbol': 'none',
                    },
                ],
            }
            ui.echart(themed_chart_option(echart_option)).classes('chart-height')

        if focus_dt:
            focus_panel = await section_panel(
                f'Matches active at {format_local_display(focus_dt)}', icon='schedule',
            )
            with focus_panel.body:
                focused_matches = await reports_service.matches_active_at(focus_dt, tournament_id=tournament_id, actor=viewer)
                if not focused_matches:
                    ui.label('No active matches at this instant.').classes('italic-note')
                else:
                    focus_rows = [
                        {
                            'match_id': m.id,
                            'tournament_id': m.tournament_id,  # type: ignore[attr-defined]
                            'tournament': m.tournament.name if m.tournament else '',
                            'scheduled_at': format_local_display(m.scheduled_at),
                            'players': len(m.players),
                            'stage': m.stage.name if m.stage else '',
                            'state': m.current_state,
                        }
                        for m in focused_matches
                    ]
                    focus_columns = [
                        {'name': 'match_id', 'label': 'Match', 'field': 'match_id', 'sortable': True},
                        {'name': 'tournament', 'label': 'Tournament', 'field': 'tournament', 'sortable': True},
                        {'name': 'scheduled_at', 'label': 'Scheduled', 'field': 'scheduled_at', 'sortable': True},
                        # Not sortable: a joined roster sorts on whoever is listed first.
                        {'name': 'players', 'label': 'Players', 'field': 'players'},
                        {'name': 'stage', 'label': 'Stage', 'field': 'stage'},
                        {'name': 'state', 'label': 'State', 'field': 'state'},
                    ]
                    focus_table = ui.table(columns=focus_columns, rows=focus_rows, row_key='match_id',
                                           pagination=25).classes('full-width')
                    # Peak players over the ceiling is fixed by moving one of
                    # *these* matches; this is the only table that names them.
                    focus_actions = enable_drill_link(
                        focus_table, focus_columns, focus_rows,
                        board_drill,
                        enabled=can_open_board,
                        hint='Open on the schedule board',
                    )
                    enable_mobile_grid(focus_table, focus_columns, actions=focus_actions,
                                       table_key=TableKeys.REPORTS_CAPACITY_FOCUS)

        with ui.element('div').classes('wiz-report-split'):
            peaks_panel = await section_panel(
                'Busiest times', icon='bar_chart', subtitle='Ranked by off-stream players.',
            )
            with peaks_panel.body:
                totals = dict(zip(intervals, player_counts, strict=True))
                peaks = ReportsService.peak_times(intervals, off_stream_counts, top_n=5)
                if not any(player_counts):
                    ui.label('No matches found in the selected window.').classes('italic-note')
                else:
                    for peak_time, count in peaks:
                        with ui.row().classes('items-center no-wrap full-width'):
                            ui.label(format_local_display(peak_time)).classes('cell-timestamp')
                            ui.label(f'{count} off stream').classes(
                                'text-weight-bold text-no-wrap' + (' text-negative' if count > capacity else ''))
                            ui.label(f'{totals[peak_time]} total').classes('text-caption text-muted text-no-wrap')
                            ui.space()
                            ui.link(
                                'Inspect',
                                reports_url(
                                    'capacity', start=start_d, end=end_d,
                                    tournament_id=tournament_id, focus=peak_time.isoformat(),
                                ),
                            )

            rows = [
                {
                    'time': format_local_display(intervals[i]),
                    'active_players': player_counts[i],
                    'off_stream_players': off_stream_counts[i],
                    'on_stream_players': on_stream_counts[i],
                    'match_ids': ','.join(str(m) for m in forecast['match_ids_per_interval'][i]),
                }
                for i in range(len(intervals))
            ]
            columns = [
                {'name': 'time', 'label': 'Time', 'field': 'time', 'sortable': True},
                {'name': 'active_players', 'label': 'Active players', 'field': 'active_players', 'sortable': True},
                {'name': 'off_stream_players', 'label': 'Off-stream players', 'field': 'off_stream_players', 'sortable': True},
                {'name': 'on_stream_players', 'label': 'On-stream players', 'field': 'on_stream_players', 'sortable': True},
                {'name': 'match_ids', 'label': 'Match IDs', 'field': 'match_ids'},
            ]
            data_panel = await section_panel('Forecast data', icon='table_chart')
            with data_panel.aside:
                csv_export_button(
                    f'capacity-forecast-{start_d}-to-{end_d}',
                    lambda: columns,
                    lambda: rows,
                )
            with data_panel.body:
                forecast_table = ui.table(columns=columns, rows=rows, pagination=25, row_key='time') \
                    .classes('full-width')
                enable_mobile_grid(forecast_table, columns,
                                   table_key=TableKeys.REPORTS_CAPACITY_FORECAST)
                # Five columns overflow the half-width panel, and QTable gives its
                # scroll region no attributes of its own: make it a keyboard stop
                # (WCAG 2.1.1, axe scrollable-region-focusable).
                ui.run_javascript(
                    f"const m = document.querySelector('#{forecast_table.html_id} .q-table__middle');"
                    "if (m) { m.tabIndex = 0; m.setAttribute('role', 'region');"
                    " m.setAttribute('aria-label', 'Forecast data'); }"
                )


def _parse_focus(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


