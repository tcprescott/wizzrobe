"""Carries live-refresh signals between app instances over Postgres LISTEN/NOTIFY.

``match_live``, ``check_in_live`` and ``event_status_live`` deliver to
subscribers in their own process. With more than one instance, a change made
on one would never refresh a screen held open on another. This relay forwards
each signal as a ``NOTIFY`` and replays signals other instances sent to this
process's subscribers.

Off unless ``MULTI_INSTANCE`` is set: a single process has nobody to tell, and
the relay costs one dedicated database connection. Each process tags what it
sends with a random origin and ignores its own echoes, since local subscribers
were already called directly.

Sends go through a serial queue that owns the connection, so publishers never
wait on the database and the connection never sees two statements at once. A
dropped connection is reopened on the next send or after a short backoff; the
signals lost in between only delay a refresh, they change no data.
"""

import asyncio
import json
import logging
import secrets
from typing import Any, Callable, Dict, Optional

from application.utils.coroutine_queue import CoroutineQueue
from application.utils.environment import env_flag

logger = logging.getLogger(__name__)

CHANNEL = 'wizzrobe_live'
_RECONNECT_SECONDS = 2.0

ORIGIN = secrets.token_hex(8)

Deliver = Callable[..., None]

_handlers: Dict[str, Deliver] = {}
_dsn: Optional[str] = None
_conn: Any = None
_q = CoroutineQueue('live relay', logger=logger)


def multi_instance() -> bool:
    return env_flag('MULTI_INSTANCE')


def register(kind: str, deliver: Deliver) -> None:
    """Route incoming ``kind`` signals to ``deliver(*args)`` (each live module registers itself)."""
    _handlers[kind] = deliver


def active() -> bool:
    return _dsn is not None


def forward(kind: str, *args: Any) -> None:
    """Send a signal this process already delivered locally to every other instance. Never raises."""
    if _dsn is None:
        return
    try:
        payload = json.dumps({'o': ORIGIN, 'k': kind, 'a': list(args)})
        _q.enqueue(_send(payload))
    except Exception:
        logger.exception('live relay could not queue a %s signal', kind)


def receive(payload: str) -> None:
    """Deliver a signal another instance sent. Never raises."""
    try:
        message = json.loads(payload)
        if message.get('o') == ORIGIN:
            return
        deliver = _handlers.get(message.get('k'))
        if deliver is not None:
            deliver(*message.get('a', []))
    except Exception:
        logger.exception('live relay could not deliver %r', payload[:200])


def _on_notify(_connection, _pid, _channel, payload: str) -> None:
    receive(payload)


def _on_terminate(_connection) -> None:
    global _conn
    _conn = None
    if _dsn is not None:
        logger.warning('live relay connection closed; reconnecting')
        _q.enqueue(_reconnect_after_backoff())


async def _connect() -> Any:
    global _conn
    if _conn is not None:
        return _conn
    import asyncpg

    conn = await asyncpg.connect(_dsn)
    await conn.add_listener(CHANNEL, _on_notify)
    conn.add_termination_listener(_on_terminate)
    _conn = conn
    return conn


async def _reconnect_after_backoff() -> None:
    await asyncio.sleep(_RECONNECT_SECONDS)
    if _dsn is not None and _conn is None:
        try:
            await _connect()
        except Exception:
            logger.warning('live relay reconnect failed; retrying', exc_info=True)
            _q.enqueue(_reconnect_after_backoff())


async def _send(payload: str) -> None:
    global _conn
    try:
        conn = await _connect()
        await conn.execute('SELECT pg_notify($1, $2)', CHANNEL, payload)
    except Exception:
        logger.warning('live relay send failed; this refresh is dropped', exc_info=True)
        if _conn is not None and _conn.is_closed():
            _conn = None


async def start(dsn: str) -> bool:
    """Open the listening connection when ``MULTI_INSTANCE`` is on and ``dsn`` is Postgres."""
    global _dsn
    if not multi_instance() or not dsn.startswith(('postgres://', 'postgresql://')):
        return False
    _dsn = dsn
    _q.start()
    await _connect()
    logger.info('live relay listening on %s (origin %s)', CHANNEL, ORIGIN)
    return True


async def stop() -> None:
    global _dsn, _conn
    _dsn = None
    await _q.stop()
    conn, _conn = _conn, None
    if conn is not None:
        try:
            await conn.close()
        except Exception:
            logger.debug('live relay close failed', exc_info=True)
