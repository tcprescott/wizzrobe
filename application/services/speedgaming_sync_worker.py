"""SpeedGaming ETL sync worker (PR 7).

A background loop (modeled on ``race_room_worker`` / ``volunteer_reminder``) that
polls each active :class:`SpeedGamingEventLink` on its configured cadence and
runs the ETL to materialize SG episodes into ``Match`` rows. Gated by
``SPEEDGAMING_SYNC_ENABLED`` (off by default). Each link's work runs inside
``tenant_scope`` (the link's tenant) acting as the reserved **system ``User``**,
so audit/event attribution and tenant scoping are correct even though the loop
runs off a timer with no request context.

Per-link failures are logged and retried next tick — one bad event slug never
stops the others, and the loop itself never dies.
"""

import logging
from datetime import datetime, timedelta, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


async def _tick() -> None:
    from application.repositories import SpeedGamingEventLinkRepository
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.speedgaming_etl_service import SpeedGamingETLService
    from application.services.user_service import UserService
    from models import FeatureFlag

    repo = SpeedGamingEventLinkRepository()
    links = await repo.list_active_all()
    if not links:
        return

    now = datetime.now(timezone.utc)
    system_user = await UserService().get_system_user()
    etl = SpeedGamingETLService()

    async def _sync(link) -> None:
        interval = timedelta(minutes=link.sync_interval_minutes or 15)
        if link.last_synced_at is not None and now - link.last_synced_at < interval:
            return  # not due yet on this link's cadence
        if not await FeatureFlagService().is_enabled(FeatureFlag.SPEEDGAMING_ETL):
            return  # tenant has SpeedGaming sync disabled
        result = await etl.sync_event_link(link, actor=system_user, now=now)
        logger.info(
            'SG sync for link %s (%s): %s',
            link.id, link.event_slug, result.as_dict(),
        )

    await for_each_tenant_scoped(
        links,
        _sync,
        tenant_id_of=lambda link: link.tenant_id,
        event_id_of=lambda link: link.tournament.event_id,
        logger=logger,
        describe=lambda link: f'event link {getattr(link, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'SpeedGaming sync', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
