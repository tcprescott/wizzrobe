"""
Personal Webhook Outbox Repository - Data Access Layer

Personal webhook messages waiting to be sent (see ``PersonalWebhookService``).
"""

from datetime import datetime
from typing import Any, List

from tortoise.expressions import Q

from application.repositories._tenant import current_tenant_id, scoped
from models import PersonalWebhookOutbox


class PersonalWebhookOutboxRepository:
    """Data access for :class:`PersonalWebhookOutbox`."""

    @staticmethod
    async def create(**fields: Any) -> PersonalWebhookOutbox:
        return await PersonalWebhookOutbox.create(tenant_id=current_tenant_id(), **fields)

    @staticmethod
    async def claim(row_id: int, now: datetime, until: datetime) -> bool:
        """Take a waiting message until ``until``; False when another sender holds it or it is gone."""
        taken = await scoped(PersonalWebhookOutbox.filter(id=row_id).filter(
            Q(claimed_until=None) | Q(claimed_until__lt=now),
        )).update(claimed_until=until)
        return bool(taken)

    @staticmethod
    async def delete(row_id: int) -> None:
        await scoped(PersonalWebhookOutbox.filter(id=row_id)).delete()

    @staticmethod
    async def list_stale(older_than: datetime, now: datetime, limit: int = 100) -> List[PersonalWebhookOutbox]:
        """Messages nobody is sending: queued before ``older_than`` and unclaimed or past their claim."""
        return await (
            scoped(PersonalWebhookOutbox.filter(created_at__lt=older_than))
            .filter(Q(claimed_until=None) | Q(claimed_until__lt=now))
            .prefetch_related('webhook__user')
            .order_by('id')
            .limit(limit)
        )

    @staticmethod
    async def tenants_with_rows() -> List[int]:
        """Cross-tenant worker scan (unscoped on purpose): the communities with a message waiting."""
        return list(await PersonalWebhookOutbox.all().distinct().values_list('tenant_id', flat=True))  # type: ignore[arg-type]
