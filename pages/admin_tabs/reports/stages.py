"""Stage Utilization report.

Per-stage scheduled hours, gaps, back-to-back transitions, unplaced
stream-candidate count, plus a stacked-bar timeline.
"""

from typing import Any, Optional

from nicegui import app, ui

from application.services import AuthService, ReportsService, get_user_from_session
from application.utils.timezone import format_local_display
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    CHART_NEUTRAL,
    ChartPalette,
    chart_palette,
    clicked_row,
    csv_export_button,
    date_range_filter,
    default_date_range,
    display_bounds,
    enable_drill_link,
    event_jump_options,
    is_custom_range,
    navigate_with_params,
    parse_int,
    report_filter_bar,
    report_jumps,
    report_page_shell,
    schedule_drill,
    themed_chart_option,
    tournament_filter,
)


async def stages_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    tournament_id: Optional[int] = None,
    stage_id: Optional[int] = None,
    **_unused,
) -> None:
    start_d, end_d = await default_date_range(start, end)
    stage_id_int = parse_int(stage_id)
    viewer = await get_user_from_session(app.storage.user)
    board_drill, can_open_board = schedule_drill(await AuthService.schedule_board_scope(viewer))

    def nav(**overrides) -> None:
        params: dict[str, Any] = {
            'report': 'stages',
            'start': start_d, 'end': end_d,
            'tournament_id': tournament_id,
            'stage_id': stage_id_int,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(tournament_id is not None)
        + int(stage_id_int is not None)
    )
    with report_page_shell(
        'Stage Utilization',
        'Hours scheduled on each stage, the gaps between, and stream candidates still without one.',
    ):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e))
            await tournament_filter(tournament_id, on_change=lambda t_id: nav(tournament_id=t_id))
            if stage_id_int is not None:
                ui.button(
                    'Clear stage', icon='close',
                    on_click=lambda: nav(stage_id=None),
                ).props('flat dense no-caps color=primary')
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e))

        bounds_start, bounds_end = display_bounds(start_d, end_d)
        data = await ReportsService().stage_utilization(
            bounds_start, bounds_end,
            tournament_id=tournament_id,
            stage_id=stage_id_int, actor=viewer,
        )
        stages = data['stages']

        chart_panel = await section_panel('Hours scheduled per stage', icon='bar_chart')
        with chart_panel.body:
            if not stages:
                ui.label('No active stages in the filter.').classes('italic-note')
            else:
                _render_utilization_chart(stages, await chart_palette())

        summary_rows = [
            {
                'stage_id': r['stage_id'],
                'stage': r['stage_name'],
                'matches': len(r['matches']),
                'scheduled_hours': r['scheduled_hours'],
                'gap_hours': r['gap_hours'],
                'back_to_back': r['back_to_back_count'],
            }
            for r in stages
        ]
        summary_columns = [
            {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'sortable': True},
            {'name': 'matches', 'label': 'Matches', 'field': 'matches', 'sortable': True},
            {'name': 'scheduled_hours', 'label': 'Scheduled hours', 'field': 'scheduled_hours', 'sortable': True},
            {'name': 'gap_hours', 'label': 'Gap hours', 'field': 'gap_hours', 'sortable': True},
            {'name': 'back_to_back', 'label': 'Back-to-back <15min', 'field': 'back_to_back', 'sortable': True},
        ]
        unplaced = data['unplaced_candidate_count']
        summary_note = f'{unplaced} stream candidate{"s" if unplaced != 1 else ""} without a stage.'
        if stage_id_int is None:
            summary_note += ' Pick a stage to see its matches.'
        summary_panel = await section_panel('Per stage', icon='tv', subtitle=summary_note)
        with summary_panel.aside:
            csv_export_button(
                f'stage-utilization-{start_d}-to-{end_d}',
                lambda: summary_columns,
                lambda: summary_rows,
            )

        def _row_clicked(e):
            rid = clicked_row(e).get('stage_id')
            if rid:
                nav(stage_id=rid)

        with summary_panel.body:
            summary_table = ui.table(
                columns=summary_columns,
                rows=summary_rows,
                row_key='stage_id',
            ).classes('full-width wiz-rowclick')
            summary_table.on('row-click', _row_clicked)
            enable_mobile_grid(summary_table, summary_columns, row_click_event='row-click',
                               table_key=TableKeys.REPORTS_STAGE_SUMMARY)

        if stage_id_int is not None and stages:
            match_panel = await section_panel(f'Matches on {stages[0]["stage_name"]}', icon='list')
            with match_panel.body:
                match_rows = [
                    {
                        'match_id': m['match_id'],
                        'tournament_id': m['tournament_id'],
                        'tournament': m['tournament_name'],
                        'scheduled_at': format_local_display(m['scheduled_at']),
                        'start': format_local_display(m['start']),
                        'end': format_local_display(m['end']),
                    }
                    for m in stages[0]['matches']
                ]
                match_columns = [
                    {'name': 'match_id', 'label': 'Match', 'field': 'match_id', 'sortable': True},
                    {'name': 'tournament', 'label': 'Tournament', 'field': 'tournament', 'sortable': True},
                    {'name': 'scheduled_at', 'label': 'Scheduled', 'field': 'scheduled_at', 'sortable': True},
                    {'name': 'start', 'label': 'Window start', 'field': 'start', 'sortable': True},
                    {'name': 'end', 'label': 'Window end', 'field': 'end', 'sortable': True},
                ]
                match_table = ui.table(
                    columns=match_columns,
                    rows=match_rows,
                    pagination=25,
                    row_key='match_id',
                ).classes('full-width')
                match_actions = enable_drill_link(
                    match_table, match_columns, match_rows,
                    board_drill,
                    enabled=can_open_board,
                    hint='Open on the schedule board',
                )
                enable_mobile_grid(match_table, match_columns, actions=match_actions,
                                   table_key=TableKeys.REPORTS_STAGE_MATCHES)


def _render_utilization_chart(stages, palette: ChartPalette) -> None:
    """Horizontal bar of scheduled vs gap hours per stage."""
    categories = [r['stage_name'] for r in stages]
    scheduled = [r['scheduled_hours'] for r in stages]
    gaps = [r['gap_hours'] for r in stages]

    option = {
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'legend': {'data': ['Scheduled hours', 'Gap hours'], 'top': 0},
        'grid': {'left': 140, 'right': 24, 'top': 48, 'bottom': 32},
        'xAxis': {'type': 'value', 'name': 'Hours'},
        'yAxis': {'type': 'category', 'data': categories},
        'series': [
            {
                'name': 'Scheduled hours',
                'type': 'bar',
                'stack': 'utilization',
                'data': scheduled,
                'itemStyle': {'color': palette.primary},
            },
            {
                'name': 'Gap hours',
                'type': 'bar',
                'stack': 'utilization',
                'data': gaps,
                'itemStyle': {'color': CHART_NEUTRAL},
            },
        ],
    }
    ui.echart(themed_chart_option(option)).classes('chart-height')
