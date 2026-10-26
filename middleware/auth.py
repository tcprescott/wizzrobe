import functools
import inspect
import logging
import re
from typing import Iterable, Optional

import sentry_sdk
from fastapi import Request
from nicegui import app, background_tasks, ui
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from application.event_context import get_current_event_id, stash_client_event_id
from application.services.auth_service import AuthService, get_user_from_session
from application.services.feature_flag_service import FeatureFlagService
from application.services.telemetry_service import TelemetryService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tenant_service import TenantService
from application.services.timezone_service import TimezoneService
from application.tenant_context import (
    get_current_tenant_id,
    is_host_mode,
    stash_client_host_mode,
    stash_client_tenant_id,
    tenant_scope,
)
from application.timezone_context import (
    bind_hour12,
    get_browser_hour_cycle,
    get_browser_timezone,
    pick_hour12,
    set_timezone_name,
    stash_client_browser_timezone,
    stash_client_timezone,
)
from application.utils.hostname import effective_request_host
from models import FeatureFlag, Role

#: A page's feature gate: one flag, or several of which any one opens it.
FeatureGate = FeatureFlag | tuple[FeatureFlag, ...] | None


def _bind_display_timezone(name: str, user=None) -> None:
    """Make ``name`` the display clock for this page build and this connection.

    Both halves are needed and neither is redundant: the contextvar serves the
    rest of *this* HTTP request (the page builder and everything it awaits), and
    the client stash serves every later websocket event handler on the same
    connection, which runs after the request — and its contextvar — is gone.

    The 12/24-hour choice rides along: ``user``'s saved one, else the browser's.
    """
    set_timezone_name(name)
    stash_client_timezone(name)
    bind_hour12(pick_hour12(getattr(user, 'hour_cycle', None), get_browser_hour_cycle()))


async def bind_display_timezone(tenant_id: int, user=None) -> str:
    """Resolve and bind this viewer's display clock. Returns the bound zone.

    The entry point for any page that does **not** go through
    :func:`_tenant_page` — currently the tenant home, which is a bare
    ``ui.page`` so that the same function can also serve the tenant-less
    community picker. A page that binds no zone silently renders on the
    fallback, which looks like the feature simply not working.
    """
    browser_tz = get_browser_timezone()
    stash_client_browser_timezone(browser_tz)
    settings = await TimezoneService.get_settings(tenant_id)
    name = TimezoneService.pick(settings, user=user, browser_timezone=browser_tz)
    _bind_display_timezone(name, user)
    return name


async def _run_in_tenant(tenant_id, coro) -> None:
    """Await a deferred (background-task) coroutine with a tenant bound.

    Page-view telemetry is captured in a background task, which runs outside the
    request and so loses the contextvar — capture the tenant at page-build time
    and rebind it here so the row is tenant-stamped."""
    with tenant_scope(tenant_id):
        await coro

# Keep page-view detail bounded: query/path params carry useful engagement
# context (which tab/report), but we cap count and length so a crafted URL
# can't bloat a telemetry row.
_MAX_TRACKED_PARAMS = 15
_MAX_PARAM_LEN = 120
# Path params whose *value* is a credential. The room-seeds view carries its
# token in the URL, and a telemetry row is read by anyone with the reports tab —
# recording the value would turn engagement data into a key ring. Matched on the
# param name so a future route gets the redaction without having to remember it.
_SECRET_PARAM_MARKERS = ('token', 'secret', 'password', 'code')


def _tracked_params(kwargs: dict) -> dict:
    """The page's params, bounded and with any credential redacted.

    Pure, and split out of :func:`record_page_view` because that one swallows
    every exception by design — a rule this important should be testable without
    a request context to raise inside.
    """
    params: dict = {}
    for key, value in kwargs.items():
        if value is None or len(params) >= _MAX_TRACKED_PARAMS:
            continue
        if any(marker in key.lower() for marker in _SECRET_PARAM_MARKERS):
            params[key] = '[redacted]'
            continue
        if isinstance(value, (str, int, float, bool)):
            params[key] = str(value)[:_MAX_PARAM_LEN]
    return params


def record_page_view(path: str, kwargs: dict) -> None:
    """Fire-and-forget a page-view telemetry row for an authenticated load.

    Reads the caller's session identity + browser id here (only valid during
    page building) and hands the write to a background task. Fully defensive:
    telemetry must never interfere with rendering the page.
    """
    try:
        discord_id = app.storage.user.get('discord_id')
        user_id = app.storage.user.get('user_id')
        username = app.storage.user.get('username')
        try:
            session_id = app.storage.browser.get('id')
        except Exception:
            session_id = None
        params = _tracked_params(kwargs)
        tenant_id = get_current_tenant_id()
        background_tasks.create(
            _run_in_tenant(
                tenant_id,
                TelemetryService().track_page_view(
                    path=path,
                    discord_id=discord_id,
                    user_id=user_id,
                    username=username,
                    session_id=session_id,
                    params=params or None,
                ),
            )
        )
    except Exception:
        pass

# Registry of routes that require authentication; populated by protected_page decorator.
# Plain strings match exactly; entries containing ``{param}`` placeholders are
# compiled to regexes so dynamic NiceGUI routes match incoming request paths.
protected_routes: set[str] = set()


def referrer_for(root_path: str, path: str, query: str = '') -> str:
    """The tenant-qualified destination to come back to after login.

    The query is part of the destination: a DM's button carries ``?match_id=``
    or ``?schedule=``, and dropping it lands the reader on the page instead of
    the dialog the message asked them to open.
    """
    return f'{root_path}{path}?{query}' if query else f'{root_path}{path}'


def _matches_protected_route(path: str) -> bool:
    for route in protected_routes:
        if '{' in route:
            pattern = '^' + re.sub(r'\{[^/}]+\}', r'[^/]+', route) + '$'
            if re.match(pattern, path):
                return True
        elif path == route:
            return True
    return False

async def enforce_membership(
    tenant_id: int,
    user,
    *,
    is_super_admin: Optional[bool] = None,
) -> bool:
    """Render the join door when ``user`` does not belong to ``tenant_id``.

    Returns True when it rendered — the caller must then return without building
    its own body.

    A non-member gets the **door**, not a 403: forbidden-by-role is a dead end,
    but not-a-member is a state with a remedy, and the page whose whole job is to
    offer that remedy should not open by saying no.

    ``SUPER_ADMIN`` bypasses, exactly as it bypasses the role gate — it belongs
    to no community by design. Deliberately **not** gated on ``Tenant.is_active``:
    an inactive community is a separate concern with its own handling, and
    folding the two together makes both harder to reason about.

    Lives here rather than inside ``_tenant_page`` because the tenant home is
    registered with a bare ``ui.page`` (the same function also serves the
    platform community picker, which has no tenant and must stay anonymous), so
    it applies the gate itself against this one implementation.
    """
    from theme.join_page import (
        render_join_page,
        resolve_ask_again_at,
        resolve_join_preview,
        resolve_join_state,
        resolve_removed,
    )

    if is_super_admin is None:
        is_super_admin = await AuthService.is_super_admin(user)
    if is_super_admin:
        return False
    if user is not None and await TenantService.is_member(user.id, tenant_id):
        return False
    # The other ways through: a community open to anyone who signs in, or one
    # that opened its door to its Discord server, lets them in here, on the
    # page they were opening.
    if user is not None:
        memberships = TenantMembershipService()
        why = None
        if await memberships.join_openly(user, tenant_id):
            why = 'it’s open to anyone who signs in'
        elif await memberships.join_via_discord(user, tenant_id):
            why = 'you’re in its Discord server'
        if why is not None:
            # Only reached for someone who wasn't a member a moment ago, so this
            # is their first landing. Without it they'd arrive on a schedule
            # with no idea they'd just joined anything.
            from theme.notice import stash_notice

            tenant = await TenantService.get_by_id(tenant_id)
            name = tenant.name if tenant else 'this community'
            stash_notice(
                f'Welcome to {name}. You’re a member now because {why}.',
                color='positive', sticky=True, tenant_id=tenant_id,
            )
            return False

    from theme.error_page import prepared_layout

    tenant = await TenantService.get_by_id(tenant_id)
    render_join_page(
        tenant_id=tenant_id,
        tenant_name=tenant.name if tenant else 'this community',
        user=user,
        pending=await resolve_join_state(user, tenant_id),
        preview=await resolve_join_preview(tenant_id),
        # The door is the community's front page: its name, palette and the
        # public half of its drawer (Event Information, Help), not Wizzrobe's.
        layout=await prepared_layout(user),
        ask_again_at=await resolve_ask_again_at(user, tenant_id),
        removed=await resolve_removed(user, tenant_id),
    )
    return True


async def _render_community_chooser() -> None:
    """A community page reached on the bare platform host: offer each community's copy.

    ``/help`` or ``/facts`` without ``/t/<slug>`` is a real page someone
    lost the prefix of, not a mistyped one, so the useful answer is the same
    page in each community rather than a dead end — and certainly not a drawer
    whose Help item points back at the URL it is on.
    """
    from theme.base import current_local_url
    from theme.error_page import prepared_layout, render_error_page

    user = await get_user_from_session(app.storage.user)
    here = current_local_url()
    tenants = [t for t in await TenantService.list_tenants() if t.is_active]
    render_error_page(
        status_code=404,
        headline='Pick a community',
        message=(
            'This page lives inside a community. Choose yours:'
            if tenants else 'This page lives inside a community, and none is open yet.'
        ),
        user=user,
        layout=await prepared_layout(user),
        actions=[(t.name, 'arrow_forward', f'/t/{t.slug}{here}') for t in tenants],
    )


def _tenant_page(
    path: str,
    *,
    roles: Optional[Iterable[Role]] = None,
    allow_tournament_membership: bool = False,
    feature: FeatureGate = None,
    telemetry_path: Optional[str] = None,
    require_auth: bool = True,
    **page_kwargs,
):
    """Shared implementation of :func:`protected_page` and :func:`public_page`.

    Every tenant page — signed-in or not — resolves a tenant, stashes it on the
    connection, honours the feature gate, and records a page view. The only
    difference is whether the route joins ``protected_routes`` (and so whether
    ``AuthMiddleware`` bounces an anonymous visitor to ``/login``) and whether a
    role gate runs on top.
    """
    role_list = list(roles) if roles else None
    view_path = telemetry_path or path
    flags: tuple[FeatureFlag, ...] = (
        () if feature is None else (feature,) if isinstance(feature, FeatureFlag) else tuple(feature)
    )

    gated = role_list is not None or allow_tournament_membership

    def decorator(func):
        # Only an auth-requiring route joins the registry AuthMiddleware
        # redirects on; a public one is reachable signed out.
        if require_auth:
            protected_routes.add(path)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Capture engagement telemetry for every page load, gated or not,
            # before any auth short-circuit. On a public page the visitor may be
            # anonymous, and the row is then attributed to the browser session
            # alone (no discord_id).
            record_page_view(view_path, kwargs)

            # Every @protected_page is a tenant page. If reached with no tenant
            # (a bare /admin on the platform host, not /t/<slug>/admin), 404.
            tid = get_current_tenant_id()
            if tid is None:
                await _render_community_chooser()
                return
            # Stash the tenant onto the connection so websocket UI event handlers
            # (which run outside any request) can resolve it via the fallback.
            stash_client_tenant_id(tid)
            stash_client_event_id(get_current_event_id())
            # Carry host mode too, so custom-domain-only affordances (e.g. the
            # Discord-connect button) can hide in websocket event handlers.
            stash_client_host_mode(is_host_mode())

            # Bind the display clock before anything renders a time — including
            # the 404/403/join pages below, which show none today but must not
            # be the surface that reintroduces a hardcoded zone. Resolved without
            # the user here (they may not be loaded, or may not exist); the
            # auth branch below refines it once a signed-in user is known.
            tz_settings = await TimezoneService.get_settings(tid)
            browser_tz = get_browser_timezone()
            stash_client_browser_timezone(browser_tz)
            _bind_display_timezone(
                TimezoneService.pick(tz_settings, user=None, browser_timezone=browser_tz)
            )

            # Feature gate (before the role gate): a subsystem the tenant hasn't
            # enabled is hidden from everyone — 404, like an unknown route — so a
            # not-yet-released feature never leaks and role has no bearing.
            if flags and not any([await FeatureFlagService().is_enabled(f) for f in flags]):
                # The unknown-route page, word for word and status for status:
                # anything more specific would say the feature exists here.
                from theme.error_page import render_not_found
                await render_not_found(
                    user=await get_user_from_session(app.storage.user),
                )
                return

            # Membership gate: a community is visible to the people who belong
            # to it. Only for auth-requiring routes — a @public_page (the
            # spectator bracket views) stays world-readable.
            user = None
            is_super_admin = False
            if require_auth:
                user = await get_user_from_session(app.storage.user)
                is_super_admin = await AuthService.is_super_admin(user)
                # Now that the viewer is known, their saved preference can
                # outrank the browser hint the first pass used.
                _bind_display_timezone(
                    TimezoneService.pick(tz_settings, user=user, browser_timezone=browser_tz), user,
                )
                if await enforce_membership(tid, user, is_super_admin=is_super_admin):
                    return

            # Authorization for a *gated* page comes from the user's tenant-scoped
            # roles / tournament-admin membership / super-admin — all evaluated in
            # this tenant's context, so a role in another tenant grants nothing
            # here. Role-less protected pages need only authentication (which
            # AuthMiddleware already enforced) plus the membership gate above.
            if gated:
                # Resolved once, above, on every auth-requiring page; a
                # @public_page with a role gate would still need to load it.
                if user is None and not require_auth:
                    user = await get_user_from_session(app.storage.user)
                    is_super_admin = await AuthService.is_super_admin(user)
                allowed = is_super_admin
                if not allowed and user is not None and role_list:
                    held = await AuthService.get_roles(user)
                    allowed = bool(held.intersection(role_list))
                if not allowed and allow_tournament_membership:
                    allowed = await AuthService.can_view_admin(user)
                if not allowed:
                    from theme.error_page import prepared_layout, render_error_page
                    render_error_page(
                        status_code=403,
                        headline='Not available to you',
                        message="You don't have access to this page. If you think that's wrong, ask a staff member to check your role.",
                        user=user,
                        layout=await prepared_layout(user),
                    )
                    return

            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        return ui.page(path, **page_kwargs)(wrapper)
    return decorator


def protected_page(
    path: str,
    *,
    roles: Optional[Iterable[Role]] = None,
    allow_tournament_membership: bool = False,
    feature: FeatureGate = None,
    telemetry_path: Optional[str] = None,
    **page_kwargs,
):
    """Register a NiceGUI page that requires authentication and optional roles.

    Args:
        path: Page route.
        roles: If set, the user must hold at least one of these global roles.
        allow_tournament_membership: If True, users who are a Tournament Admin
            or Crew Coordinator of any tournament also pass the role gate.
            Use for pages whose subset of features may be available to per-
            tournament admins (e.g. the admin dashboard shell).
        feature: If set, the page is gated behind a per-tenant feature flag —
            when the flag is not live for the current tenant the page 404s
            (hidden, like an unknown route), independent of the user's roles.
            A tuple of flags opens the page when any one of them is live: a
            hub whose tabs belong to different features.
        telemetry_path: Page-view path recorded for engagement telemetry. Lets
            sibling routes that render the same page (e.g. ``/admin`` and
            ``/admin/{section}``) report under one stable path.
    """
    return _tenant_page(
        path,
        roles=roles,
        allow_tournament_membership=allow_tournament_membership,
        feature=feature,
        telemetry_path=telemetry_path,
        require_auth=True,
        **page_kwargs,
    )


def public_page(
    path: str,
    *,
    feature: Optional[FeatureFlag] = None,
    telemetry_path: Optional[str] = None,
    **page_kwargs,
):
    """Register a tenant page that renders for signed-out visitors.

    Same tenant resolution, tenant stash, feature gate, and page-view telemetry
    as :func:`protected_page` — it just never joins ``protected_routes``, so
    ``AuthMiddleware`` lets an anonymous request through instead of redirecting
    it to ``/login``. There is deliberately no ``roles`` argument: a page that
    authorizes anyone cannot also authorize a role.

    The page function must therefore tolerate ``user is None`` throughout —
    ``get_user_from_session`` returns ``None`` and every ``AuthService``
    predicate is ``False`` for an anonymous visitor, so signed-in affordances
    hide themselves, but any surface built this way must be safe to show the
    world.
    """
    return _tenant_page(
        path,
        feature=feature,
        telemetry_path=telemetry_path,
        require_auth=False,
        **page_kwargs,
    )


def protected_tab_page(base: str, **kwargs):
    """Register a tabbed hub page under both ``base`` and ``base/{section}``.

    The section slug lives in the path (``/admin/schedule``) rather than a query
    param; both routes render the same page function, which reads the ``section``
    slug and resolves it to the active tab. Two ``protected_page`` calls (rather
    than stacked decorators, which return the ``ui.page`` object, not the
    function) with a shared ``telemetry_path`` so both report under ``base``.
    """
    def deco(func):
        protected_page(base, telemetry_path=base, **kwargs)(func)
        protected_page(f'{base}/{{section}}', telemetry_path=base, **kwargs)(func)
        return func
    return deco


logger = logging.getLogger(__name__)

SESSION_HOST_KEY = 'session_host'


def session_host_matches(storage, host: Optional[str]) -> bool:
    """Whether a signed-in session may be used on ``host``, binding it on first use.

    Every host shares one signing secret, so a session cookie minted on a custom
    domain is just as valid on the platform host. Whoever runs a custom domain's
    DNS or TLS sees those cookies, and replayed on the platform host they act as
    that person in every community, a super-admin's included. Binding the session
    to the host it is first used on keeps a stolen cookie inside the domain the
    thief already controls.

    A session from before this binding existed has no host yet and takes the
    one it's used on next, rather than signing everyone out.
    """
    if not host:
        return True
    bound = storage.get(SESSION_HOST_KEY)
    if bound is None:
        storage[SESSION_HOST_KEY] = host
        return True
    return bound == host


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if app.storage.user.get('authenticated', False) and not session_host_matches(
            app.storage.user, effective_request_host(request.headers),
        ):
            # Ends the session everywhere, the host it belongs to included: a
            # cookie showing up on another host means it has been copied.
            logger.warning(
                'Session bound to %r presented on another host; signing it out',
                app.storage.user.get(SESSION_HOST_KEY),
            )
            app.storage.user.clear()
        # Enforce authentication only for routes that were explicitly marked protected
        if not app.storage.user.get('authenticated', False):
            # Under path mode TenantMiddleware has already stripped /t/<slug> into
            # root_path, so request.url.path is the unprefixed route; rebuild the
            # tenant-qualified referrer and /login target from root_path so a
            # path-mode login round-trips back to /t/<slug>/….
            path = request.url.path
            root_path = request.scope.get('root_path', '')
            if not path.startswith('/_nicegui') and _matches_protected_route(path):
                app.storage.user['referrer_path'] = referrer_for(root_path, path, request.url.query)
                return RedirectResponse(f'{root_path}/login')
        else:
            # Attach the logged-in user to Sentry so error events show who hit them.
            # Guarded on discord_id being present: str(None) would file the events
            # under a phantom 'None' user that aggregates unrelated reports.
            discord_id = app.storage.user.get('discord_id')
            user_id = app.storage.user.get('user_id')
            if discord_id is not None or user_id:
                sentry_sdk.set_user({
                    'id': str(discord_id) if discord_id is not None else f'user:{user_id}',
                    'username': app.storage.user.get('username'),
                })
        return await call_next(request)