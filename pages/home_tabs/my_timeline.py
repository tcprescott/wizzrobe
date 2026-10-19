"""My Schedule's Timeline view: one day of the viewer's commitments on a clock.

Matches they play, matches they crew, their volunteer shifts and the community
events they host, RSVP'd to or asked a reminder for share one
column on an hour scale, the volunteer grid's scale, so a clash between a
shift and a match is two boxes side by side rather than two lines on two
different panels. A day at a time, chosen from chips for the days that have
anything on. Tapping a block opens it: a match or crew slot in the List view,
narrowed to that match; a shift in its details dialog; a community event its
own page.
"""

from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Optional

from nicegui import app, ui

from application.services import MyTimelineService, get_user_from_session
from application.services.my_timeline_service import (
    COMMUNITY,
    CREW,
    GOING,
    HOSTING,
    MATCH,
    REMINDER,
    SHIFT,
    UNAVAILABLE,
    TimelineItem,
    event_day,
    opening_day,
    place_in_lanes,
)
from application.utils.app_links import community_event_url, crew_match_url, player_match_url
from application.utils.timezone import combine_local, format_local_time
from pages.home_tabs.my_volunteer_shifts import open_shift_details
from theme.empty_state import empty_panel
from theme.section import section_panel
from theme.steady import steady_refreshable
from theme.timer_teardown import PageTimer
from theme.volunteer_grid import (
    HOUR_PX,
    day_label,
    render_column_markers,
    render_day_bar,
    render_hour_column,
    timeline_region,
)

_KINDS = {
    MATCH: ('sports_esports', 'Your matches'),
    CREW: ('headset_mic', 'Crew'),
    SHIFT: ('volunteer_activism', 'Volunteer shifts'),
    COMMUNITY: ('groups', 'Community events'),
    UNAVAILABLE: ('block', "Can't play"),
}

_ATTENDING = {HOSTING: 'Hosting', GOING: 'Going', REMINDER: 'Reminder'}

OnBlocked = Optional[Callable[[], Awaitable[None]]]


def _px(hours: float) -> str:
    return f'{hours * HOUR_PX:.1f}px'


def hour_range(items: list[TimelineItem], midnight: datetime) -> tuple[int, int]:
    """The hours the day's grid shows: an hour either side of what is on."""
    first = min((i.starts_at - midnight).total_seconds() / 3600 for i in items)
    last = max((i.ends_at - midnight).total_seconds() / 3600 for i in items)
    return max(0, int(first) - 1), min(30, -int(-last // 1) + 1)


def _open(item: TimelineItem) -> None:
    if item.kind == SHIFT and item.assignment is not None:
        open_shift_details(item.assignment)
    elif item.kind == COMMUNITY:
        ui.navigate.to(community_event_url(item.event_id))
    elif item.kind == CREW:
        ui.navigate.to(crew_match_url(item.match_id))
    else:
        ui.navigate.to(player_match_url(item.match_id))


def _legend(kinds: set, now_shown: bool) -> None:
    with ui.row().classes('wiz-vgrid-legend items-center'):
        for kind, (_, words) in _KINDS.items():
            if kind in kinds:
                with ui.row().classes('items-center gap-1 no-wrap'):
                    hatch = ' wiz-tl-hatch' if kind == UNAVAILABLE else ''
                    ui.element('span').classes(f'wiz-tl-swatch wiz-tl--{kind}{hatch}')
                    ui.label(words)
        if now_shown:
            with ui.row().classes('items-center gap-1 no-wrap'):
                ui.element('span').classes('wiz-vgrid-legend__now')
                ui.label('Now')


def _render_day(
    items: list[TimelineItem], blocked: list[TimelineItem], day: date, on_blocked: OnBlocked,
) -> None:
    midnight = combine_local(day, datetime.min.time()).astimezone(timezone.utc)
    first, last = hour_range(items + blocked, midnight)
    now = datetime.now(timezone.utc)
    now_hour = (now - midnight).total_seconds() / 3600
    now_shown = first <= now_hour < last
    _legend({i.kind for i in items + blocked}, now_shown)
    height = _px(last - first)
    with timeline_region(midnight, first, f'Your schedule for {day_label(day)}').classes('wiz-tl'):
        render_hour_column(midnight, first, last, now_hour if now_shown else None)
        with ui.element('div').classes('wiz-vgrid__col'):
            ui.label(f'{day:%A, %B} {day.day}').classes('wiz-vgrid__head')
            with ui.element('div').classes('wiz-vgrid__body wiz-vgrid__body--lines') \
                    .style(f'height: {height};'):
                for off in blocked:
                    _render_off(off, midnight, first, on_blocked)
                for placed in place_in_lanes(items):
                    _render_block(placed.item, placed.lane, placed.lanes, midnight, first)
                render_column_markers(first, now_hour if now_shown else None)


def _render_off(item: TimelineItem, midnight: datetime, first: int, on_blocked: OnBlocked) -> None:
    """A "can't play" window: a hatched band behind the blocks, so a match
    booked into it still shows, on top, as the clash it is."""
    top = (item.starts_at - midnight).total_seconds() / 3600 - first
    span = (item.ends_at - item.starts_at).total_seconds() / 3600
    when = f'{format_local_time(item.starts_at)} – {format_local_time(item.ends_at)}'
    words = ' · '.join(part for part in (item.title, when, item.detail) if part)
    band = ui.element('div').classes('wiz-tl-off wiz-tl-hatch').style(
        f'top: {_px(top)}; height: {_px(span)};'
    )
    if on_blocked is not None:
        band.props(f'tabindex=0 role=button aria-label="{words}. Edit when you can\'t play"')
        band.on('click', on_blocked)
        band.on('keydown.enter', on_blocked)
    else:
        band.props(f'role=note aria-label="{words}"')
    band.tooltip(words)
    with band, ui.row().classes('wiz-tl-off__label items-center no-wrap'):
        ui.icon('block', size='14px')
        ui.label(words)


def _render_block(item: TimelineItem, lane: int, lanes: int, midnight: datetime, first: int) -> None:
    icon, _ = _KINDS[item.kind]
    top = (item.starts_at - midnight).total_seconds() / 3600 - first
    span = (item.ends_at - item.starts_at).total_seconds() / 3600
    when = f'{format_local_time(item.starts_at)} – {format_local_time(item.ends_at)}'
    # A reminder is interest, not a commitment: dashed, so a clash with it reads as a maybe.
    tentative = ' wiz-tl-block--maybe' if item.attending == REMINDER else ''
    block = ui.element('div').classes(f'wiz-tl-block wiz-tl--{item.kind}{tentative}').style(
        f'top: calc({_px(top)} + 3px); height: calc({_px(span)} - 6px); '
        f'left: calc(4px + (100% - 8px) * {lane} / {lanes}); '
        f'width: calc((100% - 8px) / {lanes} - {4 if lanes > 1 else 0}px);'
    )
    tag = f', {_ATTENDING[item.attending]}' if item.attending else ''
    block.props(f'tabindex=0 role=button aria-label="{item.title}, {when}{tag}"')
    block.on('click', lambda i=item: _open(i))
    block.on('keydown.enter', lambda i=item: _open(i))
    with block:
        if item.attending:
            with ui.row().classes('items-center no-wrap gap-1'):
                ui.label(when).classes('wiz-tl-block__time')
                ui.label(_ATTENDING[item.attending]).classes('wiz-tl-block__tag')
        else:
            ui.label(when).classes('wiz-tl-block__time')
        with ui.row().classes('wiz-tl-block__title items-center no-wrap'):
            ui.icon(icon, size='14px')
            ui.label(item.title)
        if item.detail:
            ui.label(item.detail).classes('wiz-tl-block__detail')


async def my_timeline_view(
    *, include_shifts: bool, include_community: bool = False, on_blocked: OnBlocked = None,
) -> None:
    """``on_blocked`` runs when a "can't play" band is tapped: the editor
    for those windows lives in the List view, so the caller switches to it."""
    user = await get_user_from_session(app.storage.user)
    if user is None:
        return
    service = MyTimelineService()
    state: dict = {'day': None}

    panel = await section_panel(
        'Your timeline',
        icon='view_timeline',
        subtitle=(
            'Your matches, crew slots, volunteer shifts and community events, hour by hour.'
            if include_community else
            'Your matches, crew slots and volunteer shifts, hour by hour.'
        ),
        help_topics=[('my-schedule-timeline', 'The timeline')],
        user=user,
    )

    @steady_refreshable
    async def view() -> None:
        try:
            items = await service.items(
                user, include_shifts=include_shifts, include_community=include_community,
            )
            blocked = await service.unavailable(user)
        except ValueError as e:
            ui.label(str(e)).classes('text-muted')
            return
        by_day: dict[date, list[TimelineItem]] = {}
        for item in items:
            by_day.setdefault(event_day(item.starts_at), []).append(item)
        blocked_by_day: dict[date, list[TimelineItem]] = {}
        for item in blocked:
            blocked_by_day.setdefault(event_day(item.starts_at), []).append(item)
        days = sorted(set(by_day) | set(blocked_by_day))
        if not days:
            empty_panel('Nothing on your schedule yet.', icon='event_available')
            return
        day = state['day'] if state['day'] in days else opening_day(
            days, event_day(datetime.now(timezone.utc)),
        )
        if day is None:
            return
        state['day'] = day
        render_day_bar(days, day, pick_day)
        _render_day(by_day.get(day, []), blocked_by_day.get(day, []), day, on_blocked)

    def pick_day(d: date) -> None:
        state['day'] = d
        view.refresh()

    with panel.body:
        await view()
    PageTimer(300, view.refresh)
