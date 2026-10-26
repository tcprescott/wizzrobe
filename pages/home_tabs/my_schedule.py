"""My Schedule — everything the viewer personally committed to, on one scroll.

Your matches, the crew slots you signed up for, your volunteer shifts, when you
said you can play. (Equipment you're holding lives on Profile.) Most of these were
separate tabs, which meant the answer to "what am I doing at this event?" was spread across four taps and,
on a phone, three of them lived behind **More**.

Sections render in commitment order: what you are scheduled to play, then what
you volunteered for, then what you told staff about your time. Only what applies
to the viewer is drawn: no matches or "can't play" panel for someone in no
tournament, no crew or shifts panel while either is empty, and one "nothing yet"
card when none of it applies. A section whose feature the community has turned
off is not built at all.

Each is a panel (``theme/section.py``) rather than a heading over a hairline
separator. Stacked flat, the four ran together into one document while each
section's own header/separator margin left its title floating four ems above its
content — the tab was long *and* looked empty. The panel puts an edge round each
block and its controls in its own header.
"""

from typing import Optional

from nicegui import app, context, ui

from application.services import (
    CrewService,
    FeatureFlagService,
    TournamentService,
    VolunteerScheduleService,
    get_user_from_session,
)
from application.utils.app_links import HOME_SCHEDULE, HOME_TOURNAMENTS, home_url
from models import FeatureFlag
from pages.home_tabs.availability import AVAILABILITY_SECTION_ID, availability_tab
from pages.home_tabs.availability_reminder import render_availability_reminder
from pages.home_tabs.crew_invites import crew_invites_section
from pages.home_tabs.my_crew import my_crew_tab
from pages.home_tabs.my_timeline import my_timeline_view
from pages.home_tabs.my_volunteer_shifts import my_volunteer_shifts_section
from pages.home_tabs.player import count_waiting_on_you, render_player_dashboard
from theme.empty_state import fun_fact
from theme.layout_probe import skip_layout_probe
from theme.links import link_button
from theme.past_events import render_past_events
from theme.steady import steady_refreshable
from theme.tables.admin_crud import capture_render_context, scoped_background
from theme.url_state import consume_deep_link, url_param, url_state

#: Where the viewer's List / Timeline choice is remembered.
VIEW_KEY = 'my_schedule_view'

VIEWS = {'list': 'List', 'timeline': 'Timeline'}


def nothing_yet() -> None:
    """The whole tab for a member who plays, crews and volunteers for nothing."""
    with ui.column().classes('wiz-empty-panel items-center'):
        ui.icon('event_available', size='md').classes('wiz-empty-panel__icon')
        ui.label('Nothing on your schedule yet. Sign up for a tournament and your '
                 'matches show up here, along with any shifts or crew slots you take on.')
        with ui.row().classes('justify-center gap-2'):
            link_button('See the event schedule', home_url(HOME_SCHEDULE), new_tab=False) \
                .props('outline no-caps color=primary')
            link_button('Browse tournaments', home_url(HOME_TOURNAMENTS), icon='emoji_events', new_tab=False) \
                .props('unelevated no-caps color=primary')
        fun_fact()


#: DM link parameters that open something once. Left in the address, a reload
#: reopens the dialog or repeats its "already done" notice.
_ONE_SHOT_LINKS = ('schedule', 'reschedule', 'hard', 'agree', 'book', 'startgg', 'comments', 'release', 'shifts')


async def my_schedule_tab(
    schedule: Optional[int] = None,
    reschedule: Optional[int] = None,
    match: Optional[int] = None,
    hard: Optional[int] = None,
    agree: Optional[int] = None,
    book: Optional[int] = None,
    startgg: Optional[int] = None,
    comments: Optional[int] = None,
    release: Optional[int] = None,
    shifts: bool = False,
) -> None:
    """The deep-link params belong to the matches section and pass straight
    through to it; see :func:`pages.home_tabs.player.render_player_dashboard` for
    what each one opens. ``match`` also reaches the crew section, which scrolls
    to the viewer's slot on that match when they crew it rather than play it. They arrive here because the DMs that carry them predate
    this tab and address ``/home/player``, which resolves onto My Schedule via
    the alias declared in :mod:`pages.home`. ``release`` and ``shifts`` belong
    to the volunteer shifts section, as the old Volunteer → My Shifts did.
    """
    live = await FeatureFlagService().enabled_flags()
    user = await get_user_from_session(app.storage.user)
    volunteer = user if FeatureFlag.VOLUNTEERS in live else None

    async def what_applies() -> tuple[bool, bool, bool]:
        """Whether the viewer plays, crews and has shifts: the sections drawn."""
        if user is None:
            return False, False, False
        plays = bool(
            await TournamentService().get_enrolled_players_by_user(user)
            or await count_waiting_on_you(user)
        )
        crews = bool(await CrewService().list_my_commitments(user, upcoming_only=False))
        works = bool(volunteer and await VolunteerScheduleService().assignments_for_user(volunteer))
        return plays, crews, works

    # Only what applies to this person is drawn. Someone in no tournament has no
    # matches panel and no "can't play" editor; crew and shift panels draw
    # themselves only when there is something in them.
    applies = await what_applies()
    plays = applies[0]
    # A forwarded or stale player link still lands on the matches panel, which
    # says what that match is to the reader.
    player_link = any(
        v is not None for v in (schedule, reschedule, match, hard, agree, book, startgg, comments)
    )
    deep_link = shifts or player_link or release is not None
    # A DM's link names something to open in the List view, so it wins over a
    # remembered Timeline for this visit without changing the remembered choice.
    # An address that names the view (a reload, Back, a shared link) wins over both.
    asked = url_state().requested('view')
    if asked not in VIEWS:
        asked = 'list' if deep_link else app.storage.user.get(VIEW_KEY, 'list')
    state: dict = {'view': asked if asked in VIEWS else 'list'}

    async def shifts_section() -> bool:
        if volunteer is None:
            return False
        return await my_volunteer_shifts_section(
            volunteer, release=shift_links['release'], focus=shift_links['shifts'],
        )

    # The tab is built once and kept while the viewer moves between tabs, so a
    # crew slot taken on Event > Schedule, or the last one withdrawn here, has to
    # redraw it. Deep links were for the first build; a redraw must not reopen
    # their dialogs.
    state_links: dict = {
        'schedule': schedule, 'reschedule': reschedule, 'match': match, 'hard': hard,
        'agree': agree, 'book': book, 'startgg': startgg, 'comments': comments,
    }
    shift_links: dict = {'release': release, 'shifts': shifts}
    client = context.client
    render_context = capture_render_context()

    async def recheck() -> None:
        nonlocal applies
        now = await what_applies()
        if now == applies:
            return
        applies = now
        with client:
            await body.refresh()

    def recheck_soon() -> None:
        scoped_background(render_context, recheck())

    async def list_view() -> None:
        plays, links = applies[0], dict(state_links)
        # Someone waiting on an answer from the viewer comes before everything.
        drew = await crew_invites_section(on_change=recheck_soon)
        if plays or any(v is not None for v in links.values()):
            await render_player_dashboard(**links)
            drew = True
        if plays:
            drew = await my_crew_tab(focus_match=links['match'], on_empty=recheck_soon) or drew
            drew = await shifts_section() or drew
            await availability_tab()
        else:
            # Not playing: whatever they volunteered for is the schedule.
            drew = await shifts_section() or drew
            drew = await my_crew_tab(focus_match=links['match'], on_empty=recheck_soon) or drew
        if not drew:
            nothing_yet()
        await render_past_events(user, home_url(HOME_SCHEDULE))
        state_links.update(dict.fromkeys(state_links))
        shift_links.update(release=None, shifts=False)
        # ``match`` is a filter the board still shows, so it stays.
        consume_deep_link(*_ONE_SHOT_LINKS)

    @steady_refreshable
    async def body() -> None:
        if state['view'] == 'timeline':
            await my_timeline_view(
                include_shifts=volunteer is not None,
                include_community=FeatureFlag.COMMUNITY_EVENTS in live,
                on_blocked=edit_availability,
            )
        else:
            await list_view()

    async def restore(value: str) -> None:
        value = value if value in VIEWS else 'list'
        state['view'] = value
        toggle.set_value(value)
        await body.refresh()

    async def switch(value: str) -> None:
        if value == state['view']:
            return
        state['view'] = value
        app.storage.user[VIEW_KEY] = value
        async with view_param.changing(value):
            await body.refresh()
        if target := state.pop('scroll_to', None):
            ui.run_javascript(
                f"document.getElementById('{target}')"
                "?.scrollIntoView({behavior: 'smooth', block: 'start'})"
            )

    async def edit_availability() -> None:
        state['scroll_to'] = AVAILABILITY_SECTION_ID
        toggle.value = 'list'

    async def open_availability() -> None:
        if state['view'] != 'list':
            await edit_availability()
            return
        client.run_javascript(
            f"document.getElementById('{AVAILABILITY_SECTION_ID}')"
            "?.scrollIntoView({behavior: 'smooth', block: 'start'})"
        )

    skip_layout_probe(body)

    with ui.column().classes('page-container wiz-section-stack'):
        if plays and user is not None:
            await render_availability_reminder(user, open_availability)
        toggle = ui.toggle(
            VIEWS,
            value=state['view'],
            on_change=lambda e: switch(e.value),
        ).props('dense unelevated no-caps aria-label="Show My Schedule as"') \
            .classes('wiz-segmented wiz-schedule-view')
        view_param = url_param(
            'view', toggle, default='list', value=state['view'],
            label=lambda v: VIEWS[v] if v != 'list' else None, on_restore=restore,
        )
        await body()
    ui.on('selected_tab', lambda e: recheck_soon() if e.args == 'My Schedule' else None)
