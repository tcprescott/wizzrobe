"""Proctor Station tab — the on-site match board.

One proctor runs a room, so this is a *room* board: every on-site match, grouped
by what needs a proctor next. It is the admin Schedule's run sheet with the
proctor's line: stations beside the names and the next lifecycle step as a
button, so working the room is one press per match. Tapping a line opens the
same match sheet as the admin board, minus crew and editing. Deliberately not
the admin Schedule tab itself: that one is for scheduling matches, this one is
for running them.
"""

from typing import Optional

from nicegui import ui

from application.services import AuthService
from application.tenant_context import require_tenant_id
from models import Match, User
from theme.dialog.match_sheet import MatchSheet
from theme.help import help_icon
from theme.page_header import page_header
from theme.tables.match import MatchTableView
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_lifecycle import MatchLifecycleHandlers
from theme.tables.match_run_sheet import PROCTOR_RUN_SHEET_COLUMNS, proctor_group
from theme.tables.preferences import TableKeys


def _on_stage(row: dict) -> bool:
    return row.get('stage_id') is not None


async def proctor_station_tab(user: Optional[User] = None) -> None:
    on_stage_only = await AuthService.proctor_station_on_stage_only(user)
    with ui.column().classes('page-container-wide') as page_container:
        with page_header(
            'Proctor Station',
            'Every match on a stage, grouped by what it needs from you next.' if on_stage_only
            else 'Every match in this room, grouped by what it needs from you next.',
        ):
            # The last two read from the event handbook and are role-gated, so
            # the viewer is passed in rather than looked up per topic.
            await help_icon(
                ('proctor-station', 'The station board'),
                'proctor-run-match',
                ('proctor-escalation', 'When things go wrong'),
                ('proctor-handover', 'Shift change'),
                user=user,
            )
            ui.space()

        def get_query():
            query = Match.filter(tenant_id=require_tenant_id())
            return query.filter(stage_id__isnull=False) if on_stage_only else query

        # The proctor runs matches and nothing else: no edit dialog, no confirm,
        # no stage, no crew approval — exactly what ``can_run_match`` admits them
        # to and every other gate refuses. A Broadcast Tech's rows also follow
        # the stage, so one taken off stage mid-refresh loses its buttons.
        access = (
            MatchBoardAccess(run=True, run_scope=frozenset(), run_on_stage=True)
            if on_stage_only else MatchBoardAccess(run=True)
        )
        handlers = MatchLifecycleHandlers(page_container, access=access)
        sheet = MatchSheet(handlers, access=access, show_crew=False)
        table_view = MatchTableView(
            columns=PROCTOR_RUN_SHEET_COLUMNS,
            get_query=get_query,
            # The board's rows come from the display service, not get_query, so
            # the tech's stage-only scope has to ride on the row filter too.
            row_filter=_on_stage if on_stage_only else None,
            admin_controls=True,
            access=access,
            storage_key='proctor',
            table_key=TableKeys.PROCTOR_STATION,
            show_accommodations=True,
            show_comment_counts=True,
            searchable=True,
            exclude_racetime=True,
            on_rows_changed=sheet.sync,
            layout='run_sheet',
            on_open_match=sheet.open,
            run_sheet_group=proctor_group,
            proctor_run_sheet=True,
            **handlers.callbacks(),
        )
        handlers.table_view = table_view
        sheet.view = table_view

        # Through the view's _bg so the tab-switch refresh keeps the tenant the
        # request was resolved against (the handler runs in a detached task).
        def on_tab_selected():
            table_view._bg(table_view.refresh())
        ui.on('selected_tab', lambda e: on_tab_selected() if e.args == 'Proctor Station' else None)
