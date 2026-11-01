"""
Webhook Delivery Repository - Data Access Layer

Handles database operations for the outbound-webhook delivery log, which is
also the webhook outbox: a delivery is written ``pending`` before its first
attempt and finished in place.
"""

from datetime import datetime
from typing import Any, List, Optional

from tortoise.expressions import Q
from tortoise.functions import Count, Max

from application.repositories._tenant import current_tenant_id, scoped
from models import Webhook, WebhookDelivery


class WebhookDeliveryRepository:
    """Repository for WebhookDelivery data access."""

    async def create(self, **fields: Any) -> WebhookDelivery:
        return await WebhookDelivery.create(tenant_id=current_tenant_id(), **fields)

    async def get(self, delivery_id: int) -> Optional[WebhookDelivery]:
        return await scoped(WebhookDelivery.filter(id=delivery_id)).first()

    async def claim(self, delivery: WebhookDelivery, now: datetime, until: datetime) -> bool:
        """Take a pending delivery for one sender until ``until``; False when another sender holds it or it is done."""
        taken = await scoped(WebhookDelivery.filter(id=delivery.id, pending=True).filter(
            Q(claimed_until=None) | Q(claimed_until__lt=now),
        )).update(claimed_until=until)
        return bool(taken)

    async def finish(self, delivery: WebhookDelivery, **fields: Any) -> None:
        await scoped(WebhookDelivery.filter(id=delivery.id)).update(pending=False, claimed_until=None, **fields)

    async def list_stale_pending(self, older_than: datetime, now: datetime,
                                 limit: int = 100) -> List[WebhookDelivery]:
        """Pending deliveries nobody is sending: queued before ``older_than`` and unclaimed or past their claim."""
        return await (
            scoped(WebhookDelivery.filter(pending=True, created_at__lt=older_than).filter(Q(claimed_until=None) | Q(claimed_until__lt=now)))
            .prefetch_related('webhook')
            .order_by('id')
            .limit(limit)
        )

    @staticmethod
    async def tenants_with_pending() -> List[int]:
        """Cross-tenant worker scan (unscoped on purpose): the communities that have any pending delivery."""
        return list(await WebhookDelivery.filter(pending=True).distinct().values_list('tenant_id', flat=True))

    async def list_for_webhook(
        self, webhook: Webhook, limit: int = 50, offset: int = 0
    ) -> List[WebhookDelivery]:
        """Most recent finished deliveries first for a single webhook."""
        return (
            await scoped(WebhookDelivery.filter(webhook=webhook, pending=False))
            .order_by('-created_at', '-id')
            .offset(offset)
            .limit(limit)
        )

    async def outcome_counts_since(self, cutoff: datetime) -> List[dict]:
        """``{webhook_id, success, n, last}`` per outcome of finished deliveries since ``cutoff``.

        Aggregated in the database rather than by walking the rows: the health
        summary on the webhooks list must not cost one query per webhook, nor
        grow with a busy tenant's delivery volume.
        """
        rows = await (
            scoped(WebhookDelivery.filter(created_at__gte=cutoff, pending=False))
            .annotate(n=Count('id'), last=Max('created_at'))
            .group_by('webhook_id', 'success')
            .values('webhook_id', 'success', 'n', 'last')
        )
        return list(rows)

    async def prune_older_than(self, cutoff: datetime) -> int:
        """Delete finished delivery rows created before ``cutoff``; returns the count removed."""
        return await scoped(WebhookDelivery.filter(created_at__lt=cutoff, pending=False)).delete()
