"""Webhook outbox sweeper.

Every minute, sends the staff and personal webhook messages that were written
to their outbox but never sent: the process that queued them stopped, or the
send that claimed them died mid-way. A row only counts as stuck once it is older than
``STALE_SECONDS`` and nobody holds its claim, so it never races a live send.

A row whose webhook was turned off is closed as failed, and so is one still
unsent after ``GIVE_UP_AFTER``: a receiver gets nothing rather than a day-old
event out of order. Personal messages have no delivery log, so theirs are
dropped instead, as are all of a community's once it turns personal webhooks
off.
"""

import logging
from datetime import datetime, timedelta, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
STALE_SECONDS = 120
GIVE_UP_AFTER = timedelta(days=1)
BATCH = 100


async def _sweep_tenant(tenant_id: int) -> None:
    from application.repositories import WebhookDeliveryRepository
    from application.services.webhook_service import WebhookService

    now = datetime.now(timezone.utc)
    service = WebhookService()
    rows = await WebhookDeliveryRepository().list_stale_pending(
        older_than=now - timedelta(seconds=STALE_SECONDS), now=now, limit=BATCH,
    )
    for delivery in rows:
        if delivery.created_at < now - GIVE_UP_AFTER:
            await service.give_up(delivery, 'Not sent within a day')
        elif not delivery.webhook.is_active:
            await service.give_up(delivery, 'Webhook was turned off before this was sent')
        else:
            await service.attempt(delivery.webhook, delivery)


async def _sweep_personal(tenant_id: int) -> None:
    from application.repositories import PersonalWebhookOutboxRepository
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.personal_webhook_service import PersonalWebhookService
    from models import FeatureFlag

    now = datetime.now(timezone.utc)
    service = PersonalWebhookService()
    enabled = await FeatureFlagService().is_enabled(FeatureFlag.PERSONAL_WEBHOOKS)
    rows = await PersonalWebhookOutboxRepository.list_stale(
        older_than=now - timedelta(seconds=STALE_SECONDS), now=now, limit=BATCH,
    )
    for row in rows:
        if not enabled or not row.webhook.is_active or row.created_at < now - GIVE_UP_AFTER:
            await service.drop(row)
        else:
            await service.attempt(row.webhook, row)


async def _sweep_both(tenant_id: int) -> None:
    await _sweep_tenant(tenant_id)
    await _sweep_personal(tenant_id)


async def _tick() -> None:
    from application.repositories import PersonalWebhookOutboxRepository, WebhookDeliveryRepository

    tenants = sorted({
        *await WebhookDeliveryRepository.tenants_with_pending(),
        *await PersonalWebhookOutboxRepository.tenants_with_rows(),
    })
    await for_each_tenant_scoped(
        tenants,
        _sweep_both,
        tenant_id_of=lambda tenant_id: tenant_id,
        logger=logger,
        describe=lambda tenant_id: f'community {tenant_id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'webhook outbox', logger, tick_timeout=TICK_SECONDS * 5)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
