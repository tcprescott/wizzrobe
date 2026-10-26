"""Environment detection and startup security-config validation.

Centralizes the "are we in production?" check and the fail-fast validation of
security-critical configuration (session secret, DB credentials) so the app
refuses to start in an insecure state rather than silently degrading.
"""

import os

_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})


def env_flag(name: str, default: bool = False) -> bool:
    """Return the boolean value of environment variable ``name``.

    The single canonical truthy-env grammar: a value is true iff, once
    stripped and lowercased, it is one of ``1``/``true``/``yes``/``on``.
    An unset variable yields ``default``; any other value is false.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def get_environment() -> str:
    """Return the configured environment name (default: 'development')."""
    return os.environ.get('ENVIRONMENT', 'development').strip().lower()


def is_production() -> bool:
    return get_environment() == 'production'


def get_base_url() -> str:
    """Return the app's external base URL (no trailing slash).

    Single source of truth for links, QR codes, and OAuth redirect building —
    read lazily so tests and tooling can override BASE_URL per call.
    """
    return os.getenv('BASE_URL', 'http://localhost:8000').rstrip('/')


def get_platform_host() -> str:
    """Return the shared platform host (bare ``host[:port]``, no scheme/path).

    This is the host that serves the tenant-agnostic surface (landing page,
    ``/platform``) and every path-mode tenant at ``/t/<slug>``. Defaults to the
    network location of :func:`get_base_url` when ``PLATFORM_HOST`` is unset, so a
    single ``BASE_URL`` configures both in the common single-host deployment.
    Read lazily so tests can override per call.
    """
    explicit = (os.getenv('PLATFORM_HOST') or '').strip()
    if explicit:
        return explicit.lower()
    from urllib.parse import urlparse
    return (urlparse(get_base_url()).netloc or 'localhost:8000').lower()


def host_oauth_handoff_enabled() -> bool:
    """Whether custom-domain login uses the Design B signed-handoff (default: off).

    Off (default) is Design A — host-local Discord login, where each custom
    domain's ``/oauth/callback`` must be a registered Discord redirect URI. Set
    ``HOST_OAUTH_MODE=handoff`` to switch to Design B: OAuth always completes on
    the platform host (one registered URI regardless of domain count) and a
    short-lived signed token hands the session to the custom domain. Flip this
    once the number of custom domains approaches the provider's redirect-URI
    ceiling (see docs/features/multitenancy.md).
    """
    return (os.environ.get('HOST_OAUTH_MODE') or '').strip().lower() == 'handoff'


def telemetry_enabled() -> bool:
    """Whether engagement telemetry capture is on (default: on).

    A kill-switch for the behavioral capture path (page views, interactions,
    and the domain-event mirror). Set ``TELEMETRY_ENABLED`` to a falsey value
    (``0``/``false``/``no``/``off``) to disable capture without a redeploy of
    code — reads are unaffected, they just show whatever was already recorded.
    """
    raw = os.environ.get('TELEMETRY_ENABLED')
    if raw is None:
        return True
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def racetime_bot_enabled() -> bool:
    """Master switch for the racetime bot runtime (default: off).

    The connection loop only spins up when ``RACETIME_BOT_ENABLED`` is truthy.
    Off by default so a deployment without configured racetime bots — the common
    case — never opens outbound connections. Independent of ``MOCK_RACETIME``:
    the switch says "run the runtime", the mock flag says "run it against a
    scripted fake instead of live racetime".
    """
    return os.environ.get('RACETIME_BOT_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


def loop_watchdog_enabled() -> bool:
    """Whether the event-loop watchdog thread runs (default: on).

    ``LOOP_WATCHDOG_ENABLED=false`` is a kill switch, not a tuning knob: the
    watchdog is one thread waking four times a second.
    """
    return os.environ.get('LOOP_WATCHDOG_ENABLED', '').strip().lower() not in ('0', 'false', 'no', 'off')


def speedgaming_sync_enabled() -> bool:
    """Master switch for the SpeedGaming ETL sync worker (default: off).

    The background poll loop only spins up when ``SPEEDGAMING_SYNC_ENABLED`` is
    truthy. Off by default so a deployment with no configured SG event links —
    the common case — never opens outbound polls. Independent of
    ``MOCK_SPEEDGAMING``: the switch says "run the worker", the mock flag says
    "run it against scripted fixtures instead of the live SG API".
    """
    return os.environ.get('SPEEDGAMING_SYNC_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


def discord_events_sync_enabled() -> bool:
    """Master switch for the Discord Scheduled Events reconciler worker (default: off).

    The background reconcile loop only spins up when ``DISCORD_EVENTS_SYNC_ENABLED``
    is truthy. Off by default so a deployment with no opted-in tournaments — the
    common case — never touches Discord on a timer. The reconciler still runs
    on-demand from the admin UI regardless of this switch; this only gates the
    periodic worker. Independent of ``MOCK_DISCORD`` (which swaps the transport).
    """
    return os.environ.get('DISCORD_EVENTS_SYNC_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


def service_health_alert_dm_enabled() -> bool:
    """Whether an unhealthy-transition alert also DMs super-admins (default: off).

    Health transitions into ``down``/credential-warning always publish an event
    and capture to Sentry; this opt-in (``SERVICE_HEALTH_ALERT_DM``) additionally
    Discord-DMs every super-admin so a deployment can choose the noisier channel.
    """
    return os.environ.get('SERVICE_HEALTH_ALERT_DM', '').strip().lower() in ('1', 'true', 'yes', 'on')


def session_storage_url() -> str:
    """Return the configured Redis URL for NiceGUI session storage, or ''.

    ``NICEGUI_REDIS_URL`` is read by NiceGUI itself (``nicegui.storage.Storage``
    picks its backend from the environment at import time); this reads the same
    variable so startup can say which backend is live and refuse a broken one.
    """
    return (os.environ.get('NICEGUI_REDIS_URL') or '').strip()


def validate_session_storage() -> None:
    """Fail fast when Redis session storage is configured but unusable.

    Without this, a typo'd or unreachable ``NICEGUI_REDIS_URL`` is not caught at
    startup — NiceGUI constructs its ``RedisPersistentDict`` lazily, so the
    first symptom is a login failing in production. The reachability probe is
    deliberately synchronous and one-shot: it runs before the app serves
    anything, and a Redis that is down at boot is a configuration problem, not a
    transient to retry through.
    """
    url = session_storage_url()
    if not url:
        return

    try:
        import redis  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError(
            'NICEGUI_REDIS_URL is set but the `redis` package is not installed. '
            'Install it (it ships as a dependency) or unset the variable to fall '
            'back to file-backed session storage.'
        ) from exc

    from redis import Redis
    from redis.exceptions import RedisError

    try:
        client = Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        try:
            client.ping()
        finally:
            client.close()
    except RedisError as exc:
        raise RuntimeError(
            f'NICEGUI_REDIS_URL is set but Redis is not reachable: {exc}. Sessions '
            'would fail at login, so startup is aborted rather than degraded.'
        ) from exc


def validate_security_config() -> None:
    """Fail fast when security-critical configuration is missing.

    Always requires STORAGE_SECRET (it signs the session that the entire
    authorization model trusts). In production it additionally requires
    non-empty DB credentials. Raising here aborts startup before any request
    can be served with an insecure session store.
    """
    storage_secret = (os.environ.get('STORAGE_SECRET') or '').strip()
    if not storage_secret:
        raise RuntimeError(
            'STORAGE_SECRET is required: it signs the NiceGUI session store that '
            'holds authentication state. Set a strong random value.'
        )

    if is_production():
        if len(storage_secret) < 32:
            raise RuntimeError(
                'STORAGE_SECRET must be at least 32 characters in production: it '
                'signs the session store the entire authorization model trusts. '
                'Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.'
            )
        if not (os.environ.get('DB_USERNAME') or '').strip():
            raise RuntimeError('DB_USERNAME must be set in production.')
        if not (os.environ.get('DB_PASSWORD') or '').strip():
            raise RuntimeError('DB_PASSWORD must be set in production.')
