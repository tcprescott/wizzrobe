"""start.gg sync worker.

start.gg has no webhooks, so every linked event is re-read every two minutes:
new results, newly decided matchups (which DM their players) and roster
changes all arrive this way. Each community's work runs in its
``tenant_scope`` as the system ``User`` and skips communities with start.gg
turned off. One event failing never stops the others; the service logs it.
Nothing runs without a start.gg API token.
"""

import logging

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 120


async def _tick() -> None:
    from application.repositories import StartggRepository
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.startgg_connection_service import StartggConnectionService
    from application.services.startgg_service import StartggService
    from application.services.user_service import UserService
    from models import FeatureFlag

    if not StartggService.is_configured():
        return
    tenant_ids = sorted(await StartggRepository.linked_tenant_ids())
    if not tenant_ids:
        return
    system_user = await UserService().get_system_user()

    async def _sync(_tenant_id: int) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.STARTGG):
            return
        if not await StartggConnectionService().has_access():
            return
        synced = await StartggService().sync_linked_events(system_user)
        logger.debug('start.gg sync: %s events', synced)

    await for_each_tenant_scoped(
        tenant_ids,
        _sync,
        tenant_id_of=lambda tenant_id: tenant_id,
        each_live_event=True,
        logger=logger,
        describe=lambda tenant_id: f'start.gg events of tenant {tenant_id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'start.gg sync', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
