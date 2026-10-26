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

    # Phoenix brand chrome. The picker is the platform's front door (bare host,
    # no tenant), so it can't reuse the tenant BaseLayout drawer; apply the same
    # stylesheet + palette + header so first impression reads as the product.
    ui.dark_mode(app.storage.user.get('dark_mode'))
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">')
    # This page builds its own chrome rather than BaseLayout's, so it repeats
    # the site-wide noindex (see theme/base.py).
    ui.add_head_html('<meta name="robots" content="noindex, nofollow">')
    ui.colors(
        primary='#9C6B12', secondary='#C24E12', accent='#E0A82E',
        positive='#557A1F', negative='#B3362B', warning='#B45309', info='#0E7470',
    )
    with ui.header().classes('wiz-header items-center no-wrap'):
        ui.label('Wizzrobe').classes('wiz-wordmark')
        # Flexible spacer pushes the login/user controls to the right edge (see
        # BaseLayout._render_header for the same pattern on tenant surfaces).
        ui.space()
        if user is not None:
            ui.label(user.preferred_name).classes('text-lg user-name')
            avatar_url = app.storage.user.get('avatar')
            if avatar_url:
                ui.image(avatar_url).props('width=32 height=32 fit=cover round').classes('user-avatar')
            else:
                # No avatar URL (mock-login sessions, or Discord users on the
                # default avatar): show a placeholder glyph rather than a broken image.
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
            # The bare host has no drawer, so without this a first-time visitor
            # here has no help at all. Help lives inside each community.
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


#: Home sections that belong to the viewer rather than to one event, with
#: their retired slugs.
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
        # Bare platform host (no /t/<slug>) -> community picker, not a tenant home.
        tid = get_current_tenant_id()
        if tid is None:
            await _render_platform_landing()
            return
        # On Air became the Watch page; old links and DMs land there.
        if section == 'on-air':
            return RedirectResponse(f"{request.scope.get('root_path', '') if request else ''}/live")
        ids, malformed_link = deep_link_ids(
            schedule=schedule, reschedule=reschedule, match=match, hard=hard, agree=agree,
            book=book, startgg=startgg, comments=comments, release=release, shifts=shifts,
        )
        # A link about one match opens in that match's event. My Schedule and
        # Profile are the viewer's own, across every live event, so they keep a
        # bare URL; any other bare link opens in the default event.
        match_id = next((ids[k] for k in ('match', 'comments', 'hard', 'reschedule') if ids.get(k)), None)
        redirect = None
        if match_id is not None:
            redirect = await row_event_redirect(request, await EventService.match_event_id(match_id))
        elif section not in PERSONAL_HOME_SECTIONS:
            redirect = await default_event_redirect(request)
        if redirect is not None:
            return redirect
        # A bare ``ui.page`` misses the page view ``_tenant_page`` records, so
        # home records its own: every section under one path, like the hubs.
        record_page_view('/home', {'section': section, **ids})
        # Stash the tenant onto the connection so websocket UI handlers resolve it.
        stash_client_tenant_id(tid)
        stash_client_event_id(get_current_event_id())
        # Carry host mode too, so link-section buttons can hide on a custom domain.
        stash_client_host_mode(is_host_mode())

        ui.page_title(await TenantService.current_community_name() or 'Wizzrobe')
        signed_in = app.storage.user.get('authenticated', False)
        # get_user_from_session enforces is_active, so a deactivated account
        # is treated as logged-out here too (uniform with /admin, /equipment,
        # the login flow, and the REST API) rather than still being shown its
        # personalized home and admin/volunteer nav affordances.
        user = await get_user_from_session(app.storage.user)
        # Bind the display clock. Same reason this page applies the membership
        # gate itself: it is a bare ``ui.page``, so it never passes through
        # ``_tenant_page`` and gets nothing from it. Without this every tab below
        # — including the timezone picker on the Profile tab — renders on the
        # fallback zone no matter what the community or the viewer chose.
        await bind_display_timezone(tid, user)
        # The membership gate. This page is registered with a bare ``ui.page``
        # (the same function also renders the platform community picker, which
        # has no tenant and must stay anonymous), so it applies the gate itself
        # rather than getting it from ``_tenant_page``.
        #
        # This is where the audit measured the symptom: a platform user with no
        # roles and no membership loaded a brand-new community's home schedule,
        # saw its first match with both players' names, and was offered Sign Up
        # as commentator and tracker. The community's own spectator surface —
        # the bracket views — stays public on its own routes.
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
        # Four tabs, on every community, whatever its feature flags. The count
        # used to run from five to ten, which meant no two communities' phone nav
        # looked alike and — because the bottom bar carries ``tabs[:4]`` and hides
        # the rest behind More — a player's own matches were off the bar entirely.
        # A feature that is off now removes a *view* or a *section*, never a tab.
        #
        # Each tab lists the slugs it absorbed. Those answer on read-in only, so
        # DMs already sent (docs/features/discord.md) keep landing where they
        # always did while the URL normalizes to the new slug on the first switch.
        community_new = await community_new_count(user)
        tabs = [
            # `view` is the section slug the visitor arrived on: the Event tab
            # opens on the matching view, so /home/brackets still opens
            # Brackets rather than the board.
            # `badge` counts the community events listed since the member last
            # opened the Community view (`/home/community`, which also answers
            # the retired `/community-events`).
            {'label': 'Event', 'icon': 'stadium', 'aliases': ('schedule', 'brackets', 'community'),
             'badge': community_new,
             'content': (event_tab, (), {'view': section, 'community_new': community_new})},
            # `schedule` is a bracket matchup id (`book` the same for a Challonge
            # matchup, `startgg` for a start.gg one): the matches section opens its schedule dialog on arrival, so the "matchup ready" DM's button
            # lands on the date/time picker rather than on a page about it.
            # `reschedule` is a match id doing the same job for the declined
            # reschedule DM, whose next step is asking again with a new time.
            # `match` narrows the board to one match, for the stage DMs — the
            # player-side equivalent of `/admin/schedule?match_id=`.
            # `hard` is a match id from the harder-settings DM, opening that
            # match's private opt-in dialog. `agree` is a reschedule request id
            # from the opponent DM, opening that request with its Agree button.
            # `badge` counts the matches waiting on this player to pick a time.
            # `comments` is a match id from the staff-comment DM: the board
            # narrows to that match and opens its details on the comments.
            # `release` is a volunteer assignment id from the shift reminder's
            # "Can't make it" button, opening that shift's give-back dialog;
            # `shifts` scrolls to the shifts section (both used to address the
            # retired Volunteer → My Shifts).
            {'label': 'My Schedule', 'icon': 'event_available',
             'badge': await count_waiting_on_you(user),
             'aliases': ('player', 'my-crew', 'availability'),
             'content': (my_schedule_tab, (),
                         {'schedule': ids['schedule'], 'reschedule': ids['reschedule'],
                          'match': ids['match'], 'hard': ids['hard'], 'agree': ids['agree'],
                          'book': ids['book'], 'startgg': ids['startgg'],
                          'comments': ids['comments'], 'release': ids['release'],
                          'shifts': ids['shifts'] is not None})},
            # Signing up is the step that comes before having a schedule at all,
            # and it used to be a checkbox on Profile that a player could use the
            # app for a season without ever finding. Triforce text submission
            # hangs off the card for the tournament it belongs to.
            {'label': 'Tournaments', 'icon': 'emoji_events', 'aliases': ('triforce-texts',),
             'content': tournaments_tab},
            # `focus` is a Profile section id from the profile-nudge banner: the tab
            # scrolls to and highlights the card that fixes the item.
            # `equipment` is the old Home → Equipment tab; what you're holding
            # is now the top card on Profile.
            {'label': 'Profile', 'icon': 'account_circle', 'aliases': ('equipment',),
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

    # Home is the tenant landing (`/`) and its sections hang off `/home/<slug>`,
    # matching the /admin/<slug> and /volunteer/<slug> hubs. Bare `/` renders the
    # default section (the URL stays `/` until the first tab switch).
    ui.page('/')(home)
    ui.page('/home')(home)
    ui.page('/home/{section}')(home)
