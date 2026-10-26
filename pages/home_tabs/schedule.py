

from nicegui import app, ui

from application.services import CrewHoldService, EventService, MatchService
from application.utils.tenant_session import tenant_session_get, tenant_session_set
from theme.dialog.match_sheet import MatchSheet
from theme.help import help_icon
from theme.tables.match import MatchTableView
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_player_run_sheet import PLAYER_RUN_SHEET_COLUMNS, decorate_player_row
from theme.tables.match_run_sheet import crew_columns
from theme.tables.preferences import TableKeys

#: Session key for the Needs crew switch, namespaced like the board's filters.
NEEDS_CREW_KEY = 'home_schedule:needs_crew'

_CREW_ROLES = ('commentators', 'trackers', 'broadcasters')


def needs_crew(row: dict) -> bool:
    """Whether a volunteer could usefully sign up on this row.

    Signup still open, and some role the tournament uses has less approved crew
    than it asks for. On an event day the board runs to thirty-plus cards on a
    phone and most of them are already covered or under way; this is the list
    someone who came to commentate is actually looking for.
    """
    if not row.get('crew_signup_open'):
        return False
    wanted = row.get('crew_wanted') or {}
    required = row.get('crew_required') or {}
    for role in _CREW_ROLES:
        # A row from before broadcasters says nothing about them, and most
        # events don't have them, so only the first two default to wanted.
        if not wanted.get(role, role != 'broadcasters'):
            continue
        approved = sum(1 for c in row.get(role) or [] if isinstance(c, dict) and c.get('approved'))
        if approved < required.get(role, 1):
            return True
    return False


async def schedule():
    match_service = MatchService()

    with ui.column().classes('page-container wiz-board-page'):
        # No intro line and no title: the view switcher already names the view,
        # the Sign up buttons on each line say what the board is for, and the
        # help topic says the rest. The Needs crew switch and the help button
        # ride the board's own filter strip instead of rows of their own.
        needs_crew_on = bool(tenant_session_get(NEEDS_CREW_KEY, False))

        async def get_query():
            return await match_service.get_all_matches_for_schedule()

        # The details sheet holds what the line leaves out (seed, stations,
        # who's confirmed). A spectator's access, so it offers no staff control.
        discord_id = app.storage.user.get('discord_id')
        sheet = MatchSheet(None, access=MatchBoardAccess(),
                           decorate=lambda row: decorate_player_row(row, discord_id))

        view = MatchTableView(
            row_filter=needs_crew if needs_crew_on else None,
            columns=crew_columns(PLAYER_RUN_SHEET_COLUMNS, await EventService.current_event_broadcasters()),
            get_query=get_query,
            admin_controls=False,
            table_key=TableKeys.HOME_SCHEDULE,
            storage_key='home_schedule',
            on_rows_changed=sheet.sync,
            layout='player_run_sheet',
            on_open_match=sheet.open,
            extra_rows=CrewHoldService().dress_schedule_rows,
        )
        sheet.view = view

        # One switch per strip (phone row, desktop card); only one is ever
        # visible, and flipping either moves the other with it.
        switches: list = []

        async def on_needs_crew(event) -> None:
            on = bool(event.value)
            if view.row_filter is (needs_crew if on else None):
                return
            tenant_session_set(NEEDS_CREW_KEY, on)
            view.row_filter = needs_crew if on else None
            for other in switches:
                if other is not event.sender:
                    other.set_value(on)
            await view.refresh()

        for lead, end in zip(view.toolbar_lead, view.toolbar_end, strict=True):
            with lead:
                switches.append(
                    ui.switch('Needs crew', value=needs_crew_on, on_change=on_needs_crew)
                    .props('dense').classes('wiz-needs-crew')
                    .tooltip('Only matches still short of commentators or trackers')
                )
            with end:
                await help_icon(('schedule-columns', 'Reading the board'), 'match-states')

        # The view loads itself once its filters are restored (MatchTableView
        # ._initial_load) — kicking a refresh here as well raced that restore and
        # doubled the query fan-out.
