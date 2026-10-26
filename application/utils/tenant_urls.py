"""Pure helpers for building and validating tenant-qualified URL paths.

Path-mode addressing prefixes every tenant route with ``/t/<slug>`` (surfaced to
request handlers as ``root_path``). These helpers keep post-login / OAuth return
targets inside the originating tenant so a shared-cookie login can't bounce a
user into a *different* community whose stale referrer is still in the session.

Pure (no NiceGUI / session / DB) so they unit-test in isolation; the presentation
layer reads the pending referrer from ``app.storage.user`` and passes it in.
"""

from typing import Any, Optional, Sequence
from urllib.parse import quote, unquote

from application.utils.environment import get_base_url
from application.utils.hostname import normalize_hostname, scheme_for_host

# Routes that must never be used as a post-login return target (they would loop).
AUTH_ROUTES: tuple[str, ...] = ('/login', '/logout', '/oauth/callback', '/login/discord', '/password-login')

# A return path is a URL a person was looking at; anything longer is not one.
_MAX_RETURN_PATH = 2048


def safe_local_path(path: Any, *, auth_routes: Sequence[str] = AUTH_ROUTES) -> Optional[str]:
    """``path`` when it is a plain same-origin path (with optional query), else ``None``.

    The gate for a return target that arrived from outside — the ``next`` a
    sign-in button carries — and the backstop for the one ``AuthMiddleware``
    stores. Everything a browser could resolve to another origin is refused: a
    relative path, a protocol-relative ``//evil``, the backslash form ``/\\evil``
    (browsers normalise ``\\`` to ``/``), a control or whitespace character, and
    a scheme. Dot segments are refused too, including the ``%2e`` spelling
    browsers decode: ``/t/a/../b`` is same-origin but walks into another
    community, which is the one thing a tenant-local path must not do. An auth
    route would loop.
    """
    if not isinstance(path, str) or not path or len(path) > _MAX_RETURN_PATH:
        return None
    if not path.startswith('/') or path.startswith('//'):
        return None
    if '\\' in path or any(ord(c) < 0x21 or ord(c) == 0x7f for c in path):
        return None
    route = path.split('?', 1)[0].split('#', 1)[0]
    decoded = unquote(route)
    if decoded.startswith('//') or '\\' in decoded:
        return None
    if any(segment in ('.', '..') for segment in decoded.split('/')):
        return None
    if route in auth_routes:
        return None
    return path


def login_path(return_to: Any) -> str:
    """The tenant-local ``/login`` URL that comes back to ``return_to`` afterwards.

    ``return_to`` is the tenant-local path (and query) the reader is on. An
    unsafe one is dropped rather than carried, so the worst a bad value does is
    land the reader on the community home.
    """
    local = safe_local_path(return_to)
    if local is None or local == '/':
        return '/login'
    return f"/login?next={quote(local, safe='')}"


def safe_next(path: Any) -> str:
    """A safe same-host absolute return path for a cross-host handoff, or ``/``.

    The one gate is :func:`safe_local_path`; this only supplies the ``/``
    fallback the handoffs need. Shared by the Discord login handoff
    (``pages/auth.py``: ``/login``, ``/oauth/start``, ``/session/claim``) and
    the secondary-provider link handoff (``pages/_oauth_link.py``), so a
    ``next`` carried across a host can never be looser than one that stays.
    """
    return safe_local_path(path) or '/'


def tenant_base_url(tenant: Any) -> str:
    """The tenant's canonical absolute base URL (no trailing slash).

    A tenant with a custom ``domain`` is canonically reached there
    (``https://foo.gg``); otherwise it lives under the platform host's path-mode
    prefix (``{BASE_URL}/t/<slug>``). Use this for **outbound deep links** — QR
    codes, web-push ``navigate`` targets, Discord DM links — that are consumed
    outside any request context and so must be absolute and self-contained.
    """
    domain = getattr(tenant, 'domain', None)
    if domain:
        return f'{scheme_for_host(domain)}://{domain}'
    return f'{get_base_url()}/t/{tenant.slug}'


def encoded_host_mismatch(canonical_url: Any, browsing_host: Any) -> Optional[str]:
    """The canonical host, when it is not the host the operator is browsing.

    A printed QR label encodes an absolute link built from the tenant's
    canonical base (:func:`tenant_base_url`), which on a path-mode tenant comes
    from ``BASE_URL``. If ``BASE_URL`` is stale, thirty labels come off the
    printer encoding a host nobody can reach — and the mismatch only surfaces at
    the venue, with a phone and a cable.

    Compare against the **tenant's canonical base**, never ``BASE_URL`` itself:
    on a custom-domain tenant the encoded host is *supposed* to differ from the
    platform's. Both sides go through :func:`normalize_hostname`, so case, a
    default ``:80``/``:443``, a trailing dot and a scheme/path never produce a
    spurious warning. Returns ``None`` when they match, or when either side is
    missing — an absent ``Host`` header must not read as a misconfiguration.
    """
    canonical = normalize_hostname(canonical_url)
    browsing = normalize_hostname(browsing_host)
    if canonical is None or browsing is None:
        return None
    return canonical if canonical != browsing else None


def tenant_url(tenant: Any, path: str) -> str:
    """:func:`tenant_base_url` joined to an absolute in-app ``path`` (leading ``/``)."""
    return f'{tenant_base_url(tenant)}{path}'


def event_path(slug: Optional[str], path: str) -> str:
    """``path`` inside event ``slug`` (``/e/<slug>/…``), or ``path`` as-is without one."""
    return f'/e/{slug}{path}' if slug else path


def tenant_home(root_path: str) -> str:
    """The current tenant's home path — ``/t/<slug>/`` in path mode, ``/`` bare."""
    return f'{root_path}/'


def strip_root_path(root_path: Any, path: Any) -> str:
    """``path`` made **tenant-local**, for handing to a client-side navigate.

    ``ui.navigate.to`` is client-side, and ``nicegui.js`` unconditionally prepends
    the client's ``options.prefix`` (``X-Forwarded-Prefix`` + ``root_path``, i.e.
    ``/t/<slug>`` in path mode) to any absolute path::

        open: (msg) => { const url = msg.path.startsWith("/") ? options.prefix + msg.path : msg.path; ... }

    So a navigate issued from a page served under ``/t/<slug>`` must be given the
    path *without* that prefix, or it lands on ``/t/<slug>/t/<slug>/…``. This
    strips ``root_path`` when ``path`` sits under it and returns ``path``
    unchanged otherwise (host mode, the bare platform host, or a path belonging
    to somewhere else). Pure.

    Do **not** use it for an HTTP ``RedirectResponse`` — the browser resolves
    those against the origin, nothing prepends a prefix, and a stripped path
    would leave the tenant.
    """
    if not isinstance(path, str) or not path:
        return '/'
    if not isinstance(root_path, str) or not root_path:
        return path
    if path == root_path:
        return '/'
    if path.startswith(root_path + '/'):
        return path[len(root_path):]
    return path


def sanitize_return_path(
    root_path: str,
    referrer: Any,
    *,
    auth_routes: Sequence[str] = AUTH_ROUTES,
) -> str:
    """Where to send a user after login for the tenant at ``root_path``.

    Honors ``referrer`` only if it is a string that belongs to this tenant (shares
    its ``root_path`` prefix) and is not itself an auth route; otherwise returns
    the tenant home. ``root_path`` is ``''`` on the bare platform host.
    """
    home = tenant_home(root_path)
    if not isinstance(referrer, str) or not referrer:
        return home
    if root_path and not (referrer == root_path or referrer.startswith(root_path + '/')):
        return home
    local = strip_root_path(root_path, referrer)
    if safe_local_path(local, auth_routes=auth_routes) is None:
        return home
    return referrer


def return_path_for_login(root_path: str, next_path: Any, referrer: Any) -> str:
    """The post-login target for a ``/login`` request on the tenant at ``root_path``.

    ``next_path`` is the tenant-local ``?next=`` a sign-in button carried from
    the page the reader was on. It wins when it is safe, because it names the
    page they pressed the button on; a stale ``referrer`` in the session names
    whatever protected page last bounced them. Otherwise the referrer goes
    through :func:`sanitize_return_path` as before.
    """
    local = safe_local_path(next_path)
    if local is not None:
        return f'{root_path}{local}'
    return sanitize_return_path(root_path, referrer)
