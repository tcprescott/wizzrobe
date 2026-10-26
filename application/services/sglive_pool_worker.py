"""sglive.speedgaming.org prize pool sync worker.

Every 15 minutes, reads the sglive data feed once and writes each linked
tournament's total into its ``prize_pool``, inside that tournament's
``tenant_scope`` as the reserved system ``User``. Skips communities with Prize
Payouts turned off.

Ungated by an environment switch, like the check-in worker: it only does
anything once staff have linked a tournament, and with none linked it never
calls sglive. A failed read leaves every pool at its last value. The first
failure logs at ERROR so Sentry sees it; the same failure again drops to
WARNING, so a long sglive outage is one issue rather than one per tick. A
linked game that has vanished from the board logs at WARNING.
"""

import logging
from typing import Optional

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 15 * 60

_last_error: Optional[str] = None


async def _tick() -> None:
    global _last_error
    from application.repositories import TournamentPayoutRepository
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.payout_service import PayoutService
    from application.services.user_service import UserService
    from application.utils.clients.sglive_client import SgliveAPIError, fetch_games
    from models import FeatureFlag

    linked = await TournamentPayoutRepository.list_sglive_linked_all()
    if not linked:
        return

    try:
        games = await fetch_games()
    except SgliveAPIError as e:
        level = logging.WARNING if str(e) == _last_error else logging.ERROR
        logger.log(level, 'sglive prize pool read failed: %s', e)
        _last_error = str(e)
        return
    _last_error = None

    system_user = await UserService().get_system_user()
    service = PayoutService()
    tenants = sorted({t.tenant_id for t in linked})  # type: ignore[attr-defined]

    async def _apply(tenant_id: int) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.PAYOUTS):
            return
        missing = await service.apply_sglive_games(games, system_user)
        if missing:
            logger.warning(
                'sglive no longer lists %s (community %s); their pools keep their last value',
                ', '.join(missing), tenant_id,
            )

    await for_each_tenant_scoped(
        tenants,
        _apply,
        tenant_id_of=lambda tenant_id: tenant_id,
        each_live_event=True,
        logger=logger,
        describe=lambda tenant_id: f'community {tenant_id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'sglive prize pools', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
