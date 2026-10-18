"""Feature Flag Repository — per-tenant flag **override** rows (tri-state).

A :class:`~models.TenantFeatureFlag` row is an explicit exception layered over the
tenant's group-derived availability. ``available``/``enabled`` are tri-state
(NULL = inherit). Reads take an explicit tenant id (the service resolves the
ambient tenant); a row left all-NULL carries no information and is deleted.
"""

from typing import Dict, List, Optional

from application.repositories._save import save_fields
from models import TenantFeatureFlag

# Sentinel distinguishing "leave this column untouched" from "set it to None".
_UNSET = object()


class TenantFeatureFlagRepository:
    """Data access for per-tenant feature-flag override rows."""

    @staticmethod
    async def list_for_tenant(tenant_id: int) -> List[TenantFeatureFlag]:
        return await TenantFeatureFlag.filter(tenant_id=tenant_id)

    @staticmethod
    async def map_for_tenant(tenant_id: int) -> Dict[str, TenantFeatureFlag]:
        """``flag`` key → override row, for one tenant (single query)."""
        return {r.flag: r for r in await TenantFeatureFlag.filter(tenant_id=tenant_id)}

    @staticmethod
    async def get_for_tenant(tenant_id: int, flag: str) -> Optional[TenantFeatureFlag]:
        return await TenantFeatureFlag.get_or_none(tenant_id=tenant_id, flag=flag)

    @staticmethod
    async def set_override(
        tenant_id: int, flag: str, *, available=_UNSET, enabled=_UNSET,
    ) -> Optional[TenantFeatureFlag]:
        """Set or clear the tri-state override columns for one ``(tenant, flag)``.

        Pass ``available``/``enabled`` = ``True``/``False`` to force, or ``None``
        to clear that tier back to inherit; omit a kwarg to leave it untouched. A
        row left with both columns NULL is deleted (``None`` returned) so an
        override never lingers as a no-op.
        """
        row = await TenantFeatureFlag.get_or_none(tenant_id=tenant_id, flag=flag)
        if row is None:
            row = TenantFeatureFlag(tenant_id=tenant_id, flag=flag)
        if available is not _UNSET:
            row.available = available
        if enabled is not _UNSET:
            row.enabled = enabled
        if row.available is None and row.enabled is None:
            if row.pk is not None:
                await row.delete()
            return None
        await save_fields(row, 'available', 'enabled')
        return row
