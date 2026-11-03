"""Patreon sync worker.

Webhooks are how badges usually change; this catches what they miss. Every
five minutes it refreshes each connected community's creator token when it's
close to lapsing, and runs a full pass over the campaign's members when one is
due (every six hours), when staff pressed Sync now, or right after a member
linked whose membership Patreon didn't show at sign-in. Each community runs in
its own ``tenant_scope`` as the system ``User`` and is skipped with Patreon
turned off; one failing never stops the others.
"""

import logging

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 300


async def _tick() -> None:
    from application.repositories import PatreonRepository
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.patreon_campaign_service import PatreonCampaignService
    from application.services.user_service import UserService
    from models import FeatureFlag

    tenant_ids = sorted(await PatreonRepository.connected_tenant_ids())
    if not tenant_ids:
        return
    system_user = await UserService().get_system_user()

    async def _sync(_tenant_id: int) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.PATREON):
            return
        changed = await PatreonCampaignService().sync_if_due(system_user)
        if changed:
            logger.info('Patreon sync changed %s badges', changed)

    await for_each_tenant_scoped(
        tenant_ids,
        _sync,
        tenant_id_of=lambda tenant_id: tenant_id,
        logger=logger,
        describe=lambda tenant_id: f'Patreon campaign of tenant {tenant_id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'Patreon sync', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
