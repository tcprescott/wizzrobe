"""Lifespan-managed supervisor for the racetime bot runtime.

One process-wide :class:`RacetimeBotManager` holds a
:class:`~racetimebot.connection.CategoryConnection` task per active
:class:`~models.RacetimeBot`. It is gated by the ``RACETIME_BOT_ENABLED`` master
switch, started/stopped from the FastAPI lifespan, and drives the system-user
identity down into every connection.

Responsibilities:

* **Fan-out.** One connection (and asyncio task) per active bot category.
* **Re-adoption.** On boot it loads every not-yet-terminal ``RacetimeRoom``
  (cross-tenant, unscoped) so a redeploy doesn't orphan live rooms — with
  slug-based routing the rooms stay resolvable, and logging them makes the
  re-adoption explicit and auditable.
* **Restart.** ``/platform`` can restart a single bot (cancel + respawn),
  auditing ``racetime_bot.restarted`` as the acting super-admin. A restart asked
  for in a process without the runtime (the API beside a ``bots`` container)
  arrives here over the live relay as :data:`RESTART_KIND`, already audited.
* **Crash isolation.** A connection task that dies logs and is not allowed to
  take down the manager or sibling connections.

Like ``discord_queue``, the singleton lives at module scope (process-global
infrastructure, not per-user state).
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from application.events import live_relay
from application.services import RacetimeBotService, RacetimeRoomService, UserService
from application.services.audit_service import AuditActions, AuditService
from application.utils.clients import racetime_room_actions
from application.utils.coroutine_queue import CoroutineQueue
from application.utils.environment import racetime_bot_enabled
from models import User
from racetimebot.connection import CategoryConnection
from racetimebot.handler import RaceHandler

logger = logging.getLogger(__name__)

RESTART_KIND = racetime_room_actions.RESTART_KIND


class RacetimeBotManager:
    """Supervises one connection per active racetime bot."""

    def __init__(self) -> None:
        self.bot_service = RacetimeBotService()
        self.room_service = RacetimeRoomService()
        self.audit_service = AuditService()
        self._system_user: Optional[User] = None
        self._connections: Dict[int, Tuple[CategoryConnection, asyncio.Task]] = {}
        self.running = False

    async def start(self) -> None:
        if not racetime_bot_enabled():
            logger.info('RACETIME_BOT_ENABLED is off — racetime bot runtime not started.')
            return
        self._system_user = await UserService().get_system_user()
        self.running = True
        await self.readopt_open_rooms()
        # Registered even with no bots yet: one restarted later must be reachable.
        racetime_room_actions.register_sender(self.send)
        bots = await self.bot_service.list_active_bots()
        if not bots:
            logger.info('No active racetime bots configured — nothing to connect.')
            return
        for bot in bots:
            self._spawn(bot.id, bot.category, bot.client_id, bot.client_secret)
        logger.info('Started %d racetime bot connection(s).', len(self._connections))

    async def readopt_open_rooms(self) -> Dict[str, List[str]]:
        """Load open rooms (unscoped) and log them grouped by category.

        Returns the ``category -> [slug, ...]`` grouping. Slug-based routing means
        the rooms are already resolvable after a restart; this makes the
        re-adoption explicit so a redeploy visibly keeps live rooms.
        """
        by_category: Dict[str, List[str]] = defaultdict(list)
        for room in await self.room_service.list_open_rooms():
            by_category[room.category].append(room.slug)
        for category, slugs in by_category.items():
            logger.info(
                're-adopting %d open racetime room(s) for category %s: %s',
                len(slugs), category, ', '.join(slugs),
            )
        return dict(by_category)

    def _spawn(self, bot_id: int, category: str, client_id: str, client_secret: str) -> None:
        assert self._system_user is not None
        from application.services.race_room_service import RaceRoomLifecycle

        lifecycle = RaceRoomLifecycle()
        handler = RaceHandler(
            category=category, room_service=self.room_service, lifecycle=lifecycle,
        )
        connection = CategoryConnection(
            bot_id=bot_id,
            category=category,
            client_id=client_id,
            client_secret=client_secret,
            handler=handler,
            bot_service=self.bot_service,
            system_user=self._system_user,
            room_source=functools.partial(self.room_service.list_bot_run_rooms, category),
        )
        task = asyncio.get_event_loop().create_task(connection.run_forever())
        task.add_done_callback(lambda t, bid=bot_id: self._on_connection_done(bid, t))
        self._connections[bot_id] = (connection, task)

    async def send(self, slug: str, action: dict) -> bool:
        """Route a room action to the connection for the room's category (its slug's prefix)."""
        category = slug.split('/', 1)[0]
        for connection, _task in self._connections.values():
            if connection.category == category:
                return await connection.send(slug, action)
        return False

    def _on_connection_done(self, bot_id: int, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error('racetime connection task for bot %s exited', bot_id, exc_info=exc)

    async def restart(self, actor: User, bot_id: int) -> None:
        """Cancel and respawn one bot's connection (``/platform`` action)."""
        if not racetime_bot_enabled():
            raise ValueError('The racetime bot runtime is disabled (RACETIME_BOT_ENABLED).')
        bot = await self.bot_service.get_runtime_bot(bot_id)
        if bot is None:
            raise ValueError('Racetime bot not found')
        await self.audit_service.write_log(
            actor, AuditActions.RACETIME_BOT_RESTARTED,
            {'bot_id': bot_id, 'category': bot.category},
        )
        await self.respawn(bot_id)

    async def respawn(self, bot_id: int) -> None:
        """Drop the bot's connection and, while the bot is active, open a fresh one."""
        bot = await self.bot_service.get_runtime_bot(bot_id)
        await self._cancel(bot_id)
        if bot is None:
            return
        if self._system_user is None:
            self._system_user = await UserService().get_system_user()
        if bot.is_active:
            self._spawn(bot.id, bot.category, bot.client_id, bot.client_secret)

    async def _cancel(self, bot_id: int) -> None:
        entry = self._connections.pop(bot_id, None)
        if entry is None:
            return
        connection, task = entry
        connection.request_stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception('racetime connection for bot %s errored during cancel', bot_id)

    async def stop(self) -> None:
        self.running = False
        racetime_room_actions.register_sender(None)
        for bot_id in list(self._connections.keys()):
            await self._cancel(bot_id)
        self._connections.clear()


_manager: Optional[RacetimeBotManager] = None


def get_racetime_manager() -> RacetimeBotManager:
    """Return the process-wide manager singleton, creating it on first use."""
    global _manager
    if _manager is None:
        _manager = RacetimeBotManager()
    return _manager


async def init_racetime_bot() -> None:
    """Start the racetime bot runtime (called from the FastAPI lifespan)."""
    await get_racetime_manager().start()


_restarts: Optional[CoroutineQueue] = None


def _deliver_restart(bot_id: int) -> None:
    """A restart another process asked for: act on it if this process runs the bots."""
    global _restarts
    if not racetime_bot_enabled() or _manager is None or not _manager.running:
        return
    if _restarts is None:
        _restarts = CoroutineQueue('racetime relayed restarts', logger=logger)
        _restarts.start()
    _restarts.enqueue(_manager.respawn(int(bot_id)))


live_relay.register(RESTART_KIND, _deliver_restart)


async def close_racetime_bot() -> None:
    """Stop all racetime bot connections (called from the FastAPI lifespan)."""
    global _manager
    if _manager is None:
        return
    await _manager.stop()
