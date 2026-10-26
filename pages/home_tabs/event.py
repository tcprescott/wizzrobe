"""Event — the community's schedule, as the match board or the bracket browser.

These used to be sibling tabs (with a stage timeline, On Air, which the Watch
page at ``/live`` has replaced). On a phone that spent three of the four bottom-nav slots on
views of the same thing and pushed the player's own matches behind **More**, so
they are one tab now with a switcher across the top.

Which view opens is driven by the incoming section slug, so the retired
``/home/brackets`` path still lands where it always did (see the ``aliases`` on
the Event tab in :mod:`pages.home`; ``/home/on-air`` redirects to ``/live``).
"""

from typing import Any, Awaitable, Callable, NamedTuple, Optional

from nicegui import ui

from application.services import CommunityEventService, FeatureFlagService
from models import FeatureFlag, User
from pages.community_events import EVENT_TAB, community_events_view
from pages.home_tabs.brackets import brackets_tab
from pages.home_tabs.schedule import schedule as schedule_view
from theme.layout_probe import skip_layout_probe
from theme.nav_badges import nav_count_badge
from theme.steady import steady_refreshable
from theme.url_state import url_param, url_state


class EventView(NamedTuple):
    """One reading of the schedule, and what it takes to be offered."""

    slug: str
    label: str
    icon: str
    build: Callable[[], Awaitable[None]]
    # None = always offered. Otherwise the view is absent where the community
    # has that feature off, rather than the whole tab disappearing.
    flag: Optional[FeatureFlag] = None


COMMUNITY = 'community'

# Order is the reading order: the whole schedule, the tournament structure
# behind it, then what attendees are running around it.
VIEWS: tuple[EventView, ...] = (
    EventView('schedule', 'Schedule', 'schedule', schedule_view),
    EventView('brackets', 'Brackets', 'account_tree', brackets_tab, FeatureFlag.BRACKETS),
    EventView(COMMUNITY, 'Meetups', 'groups', community_events_view, FeatureFlag.COMMUNITY_EVENTS),
)


class _SlottedToggle(ui.toggle):
    """A ``ui.toggle`` whose segments can carry a named slot beside their label.

    QBtnToggle renders an option's ``slot`` inside its button. NiceGUI rebuilds
    the options on every update, so the slot names are put back each time.
    """

    def __init__(self, *args: Any, slots: Optional[dict[Any, str]] = None, **kwargs: Any) -> None:
        self._option_slots = slots or {}
        super().__init__(*args, **kwargs)

    def _update_options(self) -> None:
        super()._update_options()
        for option, value in zip(self._props['options'], self._values, strict=True):
            if value in self._option_slots:
                option['slot'] = self._option_slots[value]


async def community_new_count(user: Optional[User]) -> int:
    """Community events listed since ``user`` last opened the Community view.

    The Event tab's count. Zero, never an error, wherever the feature is off or
    the viewer can't see the listing: it is a nudge on a page about something else.
    """
    if user is None or not await FeatureFlagService().is_enabled(FeatureFlag.COMMUNITY_EVENTS):
        return 0
    try:
        return len(await CommunityEventService().unseen_ids(user))
    except (ValueError, PermissionError):
        return 0


def available_views(live: set) -> list[EventView]:
    """The views this community offers, in reading order.

    Pure, so the flag-gating is testable without building a page.
    """
    return [v for v in VIEWS if v.flag is None or v.flag in live]


def resolve_view(views: list[EventView], slug: Optional[str]) -> EventView:
    """The view ``slug`` names, else the first one on offer.

    Falls back rather than raising: ``slug`` arrives from the URL, and a stale
    bookmark to a view the community has since turned off should open the tab,
    not an error.
    """
    return next((v for v in views if v.slug == slug), views[0])


async def event_tab(view: Optional[str] = None, community_new: int = 0) -> None:
    live = await FeatureFlagService().enabled_flags()
    views = available_views(live)
    # The address names the view (``?view=``, or the retired ``/home/<view>``
    # section a DM still carries).
    state = {'slug': resolve_view(views, url_state().requested('view') or view).slug}

    @steady_refreshable
    async def body() -> None:
        await resolve_view(views, state['slug']).build()

    async def switch(value: str) -> None:
        if value == state['slug']:
            return
        state['slug'] = value
        if view_param is None:
            await body.refresh()
            return
        async with view_param.changing(value):
            await body.refresh()

    async def restore(value: str) -> None:
        state['slug'] = resolve_view(views, value).slug
        if toggle is not None:
            toggle.set_value(state['slug'])
        await body.refresh()

    skip_layout_probe(body)

    toggle: Optional[ui.toggle] = None
    view_param = None
    with ui.column().classes('w-full'):
        # A switcher over one option is just a label taking up room.
        if len(views) > 1:
            # `page-container`, matching every view below it, so the switcher
            # sits on the same left edge as the content it switches between.
            with ui.row().classes('page-container wiz-view-switch items-center'):
                # No `color=`: on a q-btn-toggle that prop is the *inactive*
                # background, so `color=primary` painted all three segments gold
                # and the selected one was indistinguishable from the other two.
                # `toggle-color` (primary by default) is the one that marks the
                # selection; the rest of the look is .wiz-segmented in styles.css.
                # The Event tab's count also rides the segment it leads to, and
                # clears with it when that view opens.
                badged = community_new > 0 and any(v.slug == COMMUNITY for v in views)
                toggle = _SlottedToggle(
                    {v.slug: v.label for v in views},
                    value=state['slug'],
                    on_change=lambda e: switch(e.value),
                    slots={COMMUNITY: COMMUNITY} if badged else None,
                ).props('no-caps dense unelevated').classes('wiz-segmented')
                if badged:
                    with toggle.add_slot(COMMUNITY):
                        nav_count_badge(EVENT_TAB, community_new).classes('q-ml-xs')
                labels = {v.slug: v.label for v in views}
                view_param = url_param(
                    'view', toggle, default=views[0].slug, value=state['slug'],
                    label=lambda v: labels.get(v) if v != views[0].slug else None,
                    on_restore=restore,
                )
        await body()
