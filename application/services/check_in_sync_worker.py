"""Matcherino check-in sync worker.

Polls each open :class:`~models.CheckInEvent` that has a venue, on that event's
own ``sync_interval_minutes``, so the desk's roster keeps up with late
registrations without anyone pressing Sync. Each event's work runs inside its
tenant's ``tenant_scope`` as the reserved system ``User``, and skips tenants
that have check-in turned off.

Ungated by an environment switch, like the async-qualifier worker: it only ever
touches events staff have opened, and a deployment with none has nothing for it
to find. A failing sync (Matcherino down, its shape changed) is recorded on the
event by the service and retried next tick; it never stops the other events.
A refused Matcherino login logs at INFO: the saved token expires routinely, and
the app already warns staff on Admin → Check-in and by DM. Any other new
failure logs at ERROR, and the same failure on the next tick drops to WARNING.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


def _due(event, now: datetime) -> bool:
    interval = timedelta(minutes=event.sync_interval_minutes or 5)
    last = event.last_synced_at
    if event.last_sync_error and event.updated_at is not None:
        # A failed sync leaves last_synced_at at the last *good* sync; back off
        # from the failure instead so a dead endpoint isn't hit every tick.
        last = max(last, event.updated_at) if last else event.updated_at
    return last is None or now - last >= interval


def _is_repeat(error: ValueError, previous_error: Optional[str]) -> bool:
    cause = error.__cause__
    return cause is not None and previous_error is not None and str(cause)[:1000] == previous_error


def _failure_level(error: ValueError, previous_error: Optional[str]) -> int:
    from application.utils.clients.matcherino_client import MatcherinoAuthError

    if isinstance(error.__cause__, MatcherinoAuthError):
        return logging.INFO
    return logging.WARNING if _is_repeat(error, previous_error) else logging.ERROR


async def _tick() -> None:
    from application.repositories import CheckInEventRepository
    from application.services.check_in_service import CheckInService
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.user_service import UserService
    from models import FeatureFlag

    events = await CheckInEventRepository.list_syncable_all()
    if not events:
        return

    now = datetime.now(timezone.utc)
    system_user = await UserService().get_system_user()
    service = CheckInService()

    async def _sync(event) -> None:
        if not _due(event, now):
            return
        if not await FeatureFlagService().is_enabled(FeatureFlag.EVENT_CHECK_IN):
            return
        previous_error = event.last_sync_error
        try:
            result = await service.sync_event(system_user, event.id, audit=False)
        except ValueError as e:
            logger.log(_failure_level(e, previous_error), 'Check-in sync for event %s failed: %s', event.id, e)
            return
        logger.info('Check-in sync for event %s: %s', event.id, result.as_dict())

    await for_each_tenant_scoped(
        events,
        _sync,
        tenant_id_of=lambda event: event.tenant_id,
        event_id_of=lambda event: event.event_id,
        logger=logger,
        describe=lambda event: f'check-in event {getattr(event, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'Check-in sync', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
