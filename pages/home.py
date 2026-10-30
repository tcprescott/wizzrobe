from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui

from application.event_context import get_current_event_id, stash_client_event_id
from application.services import AuthService, EventService, TenantService, get_user_from_session
from application.tenant_context import (
    get_current_tenant_id,
    is_host_mode,
    stash_client_host_mode,
    stash_client_tenant_id,
)
from middleware.auth import bind_display_timezone, can_view_bound_event, enforce_membership, record_page_view
from middleware.event_routing import default_event_redirect, row_event_redirect
from pages.home_tabs.event import community_new_count, event_tab
from pages.home_tabs.my_schedule import my_schedule_tab
from pages.home_tabs.player import count_waiting_on_you
from pages.home_tabs.player_edit_info import render_edit_info_tab
from pages.home_tabs.tournaments import tournaments_tab
from theme.assets import asset_url
from theme.base import BaseLayout
from theme.empty_state import empty_panel
from theme.links import link_button, set_link
from theme.page_header import page_title
from theme.timer_teardown import PageTimer


async def _render_platform_landing() -> None:
    """The bare platform host (no /t/<slug>) shows a community picker.

    Runs with no tenant context: lists active tenants (each linking to its
    path-mode home) and, for a super-admin, a link to the /platform surface."""
    ui.page_title('Wizzrobe')
    user = await get_user_from_session(app.storage.user)
    tenants = [t for t in await TenantService.list_tenants() if t.is_active]

    ui.dark_mode(app.storage.user.get('dark_mode'))
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">')
    # Own chrome, not BaseLayout's, so it must repeat the site-wide noindex.
    ui.add_head_html('<meta name="robots" content="noindex, nofollow">')
    ui.colors(
        primary='#9C6B12', secondary='#C24E12', accent='#E0A82E',
        positive='#557A1F', negative='#B3362B', warning='#B45309', info='#0E7470',
    )
    with ui.header().classes('wiz-header items-center no-wrap'):
        ui.label('Wizzrobe').classes('wiz-wordmark')
        ui.space()
        if user is not None:
            ui.label(user.preferred_name).classes('text-lg user-name')
            avatar_url = app.storage.user.get('avatar')
            if avatar_url:
                ui.image(avatar_url).props('width=32 height=32 fit=cover round').classes('user-avatar')
            else:
                ui.icon('account_circle').classes('user-avatar user-avatar-placeholder')
            logout_btn = ui.button(icon='logout', color=None).props('flat').tooltip('Log out')
            set_link(logout_btn, '/logout', new_tab=False)
        else:
            login_btn = ui.button(icon='login', color=None).props('flat').classes('login-button')
            set_link(login_btn, '/login', new_tab=False)
            with login_btn:
                ui.label('Login with Discord').classes('login-button-text')
            login_btn.tooltip('Login with Discord')

    with ui.column().classes('page-container-narrow p-6 gap-4 items-stretch'):
        page_title('Choose a community')
        ui.label('Pick the community you want to manage or take part in.') \
            .classes('text-muted')
        if not tenants:
            empty_panel('No communities are available yet.', icon='groups')
        with ui.element('div').classes('wiz-tile-grid'):
            for tenant in tenants:
                with ui.link(target=f'/t/{tenant.slug}/').classes('no-underline w-full'):
                    with ui.card().classes('wiz-tenant-card w-full'):
                        with ui.row().classes('items-center justify-between no-wrap w-full'):
                            with ui.column().classes('gap-0'):
                                ui.label(tenant.name).classes('wiz-tenant-name')
                                ui.label(f'/t/{tenant.slug}').classes('text-caption text-muted')
                            ui.icon('arrow_forward').classes('text-primary')
        if tenants:
            with ui.row().classes('items-center gap-x-3 gap-y-1 q-mt-sm'):
                ui.icon('help_outline').props('size=xs').classes('text-muted')
                ui.label('New here? Read how it works:').classes('text-caption text-muted')
                for tenant in tenants:
                    ui.link(f'{tenant.name} help', f'/t/{tenant.slug}/help') \
                        .classes('text-caption')
        if await AuthService.is_super_admin(user):
            ui.separator().classes('separator-spacing')
            link_button('Platform administration', '/platform', icon='admin_panel_settings', new_tab=False) \
                .props('outline color=primary')


def deep_link_ids(**raw: str | None) -> tuple[dict[str, int | None], bool]:
    """Parse the DM deep-link params, which should each be a row id.

    They used to be typed ``int`` on the route, so a mangled link
    (``?schedule=abc``, a chat client eating half the URL) got FastAPI's raw 422
    JSON instead of the page. Now a malformed one is dropped and reported, and
    the page renders as if it had not been there.
    """
    ids: dict[str, int | None] = {}
    malformed = False
    for name, value in raw.items():
        if value is None or value == '':
            ids[name] = None
        elif value.isdecimal():
            ids[name] = int(value)
        else:
            ids[name] = None
            malformed = True
    return ids, malformed


PERSONAL_HOME_SECTIONS = frozenset({
    'my-schedule', 'player', 'my-crew', 'availability', 'profile', 'equipment',
})


def create() -> None:
    async def home(
        section: str | None = None,
        request: Request = None,
        schedule: str | None = None,
        reschedule: str | None = None,
        match: str | None = None,
        hard: str | None = None,
        agree: str | None = None,
        book: str | None = None,
        startgg: str | None = None,
        comments: str | None = None,
        focus: str | None = None,
        release: str | None = None,
        shifts: str | None = None,
    ):
        tid = get_current_tenant_id()
        if tid is None:
            await _render_platform_landing()
            return
        if section == 'on-air':
            return RedirectResponse(f"{request.scope.get('root_path', '') if request else ''}/live")
        ids, malformed_link = deep_link_ids(
            schedule=schedule, reschedule=reschedule, match=match, hard=hard, agree=agree,
            book=book, startgg=startgg, comments=comments, release=release, shifts=shifts,
        )
        match_id = next((ids[k] for k in ('match', 'comments', 'hard', 'reschedule') if ids.get(k)), None)
        redirect = None
        if match_id is not None:
            redirect = await row_event_redirect(request, await EventService.match_event_id(match_id))
        elif section not in PERSONAL_HOME_SECTIONS:
            redirect = await default_event_redirect(request)
        if redirect is not None:
            return redirect
        # A bare ui.page misses _tenant_page's page view, so home records its own.
        record_page_view('/home', {'section': section, **ids})
        # Stash the tenant onto the connection so websocket UI handlers resolve it.
        stash_client_tenant_id(tid)
        stash_client_event_id(get_current_event_id())
        stash_client_host_mode(is_host_mode())

        ui.page_title(await TenantService.current_community_name() or 'Wizzrobe')
        signed_in = app.storage.user.get('authenticated', False)
        # get_user_from_session enforces is_active, so a deactivated account is logged-out here.
        user = await get_user_from_session(app.storage.user)
        # A bare ui.page never passes through _tenant_page: bind the display clock here, or every tab
        # renders on the fallback zone.
        await bind_display_timezone(tid, user)
        # A bare ui.page (it also renders the anonymous community picker), so it applies the membership gate itself.
        if await enforce_membership(tid, user):
            return
        if not await can_view_bound_event(user):
            from theme.error_page import render_not_found
            await render_not_found(user=user)
            return
        if user is None and signed_in:
            with ui.row():
                ui.label('User not found in the database. Logging out...').classes('text-error')
                app.storage.user.clear()
            PageTimer(2, lambda: ui.navigate.to('/logout'), once=True)
            return
        # Four tabs whatever the flags: the phone bottom bar carries tabs[:4], so a flag removes a view,
        # never a tab. Each tab's aliases keep links in already-sent DMs landing.
        community_new = await community_new_count(user)
        tabs = [
            {'label': 'Event', 'icon': 'stadium', 'aliases': ('schedule', 'brackets', 'community'),
             'badge': community_new,
             'content': (event_tab, (), {'view': section, 'community_new': community_new})},
            {'label': 'My Schedule', 'icon': 'event_available', 'half': 'personal',
             'badge': await count_waiting_on_you(user),
             'aliases': ('player', 'my-crew', 'availability'),
             'content': (my_schedule_tab, (),
                         {'schedule': ids['schedule'], 'reschedule': ids['reschedule'],
                          'match': ids['match'], 'hard': ids['hard'], 'agree': ids['agree'],
                          'book': ids['book'], 'startgg': ids['startgg'],
                          'comments': ids['comments'], 'release': ids['release'],
                          'shifts': ids['shifts'] is not None})},
            {'label': 'Tournaments', 'icon': 'emoji_events', 'aliases': ('triforce-texts',), 'half': 'personal',
             'content': tournaments_tab},
            {'label': 'Profile', 'icon': 'account_circle', 'aliases': ('equipment',), 'half': 'personal',
             'content': (render_edit_info_tab, (), {'focus': focus})},
        ]
        if malformed_link:
            ui.notify(
                "That link is broken: the match it points to got cut off. "
                "Open it again from the message you were sent.",
                color='warning', multi_line=True,
            )
        base_path = f"{request.scope.get('root_path', '')}/home" if request else '/home'
        await BaseLayout(
            tabs=tabs, section=section, base_path=base_path, page_name='home', user=user,
        ).render()

    ui.page('/')(home)
    ui.page('/home')(home)
    ui.page('/home/{section}')(home)
