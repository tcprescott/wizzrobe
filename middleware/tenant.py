"""Tenant resolution middleware (path mode + host mode).

Every tenant is reachable at ``https://<platform>/t/<slug>/…``. In **path mode**
this middleware resolves the slug to a :class:`~models.Tenant`, sets the
request-time tenant context, and **rewrites the ASGI scope** so the app's single
set of unprefixed routes serve the request: ``/t/<slug>`` is stripped from
``scope['path']`` and appended to ``scope['root_path']``, so downstream routing
matches ``/admin`` while redirects and ``url_for`` built from ``root_path`` keep
the ``/t/<slug>`` prefix.

In **host mode** a request whose (normalized) ``Host`` matches a tenant's custom
``domain`` resolves to that tenant with the scope **left untouched**: the whole
host is the tenant, so the unprefixed routes already match and ``root_path``
stays empty (absolute links remain bare paths on-host). Host mode is checked
first and is authoritative for a custom domain — a stray ``/t/<other>`` there is
left literal and 404s, so one domain serves exactly one tenant. Path mode stays
authoritative on the platform host, where every tenant is reachable at
``/t/<slug>`` regardless of any domain. An unknown host falls through leniently
to the platform surface (a rate-limited warning flags a likely proxy misconfig).

Below the community, ``/e/<event>`` names one of its events
(``/t/<community>/e/<event>/…``, or ``/e/<event>/…`` on a custom domain). It is
stripped into ``root_path`` the same way, so every link a page builds stays
inside that event, and the event is bound beside the tenant
(``application/event_context.py``). A path with no ``/e/`` binds the
community's default event; the page decides whether to redirect to the default's
``/e/`` form (``middleware/event_routing.py``). An ``/e/<slug>`` the community
has no event for is left unrouted, so it 404s like any unknown page.

Requests with neither a matching custom host nor a ``/t/`` prefix run with **no**
tenant context: that is the platform surface (landing page, ``/platform``, the
shared OAuth callbacks). Tenant pages guard themselves — ``@protected_page``
returns 404 when reached with no tenant.

Transport/API paths (``/_nicegui``, ``/static``, ``/sw.js``, ``/api``) and the
logo route (``/brand-logo``, which names its tenant in the path) are
tenant-agnostic and skipped: the REST API derives its tenant from the bearer
token, and websocket/asset traffic carries no tenant of its own.

Ordering: added in ``frontend.py`` after ``AuthMiddleware`` so it wraps auth
(Starlette runs last-added outermost) — tenant context and the path rewrite
happen before authentication reads the (already-rewritten) path.
"""

import logging
import re
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware

from application.event_context import reset_event_id, set_event_id
from application.services.event_service import EventService
from application.services.tenant_service import TenantService
from application.tenant_context import (
    reset_host_mode,
    reset_tenant_id,
    set_host_mode,
    set_tenant_id,
)
from application.timezone_context import (
    HOUR_CYCLE_COOKIE,
    TZ_COOKIE,
    read_hour_cycle_cookie,
    read_timezone_cookie,
    reset_browser_hour_cycle,
    reset_browser_timezone,
    set_browser_hour_cycle,
    set_browser_timezone,
)
from application.utils.environment import get_platform_host
from application.utils.hostname import effective_request_host, normalize_hostname

logger = logging.getLogger(__name__)

UNKNOWN_COMMUNITY_SCOPE_KEY = 'wizzrobe.unknown_community'
EVENT_SLUG_SCOPE_KEY = 'wizzrobe.event_slug'
COMMUNITY_ROOT_SCOPE_KEY = 'wizzrobe.community_root'

_EVENT_PATH_RE = re.compile(r'^/e/(?P<slug>[a-z0-9][a-z0-9-]*)(?P<rest>/.*)?$')

_TENANT_PATH_RE = re.compile(r'^/t/(?P<slug>[a-z0-9][a-z0-9-]*)(?P<rest>/.*)?$')

# _nicegui_ws must stay before _nicegui so the alternation matches the longer segment.
_TENANT_TRANSPORT_RE = re.compile(
    r'^(?P<prefix>(?:/t/[a-z0-9][a-z0-9-]*)?(?:/e/[a-z0-9][a-z0-9-]*)?)'
    r'(?P<rest>/(?:_nicegui_ws|_nicegui|static)(?:/.*)?|/sw\.js)$'
)

_EXCLUDED_PREFIXES = ('/_nicegui', '/static', '/api', '/brand-logo/')
_EXCLUDED_EXACT = ('/sw.js',)


def _is_excluded(path: str) -> bool:
    return path in _EXCLUDED_EXACT or path.startswith(_EXCLUDED_PREFIXES)


_warned_hosts: dict[str, None] = {}
_WARNED_MAX = 256


def _warn_unresolved_host(host: str) -> None:
    if host in _warned_hosts:
        return
    if len(_warned_hosts) >= _WARNED_MAX:
        _warned_hosts.pop(next(iter(_warned_hosts)), None)
    _warned_hosts[host] = None
    logger.warning(
        'Request Host %r is neither the platform host nor a known tenant domain; '
        'serving the platform surface. If %r is a configured custom domain, the '
        'reverse proxy is likely not forwarding Host verbatim (set '
        'TRUST_FORWARDED_HOST behind a trusted proxy, or forward Host).',
        host, host,
    )


def _strip_prefix(scope, prefix: str, rest: str) -> None:
    """Move ``prefix`` off the front of the path into ``root_path``.

    ``raw_path`` is the *undecoded* bytes by contract, so the prefix comes off
    the original raw bytes rather than re-encoding the already-decoded ``rest``
    (which would drop percent-encoding in the remainder of the path). Slugs are
    ``[a-z0-9-]``, never encoded, so the prefix matches the raw bytes verbatim.
    """
    scope['path'] = rest
    scope['root_path'] = scope.get('root_path', '') + prefix
    raw = scope.get('raw_path')
    if raw is not None:
        raw_path_part, sep, raw_query = raw.partition(b'?')
        raw_prefix = prefix.encode()
        if raw_path_part.startswith(raw_prefix):
            new_path_part = raw_path_part[len(raw_prefix):] or b'/'
        else:
            new_path_part = rest.encode()
        scope['raw_path'] = new_path_part + (sep + raw_query if sep else b'')


async def _bind_event_path(scope, tenant_id: int) -> Optional[int]:
    """Resolve the event the (community-local) path names and return its id.

    ``/e/<slug>/…`` is stripped into ``root_path``. A bare path gets the
    community's default event. An unknown slug leaves the path as it is, so no
    route matches and it 404s, with the default bound for the error page.
    """
    scope[COMMUNITY_ROOT_SCOPE_KEY] = scope.get('root_path', '')
    scope[EVENT_SLUG_SCOPE_KEY] = None
    match = _EVENT_PATH_RE.match(scope.get('path', '/'))
    if match is None:
        return await EventService.default_event_id(tenant_id)
    event = await EventService.ref_by_slug(tenant_id, match.group('slug'))
    if event is None:
        return await EventService.default_event_id(tenant_id)
    _strip_prefix(scope, f'/e/{event.slug}', match.group('rest') or '/')
    scope[EVENT_SLUG_SCOPE_KEY] = event.slug
    return event.id


class TransportPrefixMiddleware:
    """Strip the ``/t/<slug>`` (and ``/e/<slug>``) prefix off tenant-agnostic transport paths.

    NiceGUI derives its client asset + websocket URL prefix from the page's
    ``root_path`` (``/t/<slug>`` in path mode), so a tenant page tells the browser
    to load ``/t/<slug>/_nicegui/…`` and open ``/t/<slug>/_nicegui_ws/…``. Those
    resources are shared, served by the app's *unprefixed* routes, so the prefix
    must be removed — with ``root_path`` left empty, exactly like a bare-host
    asset request (a non-empty ``root_path`` makes NiceGUI's static route 404).

    This is a **pure ASGI** middleware, not ``BaseHTTPMiddleware``, so it also
    handles the ``websocket`` scope — ``BaseHTTPMiddleware`` silently passes
    websockets through untouched, which is why the socket.io connection to
    ``/t/<slug>/_nicegui_ws/…`` would otherwise never be stripped and 404. It
    sets no tenant context (assets carry no tenant; the UI's tenant is resolved
    from the per-client stash written at page build).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            match = _TENANT_TRANSPORT_RE.match(scope.get('path', ''))
            if match is not None and match.group('prefix'):
                scope = dict(scope)
                scope['path'] = match.group('rest')
                raw = scope.get('raw_path')
                if raw is not None:
                    raw_head, sep, raw_query = raw.partition(b'?')
                    prefix = match.group('prefix').encode()
                    if raw_head.startswith(prefix):
                        raw_head = raw_head[len(prefix):]
                    scope['raw_path'] = raw_head + (sep + raw_query if sep else b'')
        await self.app(scope, receive, send)


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Bind on every path, platform included and before the tenant is known: it's the browser's zone.
        tz_token = set_browser_timezone(read_timezone_cookie(request.cookies.get(TZ_COOKIE)))
        hc_token = set_browser_hour_cycle(read_hour_cycle_cookie(request.cookies.get(HOUR_CYCLE_COOKIE)))
        try:
            return await self._dispatch(request, call_next)
        finally:
            reset_browser_hour_cycle(hc_token)
            reset_browser_timezone(tz_token)

    async def _dispatch(self, request, call_next):
        scope = request.scope
        path = scope.get('path', '/')

        if _is_excluded(path):
            return await call_next(request)

        # Host mode must run before path mode, so a /t/<other> on a custom domain stays unrouted.
        platform_host = normalize_hostname(get_platform_host()) or get_platform_host()
        host = effective_request_host(request.headers)
        if host and host != platform_host:
            tenant = await TenantService.get_by_domain(host)
            if tenant is not None and tenant.is_active:
                tenant_token = set_tenant_id(tenant.id)
                event_token = set_event_id(await _bind_event_path(scope, tenant.id))
                host_token = set_host_mode(True)
                try:
                    return await call_next(request)
                finally:
                    reset_host_mode(host_token)
                    reset_event_id(event_token)
                    reset_tenant_id(tenant_token)
            # Fall through, don't 404: deactivating one tenant's domain must not block path mode on this host.
            if tenant is None and _TENANT_PATH_RE.match(path) is None:
                _warn_unresolved_host(host)

        match = _TENANT_PATH_RE.match(path)
        if match is None:
            return await call_next(request)

        slug = match.group('slug')
        tenant = await TenantService.get_by_slug(slug)
        if tenant is None or not tenant.is_active:
            # Leave unrouted for the themed 404; a bare Response had no Content-Type and Chrome downloaded it.
            scope[UNKNOWN_COMMUNITY_SCOPE_KEY] = True
            return await call_next(request)

        _strip_prefix(scope, f'/t/{slug}', match.group('rest') or '/')

        token = set_tenant_id(tenant.id)
        event_token = set_event_id(await _bind_event_path(scope, tenant.id))
        try:
            return await call_next(request)
        finally:
            reset_event_id(event_token)
            reset_tenant_id(token)
