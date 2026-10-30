"""Volunteer Section Page (self-service for volunteers)."""

from fastapi import Request
from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.services import (
    AuthService,
    CheckInService,
    TenantService,
    get_user_from_session,
)
from application.utils.app_links import HOME_MY_SCHEDULE, VOLUNTEER_CHECK_IN, VOLUNTEER_MY_SHIFTS, home_url
from middleware.auth import protected_tab_page
from middleware.event_routing import default_event_redirect
from models import Role, User
from pages.check_in import check_in_desk_tab
from pages.volunteer_tabs.availability import availability_tab
from pages.volunteer_tabs.full_schedule import full_schedule_tab
from pages.volunteer_tabs.proctor_station import proctor_station_tab
from theme.base import BaseLayout, tab_slug
from theme.section_menu import register_section_menu


async def build_volunteer_tabs(user: User | None, *, section: str | None = None,
                               event: str | None = None) -> list[dict]:
    """The hub's tabs this viewer may open, in order. Also the drawer's page list."""
    roles = await AuthService.get_roles(user)
    # is_staff (staff-equivalence), so a super-admin sees the whole hub.
    is_staff = await AuthService.is_staff(user)
    is_volunteer = Role.VOLUNTEER in roles
    volunteers_live = await AuthService.can_view_volunteer(user)
    proctor_station = await AuthService.can_open_proctor_station(user)
    desk = await AuthService.can_view_check_in_desk(user)

    tabs = []
    if volunteers_live:
        if is_volunteer or is_staff:
            tabs.append({'label': 'My Availability', 'icon': 'edit_calendar', 'content': availability_tab})
        tabs.append({'label': 'Full Schedule', 'icon': 'calendar_view_week', 'content': full_schedule_tab})
    if proctor_station:
        tabs.append({'label': 'Proctor Station', 'icon': 'sports_esports',
                     'content': (proctor_station_tab, (user,))})
    if desk and (
        section == VOLUNTEER_CHECK_IN or event is not None or not tabs
        or await CheckInService().has_open_event()
    ):
        tabs.append({'label': 'Check-in Desk', 'icon': 'how_to_reg', 'slug': VOLUNTEER_CHECK_IN,
                     'content': (check_in_desk_tab, (user,), {'event': event})})
    return tabs


async def _volunteer_menu(user: User) -> list[dict]:
    return await build_volunteer_tabs(user)


register_section_menu('/volunteer', _volunteer_menu)


def create() -> None:
    # No feature gate on the hub itself: each tab below keeps its own.
    @protected_tab_page('/volunteer', roles=[Role.VOLUNTEER, Role.PROCTOR, Role.STAFF, Role.CHECK_IN_DESK,
                                             Role.VOLUNTEER_COORDINATOR, Role.STREAM_MANAGER],
                        scope='community')
    async def volunteer_page(
        section: str | None = None, request: Request = None, release: str | None = None,
        event: str | None = None,
    ):
        root_path = request.scope.get('root_path', '') if request else ''
        if section == VOLUNTEER_MY_SHIFTS:
            params = {'release': release} if release else {'shifts': 1}
            return RedirectResponse(f'{root_path}{home_url(HOME_MY_SCHEDULE, **params)}')

        if section != tab_slug('My Availability'):
            redirect = await default_event_redirect(request)
            if redirect is not None:
                return redirect

        ui.page_title(f'Volunteer — {await TenantService.current_community_name() or "Wizzrobe"}')
        user = await get_user_from_session(app.storage.user)

        tabs = await build_volunteer_tabs(user, section=section, event=event)
        if not tabs:
            from theme.error_page import render_not_found
            await render_not_found(user=user)
            return
        if event is not None and section is None:
            section = VOLUNTEER_CHECK_IN
        base_path = f"{root_path}/volunteer"
        await BaseLayout(
            tabs=tabs, section=section, base_path=base_path, page_name='volunteer', user=user,
            show_volunteer=await AuthService.can_view_volunteer(user),
        ).render()
