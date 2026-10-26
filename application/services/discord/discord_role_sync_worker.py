"""Discord role sync worker.

Keeps the Discord roles Wizzrobe hands out (see ``discord_role_sync_service``)
in step with who qualifies. Two cadences share one loop: a community marked by
``request_sync`` (a rule changed, a match started or finished, an enrollment or
badge sync landed) syncs on the next short tick, and every community with rules
or roles to take back gets a full pass every ``FULL_SYNC_SECONDS`` to catch what
no event announces (a Challonge or start.gg roster sync, a tournament going
inactive, a role reordered in Discord).

Each run is inside its tenant's ``tenant_scope`` and skips communities that have
the feature off. A run that can't reach Discord records why on the community's
sync state and changes nothing; the next pass retries.
"""

import logging
import time
from typing import Dict, Set

from application.events import Event, EventType, event_bus
from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 15
FULL_SYNC_SECONDS = 600

# The events that change who qualifies, so a role follows within one tick.
TRIGGERS = (
    EventType.MATCH_STARTED,
    EventType.MATCH_FINISHED,
    EventType.MATCH_DELETED,
    EventType.TOURNAMENT_ENROLLED,
    EventType.TOURNAMENT_WITHDRAWN,
    EventType.CHECK_IN_EVENT_SYNCED,
    EventType.CHECK_IN_ENTRANT_LINKED,
    EventType.CHECK_IN_ENTRANT_UNLINKED,
)

_last_full: Dict[int, float] = {}
_subscription: Dict[str, int] = {}


def _on_event(event: Event) -> None:
    from application.services.discord.discord_role_sync_service import request_sync

    request_sync(event.tenant_id)


async def _tick() -> None:
    from application.repositories import DiscordRoleSyncRepository
    from application.services.discord.discord_role_sync_service import (
        DiscordRoleSyncService,
        request_sync,
        take_pending,
    )
    from application.services.feature_flag_service import FeatureFlagService
    from models import FeatureFlag

    pending = take_pending()
    with_work = await DiscordRoleSyncRepository.tenant_ids_with_work()
    now = time.monotonic()
    due: Set[int] = {
        tid for tid in with_work
        if tid in pending or now - _last_full.get(tid, 0.0) >= FULL_SYNC_SECONDS
    }
    if not due:
        return
    service = DiscordRoleSyncService()

    async def _sync(tenant_id: int) -> None:
        _last_full[tenant_id] = now
        if not await FeatureFlagService().is_enabled(FeatureFlag.DISCORD_ROLE_SYNC):
            return
        result = await service.sync_tenant()
        if result.skipped:
            request_sync(tenant_id)
            return
        if result.error:
            logger.warning('Discord role sync for tenant %s: %s', tenant_id, result.error)

    await for_each_tenant_scoped(
        sorted(due),
        _sync,
        tenant_id_of=lambda tenant_id: tenant_id,
        logger=logger,
        describe=lambda tenant_id: f'tenant {tenant_id}',
    )


# A full pass over a big guild waits out Discord's role-edit rate limits.
_loop = run_worker_loop(_tick, TICK_SECONDS, 'Discord role sync', logger, tick_timeout=900)


def start() -> None:
    if 'token' not in _subscription:
        _subscription['token'] = event_bus.subscribe_sync(_on_event, TRIGGERS)
    _loop.start()


async def stop() -> None:
    token = _subscription.pop('token', None)
    if token is not None:
        event_bus.unsubscribe(token)
    await _loop.stop()
