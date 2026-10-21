"""My Schedule — everything the viewer personally committed to, on one scroll.

Your matches, the crew slots you signed up for, your volunteer shifts, when you
said you can play. (Equipment you're holding lives on Profile.) Most of these were
separate tabs, which meant the answer to "what am I doing at this event?" was spread across four taps and,
on a phone, three of them lived behind **More**.

Sections render in commitment order: what you are scheduled to play, then what
you volunteered for, then what you told staff about your time. Every section stays open. A section whose feature the community has
turned off is not built at all.

Each is a panel (``theme/section.py``) rather than a heading over a hairline
separator. Stacked flat, the four ran together into one document while each
section's own header/separator margin left its title floating four ems above its
content — the tab was long *and* looked empty. The panel puts an edge round each
block and its controls in its own header.
"""

from typing import Optional

from nicegui import app, ui

from application.services import FeatureFlagService, TournamentService, get_user_from_session
from models import FeatureFlag
from pages.home_tabs.availability import AVAILABILITY_SECTION_ID, availability_tab
from pages.home_tabs.my_crew import my_crew_tab
from pages.home_tabs.my_timeline import my_timeline_view
from pages.home_tabs.my_volunteer_shifts import my_volunteer_shifts_section
from pages.home_tabs.player import render_player_dashboard
from theme.layout_probe import skip_layout_probe
from theme.steady import steady_refreshable
from theme.url_state import url_param, url_state

#: Where the viewer's List / Timeline choice is remembered.
VIEW_KEY = 'my_schedule_view'

VIEWS = {'list': 'List', 'timeline': 'Timeline'}


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
    # Someone entered in no tournament is here for their crew slots, and the
    # matches panel above them was an empty board with six help chips: on a
    # phone it pushed the crew list, and its Confirm button, below the fold.
    # Their crew comes first; the empty panel still renders after it, because
    # it is where a forwarded player link explains itself.
    user = await get_user_from_session(app.storage.user)
    plays = bool(user and await TournamentService().get_enrolled_players_by_user(user))
    volunteer = user if FeatureFlag.VOLUNTEERS in live else None

    deep_link = shifts or any(
        v is not None for v in (schedule, reschedule, match, hard, agree, book, startgg, comments, release)
    )
    # A DM's link names something to open in the List view, so it wins over a
    # remembered Timeline for this visit without changing the remembered choice.
    # An address that names the view (a reload, Back, a shared link) wins over both.
    asked = url_state().requested('view')
    if asked not in VIEWS:
        asked = 'list' if deep_link else app.storage.user.get(VIEW_KEY, 'list')
    state: dict = {'view': asked if asked in VIEWS else 'list'}

    async def list_view() -> None:
        if not plays:
            if volunteer:
                await my_volunteer_shifts_section(volunteer, release=release, focus=shifts)
            await my_crew_tab(focus_match=match)
        await render_player_dashboard(
            schedule=schedule, reschedule=reschedule, match=match, hard=hard,
            agree=agree, book=book, startgg=startgg, comments=comments,
        )
        if plays:
            await my_crew_tab(focus_match=match)
            if volunteer:
                await my_volunteer_shifts_section(volunteer, release=release, focus=shifts)
        await availability_tab()

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
        await body.refresh()
        view_param.set(value)
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
