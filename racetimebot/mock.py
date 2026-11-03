"""Scripted racetime transport for ``MOCK_RACETIME`` and tests.

A *scripted event-emitting fake* — not a no-op skip. It fabricates the room
lifecycle so development and tests can exercise the connection loop, health
tracking, and event routing without a real racetime OAuth app or network. It is
never active in production: :func:`~application.utils.mocks.mock_racetime.is_mock_racetime`
raises there, since fabricated finishes would corrupt real results.

The transport is configurable so a test can force each health path:

* ``fail_auth`` → :meth:`authenticate` raises :class:`RacetimeAuthError`
  (the loop must stop retrying).
* ``fail_transient`` → :meth:`authenticate` raises :class:`RacetimeTransientError`
  (the loop must back off and reconnect).
* ``script`` → a list of :class:`RaceRoomEvent` :meth:`run` emits in order, each
  preceded by a heartbeat, then returns (a graceful stop).
* :meth:`send` records each action in ``sent`` as ``(slug, action)``, so a test
  can read back what the bot said in a room.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from racetimebot.transport import (
    EventCallback,
    HeartbeatCallback,
    RaceRoomEvent,
    RacetimeAuthError,
    RacetimeTransientError,
    RacetimeTransport,
    RoomSource,
)


class MockRacetimeTransport(RacetimeTransport):
    """Canned transport that drives a scripted room lifecycle."""

    def __init__(
        self,
        *,
        category: str,
        script: Optional[List[RaceRoomEvent]] = None,
        fail_auth: bool = False,
        fail_transient: bool = False,
        heartbeats: int = 1,
        room_source: Optional[RoomSource] = None,
    ) -> None:
        self.category = category
        self.script = script or []
        self.fail_auth = fail_auth
        self.fail_transient = fail_transient
        self.heartbeats = heartbeats
        self.authenticated = False
        self.closed = False
        self.room_source = room_source
        self.sent: List[Tuple[str, dict]] = []

    async def authenticate(self) -> None:
        if self.fail_auth:
            raise RacetimeAuthError(f'mock auth failure for {self.category}')
        if self.fail_transient:
            raise RacetimeTransientError(f'mock transient failure for {self.category}')
        self.authenticated = True

    async def run(self, on_event: EventCallback, on_heartbeat: HeartbeatCallback) -> None:
        for _ in range(max(0, self.heartbeats)):
            await on_heartbeat()
        for event in self.script:
            await on_event(event)

    async def send(self, slug: str, action: dict) -> bool:
        self.sent.append((slug, action))
        return True

    async def close(self) -> None:
        self.closed = True
