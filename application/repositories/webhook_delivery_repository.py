"""
Webhook Delivery Repository - Data Access Layer

Handles database operations for the outbound-webhook delivery log.
"""

from datetime import datetime
from typing import Any, List

from tortoise.functions import Count, Max

from application.repositories._tenant import current_tenant_id, scoped
from models import Webhook, WebhookDelivery


class WebhookDeliveryRepository:
    """Repository for WebhookDelivery data access."""

    async def create(self, **fields: Any) -> WebhookDelivery:
        return await WebhookDelivery.create(tenant_id=current_tenant_id(), **fields)

    async def list_for_webhook(
        self, webhook: Webhook, limit: int = 50, offset: int = 0
    ) -> List[WebhookDelivery]:
        """Most recent delivery attempts first for a single webhook."""
        return (
            await scoped(WebhookDelivery.filter(webhook=webhook))
            .order_by('-created_at', '-id')
            .offset(offset)
            .limit(limit)
        )

    async def outcome_counts_since(self, cutoff: datetime) -> List[dict]:
        """``{webhook_id, success, n, last}`` per outcome since ``cutoff``.

        Aggregated in the database rather than by walking the rows: the health
        summary on the webhooks list must not cost one query per webhook, nor
        grow with a busy tenant's delivery volume.
        """
        rows = await (
            scoped(WebhookDelivery.filter(created_at__gte=cutoff))
            .annotate(n=Count('id'), last=Max('created_at'))
            .group_by('webhook_id', 'success')
            .values('webhook_id', 'success', 'n', 'last')
        )
        return list(rows)

    async def prune_older_than(self, cutoff: datetime) -> int:
        """Delete delivery rows created before ``cutoff``; returns the count removed."""
        return await scoped(WebhookDelivery.filter(created_at__lt=cutoff)).delete()
