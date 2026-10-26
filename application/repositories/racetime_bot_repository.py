"""Racetime Bot Repository — data access for the platform-managed bots.

``RacetimeBot`` is **global** (no tenant column), so this repository is
deliberately *not* tenant-scoped: the bots and their SUPER_ADMIN authorization
grants (``RacetimeBotTenant``) are managed on ``/platform`` with explicit ids.
``list_active_for_tenant`` is the one tenant-facing read — it takes the tenant
id explicitly (the caller passes ``require_tenant_id()``) rather than relying on
ambient scope, so it never leaks another tenant's grants.
"""

from typing import Any, List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._save import save_fields
from models import RacetimeBot, RacetimeBotTenant


class RacetimeBotRepository(TenantScopedRepository[RacetimeBot]):
    """Global CRUD for :class:`~models.RacetimeBot` and its tenant grants.

    ``RacetimeBot`` has no tenant column, so ``create``/``get_by_id`` are
    overridden to stay unscoped; only the tenant-agnostic ``update``/``delete``
    come from the base.
    """

    model = RacetimeBot

    # ---- bots (global) ----------------------------------------------------

    async def list_all(self) -> List[RacetimeBot]:
        return await RacetimeBot.all().order_by('category')

    async def list_active(self) -> List[RacetimeBot]:
        """Every active bot — the set the runtime opens a connection for."""
        return await RacetimeBot.filter(is_active=True).order_by('category')

    async def get_by_id(self, bot_id: int) -> Optional[RacetimeBot]:
        return await RacetimeBot.get_or_none(id=bot_id)

    async def get_by_category(self, category: str) -> Optional[RacetimeBot]:
        return await RacetimeBot.get_or_none(category=category)

    async def create(self, **fields: Any) -> RacetimeBot:
        return await RacetimeBot.create(**fields)

    # ---- tenant authorization grants (many-to-many) ----------------------

    async def get_grant(self, bot_id: int, tenant_id: int) -> Optional[RacetimeBotTenant]:
        return await RacetimeBotTenant.get_or_none(bot_id=bot_id, tenant_id=tenant_id)

    async def list_grants_for_bot(self, bot_id: int) -> List[RacetimeBotTenant]:
        return await RacetimeBotTenant.filter(bot_id=bot_id).prefetch_related('tenant').order_by('tenant_id')

    async def create_grant(self, bot_id: int, tenant_id: int) -> RacetimeBotTenant:
        return await RacetimeBotTenant.create(bot_id=bot_id, tenant_id=tenant_id)

    async def set_grant_active(self, grant: RacetimeBotTenant, is_active: bool) -> RacetimeBotTenant:
        grant.is_active = is_active
        await save_fields(grant, 'is_active')
        return grant

    async def delete_grant(self, grant: RacetimeBotTenant) -> None:
        await grant.delete()

    async def list_active_for_tenant(self, tenant_id: int) -> List[RacetimeBot]:
        """Active bots a tenant is authorized (via an active grant) to use.

        Cross-tenant by construction (``RacetimeBot`` is global); the tenant id
        is passed explicitly so this can never surface a category the tenant was
        not granted.
        """
        grants = await RacetimeBotTenant.filter(
            tenant_id=tenant_id, is_active=True, bot__is_active=True,
        ).prefetch_related('bot')
        bots = [g.bot for g in grants]
        bots.sort(key=lambda b: b.category)
        return bots
