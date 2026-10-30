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

from application.event_context import event_scope, get_current_event_id, stash_client_event_id
from application.services.auth_service import AuthService, RoleScope, get_user_from_session
from application.services.event_service import EventService
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
from middleware.event_routing import PAGE_SCOPE_KEY, PageScope, default_event_redirect, page_request
from models import FeatureFlag, Role

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


async def can_view_bound_event(user=None) -> bool:
    """Whether the viewer may open the event this page is bound to.

    A draft is for staff and the people holding a role in it until it goes live.
    ``user`` is loaded from the session when not passed; only a draft needs it.
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return True
    event = await EventService.ref(tenant_id, get_current_event_id())
    if event is None or not event.is_draft:
        return True
    if user is None:
        user = await get_user_from_session(app.storage.user)
    return await EventService.can_view(user, event)


async def _run_in_tenant(tenant_id, coro, event_id=None) -> None:
    """Await a deferred (background-task) coroutine with a tenant bound.

    Page-view telemetry is captured in a background task, which runs outside the
    request and so loses the contextvar — capture the tenant (and the page's
    event) at page-build time and rebind them here so the row is stamped."""
    with tenant_scope(tenant_id), event_scope(event_id, tenant_id):
        await coro

_MAX_TRACKED_PARAMS = 15
_MAX_PARAM_LEN = 120
# Matched on the param name so a new route is redacted without opting in.
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
                get_current_event_id(),
            )
        )
    except Exception:
        pass

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
    if user is not None:
        memberships = TenantMembershipService()
        why = None
        if await memberships.join_openly(user, tenant_id):
            why = 'it’s open to anyone who signs in'
        elif await memberships.join_via_discord(user, tenant_id):
            why = 'you’re in its Discord server'
        if why is not None:
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
    scope: PageScope = 'event',
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
        if require_auth:
            protected_routes.add(path)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            request = page_request()
            if request is not None:
                request.scope[PAGE_SCOPE_KEY] = scope
            if scope == 'event':
                redirect = await default_event_redirect(request)
                if redirect is not None:
                    return redirect

            # Must run before any auth short-circuit, so gated and anonymous loads are counted.
            record_page_view(view_path, kwargs)

            tid = get_current_tenant_id()
            if tid is None:
                await _render_community_chooser()
                return
            stash_client_tenant_id(tid)
            stash_client_event_id(get_current_event_id())
            stash_client_host_mode(is_host_mode())

            # Bind the display clock before anything renders, the 404/403/join pages included.
            tz_settings = await TimezoneService.get_settings(tid)
            browser_tz = get_browser_timezone()
            stash_client_browser_timezone(browser_tz)
            _bind_display_timezone(
                TimezoneService.pick(tz_settings, user=None, browser_timezone=browser_tz)
            )

            # Feature gate must run before the role gate: a disabled feature 404s for everyone.
            if flags and not any([await FeatureFlagService().is_enabled(f) for f in flags]):
                # Must match the unknown-route page exactly, or it reveals the feature exists.
                from theme.error_page import render_not_found
                await render_not_found(
                    user=await get_user_from_session(app.storage.user),
                )
                return

            user = None
            is_super_admin = False
            if require_auth:
                user = await get_user_from_session(app.storage.user)
                is_super_admin = await AuthService.is_super_admin(user)
                _bind_display_timezone(
                    TimezoneService.pick(tz_settings, user=user, browser_timezone=browser_tz), user,
                )
                if await enforce_membership(tid, user, is_super_admin=is_super_admin):
                    return

            if not await can_view_bound_event(user if require_auth else None):
                from theme.error_page import render_not_found
                await render_not_found(user=user)
                return

            if gated:
                if user is None and not require_auth:
                    user = await get_user_from_session(app.storage.user)
                    is_super_admin = await AuthService.is_super_admin(user)
                allowed = is_super_admin
                if not allowed and user is not None and role_list:
                    held = await AuthService.get_roles(user, RoleScope.LIVE if scope == 'community' else None)
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
    scope: PageScope = 'event',
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
        scope: ``'event'`` (the default) for a page that shows one event: a
            bare link redirects to the default event's ``/e/`` path.
            ``'row'`` for a page about one row, which redirects to its row's
            event itself (``middleware.event_routing.row_event_redirect``).
            ``'community'`` for a page that belongs to the whole community and
            never redirects.
    """
    return _tenant_page(
        path,
        roles=roles,
        allow_tournament_membership=allow_tournament_membership,
        feature=feature,
        telemetry_path=telemetry_path,
        require_auth=True,
        scope=scope,
        **page_kwargs,
    )


def public_page(
    path: str,
    *,
    feature: Optional[FeatureFlag] = None,
    telemetry_path: Optional[str] = None,
    scope: PageScope = 'event',
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
        scope=scope,
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
            logger.warning(
                'Session bound to %r presented on another host; signing it out',
                app.storage.user.get(SESSION_HOST_KEY),
            )
            app.storage.user.clear()
        if not app.storage.user.get('authenticated', False):
            # Path mode: TenantMiddleware moved /t/<slug> into root_path, so rebuild URLs from it.
            path = request.url.path
            root_path = request.scope.get('root_path', '')
            if not path.startswith('/_nicegui') and _matches_protected_route(path):
                app.storage.user['referrer_path'] = referrer_for(root_path, path, request.url.query)
                return RedirectResponse(f'{root_path}/login')
        else:
            # Guard on discord_id: str(None) would file events under a phantom 'None' user.
            discord_id = app.storage.user.get('discord_id')
            user_id = app.storage.user.get('user_id')
            if discord_id is not None or user_id:
                sentry_sdk.set_user({
                    'id': str(discord_id) if discord_id is not None else f'user:{user_id}',
                    'username': app.storage.user.get('username'),
                })
        return await call_next(request)