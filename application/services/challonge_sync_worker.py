"""Challonge auto-sync worker.

Challonge has no webhooks, so a community that turns on automatic sync
(Settings → Integrations → Challonge) has its unfinished linked brackets
re-read on the interval it picked. The worker checks every five minutes which
brackets are due. Each community's work runs in its ``tenant_scope`` as the
system ``User`` and skips communities with Challonge turned off or not
connected. One community failing never stops the others.
"""

import logging

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 300


async def _tick() -> None:
    from application.repositories import ChallongeRepository
    from application.services.challonge_service import ChallongeService
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.user_service import UserService
    from models import FeatureFlag

    if not ChallongeService.is_configured():
        return
    tenant_ids = sorted(await ChallongeRepository.linked_tenant_ids())
    if not tenant_ids:
        return
    system_user = await UserService().get_system_user()

    async def _sync(_tenant_id: int) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE):
            return
        service = ChallongeService()
        if not await service.auto_sync_minutes():
            return
        if await ChallongeRepository.get_connection() is None:
            return
        synced = await service.sync_due_brackets(system_user)
        logger.debug('Challonge auto-sync: %s brackets', synced)

    await for_each_tenant_scoped(
        tenant_ids,
        _sync,
        tenant_id_of=lambda tenant_id: tenant_id,
        logger=logger,
        describe=lambda tenant_id: f'Challonge brackets of tenant {tenant_id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'Challonge auto-sync', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
