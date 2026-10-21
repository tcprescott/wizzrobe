"""Transport seam for the racetime bot runtime.

The connection loop (:mod:`racetimebot.connection`) is deliberately transport-
agnostic: it owns health tracking, backoff, heartbeats, and tenant-routed event
dispatch, and delegates the actual network I/O to a :class:`RacetimeTransport`.
Two implementations exist:

* :class:`RealRacetimeTransport` — a real ``aiohttp`` client-credentials token
  fetch against racetime.gg, then a liveness loop. This proves the bot's OAuth
  credentials and tracks health against the live service. The full bot websocket
  protocol (per-race action handlers) is the integration surface later PRs build
  on; this PR's job is to *connect and track state*, not to open rooms for real
  matches.
* :class:`~racetimebot.mock.MockRacetimeTransport` — a scripted fake used under
  ``MOCK_RACETIME`` and in tests, driving a room lifecycle without any network.

Errors are split into two kinds so the loop can react correctly:

* :class:`RacetimeAuthError` — credentials rejected (HTTP 401/invalid client).
  The loop stops retrying: hammering a bad secret only earns rate limits.
* :class:`RacetimeTransientError` — anything retryable (network blip, 5xx,
  timeout). The loop backs off exponentially (capped) and reconnects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, List, Optional

from models import RaceRoomStatus


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


# Callback signatures the connection loop hands to a transport's ``run``.
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


class RealRacetimeTransport(RacetimeTransport):
    """Live racetime.gg transport: OAuth token fetch + a liveness loop.

    ``authenticate`` performs the standard ``client_credentials`` grant against
    ``/o/token``; a 401 (or ``invalid_client``) maps to :class:`RacetimeAuthError`
    so the loop stops retrying, any other failure to :class:`RacetimeTransientError`.
    ``run`` holds the connection with a periodic heartbeat, **re-authorizing before
    the token expires**; the per-race websocket protocol is layered on top of this
    by the room lifecycle work.

    The refresh is not optional housekeeping. A racetime access token lives on the
    order of hours while these connections are meant to live for weeks, and a race
    room outlives its token routinely — so a transport that authenticates once at
    connect and never again is holding a credential that is dead long before the
    next race needs it, with nothing to say so.
    """

    OAUTH_EXCHANGE_URL = 'https://racetime.gg/o/token'
    HEARTBEAT_SECONDS = 30
    # Fall back to racetime's own default lifetime when the grant omits
    # ``expires_in``, and always refresh with this much margin to spare so a
    # request in flight never rides an expiring token.
    DEFAULT_TOKEN_LIFETIME_SECONDS = 36000
    REFRESH_MARGIN_SECONDS = 600

    def __init__(self, *, client_id: str, client_secret: str, category: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.category = category
        self._access_token: Optional[str] = None
        # Monotonic deadline at which the token must have been replaced.
        self._token_expires_at: Optional[float] = None
        self._stop = False

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
        except aiohttp.ClientError as exc:  # network-level: retryable
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
        import asyncio

        # Liveness loop: prove the task is alive on each tick, and replace the
        # access token before it expires. Room automation over the per-race
        # websocket attaches here in the lifecycle work; until then the connection
        # is held open, its credential kept valid, and its health kept fresh.
        while not self._stop:  # worker-loop: the connection's run_forever reconnects with backoff and records health
            await on_heartbeat()
            if self._token_is_stale():
                # An auth error here is a real credential problem (revoked, or
                # rotated behind our back) and propagates so the loop stops
                # retrying; anything else is transient and earns a reconnect,
                # which re-authenticates from scratch.
                await self.authenticate()
            await asyncio.sleep(self.HEARTBEAT_SECONDS)

    async def close(self) -> None:
        self._stop = True
        self._access_token = None
        self._token_expires_at = None


def build_transport(
    *, client_id: str, client_secret: str, category: str,
) -> RacetimeTransport:
    """Return the mock transport under ``MOCK_RACETIME``, else the live one."""
    from application.utils.mocks.mock_racetime import is_mock_racetime

    if is_mock_racetime():
        from racetimebot.mock import MockRacetimeTransport

        return MockRacetimeTransport(category=category)
    return RealRacetimeTransport(
        client_id=client_id, client_secret=client_secret, category=category,
    )
