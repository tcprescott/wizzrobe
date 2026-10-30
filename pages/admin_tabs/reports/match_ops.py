"""Match Operations detail report.

Per-match operational metrics (start delay, duration, confirmation lag)
plus per-tournament aggregates.
"""

from typing import Any, Optional

from nicegui import app, ui

from application.services import AuthService, ReportsService, get_user_from_session
from application.utils.timezone import format_local_display
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
    filter_field,
    is_custom_range,
    navigate_with_params,
    report_filter_bar,
    report_jumps,
    report_page_shell,
    reports_url,
    schedule_drill,
    tournament_filter,
)

STATE_OPTIONS = ['All', 'Scheduled', 'Checked In', 'In Progress', 'Finished']


async def match_ops_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    tournament_id: Optional[int] = None,
    state: Optional[str] = None,
    **_unused,
) -> None:
    start_d, end_d = await default_date_range(start, end)
    selected_state = state if state in STATE_OPTIONS else 'All'
    viewer = await get_user_from_session(app.storage.user)
    board_drill, can_open_board = schedule_drill(await AuthService.schedule_board_scope(viewer))

    def nav(**overrides) -> None:
        params: dict[str, Any] = {
            'report': 'match_ops',
            'start': start_d, 'end': end_d,
            'tournament_id': tournament_id,
            'state': None if selected_state == 'All' else selected_state,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(tournament_id is not None)
        + int(selected_state != 'All')
    )
    with report_page_shell(
        'Match Operations',
        'Start delays, durations and on-time rate, per tournament and per match.',
    ):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e))
            await tournament_filter(tournament_id, on_change=lambda t_id: nav(tournament_id=t_id))
            state_select = filter_field(ui.select(STATE_OPTIONS, value=selected_state, label='State'))
            state_select.on(
                'update:model-value',
                lambda _e: nav(state=None if state_select.value == 'All' else state_select.value),
            )
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e))

        bounds_start, bounds_end = display_bounds(start_d, end_d)
        ops = await ReportsService().match_operations(
            bounds_start, bounds_end, tournament_id=tournament_id, actor=viewer,
        )
        rows = ops['rows']
        if selected_state != 'All':
            rows = [r for r in rows if r['state'] == selected_state]

        agg_panel = await section_panel('Per tournament', icon='emoji_events')
        with agg_panel.body:
            if not ops['aggregates']:
                ui.label('No matches in window.').classes('italic-note')
            else:
                agg_columns = [
                    {'name': 'tournament_name', 'label': 'Tournament', 'field': 'tournament_name', 'sortable': True},
                    {'name': 'matches_total', 'label': 'Matches', 'field': 'matches_total', 'sortable': True},
                    {'name': 'matches_started', 'label': 'Started', 'field': 'matches_started', 'sortable': True},
                    {'name': 'matches_finished', 'label': 'Finished', 'field': 'matches_finished', 'sortable': True},
                    {'name': 'avg_start_delay_min', 'label': 'Avg start delay (min)', 'field': 'avg_start_delay_min', 'sortable': True},
                    {'name': 'avg_duration_min', 'label': 'Avg duration (min)', 'field': 'avg_duration_min', 'sortable': True},
                    {'name': 'expected_avg_min', 'label': 'Expected (min)', 'field': 'expected_avg_min', 'sortable': True},
                    {'name': 'on_time_pct', 'label': 'On-time %', 'field': 'on_time_pct', 'sortable': True},
                ]
                agg_table = ui.table(
                    columns=agg_columns,
                    rows=ops['aggregates'],
                    row_key='tournament_id',
                ).classes('full-width')
                agg_actions = enable_drill_link(
                    agg_table, agg_columns, ops['aggregates'],
                    lambda r: reports_url(
                        'match_ops',
                        start=start_d, end=end_d,
                        tournament_id=r['tournament_id'],
                        state=None if selected_state == 'All' else selected_state,
                    ),
                    hint='Show only this tournament',
                )
                enable_mobile_grid(agg_table, agg_columns, actions=agg_actions,
                                   table_key=TableKeys.REPORTS_MATCH_OPS_SUMMARY)

        detail_rows = [
            {
                **r,
                'scheduled_at': format_local_display(r['scheduled_at']),
            }
            for r in rows
        ]
        detail_columns = [
            {'name': 'match_id', 'label': 'Match', 'field': 'match_id', 'sortable': True},
            {'name': 'tournament_name', 'label': 'Tournament', 'field': 'tournament_name', 'sortable': True},
            {'name': 'scheduled_at', 'label': 'Scheduled', 'field': 'scheduled_at', 'sortable': True},
            {'name': 'state', 'label': 'State', 'field': 'state', 'sortable': True},
            {'name': 'start_delay_min', 'label': 'Start delay (min)', 'field': 'start_delay_min', 'sortable': True},
            {'name': 'duration_min', 'label': 'Duration (min)', 'field': 'duration_min', 'sortable': True},
            {'name': 'confirmation_lag_min', 'label': 'Confirm lag (min)', 'field': 'confirmation_lag_min', 'sortable': True},
            {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'sortable': True},
            {'name': 'player_count', 'label': 'Players', 'field': 'player_count', 'sortable': True},
        ]
        detail_panel = await section_panel('Matches', icon='sports_esports')
        with detail_panel.aside:
            csv_export_button(
                f'match-ops-{start_d}-to-{end_d}',
                lambda: detail_columns,
                lambda: detail_rows,
            )
        with detail_panel.body:
            detail_table = ui.table(
                columns=detail_columns,
                rows=detail_rows,
                pagination=25,
                row_key='match_id',
            ).classes('full-width')
            detail_actions = enable_drill_link(
                detail_table, detail_columns, detail_rows,
                board_drill,
                enabled=can_open_board,
                hint='Open on the schedule board',
            )
            enable_mobile_grid(detail_table, detail_columns, actions=detail_actions,
                               table_key=TableKeys.REPORTS_MATCH_OPS_DETAIL)
