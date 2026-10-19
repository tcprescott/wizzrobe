"""Sentry error-monitoring initialization.

Wires the Sentry Python SDK into the application. Initialization is a no-op
unless ``SENTRY_DSN`` is set, so local development and tests are unaffected.

The SDK auto-instruments FastAPI/Starlette and the stdlib ``logging`` module,
so once initialized it captures unhandled exceptions across the request path,
the NiceGUI pages, and the Discord bot (all run in the same process), plus any
``logger.error``/``logger.exception`` records emitted anywhere in the codebase.

Four things on top of the SDK defaults make an event diagnosable mid-event:

* **Release** (``SENTRY_RELEASE``, else ``GIT_SHA`` baked into the image) so a
  regression points at the deploy that introduced it.
* **Sentry Logs** at INFO and up (``SENTRY_LOGS_LEVEL``), so the lines around an
  error are readable in Sentry without shell access to the container. DEBUG and
  TRACE, which a super-admin can switch on per module from ``/platform``, stay
  in the container log.
* **Metrics** (``SENTRY_METRICS``, default on): process load, per-route request
  counts and timings, and worker tick times; see :mod:`application.utils.sentry_metrics`.
* **Ambient tags** on every event and log: the tenant in scope and, inside a
  NiceGUI websocket handler where the request-scoped user is gone, the signed-in
  user. Workers, queues and bot handlers add their own with :func:`sentry_tags`.

The browser reports to the same project through the JavaScript SDK
(:func:`browser_sdk_config`, ``static/js/sentry-browser.js``). Server events
carry ``surface: server`` and browser ones ``surface: browser`` so either side
filters out cleanly, and a server error raised by a tap names that tab's
session replay, so the issue links to a recording of what was tapped.
"""

import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger_for_sentry_logs
from uvicorn.protocols.utils import ClientDisconnected

from application.tenant_context import client_stash, get_current_tenant_id
from application.utils import browser_monitoring, sentry_metrics
from application.utils.environment import get_environment, is_production
from application.utils.log_levels import level_number
from application.utils.timer_teardown import is_timer_teardown_race

logger = logging.getLogger(__name__)

# Request headers that must never leave the process in an error report.
_SENSITIVE_HEADERS = {'authorization', 'cookie', 'set-cookie', 'x-api-key'}

#: Production's default share of requests traced when SENTRY_TRACES_SAMPLE_RATE
#: is unset: enough to see slow pages and slow DB/HTTP calls at event load.
_PRODUCTION_TRACES_SAMPLE_RATE = 0.1

#: One line per HTTP request or outbound call: useful in the container log,
#: pure quota burn in Sentry Logs.
_NOISY_FOR_SENTRY_LOGS = ('uvicorn.access', 'httpx', 'httpcore')

#: uvicorn's wording both for its "Cancel N running task(s)" error and for the
#: ``CancelledError`` it throws into each request still open at the deadline.
_GRACEFUL_SHUTDOWN_CANCEL = 'timeout graceful shutdown exceeded'

#: How long the lifespan shutdown waits for queued events and logs to ship.
_SHUTDOWN_FLUSH_TIMEOUT = 2.0

#: Where theme/browser_monitoring.py keeps the tab's replay id in ``app.storage.client``.
REPLAY_ID_KEY = 'sentry_replay_id'


#: engine.io's ERRORs caused by the client, not the server: a poll naming a
#: session the server no longer holds (a tab left open across a deploy, a phone
#: waking from sleep), which the browser recovers from on its own, and a
#: crawler GETting the socket URL without upgrade headers (SGLMAN-J). Nobody can
#: act on either report.
_STALE_SESSION_PREFIXES = ('Invalid session ', "'Session is disconnected' ", 'Invalid websocket upgrade')


class StaleSocketSessionFilter(logging.Filter):
    """Downgrades engine.io's client-caused errors to a WARNING.

    Sentry SGLMAN-13/14/J. Still in the container log and Sentry Logs, so a burst
    after a deploy stays visible, but no longer opens an issue.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR and record.getMessage().startswith(_STALE_SESSION_PREFIXES):
            record.levelno = logging.WARNING
            record.levelname = logging.getLevelName(logging.WARNING)
        return True


def install_stale_session_filter() -> None:
    """Attach the filter to the ``engineio.server`` logger once; safe to call repeatedly."""
    engineio_logger = logging.getLogger('engineio.server')
    if not any(isinstance(f, StaleSocketSessionFilter) for f in engineio_logger.filters):
        engineio_logger.addFilter(StaleSocketSessionFilter())


def _scrub_event(event: Dict[str, Any], hint: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip auth headers and cookies from outgoing Sentry events.

    Defense in depth alongside ``send_default_pii=False``: ensures bearer
    tokens / session cookies are never transmitted even if an integration
    attaches request data.
    """
    request = event.get('request')
    if isinstance(request, dict):
        headers = request.get('headers')
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in _SENSITIVE_HEADERS:
                    headers[name] = '[Filtered]'
        request.pop('cookies', None)
    return event


def _session_user() -> Optional[Dict[str, Any]]:
    """The signed-in user from the NiceGUI session, or None outside one.

    ``AuthMiddleware`` sets the Sentry user on the HTTP request's scope, but a
    button click arrives over the websocket after that scope is gone, so an
    error in a UI handler would otherwise carry no user at all.
    """
    try:
        from nicegui import app
        storage = app.storage.user
        # A password-only account has no Discord id; its account id stands in.
        ident = storage.get('discord_id') or (
            f"user:{storage['user_id']}" if storage.get('user_id') else None)
    except Exception:
        return None
    if ident is None:
        return None
    return {'id': str(ident), 'username': storage.get('username')}


def _session_replay_id() -> Optional[str]:
    """The browser replay recording this tab, when a websocket handler raised."""
    stash = client_stash()
    return stash.get(REPLAY_ID_KEY) if stash is not None else None


def _enrich_event(event: Dict[str, Any]) -> Dict[str, Any]:
    tags = event.get('tags')
    if tags is None:
        tags = event['tags'] = {}
    if isinstance(tags, dict):
        tags.setdefault('surface', 'server')
        tenant_id = get_current_tenant_id()
        if tenant_id is not None:
            tags.setdefault('tenant_id', str(tenant_id))
    if not event.get('user'):
        user = _session_user()
        if user is not None:
            event['user'] = user
    replay_id = _session_replay_id()
    if replay_id:
        event.setdefault('contexts', {}).setdefault('replay', {'replay_id': replay_id})
    return event


def _is_graceful_shutdown_cancel(event: Dict[str, Any], exc: Optional[BaseException]) -> bool:
    """Whether the event is uvicorn cutting off requests at the shutdown deadline.

    Every deploy leaves a few socket.io long-polls open past the 10s deadline in
    ``start.sh``, and uvicorn reports each as an ERROR: once as a count, once per
    request as a ``CancelledError`` traceback. Neither is actionable on its own.
    ``middleware/inflight_requests.py`` names each cancelled request on
    ``wizzrobe.shutdown`` at WARNING, which still reaches Sentry Logs, so a
    route that really does stall the shutdown stays visible there.
    """
    if isinstance(exc, asyncio.CancelledError):
        return _GRACEFUL_SHUTDOWN_CANCEL in str(exc)
    if exc is not None or event.get('logger') != 'uvicorn.error':
        return False
    logentry = event.get('logentry') or {}
    text = logentry.get('formatted') or logentry.get('message') or ''
    return text.startswith('Cancel ') and _GRACEFUL_SHUTDOWN_CANCEL in text


def _before_send(event: Dict[str, Any], hint: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Drop known noise, then enrich and scrub what's left.

    Noise is the NiceGUI timer-teardown race (the ``nicegui`` logger filter
    already stops it reaching the logging hook; this catches it arriving by any
    other integration), uvicorn's graceful-shutdown cancellations, and a socket
    write to a browser that has already gone (SGLMAN-1D: a tab closed mid
    engine.io upgrade; uvicorn raises ``ClientDisconnected`` only then).
    """
    exc_info = (hint or {}).get('exc_info')
    exc = exc_info[1] if exc_info else None
    if exc is not None and is_timer_teardown_race(exc):
        return None
    if isinstance(exc, ClientDisconnected):
        return None
    if _is_graceful_shutdown_cancel(event, exc):
        return None
    try:
        _enrich_event(event)
    except Exception:
        pass
    return _scrub_event(event, hint)


def _before_send_log(log: Dict[str, Any], hint: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Stamp the tenant on each Sentry Log so they filter like events do."""
    try:
        tenant_id = get_current_tenant_id()
        if tenant_id is not None:
            log.setdefault('attributes', {})['tenant_id'] = tenant_id
    except Exception:
        pass
    return log


def _traces_sample_rate() -> float:
    raw = (os.environ.get('SENTRY_TRACES_SAMPLE_RATE') or '').strip()
    if not raw:
        return _PRODUCTION_TRACES_SAMPLE_RATE if is_production() else 0.0
    try:
        return min(max(float(raw), 0.0), 1.0)
    except ValueError:
        return 0.0


def _profiles_sample_rate() -> float:
    """Share of *traced* requests that are also profiled.

    A trace says which page was slow; the profile says which function burned
    the CPU, which is what a fix needs on a CPU-bound single-loop app.
    """
    raw = (os.environ.get('SENTRY_PROFILES_SAMPLE_RATE') or '').strip()
    if not raw:
        return 1.0 if is_production() else 0.0
    try:
        return min(max(float(raw), 0.0), 1.0)
    except ValueError:
        return 0.0


def _sentry_logs_level() -> Optional[int]:
    """The minimum level shipped to Sentry Logs; ``None`` turns them off."""
    raw = (os.environ.get('SENTRY_LOGS_LEVEL') or 'INFO').strip()
    if raw.lower() in ('off', 'none', 'false', '0'):
        return None
    try:
        return level_number(raw)
    except ValueError:
        return logging.INFO


def _release() -> Optional[str]:
    explicit = (os.environ.get('SENTRY_RELEASE') or '').strip()
    if explicit:
        return explicit
    sha = (os.environ.get('GIT_SHA') or '').strip()
    return f'wizzrobe@{sha[:12]}' if sha else None


def browser_sdk_config() -> Optional[Dict[str, Any]]:
    """What a page hands the browser SDK, or ``None`` to not load it at all.

    The browser shares the server's DSN (one Sentry project), so with no
    ``SENTRY_DSN`` nothing loads; nor does it while a super-admin has browser
    errors switched off. Built per page build, so the user and tenant are the
    viewer's.
    """
    dsn = (os.environ.get('SENTRY_DSN') or '').strip()
    snapshot = browser_monitoring.current()
    if not dsn or not snapshot.enabled:
        return None
    tags = {'surface': 'browser'}
    tenant_id = get_current_tenant_id()
    if tenant_id is not None:
        tags['tenant_id'] = str(tenant_id)
    return {
        'dsn': dsn,
        'environment': get_environment(),
        'release': _release(),
        'user': _session_user(),
        'tags': tags,
        'builtin': [{'pattern': r.pattern, 'match': r.match} for r in browser_monitoring.BUILTIN_RULES],
        **browser_monitoring.live_config(snapshot),
    }


def init_sentry() -> None:
    """Initialize Sentry when ``SENTRY_DSN`` is configured; otherwise do nothing.

    Must be called before the FastAPI app and middleware are constructed so the
    SDK's instrumentation wraps them.
    """
    dsn = (os.environ.get('SENTRY_DSN') or '').strip()
    if not dsn:
        logger.debug('SENTRY_DSN not set — Sentry reporting disabled.')
        return

    logs_level = _sentry_logs_level()
    install_stale_session_filter()
    for name in _NOISY_FOR_SENTRY_LOGS:
        ignore_logger_for_sentry_logs(name)

    sentry_sdk.init(
        dsn=dsn,
        environment=get_environment(),
        release=_release(),
        send_default_pii=False,
        before_send=_before_send,  # type: ignore[arg-type]
        traces_sample_rate=_traces_sample_rate(),
        profiles_sample_rate=_profiles_sample_rate(),
        enable_logs=logs_level is not None,
        before_send_log=_before_send_log,  # type: ignore[arg-type]
        integrations=[
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
                sentry_logs_level=logs_level,
            ),
        ],
    )
    sentry_metrics.configure(sentry_metrics.metrics_requested())
    logger.info(
        'Sentry initialized (environment=%s, release=%s, traces=%s, profiles=%s, logs=%s, metrics=%s).',
        get_environment(), _release() or 'unset', _traces_sample_rate(), _profiles_sample_rate(),
        logging.getLevelName(logs_level) if logs_level is not None else 'off',
        'on' if sentry_metrics.enabled() else 'off',
    )


async def flush_sentry() -> None:
    """Ship queued events and logs before the process exits.

    Sentry Logs go out in batches every few seconds, so without this the last
    lines of a shutdown (including ``wizzrobe.shutdown``'s cancelled-request
    warnings) never left the process. Runs off the event loop because
    ``flush`` blocks. Safe with Sentry disabled.
    """
    await asyncio.to_thread(sentry_sdk.flush, _SHUTDOWN_FLUSH_TIMEOUT)


@contextmanager
def sentry_tags(**tags: Any) -> Iterator[None]:
    """Tag every Sentry event raised or logged inside the block.

    Forks the current scope, so the tags vanish when the block exits and never
    leak onto the next unrelated event (a worker's next tick, the queue's next
    item). ``None`` values are skipped. Safe with Sentry disabled.
    """
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            if value is not None:
                scope.set_tag(key, str(value))
        yield


def tag_current_scope(**tags: Any) -> None:
    """Add tags to the scope already in effect, for a value learned mid-block.

    Meant for use inside :func:`sentry_tags`, whose forked scope bounds them.
    """
    scope = sentry_sdk.get_current_scope()
    for key, value in tags.items():
        if value is not None:
            scope.set_tag(key, str(value))
