"""Tenant Membership Repository.

Ties a global :class:`~models.User` to the :class:`~models.Tenant` rows they
belong to. Queried across tenants (membership is *how* the auth layer decides
whether a user may see a tenant at all), so it is **not** tenant-scoped.
"""

from typing import Iterable, List, Optional, Set

from models import MembershipSource, TenantMemberBlock, TenantMembership, User, UserRole


class TenantMembershipRepository:
    """Cross-tenant membership lookups + writes."""

    @staticmethod
    async def is_member(user_id: int, tenant_id: int) -> bool:
        return await TenantMembership.exists(user_id=user_id, tenant_id=tenant_id)

    @staticmethod
    async def member_ids(user_ids: Iterable[int], tenant_id: int) -> Set[int]:
        """The subset of ``user_ids`` who belong to ``tenant_id`` (one query)."""
        wanted = set(user_ids)
        if not wanted:
            return set()
        rows = await TenantMembership.filter(
            user_id__in=list(wanted), tenant_id=tenant_id,
        ).values_list('user_id', flat=True)
        return set(rows)  # type: ignore[arg-type]  # flat=True yields ints; Tortoise types it as tuples

    @staticmethod
    async def belongs_elsewhere(user_id: int, tenant_id: int) -> bool:
        """Whether the user is a member of, or holds a role in, any other tenant."""
        if await TenantMembership.filter(user_id=user_id).exclude(tenant_id=tenant_id).exists():
            return True
        return await UserRole.filter(
            user_id=user_id, tenant_id__isnull=False,
        ).exclude(tenant_id=tenant_id).exists()

    @staticmethod
    async def add(
        user: User, tenant_id: int, source: Optional[MembershipSource] = None,
    ) -> TenantMembership:
        """Idempotent; ``source`` is recorded only when the row is new."""
        membership, _ = await TenantMembership.get_or_create(
            user=user, tenant_id=tenant_id, defaults={'source': source},
        )
        return membership

    @staticmethod
    async def is_blocked(user_id: int, tenant_id: int) -> bool:
        return await TenantMemberBlock.exists(user_id=user_id, tenant_id=tenant_id)

    @staticmethod
    async def blocked_ids(user_ids: Iterable[int], tenant_id: int) -> Set[int]:
        """The subset of ``user_ids`` blocked from ``tenant_id`` (one query)."""
        wanted = set(user_ids)
        if not wanted:
            return set()
        rows = await TenantMemberBlock.filter(
            user_id__in=list(wanted), tenant_id=tenant_id,
        ).values_list('user_id', flat=True)
        return set(rows)  # type: ignore[arg-type]

    @staticmethod
    async def bulk_add(user_ids: Iterable[int], tenant_id: int, source: MembershipSource) -> None:
        """Memberships for ``user_ids`` in one insert; existing rows are left alone."""
        rows = [
            TenantMembership(user_id=user_id, tenant_id=tenant_id, source=source)
            for user_id in set(user_ids)
        ]
        if rows:
            await TenantMembership.bulk_create(rows, ignore_conflicts=True)

    @staticmethod
    async def block(user_id: int, tenant_id: int, blocked_by: Optional[User]) -> None:
        await TenantMemberBlock.get_or_create(
            user_id=user_id, tenant_id=tenant_id, defaults={'blocked_by': blocked_by},
        )

    @staticmethod
    async def unblock(user_id: int, tenant_id: int) -> int:
        return await TenantMemberBlock.filter(user_id=user_id, tenant_id=tenant_id).delete()

    @staticmethod
    async def add_returning_created(
        user: User, tenant_id: int, source: Optional[MembershipSource] = None,
    ) -> bool:
        """:meth:`add`, reporting whether the row is new."""
        _membership, created = await TenantMembership.get_or_create(
            user=user, tenant_id=tenant_id, defaults={'source': source},
        )
        return created

    @staticmethod
    async def remove(user_id: int, tenant_id: int) -> int:
        return await TenantMembership.filter(user_id=user_id, tenant_id=tenant_id).delete()

    @staticmethod
    async def list_for_user(user: User) -> List[TenantMembership]:
        return await TenantMembership.filter(user=user).prefetch_related('tenant')

    @staticmethod
    async def list_for_tenant(tenant_id: int) -> List[TenantMembership]:
        return await TenantMembership.filter(tenant_id=tenant_id).prefetch_related('user')
