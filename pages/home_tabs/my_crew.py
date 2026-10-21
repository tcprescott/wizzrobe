"""My Crew — a volunteer's own commentator and tracker commitments.

The crew loop had a surface for the admin (the Schedule board's cells) and none
for the person doing the work. After signing up, their only handle on the
commitment was a Withdraw button in one row of a shared, chronologically-sorted
board — 17 rows and ~5,900 px of scroll on a phone — and their only reminder was
the approval DM, if approval ever happened. The Player tab ("Your Schedule") is
players-only and mentions crew nowhere.

It's the player run sheet Event > Schedule uses, narrowed to the matches the
viewer crews, so their Withdraw and Confirm sit in their own role's tray exactly
where that board puts them. Lives on Home rather than the Volunteer hub because
crew signup is neither role-gated nor behind ``FeatureFlag.VOLUNTEERS`` —
anyone in the community can sign up from the Schedule board, so anyone must be
able to see what they signed up for.
"""

from typing import Optional

from nicegui import app, ui

from application.services import CrewService, get_user_from_session
from application.utils.app_links import HOME_MY_CREW, home_url
from theme.dialog.match_sheet import MatchSheet
from theme.section import section_panel
from theme.tables.match import MatchTableView
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_player_run_sheet import PLAYER_RUN_SHEET_COLUMNS, decorate_player_row
from theme.tables.preferences import TableKeys

#: The section's DOM id, so a crew DM's link can scroll to it.
CREW_SECTION_ID = 'my-crew'


async def my_crew_tab(focus_match: Optional[int] = None) -> None:
    """``focus_match`` comes from a crew DM's button (``/home/my-crew?match=``):
    the board narrows to that match, says so, and the page scrolls to it. A
    match the viewer does not crew leaves the board whole and is left to the
    player section's notice.
    """
    discord_id = app.storage.user.get('discord_id')
    user = await get_user_from_session(app.storage.user)
    if user is None:
        ui.label('You must be logged in to see your crew commitments.').classes('text-error')
        return

    commitments = await CrewService().list_my_commitments(user, upcoming_only=False)
    match_ids = None
    if focus_match is not None and any(r['match_id'] == int(focus_match) for r in commitments):
        match_ids = [int(focus_match)]

    panel = await section_panel(
        'Crew you signed up for',
        icon='headset_mic',
        subtitle='Commentary and tracking slots you volunteered for.',
        help_topics=[
            ('crew-status', 'Your crew slots'),
            'crew-approval',
            'crew-withdraw',
        ],
        user=user,
    )
    panel.body.props(f'id={CREW_SECTION_ID}')
    with panel.body:
        if not commitments:
            ui.label(
                'Nothing yet. Sign up to commentate or track a match from the '
                'Schedule tab.'
            ).classes('text-muted')
            return

        if match_ids:
            with ui.row().classes('items-center gap-2 q-mb-sm'):
                with ui.element('span').classes('wiz-chip wiz-chip--pending'):
                    ui.icon('filter_alt', size='14px')
                    ui.label('Showing one match only')
                ui.button(
                    'Show all your crew slots', icon='clear',
                    on_click=lambda: ui.navigate.to(home_url(HOME_MY_CREW)),
                ).props('flat dense color=primary')

        sheet = MatchSheet(None, access=MatchBoardAccess(), url_key='crew_sheet',
                           decorate=lambda row: decorate_player_row(row, discord_id))
        view = MatchTableView(
            columns=PLAYER_RUN_SHEET_COLUMNS,
            get_query=None,
            admin_controls=False,
            table_key=TableKeys.HOME_MY_CREW,
            storage_key='my_crew',
            crew_discord_id=discord_id,
            match_ids=match_ids,
            on_rows_changed=sheet.sync,
            layout='player_run_sheet',
            on_open_match=sheet.open,
        )
        sheet.view = view

    if match_ids:
        ui.run_javascript(
            f"setTimeout(() => document.getElementById('{CREW_SECTION_ID}')"
            "?.scrollIntoView({behavior: 'smooth', block: 'start'}), 400)"
        )
