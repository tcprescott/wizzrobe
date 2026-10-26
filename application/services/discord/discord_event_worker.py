"""Discord Scheduled Events reconciler worker (PR 8).

A background loop (modeled on ``speedgaming_sync_worker`` / ``race_room_worker``)
that periodically reconciles every tenant with a linked Discord guild: its
opted-in tournaments' scheduled matches are mirrored into that guild's Discord
Scheduled Events. Gated by ``DISCORD_EVENTS_SYNC_ENABLED`` (off by default). Each
tenant's reconcile runs inside ``tenant_scope`` acting as the reserved **system
``User``**, so audit/event attribution and the shared-guild safety scoping are
correct even though the loop runs off a timer with no request context.

Per-tenant failures are logged and retried next tick — one bad guild never stops
the others, and the loop itself never dies. Reconciliation is idempotent, so a
tenant whose schedule hasn't changed is a cheap no-op (content hashes match, no
Discord calls).
"""

import logging
from datetime import datetime, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

# Reconcile is idempotent and mostly no-op, so a slower tick than the SG/racetime
# workers is plenty — a schedule change surfaces within a few minutes.
TICK_SECONDS = 300


async def _tick() -> None:
    from application.repositories import TenantRepository
    from application.services.discord.discord_event_reconciler_service import DiscordEventReconcilerService
    from application.services.user_service import UserService

    tenants = [t for t in await TenantRepository.list_all() if t.discord_guild_id is not None and t.is_active]
    if not tenants:
        return

    now = datetime.now(timezone.utc)
    system_user = await UserService().get_system_user()
    reconciler = DiscordEventReconcilerService()

    async def _reconcile(tenant) -> None:
        result = await reconciler.reconcile_tenant(tenant, actor=system_user, now=now)
        if result.created or result.updated or result.cancelled or result.errors:
            logger.info('Discord events reconcile for tenant %s: %s', tenant.id, result.as_dict())

    await for_each_tenant_scoped(
        tenants,
        _reconcile,
        tenant_id_of=lambda tenant: tenant.id,
        each_live_event=True,
        logger=logger,
        describe=lambda tenant: f'tenant {getattr(tenant, "id", None)}',
    )


# Creating a tournament's worth of scheduled events waits out Discord's rate limits.
_loop = run_worker_loop(_tick, TICK_SECONDS, 'Discord events reconcile', logger, tick_timeout=900)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
