"""Tenant Repository — lookups for the tenancy machinery.

Intentionally **not** tenant-scoped: a tenant is resolved *before* any tenant
context exists (from the URL by the middleware, or from a Discord guild id by the
bot), and the ``/platform`` super-admin surface queries every tenant. So these
methods never call the ``_tenant`` scoping helpers.

A Discord guild may back several tenants (multiple communities sharing one
server), so guild lookup is ``list_by_guild_id`` — the bot fans out over every
tenant linked to the guild rather than resolving a single one.
"""

from typing import Iterable, List, Optional, Set

from application.repositories._base import TenantScopedRepository
from models import Tenant


class TenantRepository(TenantScopedRepository[Tenant]):
    """Cross-tenant lookups + CRUD for :class:`~models.Tenant`.

    ``Tenant`` is resolved before any tenant context exists, so ``create`` and
    ``get_by_id`` are overridden to stay unscoped; only the tenant-agnostic
    ``update``/``delete`` come from the base.
    """

    model = Tenant

    @staticmethod
    async def get_by_id(tenant_id: int) -> Optional[Tenant]:
        return await Tenant.get_or_none(id=tenant_id)

    @staticmethod
    async def get_by_slug(slug: str) -> Optional[Tenant]:
        return await Tenant.get_or_none(slug=slug)

    @staticmethod
    async def get_by_domain(domain: str) -> Optional[Tenant]:
        return await Tenant.get_or_none(domain=domain)

    @staticmethod
    async def list_by_guild_id(guild_id: int) -> List[Tenant]:
        # A Discord guild may back several tenants, so this returns every tenant
        # linked to the guild (stable id order). Callers fan out over the result.
        return await Tenant.filter(discord_guild_id=guild_id).order_by('id')

    @staticmethod
    async def list_all() -> List[Tenant]:
        return await Tenant.all().order_by('name')

    @staticmethod
    async def active_ids(ids: Iterable[int]) -> Set[int]:
        wanted = set(ids)
        if not wanted:
            return set()
        rows = await Tenant.filter(id__in=wanted, is_active=True).values_list('id', flat=True)
        return set(rows)  # type: ignore[arg-type]

    @staticmethod
    async def slug_exists(slug: str, exclude_id: Optional[int] = None) -> bool:
        query = Tenant.filter(slug=slug)
        if exclude_id is not None:
            query = query.exclude(id=exclude_id)
        return await query.exists()

    @staticmethod
    async def domain_exists(domain: str, exclude_id: Optional[int] = None) -> bool:
        query = Tenant.filter(domain=domain)
        if exclude_id is not None:
            query = query.exclude(id=exclude_id)
        return await query.exists()

    @staticmethod
    async def create(**fields) -> Tenant:
        return await Tenant.create(**fields)

    @staticmethod
    async def set_feature_group(tenant_id: int, group_id: Optional[int]) -> None:
        """Assign (or clear, with ``None``) a tenant's feature-flag group."""
        await Tenant.filter(id=tenant_id).update(feature_group_id=group_id)

    @staticmethod
    async def clear_feature_group(group_id: int) -> None:
        """Un-assign every tenant on a group (used before deleting it)."""
        await Tenant.filter(feature_group_id=group_id).update(feature_group_id=None)

    @staticmethod
    async def delete(tenant: Tenant) -> None:
        await tenant.delete()
