

from nicegui import app, ui

from application.services import CrewHoldService, EventService, FriendService, MatchService, get_user_from_discord_id
from application.utils.tenant_session import tenant_session_get, tenant_session_set
from theme.dialog.match_sheet import MatchSheet
from theme.help import help_icon
from theme.tables.match import MatchTableView
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_player_run_sheet import PLAYER_RUN_SHEET_COLUMNS, decorate_player_row, has_friend
from theme.tables.preferences import TableKeys
from theme.tables.run_sheet_meta import crew_columns

NEEDS_CREW_KEY = 'home_schedule:needs_crew'
FRIENDS_KEY = 'home_schedule:friends'

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
        # Rows saved before broadcasters existed omit them: default those to unwanted.
        if not wanted.get(role, role != 'broadcasters'):
            continue
        approved = sum(1 for c in row.get(role) or [] if isinstance(c, dict) and c.get('approved'))
        if approved < required.get(role, 1):
            return True
    return False


async def schedule():
    match_service = MatchService()
    friend_service = FriendService()

    with ui.column().classes('page-container wiz-board-page'):
        discord_id = app.storage.user.get('discord_id')
        viewer = await get_user_from_discord_id(discord_id)
        friend_ids = await friend_service.friend_ids(viewer)
        friend_options = await friend_service.picker_options(viewer) if viewer else {}
        on = {
            'crew': bool(tenant_session_get(NEEDS_CREW_KEY, False)),
            'friends': bool(tenant_session_get(FRIENDS_KEY, False)) and bool(friend_ids),
        }

        def row_filter():
            checks = []
            if on['crew']:
                checks.append(needs_crew)
            if on['friends']:
                checks.append(lambda row: has_friend(row, view.friend_ids))
            if not checks:
                return None
            return lambda row: all(check(row) for check in checks)

        async def get_query():
            return await match_service.get_all_matches_for_schedule()

        sheet = MatchSheet(None, access=MatchBoardAccess(),
                           decorate=lambda row: decorate_player_row(row, discord_id))

        async def on_friends_change(event) -> None:
            if viewer is None:
                return
            try:
                saved = await friend_service.set_friends(viewer, event.value or [])
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            view.friend_ids = frozenset(saved)
            if not saved and on['friends']:
                for switch in switches['friends']:
                    switch.set_value(False)
                on['friends'] = False
                tenant_session_set(FRIENDS_KEY, False)
                view.row_filter = row_filter()
            await view.refresh()

        def build_friends_field() -> None:
            if viewer is None:
                return
            ui.select(
                options=friend_options,
                label='Friends',
                value=[uid for uid in friend_options if uid in friend_ids],
                multiple=True,
                with_input=True,
                on_change=on_friends_change,
            ).classes('match-filter-field match-filter-field--wide').props('outlined dense options-dense')

        view = MatchTableView(
            row_filter=row_filter(),
            columns=crew_columns(PLAYER_RUN_SHEET_COLUMNS, await EventService.current_event_broadcasters()),
            get_query=get_query,
            admin_controls=False,
            table_key=TableKeys.HOME_SCHEDULE,
            storage_key='home_schedule',
            on_rows_changed=sheet.sync,
            layout='player_run_sheet',
            on_open_match=sheet.open,
            extra_rows=CrewHoldService().dress_schedule_rows,
            extra_filters=build_friends_field,
        )
        view.friend_ids = frozenset(friend_ids)
        sheet.view = view

        # One switch of each per strip (phone row, desktop card); flipping either moves the other.
        switches: dict[str, list] = {'crew': [], 'friends': []}

        async def flip(key: str, value: bool, sender, session_key: str) -> None:
            if on[key] == value:
                return
            on[key] = value
            tenant_session_set(session_key, value)
            view.row_filter = row_filter()
            for other in switches[key]:
                if other is not sender:
                    other.set_value(value)
            await view.refresh()

        async def on_needs_crew(event) -> None:
            await flip('crew', bool(event.value), event.sender, NEEDS_CREW_KEY)

        async def on_friends(event) -> None:
            if event.value and not view.friend_ids:
                event.sender.set_value(False)
                view.open_filters()
                ui.notify('Pick your friends in the Friends field first.', color='info')
                return
            await flip('friends', bool(event.value), event.sender, FRIENDS_KEY)

        for lead, end in zip(view.toolbar_lead, view.toolbar_end, strict=True):
            with lead:
                switches['crew'].append(
                    ui.switch('Needs crew', value=on['crew'], on_change=on_needs_crew)
                    .props('dense').classes('wiz-needs-crew')
                    .tooltip('Only matches still short of commentators or trackers')
                )
                if viewer is not None:
                    switches['friends'].append(
                        ui.switch('Friends', value=on['friends'], on_change=on_friends)
                        .props('dense').classes('wiz-friends-switch')
                        .tooltip("Only matches your friends play in. Pick them in the Friends field.")
                    )
            with end:
                await help_icon(('schedule-columns', 'Reading the board'), 'match-states')

        # No refresh here: the view loads itself after its filters are restored, and a second load races that.
