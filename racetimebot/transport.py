"""Transport seam for the racetime bot runtime.

The connection loop (:mod:`racetimebot.connection`) is deliberately transport-
agnostic: it owns health tracking, backoff, heartbeats, and tenant-routed event
dispatch, and delegates the actual network I/O to a :class:`RacetimeTransport`.
Two implementations exist:

* :class:`RealRacetimeTransport` — a real ``aiohttp`` client-credentials token
  fetch against racetime.gg, then a supervisor that sits the bot in each room
  ``room_source`` names, over racetime's bot websocket, and speaks there through
  :meth:`RacetimeTransport.send`.
* :class:`~racetimebot.mock.MockRacetimeTransport` — a scripted fake used under
  ``MOCK_RACETIME`` and in tests, driving a room lifecycle without any network.

Errors are split into two kinds so the loop can react correctly:

* :class:`RacetimeAuthError` — credentials rejected (HTTP 401/invalid client).
  The loop stops retrying: hammering a bad secret only earns rate limits.
* :class:`RacetimeTransientError` — anything retryable (network blip, 5xx,
  timeout). The loop backs off exponentially (capped) and reconnects.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from models import RaceRoomStatus

logger = logging.getLogger(__name__)


class RacetimeAuthError(Exception):
    """Credentials were rejected — stop retrying."""


class RacetimeTransientError(Exception):
    """A retryable failure — back off and reconnect."""


class EntrantStatus(str, Enum):
    """Normalized per-entrant outcome, decoupled from racetime's wire strings."""

    DONE = 'done'
    DID_NOT_FINISH = 'dnf'
    DISQUALIFIED = 'dq'
    IN_PROGRESS = 'in_progress'


@dataclass
class RaceEntrant:
    """One entrant's state in a race-room event.

    ``user_id`` is the racetime account id (maps to ``User.racetime_user_id``);
    ``finish_time`` is whole seconds (``None`` unless finished); ``place`` is the
    1-based finishing position racetime reports (``None`` for non-finishers).
    """

    user_id: str
    display_name: str
    status: EntrantStatus = EntrantStatus.IN_PROGRESS
    finish_time: Optional[int] = None
    place: Optional[int] = None
    finish_ms: Optional[int] = None


@dataclass
class RoomChat:
    """One chat line from a room the bot sits in."""

    user_id: str
    display_name: str
    text: str
    is_bot: bool = False
    is_system: bool = False


@dataclass
class RaceRoomEvent:
    """A snapshot of a race room's state pushed by the transport.

    Carries only the room ``slug`` (the globally-unique routing key), the derived
    lifecycle ``status``, and the entrant list. The handler resolves slug → room
    → tenant before doing anything tenant-scoped.
    """

    slug: str
    category: str
    status: RaceRoomStatus
    entrants: List[RaceEntrant] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    kind: str = 'race'
    chat: Optional[RoomChat] = None
    monitor_ids: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None


EventCallback = Callable[[RaceRoomEvent], Awaitable[None]]
HeartbeatCallback = Callable[[], Awaitable[None]]


class RacetimeTransport:
    """Abstract transport. Subclasses implement authenticate / run / close."""

    async def authenticate(self) -> None:
        """Establish credentials. Raise auth/transient errors on failure."""
        raise NotImplementedError

    async def run(self, on_event: EventCallback, on_heartbeat: HeartbeatCallback) -> None:
        """Long-lived loop: invoke callbacks, return on graceful stop.

        Raise :class:`RacetimeTransientError` to trigger a reconnect; return
        normally only for an intentional shutdown.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release any resources. Always safe to call more than once."""

    async def send(self, slug: str, action: dict) -> bool:
        """Send a bot action to room ``slug``. ``False`` when not connected to it."""
        return False


RoomSource = Callable[[], Awaitable[List[Tuple[str, RaceRoomStatus]]]]


class RoomState:
    """What one joined room has already reported, so each change goes up once.

    racetime pushes the whole race on every change (an entrant readying up, a
    comment), so without this the room would "start" or "finish" dozens of
    times. Seeded with the status the database already holds: a room rejoined
    after a restart reports only what changed while the bot was away.
    """

    def __init__(self, recorded: RaceRoomStatus) -> None:
        self.reported = recorded
        self.latest: Optional[RaceRoomEvent] = None

    def advance(self, event: RaceRoomEvent) -> bool:
        """Remember ``event``; ``True`` when its status is news."""
        self.latest = event
        if event.status == self.reported:
            return False
        self.reported = event.status
        return True


class RealRacetimeTransport(RacetimeTransport):
    """Live racetime.gg transport: the bot's token, and a seat in each of its rooms.

    ``authenticate`` performs the standard ``client_credentials`` grant against
    ``/o/token``; a 401 (or ``invalid_client``) maps to :class:`RacetimeAuthError`
    so the loop stops retrying, any other failure to :class:`RacetimeTransientError`.

    ``run`` keeps the token fresh and, every :attr:`ROOM_POLL_SECONDS`, asks
    ``room_source`` which rooms the bot should be in. It joins each one's bot
    websocket (``/ws/o/bot/<room>``), reports the bot arriving (``joined``),
    every status change and every chat line, and leaves once racetime says the
    race finished or was cancelled. A room whose socket drops is rejoined on a
    later poll, after :attr:`ROOM_RETRY_SECONDS`. :meth:`send` speaks in a room
    through its open socket.

    The refresh is not optional housekeeping. A racetime access token lives on the
    order of hours while these connections are meant to live for weeks, and a race
    room outlives its token routinely — so a transport that authenticates once at
    connect and never again is holding a credential that is dead long before the
    next race needs it, with nothing to say so.
    """

    OAUTH_EXCHANGE_URL = 'https://racetime.gg/o/token'
    BASE_URL = 'https://racetime.gg'
    WS_BASE_URL = 'wss://racetime.gg'
    HEARTBEAT_SECONDS = 30
    ROOM_POLL_SECONDS = 15
    ROOM_RETRY_SECONDS = 60
    DEFAULT_TOKEN_LIFETIME_SECONDS = 36000
    REFRESH_MARGIN_SECONDS = 600

    def __init__(
        self, *, client_id: str, client_secret: str, category: str,
        room_source: Optional[RoomSource] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.category = category
        self.room_source = room_source
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[float] = None
        self._stop = False
        self._session: Any = None
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._sockets: Dict[str, Any] = {}
        self._retry_after: Dict[str, float] = {}

    async def authenticate(self) -> None:
        import aiohttp

        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials',
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(self.OAUTH_EXCHANGE_URL, data=data) as resp:
                    if resp.status in (400, 401, 403):
                        body = await resp.text()
                        raise RacetimeAuthError(
                            f'racetime rejected credentials for {self.category} '
                            f'({resp.status}): {body[:200]}'
                        )
                    if resp.status >= 400:
                        raise RacetimeTransientError(
                            f'racetime token request for {self.category} failed '
                            f'({resp.status})'
                        )
                    payload = await resp.json()
        except aiohttp.ClientError as exc:
            raise RacetimeTransientError(str(exc)) from exc
        token = payload.get('access_token')
        if not token:
            raise RacetimeTransientError('racetime token response missing access_token')
        self._access_token = token
        lifetime = payload.get('expires_in')
        if not isinstance(lifetime, (int, float)) or lifetime <= 0:
            lifetime = self.DEFAULT_TOKEN_LIFETIME_SECONDS
        self._token_expires_at = time.monotonic() + max(
            self.REFRESH_MARGIN_SECONDS, float(lifetime) - self.REFRESH_MARGIN_SECONDS,
        )

    def _token_is_stale(self) -> bool:
        return (
            self._token_expires_at is not None
            and time.monotonic() >= self._token_expires_at
        )

    async def run(self, on_event: EventCallback, on_heartbeat: HeartbeatCallback) -> None:
        import aiohttp

        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=20))
        last_heartbeat = 0.0
        try:
            while not self._stop:  # worker-loop: the connection's run_forever reconnects with backoff and records health
                if time.monotonic() - last_heartbeat >= self.HEARTBEAT_SECONDS:
                    await on_heartbeat()
                    last_heartbeat = time.monotonic()
                if self._token_is_stale():
                    # An auth error must propagate to stop the retries; anything else is transient and reconnects.
                    await self.authenticate()
                await self.sync_rooms(on_event)
                await asyncio.sleep(self.ROOM_POLL_SECONDS)
        finally:
            await self._leave_all()

    async def sync_rooms(self, on_event: EventCallback) -> None:
        """Join the rooms ``room_source`` names that the bot isn't in; leave the rest."""
        if self.room_source is None:
            return
        try:
            wanted = dict(await self.room_source())
        except Exception:
            logger.exception('racetime %s: could not list the rooms to join', self.category)
            return
        for slug in [s for s in self._room_tasks if s not in wanted]:
            self._room_tasks.pop(slug).cancel()
        now = time.monotonic()
        for slug, recorded in wanted.items():
            if slug in self._room_tasks or self._retry_after.get(slug, 0) > now:
                continue
            task = asyncio.get_event_loop().create_task(self._sit_in_room(slug, recorded, on_event))
            task.add_done_callback(functools.partial(self._room_done, slug))
            self._room_tasks[slug] = task

    def _room_done(self, slug: str, task: asyncio.Task) -> None:
        if self._room_tasks.get(slug) is task:
            self._room_tasks.pop(slug, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning('racetime room %s: left after an error: %s', slug, exc)
            self._retry_after[slug] = time.monotonic() + self.ROOM_RETRY_SECONDS
        else:
            self._retry_after[slug] = time.monotonic() + self.ROOM_POLL_SECONDS

    async def _sit_in_room(self, slug: str, recorded: RaceRoomStatus, on_event: EventCallback) -> None:

        race = await self._race_data(slug)
        path = race.get('websocket_bot_url')
        if not path:
            raise RacetimeTransientError(f'racetime gave no bot websocket for {slug}')
        state = RoomState(recorded)
        headers = {'Authorization': f'Bearer {self._access_token}'}
        async with self._session.ws_connect(f'{self.WS_BASE_URL}{path}', headers=headers, heartbeat=30) as ws:
            self._sockets[slug] = ws
            try:
                if not await self.on_race(state, race, slug, on_event, joined=True):
                    return
                async for message in ws:
                    if message.type.name != 'TEXT':
                        if message.type.name in ('CLOSED', 'CLOSING', 'ERROR'):
                            break
                        continue
                    if not await self.on_message(state, json.loads(message.data), slug, on_event):
                        return
            finally:
                self._sockets.pop(slug, None)

    async def on_race(
        self, state: RoomState, race: dict, slug: str, on_event: EventCallback, *, joined: bool = False,
    ) -> bool:
        """Report a race payload; ``False`` once the race is over and the bot can leave."""
        from racetimebot import protocol

        event = protocol.race_event(race, slug=slug, category=self.category)
        changed = state.advance(event)
        if joined:
            await on_event(protocol.race_event(race, slug=slug, category=self.category, kind='joined'))
        if changed:
            await on_event(event)
        return event.status not in protocol.TERMINAL

    async def on_message(self, state: RoomState, payload: dict, slug: str, on_event: EventCallback) -> bool:
        """Report one websocket message; ``False`` once the bot can leave the room."""
        from racetimebot import protocol

        kind = payload.get('type')
        if kind == 'race.data' and isinstance(payload.get('race'), dict):
            return await self.on_race(state, payload['race'], slug, on_event)
        if kind == 'chat.message' and isinstance(payload.get('message'), dict) and state.latest is not None:
            line = protocol.chat(payload['message'])
            if line is not None and not line.is_bot and not line.is_system:
                latest = state.latest
                await on_event(RaceRoomEvent(
                    slug=slug, category=self.category, status=latest.status,
                    entrants=latest.entrants, kind='chat', chat=line,
                    monitor_ids=latest.monitor_ids, started_at=latest.started_at,
                ))
        elif kind == 'error':
            logger.warning('racetime room %s: %s', slug, payload.get('errors'))
        return True

    async def _race_data(self, slug: str) -> dict:
        import aiohttp

        try:
            async with self._session.get(f'{self.BASE_URL}/{slug}/data', timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status >= 400:
                    raise RacetimeTransientError(f'racetime room {slug} answered {resp.status}')
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise RacetimeTransientError(str(exc)) from exc

    async def send(self, slug: str, action: dict) -> bool:
        ws = self._sockets.get(slug)
        if ws is None or getattr(ws, 'closed', True):
            return False
        try:
            await ws.send_json(action)
        except Exception:
            logger.exception('racetime room %s: sending %s failed', slug, action.get('action'))
            return False
        return True

    async def _leave_all(self) -> None:
        tasks = list(self._room_tasks.values())
        self._room_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def close(self) -> None:
        self._stop = True
        await self._leave_all()
        self._access_token = None
        self._token_expires_at = None


def build_transport(
    *, client_id: str, client_secret: str, category: str,
    room_source: Optional[RoomSource] = None,
) -> RacetimeTransport:
    """Return the mock transport under ``MOCK_RACETIME``, else the live one."""
    from application.utils.mocks.mock_racetime import is_mock_racetime

    if is_mock_racetime():
        from racetimebot.mock import MockRacetimeTransport

        return MockRacetimeTransport(category=category, room_source=room_source)
    return RealRacetimeTransport(
        client_id=client_id, client_secret=client_secret, category=category,
        room_source=room_source,
    )
