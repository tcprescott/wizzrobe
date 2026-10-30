"""Per-connection race-room event handler.

The handler is the routing seam between the transport's raw room events and the
tenant-scoped business layer. It follows the presentation-layer rule (call
services, never repositories) and does three jobs:

1. **Tenant routing.** An inbound event carries only the room slug — no tenant.
   The handler resolves slug → room via the deliberately-unscoped
   :meth:`RacetimeRoomService.get_by_slug`, then binds ``room.tenant_id`` with
   :func:`tenant_scope` so every downstream write lands in the right tenant.
2. **Crash containment.** Each event is dispatched inside a ``try/except`` so a
   handler that raises can never take down the connection or its siblings.
3. **Delegation.** The tenant-scoped work is handed to an injected *lifecycle*
   object (duck-typed ``async handle_event(room, event)``). This PR ships a
   status-only lifecycle; the room-lifecycle PR injects the richer service that
   attaches seeds and captures results.

The connection acts as the **system user**; the lifecycle is what actually
performs the writes/audits under that identity.
"""

from __future__ import annotations

import logging

from application.tenant_context import tenant_scope
from application.utils.sentry import sentry_tags
from models import RacetimeRoom
from racetimebot.transport import RaceRoomEvent

logger = logging.getLogger(__name__)


class RoomStatusLifecycle:
    """Minimal lifecycle: mirror the room's reported status into its record.

    The runtime-half deliverable — it proves event routing and state tracking.
    The room-lifecycle PR replaces this with a service that also attaches the
    seed on open and captures results on finish.
    """

    def __init__(self, room_service) -> None:
        self.room_service = room_service

    async def handle_event(self, room: RacetimeRoom, event: RaceRoomEvent) -> None:
        await self.room_service.set_status(room, event.status)


class RaceHandler:
    """Routes a category's inbound room events to the tenant-scoped lifecycle."""

    def __init__(self, *, category: str, room_service, lifecycle) -> None:
        self.category = category
        self.room_service = room_service
        self.lifecycle = lifecycle

    async def on_event(self, event: RaceRoomEvent) -> None:
        with sentry_tags(racetime_room=event.slug, racetime_category=self.category):
            await self._on_event(event)

    async def _on_event(self, event: RaceRoomEvent) -> None:
        tenant_id = None
        try:
            room = await self.room_service.get_by_slug(event.slug)
            if room is None:
                logger.info(
                    'racetime event for unknown room slug %r (category %s) — ignoring',
                    event.slug, self.category,
                )
                return
            tenant_id = room.tenant_id
            with tenant_scope(tenant_id):
                await self.lifecycle.handle_event(room, event)
        except Exception:
            # A crashing handler must never take down the bot; re-bound so the failure carries its community.
            with tenant_scope(tenant_id):
                logger.exception(
                    'racetime handler failed for room %r (category %s, tenant %s)',
                    event.slug, self.category, tenant_id,
                )
