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

from nicegui import app, ui

from application.services import FeatureFlagService, TournamentService, get_user_from_session
from application.utils.app_links import HOME_SCHEDULE, HOME_TOURNAMENTS, home_url
from models import FeatureFlag
from pages.home_tabs.availability import AVAILABILITY_SECTION_ID, availability_tab
from pages.home_tabs.my_crew import my_crew_tab
from pages.home_tabs.my_timeline import my_timeline_view
from pages.home_tabs.my_volunteer_shifts import my_volunteer_shifts_section
from pages.home_tabs.player import count_waiting_on_you, render_player_dashboard
from theme.empty_state import cat_fact
from theme.layout_probe import skip_layout_probe
from theme.steady import steady_refreshable
from theme.url_state import url_param, url_state

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
            ui.button('See the event schedule', on_click=lambda: ui.navigate.to(home_url(HOME_SCHEDULE))) \
                .props('outline no-caps color=primary')
            ui.button('Browse tournaments', icon='emoji_events',
                      on_click=lambda: ui.navigate.to(home_url(HOME_TOURNAMENTS))) \
                .props('unelevated no-caps color=primary')
        cat_fact()


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
    # Only what applies to this person is drawn. Someone in no tournament has no
    # matches panel and no "can't play" editor; crew and shift panels draw
    # themselves only when there is something in them.
    plays = bool(user and (
        await TournamentService().get_enrolled_players_by_user(user)
        or await count_waiting_on_you(user)
    ))
    volunteer = user if FeatureFlag.VOLUNTEERS in live else None
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
        return await my_volunteer_shifts_section(volunteer, release=release, focus=shifts)

    async def list_view() -> None:
        drew = False
        if plays or player_link:
            await render_player_dashboard(
                schedule=schedule, reschedule=reschedule, match=match, hard=hard,
                agree=agree, book=book, startgg=startgg, comments=comments,
            )
            drew = True
        if plays:
            drew = await my_crew_tab(focus_match=match) or drew
            drew = await shifts_section() or drew
            await availability_tab()
        else:
            # Not playing: whatever they volunteered for is the schedule.
            drew = await shifts_section() or drew
            drew = await my_crew_tab(focus_match=match) or drew
        if not drew:
            nothing_yet()

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

    skip_layout_probe(body)

    with ui.column().classes('page-container wiz-section-stack'):
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
