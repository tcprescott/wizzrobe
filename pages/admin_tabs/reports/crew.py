"""Staff / Crew Activity report.

Coverage-by-match table plus contribution-by-person table.
"""

from typing import Any, Optional

from nicegui import app, ui

from application.services import AuthService, ReportsService, get_user_from_session
from application.utils.timezone import format_local_display
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    clicked_row,
    csv_export_button,
    date_range_filter,
    default_date_range,
    display_bounds,
    enable_drill_link,
    event_jump_options,
    filter_field,
    is_custom_range,
    navigate_with_params,
    parse_int,
    report_filter_bar,
    report_jumps,
    report_page_shell,
    schedule_drill,
    tournament_filter,
)

APPROVAL_OPTIONS = ['All', 'Approved only', 'Pending only']


async def crew_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    tournament_id: Optional[int] = None,
    user_id: Optional[int] = None,
    approval: Optional[str] = None,
    **_unused,
) -> None:
    start_d, end_d = await default_date_range(start, end)
    selected_approval = approval if approval in APPROVAL_OPTIONS else 'All'
    user_id_int = parse_int(user_id)
    # Resolved once, not per row: this report is readable by staff, tournament
    # admins and crew coordinators, and all three also have the Schedule tab —
    # but that is the destination's predicate, so ask it rather than assume it.
    viewer = await get_user_from_session(app.storage.user)
    board_drill, can_open_board = schedule_drill(await AuthService.schedule_board_scope(viewer))

    def nav(**overrides) -> None:
        params: dict[str, Any] = {
            'report': 'crew',
            'start': start_d, 'end': end_d,
            'tournament_id': tournament_id,
            'user_id': user_id_int,
            'approval': None if selected_approval == 'All' else selected_approval,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(tournament_id is not None)
        + int(selected_approval != 'All') + int(user_id_int is not None)
    )
    with report_page_shell(
        'Staff / Crew Activity',
        'Commentator and tracker coverage for each match, and who covered it.',
    ):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e))
            await tournament_filter(tournament_id, on_change=lambda t_id: nav(tournament_id=t_id))
            approval_select = filter_field(ui.select(
                APPROVAL_OPTIONS,
                value=selected_approval,
                label='Approval',
            ))
            approval_select.on(
                'update:model-value',
                lambda _e: nav(approval=None if approval_select.value == 'All' else approval_select.value),
            )
            if user_id_int is not None:
                ui.button(
                    'Clear person', icon='close',
                    on_click=lambda: nav(user_id=None),
                ).props('flat dense no-caps color=primary')
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e))

        bounds_start, bounds_end = display_bounds(start_d, end_d)
        data = await ReportsService().crew_coverage(
            bounds_start, bounds_end,
            tournament_id=tournament_id,
            user_id=user_id_int, actor=viewer,
        )

        coverage_rows = data['coverage_rows']
        if selected_approval == 'Approved only':
            coverage_rows = [
                r for r in coverage_rows
                if r['commentators_approved'] > 0 or r['trackers_approved'] > 0
            ]
        elif selected_approval == 'Pending only':
            coverage_rows = [
                r for r in coverage_rows
                if r['commentators_total'] > r['commentators_approved']
                or r['trackers_total'] > r['trackers_approved']
            ]

        cov_rows_display = [
            {
                'match_id': r['match_id'],
                'tournament_id': r['tournament_id'],
                'tournament': r['tournament_name'],
                'scheduled_at': format_local_display(r['scheduled_at']),
                'stage': r['stage'],
                'stream_candidate': 'yes' if r['is_stream_candidate'] else '',
                # The requirement is per-tournament, so "0/0" only means a
                # gap when the tournament actually asks for the role.
                'commentators': (
                    f"{r['commentators_approved']}/{r['commentators_total']}"
                    f" · need {r['commentators_required']}"
                ),
                'trackers': (
                    f"{r['trackers_approved']}/{r['trackers_total']}"
                    f" · need {r['trackers_required']}"
                ),
                'gap': 'GAP' if r['coverage_gap'] else '',
            }
            for r in coverage_rows
        ]
        cov_columns = [
            {'name': 'match_id', 'label': 'Match', 'field': 'match_id', 'sortable': True},
            {'name': 'tournament', 'label': 'Tournament', 'field': 'tournament', 'sortable': True},
            {'name': 'scheduled_at', 'label': 'Scheduled', 'field': 'scheduled_at', 'sortable': True},
            {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'sortable': True},
            {'name': 'stream_candidate', 'label': 'Candidate', 'field': 'stream_candidate'},
            {'name': 'commentators', 'label': 'Comms (apprv/total)', 'field': 'commentators'},
            {'name': 'trackers', 'label': 'Trackers (apprv/total)', 'field': 'trackers'},
            {'name': 'gap', 'label': 'Coverage gap', 'field': 'gap'},
        ]
        cov_panel = await section_panel('Coverage by match', icon='headset_mic')
        with cov_panel.aside:
            csv_export_button(
                f'crew-coverage-{start_d}-to-{end_d}',
                lambda: cov_columns,
                lambda: cov_rows_display,
            )
        with cov_panel.body:
            cov_table = ui.table(
                columns=cov_columns,
                rows=cov_rows_display,
                pagination=25,
                row_key='match_id',
            ).classes('full-width')
            # The crew cell that approves a pending signup lives on the board;
            # this report is the only surface that can see the gap.
            drill_actions = enable_drill_link(
                cov_table, cov_columns, cov_rows_display,
                board_drill,
                enabled=can_open_board,
                hint='Open on the schedule board',
            )
            enable_mobile_grid(cov_table, cov_columns, actions=drill_actions,
                               table_key=TableKeys.REPORTS_CREW_COVERAGE)

        contrib_display = [
            {
                **r,
                'commentary_pct': _ratio_pct(r['commentator_approved'], r['commentator_total']),
                'tracker_pct': _ratio_pct(r['tracker_approved'], r['tracker_total']),
            }
            for r in data['contribution_rows']
        ]
        contrib_columns = [
            {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True},
            {'name': 'commentator_total', 'label': 'Commentary signups', 'field': 'commentator_total', 'sortable': True},
            {'name': 'commentator_approved', 'label': 'Commentary approved', 'field': 'commentator_approved', 'sortable': True},
            {'name': 'commentary_pct', 'label': 'Commentary %', 'field': 'commentary_pct', 'sortable': True},
            {'name': 'tracker_total', 'label': 'Tracker signups', 'field': 'tracker_total', 'sortable': True},
            {'name': 'tracker_approved', 'label': 'Tracker approved', 'field': 'tracker_approved', 'sortable': True},
            {'name': 'tracker_pct', 'label': 'Tracker %', 'field': 'tracker_pct', 'sortable': True},
            {'name': 'hours_covered', 'label': 'Hours covered', 'field': 'hours_covered', 'sortable': True},
        ]
        contrib_panel = await section_panel(
            'Contribution by person', icon='groups',
            subtitle='' if user_id_int is not None else 'Pick a person to filter both tables to them.',
        )
        with contrib_panel.aside:
            csv_export_button(
                f'crew-contribution-{start_d}-to-{end_d}',
                lambda: contrib_columns,
                lambda: contrib_display,
            )

        def _row_clicked(e):
            clicked_uid = clicked_row(e).get('user_id')
            if clicked_uid:
                nav(user_id=clicked_uid)

        with contrib_panel.body:
            contrib_table = ui.table(
                columns=contrib_columns,
                rows=contrib_display,
                pagination=25,
                row_key='user_id',
            ).classes('full-width wiz-rowclick')
            contrib_table.on('row-click', _row_clicked)
            enable_mobile_grid(contrib_table, contrib_columns, row_click_event='row-click',
                               table_key=TableKeys.REPORTS_CREW_CONTRIBUTIONS)


def _ratio_pct(num: int, denom: int) -> Optional[float]:
    if not denom:
        return None
    return round(num / denom * 100, 1)
