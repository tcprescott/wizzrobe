"""
Personal Webhook Repository - Data Access Layer

Member-owned outbound webhooks (see ``PersonalWebhookService``).
"""

from datetime import datetime
from typing import List, Optional

from tortoise.expressions import F

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import PersonalWebhook


class PersonalWebhookRepository(TenantScopedRepository[PersonalWebhook]):
    model = PersonalWebhook

    @staticmethod
    async def list_for_user(user_id: int) -> List[PersonalWebhook]:
        return await scoped(PersonalWebhook.filter(user_id=user_id)).order_by('created_at', 'id')

    @staticmethod
    async def count_for_user(user_id: int) -> int:
        return await scoped(PersonalWebhook.filter(user_id=user_id)).count()

    @staticmethod
    async def get_for_user(webhook_id: int, user_id: int) -> Optional[PersonalWebhook]:
        return await PersonalWebhook.get_or_none(
            id=webhook_id, user_id=user_id, tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def list_active() -> List[PersonalWebhook]:
        return await scoped(PersonalWebhook.filter(is_active=True)).prefetch_related('user')

    @staticmethod
    async def record_success(webhook_id: int, status: Optional[int], when: datetime) -> None:
        await PersonalWebhook.filter(id=webhook_id, tenant_id=current_tenant_id()).update(
            consecutive_failures=0, last_delivery_at=when, last_status=status,
            last_error=None, delivered_count=F('delivered_count') + 1,
        )

    @staticmethod
    async def record_failure(
        webhook_id: int, status: Optional[int], error: str, when: datetime,
    ) -> int:
        """Count one failed delivery; returns the failure streak after it."""
        await PersonalWebhook.filter(id=webhook_id, tenant_id=current_tenant_id()).update(
            consecutive_failures=F('consecutive_failures') + 1, last_delivery_at=when,
            last_status=status, last_error=error[:255], failed_count=F('failed_count') + 1,
        )
        row = await PersonalWebhook.get_or_none(id=webhook_id, tenant_id=current_tenant_id()).only('id', 'consecutive_failures')
        return row.consecutive_failures if row else 0

    @staticmethod
    async def pause(webhook_id: int, reason: str) -> None:
        await PersonalWebhook.filter(id=webhook_id, tenant_id=current_tenant_id()).update(is_active=False, paused_reason=reason[:255])
