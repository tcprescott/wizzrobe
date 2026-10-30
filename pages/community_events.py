"""Community events: what members are running for each other during the event.

The listing is the Community view on Home's Event tab (``/home/community``;
``/community-events`` redirects there): the approved events still to come,
grouped by day, with a reminder toggle (or an RSVP button, when the host takes
RSVPs) on each, the viewer's own submissions underneath, and the Submit button.
``/community-events/{id}`` is one event, and is where every DM
about it lands: a subscriber's reminder, a host's approval or rejection.

Members only (``protected_page`` runs the membership gate) and behind
``COMMUNITY_EVENTS``. Presentation only: everything goes through
:class:`CommunityEventService`, and descriptions are rendered through the closed
block parser, never as markup.
"""

from itertools import groupby
from typing import Optional

from fastapi import Request
from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.content import parse_user_blocks
from application.services import (
    AuthService,
    CommunityEventService,
    SystemConfigService,
    TenantService,
    get_user_from_session,
)
from middleware.auth import protected_page
from middleware.event_routing import page_request, row_event_redirect
from models import CommunityEvent, CommunityEventStatus, FeatureFlag, User
from theme.base import BaseLayout
from theme.community_events import (
    chip,
    date_tile,
    day_label,
    disclaimer,
    is_live,
    meta_row,
    status_chip,
    time_tile,
    when_line,
)
from theme.dialog.community_event_dialog import CommunityEventDialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_state
from theme.error_page import render_not_found
from theme.help import help_icon
from theme.help.render import render_user_blocks
from theme.menu import menu_action, more_menu
from theme.nav_badges import set_nav_count
from theme.notify import notify_error
from theme.page_header import page_header
from theme.section import section_panel
from theme.steady import steady_refreshable

__all__ = ['create']

INDEX = '/community-events'
LISTING = '/home/community'
EVENT_TAB = 'Event'


async def _viewer() -> Optional[User]:
    return await get_user_from_session(app.storage.user)


async def _chrome(user: User, *parts: str) -> None:
    community = await TenantService.current_community_name() or 'Wizzrobe'
    ui.page_title(' — '.join((*parts, community)))
    await BaseLayout(user=user, show_admin=await AuthService.can_open_admin(user)).render()


def _subscribe_button(service: CommunityEventService, user: User, event: CommunityEvent,
                      subscribed: bool, on_change) -> None:
    async def toggle() -> None:
        try:
            if subscribed:
                await service.unsubscribe(user, event.id)
                ui.notify("Unsubscribed. You won't get a reminder.", color='positive')
            else:
                await service.subscribe(user, event.id)
                message = "Subscribed. We'll DM you before it starts."
                if not user.dm_notifications:
                    message += ' Turn DMs back on in your profile to get it.'
                ui.notify(message, color='positive')
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        await on_change()

    if subscribed:
        ui.button('Subscribed', icon='notifications_active', on_click=toggle) \
            .props('outline color=primary').tooltip('Stop the reminder for this event')
    else:
        ui.button('Remind me', icon='notifications_none', on_click=toggle) \
            .props('flat color=primary').tooltip('Get a Discord DM before this event starts')


def going_line(event: CommunityEvent, going: int) -> str:
    if event.rsvp_capacity is not None:
        return f'{going} of {event.rsvp_capacity} going'
    return f'{going} going'


def _rsvp_button(service: CommunityEventService, user: User, event: CommunityEvent,
                 going: bool, count: int, on_change) -> None:
    async def toggle() -> None:
        try:
            if going:
                await service.cancel_rsvp(user, event.id)  # style-guide: exempt — an RSVP toggle, re-tapped to undo
                ui.notify("RSVP cancelled. You won't get a reminder.", color='positive')
            else:
                await service.rsvp(user, event.id)
                message = "You're going. We'll DM you before it starts."
                if not user.dm_notifications:
                    message += ' Turn DMs back on in your profile to get it.'
                ui.notify(message, color='positive')
        except (ValueError, PermissionError) as e:
            notify_error(e)
            await on_change()
            return
        await on_change()

    if going:
        ui.button('Going', icon='event_available', on_click=toggle) \
            .props('outline color=primary').tooltip('Cancel your RSVP')
    elif event.rsvp_capacity is not None and count >= event.rsvp_capacity:
        ui.button('Full', icon='event_busy').props('flat disable').tooltip('Every place is taken')
    else:
        ui.button('RSVP', icon='how_to_reg', on_click=toggle) \
            .props('flat color=primary').tooltip("Say you're coming and get a DM before it starts")


def attendance_controls(event: CommunityEvent, state: dict) -> tuple[str, ...]:
    """Which attendance buttons the viewer gets: ``'rsvp'``, ``'reminder'`` or both.

    The RSVP button when the event takes RSVPs, else the reminder toggle. Someone
    who asked for a reminder before the host turned RSVPs on still holds it, so
    they keep the toggle beside the RSVP button to switch it off.
    """
    if not event.rsvp_enabled:
        return ('reminder',)
    if event.id in state['subscribed'] and event.id not in state['going']:
        return ('rsvp', 'reminder')
    return ('rsvp',)


def _attendance(service, user, event: CommunityEvent, state: dict, on_change) -> None:
    controls = attendance_controls(event, state)
    with ui.row().classes('items-center justify-end gap-1 no-wrap'):
        if 'rsvp' in controls:
            _rsvp_button(service, user, event, event.id in state['going'],
                         state['going_counts'].get(event.id, 0), on_change)
        if 'reminder' in controls:
            _subscribe_button(service, user, event, event.id in state['subscribed'], on_change)


async def _attendance_state(service: CommunityEventService, user: User, event_ids: list[int]) -> dict:
    subscribed, counts = await service.subscription_state(user, event_ids)
    going, going_counts = await service.rsvp_state(user, event_ids)
    return {'subscribed': subscribed, 'counts': counts, 'going': going, 'going_counts': going_counts}


def _count_text(event: CommunityEvent, state: dict) -> str:
    if event.rsvp_enabled:
        return going_line(event, state['going_counts'].get(event.id, 0))
    count = state['counts'].get(event.id)
    return f'{count} subscribed' if count else ''


def _title_row(event: CommunityEvent, *, new: bool = False, link: bool = True, own: bool = False) -> None:
    with ui.row().classes('items-center gap-2 min-w-0'):
        if link:
            ui.link(event.title, f'{INDEX}/{event.id}').classes('wiz-community-event__title')
        if new:
            chip('New', 'new')
        if own or event.status is not CommunityEventStatus.APPROVED:
            status_chip(event)
        elif is_live(event):
            chip('Happening now', 'live')


def _meta(event: CommunityEvent) -> None:
    with ui.column().classes('gap-1'):
        if event.location:
            meta_row('place', event.location)
        meta_row('person', f'Hosted by {event.host.preferred_name}')


def _event_card(service, user, event: CommunityEvent, state: dict, on_change, *, new: bool = False) -> None:
    with ui.card().classes('wiz-community-event wiz-ce-card full-width'):
        with ui.row().classes('wiz-ce-card__main no-wrap items-start full-width'):
            time_tile(event)
            with ui.column().classes('gap-1 col min-w-0'):
                _title_row(event, new=new)
                _meta(event)
        with ui.row().classes('wiz-ce-card__foot items-center justify-between full-width'):
            ui.label(_count_text(event, state)).classes('wiz-community-event__meta')
            _attendance(service, user, event, state, on_change)


def _own_card(event: CommunityEvent) -> None:
    with ui.card().classes('wiz-community-event wiz-ce-card full-width'):
        with ui.column().classes('wiz-ce-card__main full-width'):
            _title_row(event, own=True)
            meta_row('schedule', when_line(event, with_day=True))
            if event.status is CommunityEventStatus.REJECTED and event.review_note:
                meta_row('feedback', f'Staff said: {event.review_note}')


async def community_events_view() -> None:
    """The listing, as the Community view on Home's Event tab.

    Opening it is what "seen" means: the events new since the last visit carry
    a New chip this once, and the count on the Event tab clears.
    """
    user = await _viewer()
    if user is None:
        ui.label('Your account could not be found.').classes('text-error')
        return
    service = CommunityEventService()
    is_staff = await AuthService.is_staff(user)
    needs_review = await SystemConfigService.community_events_require_approval()
    try:
        new_ids = await service.unseen_ids(user)
        await service.mark_seen(user)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return
    set_nav_count(EVENT_TAB, 0)

    with ui.column().classes('wiz-community-events page-container w-full gap-3'):
        disclaimer()
        with ui.row().classes('items-center justify-between w-full gap-2'):
            with ui.row().classes('items-center gap-1 no-wrap'):
                ui.label('Meetups, panels and side events attendees are running.') \
                    .classes('wiz-community-event__meta')
                await help_icon('community-events-attend', 'community-events-submit', user=user)
            ui.button(
                'Submit an event', icon='add',
                on_click=lambda: CommunityEventDialog(
                    user, is_staff=is_staff, needs_review=needs_review, on_saved=refresh_all,
                ).open(),
            ).props('color=primary')

        @steady_refreshable
        async def upcoming() -> None:
            events = await service.list_upcoming(user)
            if not events:
                empty_state('Nothing is listed yet. Running something? Submit it.')
                return
            state = await _attendance_state(service, user, [e.id for e in events])
            for day, group in groupby(events, key=day_label):
                ui.label(day).classes('wiz-community-event__day')
                with ui.element('div').classes('wiz-tile-grid'):
                    for event in group:
                        _event_card(service, user, event, state, refresh_all, new=event.id in new_ids)

        @steady_refreshable
        async def submissions() -> None:
            events = await service.list_mine(user)
            if not events:
                return
            ui.label('Your events').classes('wiz-community-event__day')
            with ui.element('div').classes('wiz-tile-grid'):
                for event in events:
                    _own_card(event)

        async def refresh_all(_saved=None) -> None:
            upcoming.refresh()
            submissions.refresh()

        await upcoming()
        await submissions()


def create() -> None:
    # The listing moved onto Home's Event tab; bookmarks and old links follow it.
    @protected_page('/community-events', feature=FeatureFlag.COMMUNITY_EVENTS)
    async def community_events_index(request: Request) -> RedirectResponse:
        return RedirectResponse(f"{request.scope.get('root_path', '')}{LISTING}")

    @protected_page('/community-events/{event_id}', feature=FeatureFlag.COMMUNITY_EVENTS, scope='row')
    async def community_event_detail(event_id: int) -> Optional[RedirectResponse]:
        user = await _viewer()
        if user is None:
            ui.label('Your account could not be found.').classes('text-error')
            return None
        service = CommunityEventService()
        try:
            event = await service.get_event(user, event_id)
        except ValueError:
            await render_not_found(
                user=user,
                message="We couldn't find that event. It may have been cancelled or taken down.",
                actions=[('All meetups', 'event', LISTING)],
            )
            return None
        redirect = await row_event_redirect(page_request(), event.event_id)
        if redirect is not None:
            return redirect

        await _chrome(user, event.title, 'Meetups')
        can_manage = event.host_id == user.id or await AuthService.is_staff(user)
        is_staff = await AuthService.is_staff(user)

        @steady_refreshable
        async def body() -> None:
            current = await service.get_event(user, event_id)
            with ui.column().classes('page-container-narrow w-full gap-3'):
                ui.link('← All meetups', LISTING).classes('text-caption')
                page_header(current.title)
                disclaimer()
                approved = current.status is CommunityEventStatus.APPROVED
                state: dict = await _attendance_state(service, user, [current.id]) if approved else {}
                with ui.card().classes('wiz-community-event wiz-ce-card full-width'):
                    with ui.column().classes('wiz-ce-card__main full-width'):
                        if not approved or is_live(current):
                            _title_row(current, link=False)
                        if current.status is CommunityEventStatus.REJECTED and current.review_note:
                            meta_row('feedback', f'Staff said: {current.review_note}')
                        with ui.row().classes('no-wrap items-center gap-3 full-width'):
                            date_tile(current)
                            with ui.column().classes('gap-1 col min-w-0'):
                                ui.label(when_line(current)).classes('wiz-community-event__when')
                                _meta(current)
                    manage = can_manage and current.status is not CommunityEventStatus.CANCELLED
                    if approved or manage:
                        with ui.row().classes('wiz-ce-card__foot items-center justify-between full-width'):
                            ui.label(_count_text(current, state) if approved else '') \
                                .classes('wiz-community-event__meta')
                            with ui.row().classes('items-center gap-1 no-wrap'):
                                if approved:
                                    _attendance(service, user, current, state, body.refresh)
                                if manage:
                                    with more_menu(current.title):
                                        menu_action('edit', 'Edit', lambda: CommunityEventDialog(
                                            user, current, is_staff=is_staff, on_saved=_after_save,
                                        ).open())
                                        menu_action('event_busy', 'Cancel event…',
                                                    lambda: _confirm_cancel(current), negative=True)

                if current.description:
                    about = await section_panel('About this event', icon='notes', user=user)
                    with about.body.classes('wiz-community-event__body'):
                        render_user_blocks(parse_user_blocks(current.description))

                if can_manage and current.rsvp_enabled:
                    attendees = await service.list_attendees(user, current.id)
                    going = await section_panel("Who's going", icon='groups', user=user)
                    with going.body:
                        if attendees:
                            for person in attendees:
                                meta_row('person', person.preferred_name)
                        else:
                            ui.label('No RSVPs yet.').classes('text-muted')

        async def _after_save(_saved) -> None:
            body.refresh()

        def _confirm_cancel(current: CommunityEvent) -> None:
            async def cancel() -> None:
                try:
                    await service.cancel(user, current.id)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Event cancelled. Anyone subscribed has been told.', color='positive')
                body.refresh()

            ConfirmationDialog(
                f'Cancel {current.title}? It comes off the listing, and anyone subscribed gets a DM.',
                on_confirm=cancel, confirm_text='Cancel event', cancel_text='Keep it',
                title='Cancel this event?',
            ).open()

        await body()
        return None

