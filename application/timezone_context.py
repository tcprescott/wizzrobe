"""Request-time display-timezone context — the primitive every rendered time reads.

Datetimes are stored UTC everywhere. *Which* wall clock a human sees them on is a
presentation concern that varies per viewer, so — exactly like the tenant id — it
is resolved once per page build and stashed in a :class:`~contextvars.ContextVar`
rather than threaded through every formatting call.

**Resolution is async and does DB reads; reading it back is sync and free.** The
resolver (:func:`application.services.TimezoneService.resolve`) runs once at page
build, where the tenant and the signed-in user are already loaded, and writes the
*answer* — a plain IANA name — here. ``application/utils/timezone.py`` then reads
it synchronously from ~390 formatting call sites at no cost.

**Why a contextvar and a fallback.** Same two-tier shape as
``application/tenant_context.py``, and for the same reason: the HTTP middleware
and the page builder only see the initial load. Once NiceGUI's websocket takes
over, every UI event handler runs *outside* any request and the contextvar is
gone. So resolution reads:

1. the contextvar (set at page build, and by :func:`tz_scope` for background code); else
2. the current NiceGUI client's per-connection stash (``app.storage.client['tz']``); else
3. nothing — and the caller falls back to :data:`FALLBACK_TIMEZONE`.

Anything running with neither — the Discord bot loop, workers, ``background_tasks``,
and every **shared-output** surface (cached spectator pages, channel-wide posts) —
must either bind a tz explicitly with :func:`tz_scope` or render in the tenant's
own timezone. A shared artifact must never inherit one viewer's clock.

This module is a peer of ``application/services`` (like ``application/events`` and
``application/tenant_context``) and is import-safe from every layer. It lazy-imports
NiceGUI only inside the fallback so it stays usable with no UI context.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from application.tenant_context import (
    client_stash,
    get_current_tenant_id,
    is_host_mode,
    reset_host_mode,
    reset_tenant_id,
    set_host_mode,
    set_tenant_id,
)

# Don't change: matches the app's historical hardcoded zone.
FALLBACK_TIMEZONE = 'America/New_York'

TZ_COOKIE = 'wiz_tz'

HOUR_CYCLE_COOKIE = 'wiz_hc'
HOUR_CYCLES = ('12', '24')

_tz_var: ContextVar[Optional[str]] = ContextVar('wizzrobe_display_tz', default=None)

_browser_tz_var: ContextVar[Optional[str]] = ContextVar('wizzrobe_browser_tz', default=None)

_hour12_var: ContextVar[Optional[bool]] = ContextVar('wizzrobe_hour12', default=None)
_browser_hour_cycle_var: ContextVar[Optional[str]] = ContextVar('wizzrobe_browser_hour_cycle', default=None)

# Also memoizes failures, so a bad stored name doesn't stat the tzdata tree per render.
_zone_cache: dict[str, ZoneInfo] = {}


def is_valid_timezone(name: Optional[str]) -> bool:
    """Whether ``name`` is a zone this system's tzdata can actually load.

    The validation gate for anything user- or admin-supplied. Names are stored as
    plain strings (a tenant config blob, a user column, a cookie), so every read
    path is defensive: an unknown zone degrades to the fallback rather than
    raising mid-render.
    """
    if not name or not isinstance(name, str):
        return False
    try:
        get_zone(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def get_zone(name: str) -> ZoneInfo:
    """A cached :class:`ZoneInfo` for ``name``. Raises if tzdata has no such zone."""
    zone = _zone_cache.get(name)
    if zone is None:
        zone = ZoneInfo(name)
        _zone_cache[name] = zone
    return zone


def set_timezone_name(name: Optional[str]) -> Token:
    """Bind the ambient display zone, returning a token for :func:`reset_timezone_name`."""
    return _tz_var.set(name)


def reset_timezone_name(token: Token) -> None:
    """Restore the zone the matching :func:`set_timezone_name` replaced."""
    _tz_var.reset(token)


def read_timezone_cookie(raw: Optional[str]) -> Optional[str]:
    """Decode a ``wiz_tz`` cookie value into an IANA zone name.

    The browser writes it with ``encodeURIComponent``, so ``America/New_York``
    arrives as ``America%2FNew_York`` — Starlette does not percent-decode cookie
    values, and an encoded name loads in no tzdata. Left undecoded it silently
    failed validation and every viewer fell through to the community default,
    which looks exactly like the browser tier not existing.

    Tolerates an already-plain value (``unquote`` is a no-op on one), so it does
    not matter whether a future writer encodes.
    """
    if not raw:
        return None
    return unquote(raw)


def set_browser_timezone(name: Optional[str]) -> Token:
    """Record the zone the browser reported, returning a token to reset it."""
    return _browser_tz_var.set(name)


def reset_browser_timezone(token: Token) -> None:
    """Restore the browser zone the matching :func:`set_browser_timezone` replaced."""
    _browser_tz_var.reset(token)


def get_browser_timezone() -> Optional[str]:
    """The zone the browser reported for this request, if any.

    Contextvar first (set per HTTP request from the cookie), then the per-client
    stash, so a websocket handler re-resolving a zone mid-connection still sees
    the device's own zone rather than dropping to the community default.
    """
    name = _browser_tz_var.get()
    if name is not None:
        return name
    stash = client_stash()
    return stash.get('browser_tz') if stash is not None else None


def stash_client_browser_timezone(name: Optional[str]) -> None:
    """Persist the browser-reported zone onto the current NiceGUI client."""
    try:
        from nicegui import app
    except Exception:
        return
    try:
        app.storage.client['browser_tz'] = name
    except Exception:
        pass


def _client_stash_timezone() -> Optional[str]:
    """The zone stashed on the current NiceGUI client, or None.

    Fully defensive, like ``tenant_context._client_stash_tenant_id``: returns None
    whenever NiceGUI is unavailable or there is no active client context (tests,
    workers, the bot loop), so resolution never raises just because the UI layer
    is absent.
    """
    stash = client_stash()
    return stash.get('tz') if stash is not None else None


def get_current_timezone_name() -> Optional[str]:
    """The resolved display zone, or None: contextvar first, then client stash."""
    name = _tz_var.get()
    if name is not None:
        return name
    return _client_stash_timezone()


def current_timezone_name() -> str:
    """The display zone to render in — always a usable IANA name.

    Never raises and never returns None: an unresolved or unloadable zone becomes
    :data:`FALLBACK_TIMEZONE`. Formatting a timestamp must not be able to fail,
    so this is deliberately total.
    """
    name = get_current_timezone_name()
    if is_valid_timezone(name):
        return name
    return FALLBACK_TIMEZONE


def current_timezone() -> ZoneInfo:
    """The resolved display zone as a :class:`ZoneInfo`."""
    return get_zone(current_timezone_name())


def stash_client_timezone(name: Optional[str]) -> None:
    """Persist the resolved zone onto the current NiceGUI client (page-build time).

    Read back by :func:`get_current_timezone_name` when a websocket UI event
    handler runs with the contextvar unset. No-op when there is no client context.
    Unlike the tenant stash this **does** write on every page build including the
    fallback value, because a connection's viewer can change zone (a re-login as
    someone else, an admin flipping the tenant pin) and a stale stash would
    outlive the change for the life of the connection.
    """
    try:
        from nicegui import app
    except Exception:
        return
    try:
        app.storage.client['tz'] = name
    except Exception:
        pass


def read_hour_cycle_cookie(raw: Optional[str]) -> Optional[str]:
    """``'12'`` or ``'24'`` from a ``wiz_hc`` cookie, or None for anything else."""
    return raw if raw in HOUR_CYCLES else None


def set_browser_hour_cycle(value: Optional[str]) -> Token:
    """Record the clock the browser reported, returning a token to reset it."""
    return _browser_hour_cycle_var.set(value)


def reset_browser_hour_cycle(token: Token) -> None:
    """Restore the value the matching :func:`set_browser_hour_cycle` replaced."""
    _browser_hour_cycle_var.reset(token)


def get_browser_hour_cycle() -> Optional[str]:
    """The clock the browser reported for this request: contextvar, then client stash."""
    value = _browser_hour_cycle_var.get()
    if value is not None:
        return value
    stash = client_stash()
    return stash.get('browser_hour_cycle') if stash is not None else None


def pick_hour12(user_hour_cycle: Optional[str], browser_hour_cycle: Optional[str]) -> bool:
    """Whether a viewer reads 12-hour times: their saved choice, else the browser's, else 12-hour.

    The last fallback covers a first visit before the browser has reported and a
    browser whose cookie is blocked, and matches what output with no viewer gets.
    """
    for value in (user_hour_cycle, browser_hour_cycle):
        if value in HOUR_CYCLES:
            return value == '12'
    return True


def bind_hour12(hour12: bool) -> None:
    """Make ``hour12`` the clock for this page build and this connection.

    Both halves for the same reason as the zone: the contextvar serves the rest
    of the request, the client stash every later websocket handler. The
    browser's own report is stashed too, so a Profile change back to Detect can
    re-resolve without a reload.
    """
    _hour12_var.set(hour12)
    try:
        from nicegui import app
    except Exception:
        return
    try:
        app.storage.client['hour12'] = hour12
        app.storage.client['browser_hour_cycle'] = _browser_hour_cycle_var.get()
    except Exception:
        pass


def _get_hour12() -> Optional[bool]:
    value = _hour12_var.get()
    if value is not None:
        return value
    stash = client_stash()
    return stash.get('hour12') if stash is not None else None


def uses_12_hour() -> bool:
    """Whether times render as ``9:30 PM`` rather than ``21:30``. Never raises."""
    value = _get_hour12()
    return True if value is None else value


@contextmanager
def hour_cycle_scope(hour12: Optional[bool]) -> Iterator[None]:
    """Bind the clock for a block; ``True`` forces 12-hour for output with no single viewer."""
    token = _hour12_var.set(hour12)
    try:
        yield
    finally:
        _hour12_var.reset(token)


@contextmanager
def tz_scope(name: Optional[str]) -> Iterator[None]:
    """Bind ``name`` as the ambient display zone for the duration of the block.

    Safe inside ``async`` code for the same reason :func:`tenant_scope` is: the
    contextvar is set/reset synchronously around the ``yield`` and propagates
    across ``await`` within the task.

    Use it wherever output is **not** for the current request's viewer:

    - a worker or bot rendering for a specific recipient → that recipient's zone
    - a cached or shared artifact (spectator brackets, a channel post) → the
      tenant's zone, via ``TimezoneService.tenant_timezone_name``

    Passing ``None`` deliberately unbinds, restoring fallback behaviour.
    """
    token = _tz_var.set(name)
    try:
        yield
    finally:
        _tz_var.reset(token)


@contextmanager
def pin_client_context() -> Iterator[None]:
    """Bind the tenant, host mode, zones and clock the current UI handler resolves.

    A websocket handler finds them through the NiceGUI client's slot stack, which
    is per asyncio task, so a child task (``asyncio.gather``) sees none of them
    and every tenant-scoped read raises. Contextvars *are* copied into child
    tasks, so pinning them here first carries the context across.
    """
    tokens = (
        set_tenant_id(get_current_tenant_id()),
        set_host_mode(is_host_mode()),
        _tz_var.set(get_current_timezone_name()),
        _browser_tz_var.set(get_browser_timezone()),
        _hour12_var.set(_get_hour12()),
        _browser_hour_cycle_var.set(get_browser_hour_cycle()),
    )
    try:
        yield
    finally:
        _browser_hour_cycle_var.reset(tokens[5])
        _hour12_var.reset(tokens[4])
        _browser_tz_var.reset(tokens[3])
        _tz_var.reset(tokens[2])
        reset_host_mode(tokens[1])
        reset_tenant_id(tokens[0])
