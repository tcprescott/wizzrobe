"""Per-category connection loop with first-class health tracking.

One :class:`CategoryConnection` owns the long-lived connection for a single
active :class:`~models.RacetimeBot`. It is transport-agnostic — the network I/O
lives behind a :class:`~racetimebot.transport.RacetimeTransport` — and it owns
the durable concerns the runtime cares about:

* **Health as state.** Each transition is written to ``RacetimeBot`` via
  :class:`~application.services.racetime_bot_service.RacetimeBotService` acting
  as the system user: connect → ``connected`` (+ ``last_connected_at``); auth
  rejection → ``error`` and **stop retrying**; a retryable failure → ``error``
  then reconnect with **exponential backoff capped at 5 minutes**; graceful stop
  → ``disconnected``.
* **Liveness heartbeat.** The transport pings ``record_heartbeat`` on a timer so
  ``last_checked_at`` advances; a task wedged *without* an error is then
  detectable (stale ``last_checked_at`` under a ``connected`` status).
* **Backoff reset on success.** A connection that stays up for a while and then
  drops retries promptly rather than inheriting a long backoff.

``_attempt`` is a single connect cycle exposed for tests; ``run_forever`` is the
supervised loop the manager schedules as a task.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Tuple

from models import User
from racetimebot.handler import RaceHandler
from racetimebot.transport import (
    RacetimeAuthError,
    RacetimeTransientError,
    RacetimeTransport,
    RoomSource,
    build_transport,
)

logger = logging.getLogger(__name__)

INITIAL_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300


class CategoryConnection:
    """Supervises one racetime category's connection and its health."""

    def __init__(
        self,
        *,
        bot_id: int,
        category: str,
        client_id: str,
        client_secret: str,
        handler: RaceHandler,
        bot_service,
        system_user: User,
        transport_factory: Callable[..., RacetimeTransport] = build_transport,
        room_source: Optional[RoomSource] = None,
    ) -> None:
        self.bot_id = bot_id
        self.category = category
        self.client_id = client_id
        self.client_secret = client_secret
        self.handler = handler
        self.bot_service = bot_service
        self.system_user = system_user
        self._transport_factory = transport_factory
        self.room_source = room_source
        self._stop = asyncio.Event()
        self._transport: RacetimeTransport | None = None

    def _build_transport(self) -> RacetimeTransport:
        return self._transport_factory(
            client_id=self.client_id,
            client_secret=self.client_secret,
            category=self.category,
            room_source=self.room_source,
        )

    async def send(self, slug: str, action: dict) -> bool:
        """Speak in room ``slug`` over the live transport; ``False`` while disconnected."""
        transport = self._transport
        if transport is None:
            return False
        return await transport.send(slug, action)

    async def _heartbeat(self) -> None:
        await self.bot_service.record_heartbeat(self.bot_id)

    async def _attempt(self) -> Tuple[str, bool]:
        """Run one connect cycle.

        Returns ``(outcome, connected)`` where outcome is ``'stopped'``,
        ``'auth_failed'``, or ``'transient'`` and ``connected`` says whether
        authentication succeeded (so the loop can reset backoff).
        """
        transport = self._build_transport()
        self._transport = transport
        try:
            try:
                await transport.authenticate()
            except RacetimeAuthError as exc:
                logger.error('racetime bot %s failed to authenticate: %s', self.category, exc)
                await self.bot_service.record_error(
                    self.bot_id, self.system_user, str(exc), auth_failed=True,
                )
                return 'auth_failed', False
            except RacetimeTransientError as exc:
                logger.warning('racetime bot %s could not connect: %s', self.category, exc)
                await self.bot_service.record_error(self.bot_id, self.system_user, str(exc))
                return 'transient', False

            await self.bot_service.record_connected(self.bot_id, self.system_user)
            try:
                await transport.run(self.handler.on_event, self._heartbeat)
            except RacetimeTransientError as exc:
                logger.warning('racetime bot %s lost its connection: %s', self.category, exc)
                await self.bot_service.record_error(self.bot_id, self.system_user, str(exc))
                return 'transient', True
            return 'stopped', True
        finally:
            await transport.close()
            self._transport = None

    async def _sleep_backoff(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run_forever(self) -> None:
        backoff = INITIAL_BACKOFF_SECONDS
        while not self._stop.is_set():
            try:
                outcome, connected = await self._attempt()
            except asyncio.CancelledError:
                await self._safe_mark_disconnected()
                raise
            except Exception as exc:
                logger.exception('racetime connection for %s crashed', self.category)
                try:
                    await self.bot_service.record_error(self.bot_id, self.system_user, str(exc))
                except Exception:
                    # Never let this escape: it would end the connection loop with nothing to restart it.
                    logger.exception('failed to record racetime bot %s error', self.category)
                outcome, connected = 'transient', False

            if connected:
                backoff = INITIAL_BACKOFF_SECONDS
            if outcome == 'auth_failed':
                logger.error(
                    'racetime bot %s auth failed — not retrying until restarted', self.category,
                )
                return
            if outcome == 'stopped':
                await self.bot_service.mark_disconnected(self.bot_id, self.system_user)
                return

            await self._sleep_backoff(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def _safe_mark_disconnected(self) -> None:
        try:
            await self.bot_service.mark_disconnected(self.bot_id, self.system_user)
        except Exception:
            logger.exception('failed to mark racetime bot %s disconnected', self.category)

    def request_stop(self) -> None:
        self._stop.set()
        transport = self._transport
        if transport is not None:
            try:
                asyncio.get_event_loop().create_task(transport.close())
            except Exception:
                pass
