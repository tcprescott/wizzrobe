"""
AI Provider Repository - Data Access Layer

A community's one AI provider row (provider, model, sealed key, daily cap).
"""

from typing import Any, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import AiProviderSetting


class AiProviderRepository(TenantScopedRepository[AiProviderSetting]):
    """Repository for the current tenant's AiProviderSetting."""

    model = AiProviderSetting

    async def get_current(self) -> Optional[AiProviderSetting]:
        return await scoped(AiProviderSetting.all()).first()

    async def upsert(self, **fields: Any) -> AiProviderSetting:
        setting, _ = await AiProviderSetting.update_or_create(tenant_id=current_tenant_id(), defaults=fields)
        return setting

    async def delete_current(self) -> int:
        return await scoped(AiProviderSetting.all()).delete()
