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
from models import Role
from pages.check_in import check_in_desk_tab
from pages.volunteer_tabs.availability import availability_tab
from pages.volunteer_tabs.full_schedule import full_schedule_tab
from pages.volunteer_tabs.proctor_station import proctor_station_tab
from theme.base import BaseLayout


def create() -> None:
    # The hub carries no feature gate of its own: the volunteer tabs follow
    # VOLUNTEERS, the desk follows EVENT_CHECK_IN, and Proctor Station stays with
    # a proctor under neither, so each tab below keeps its own gate and a
    # visitor with none of them gets the unknown-route page the flags would give.
    @protected_tab_page('/volunteer', roles=[Role.VOLUNTEER, Role.PROCTOR, Role.STAFF, Role.CHECK_IN_DESK,
                                             Role.VOLUNTEER_COORDINATOR])
    async def volunteer_page(
        section: str | None = None, request: Request = None, release: str | None = None,
        event: str | None = None,
    ):
        root_path = request.scope.get('root_path', '') if request else ''
        # My Shifts folded into My Schedule; the reminder DMs already sent carry
        # this address, and ``?release=`` still opens the give-back dialog.
        if section == VOLUNTEER_MY_SHIFTS:
            params = {'release': release} if release else {'shifts': 1}
            return RedirectResponse(f'{root_path}{home_url(HOME_MY_SCHEDULE, **params)}')

        ui.page_title(f'Volunteer — {await TenantService.current_community_name() or "Wizzrobe"}')
        user = await get_user_from_session(app.storage.user)

        roles = await AuthService.get_roles(user)
        # Staff-equivalence throughout, so a platform super-admin sees the whole
        # hub in a tenant they hold no roles in.
        is_staff = await AuthService.is_staff(user)
        is_volunteer = Role.VOLUNTEER in roles
        volunteers_live = await AuthService.can_view_volunteer(user)
        proctor_station = await AuthService.can_open_proctor_station(user)
        desk = await AuthService.can_view_check_in_desk(user)

        tabs = []
        if volunteers_live:
            # Reads the *signed-in* user's own availability, so it is safe (and
            # empty) for someone who volunteers for nothing; the role gate above
            # is the only guard on saving it. This tab is when you can *work*,
            # and it is opt-in — the player one on Home is when you can't
            # *play*, opt-out. Their shifts are on Home → My Schedule.
            if is_volunteer or is_staff:
                tabs.append({'label': 'My Availability', 'icon': 'edit_calendar', 'content': availability_tab})
            # Everyone the volunteer roles admit reads the whole published
            # schedule: knowing who is at the next station over is the point,
            # and volunteers may see what staff see.
            tabs.append({'label': 'Full Schedule', 'icon': 'calendar_view_week', 'content': full_schedule_tab})
        if proctor_station:
            # Bound to the viewer: the tab's help icons read role-gated
            # snippets from the event handbook, and the page has already
            # resolved who is looking.
            tabs.append({'label': 'Proctor Station', 'icon': 'sports_esports',
                         'content': (proctor_station_tab, (user,))})
        # Offered while an event is open, as the menu item it replaced was. A
        # link straight to it (``/checkin``, an old bookmark) still lands, so the
        # tab can say nothing is open rather than drop the visitor elsewhere.
        if desk and (
            section == VOLUNTEER_CHECK_IN or event is not None or not tabs
            or await CheckInService().has_open_event()
        ):
            tabs.append({'label': 'Check-in Desk', 'icon': 'how_to_reg', 'slug': VOLUNTEER_CHECK_IN,
                         'content': (check_in_desk_tab, (user,), {'event': event})})
        if not tabs:
            # A hub role whose feature is off here (a desk worker with check-in
            # off, a volunteer with VOLUNTEERS off): the same page a page-level
            # feature gate gives, and no menu link points here.
            from theme.error_page import render_not_found
            await render_not_found(user=user)
            return
        if event is not None and section is None:
            section = VOLUNTEER_CHECK_IN
        base_path = f"{root_path}/volunteer"
        await BaseLayout(
            tabs=tabs, section=section, base_path=base_path, page_name='volunteer', user=user,
            show_volunteer=volunteers_live,
        ).render()
