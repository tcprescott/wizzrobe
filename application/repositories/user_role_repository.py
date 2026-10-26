"""
UserRole Repository - Data Access Layer

Handles database operations for the userrole join table.

Roles are per-tenant, with one exception: ``SUPER_ADMIN`` is a global platform
role whose row carries ``tenant=NULL``. ``_tenant_id_for_role`` encodes that
invariant so every method scopes normal roles to the current tenant while
SUPER_ADMIN operations target the global (NULL-tenant) row.
"""

from typing import Iterable, List, Optional, Set, cast

from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id
from models import Role, RoleSource, User, UserRole


def _tenant_id_for_role(role: Role) -> Optional[int]:
    return None if role == Role.SUPER_ADMIN else current_tenant_id()


class UserRoleRepository:
    """Repository for UserRole data access."""

    @staticmethod
    async def add(
        user: User,
        role: Role,
        granted_by: Optional[User] = None,
        source: RoleSource = RoleSource.MANUAL,
    ) -> UserRole:
        tenant_id = _tenant_id_for_role(role)
        instance, created = await UserRole.get_or_create(
            user=user, role=role, tenant_id=tenant_id,
            defaults={'granted_by': granted_by, 'source': source},
        )
        # A manual grant pins the role so a later Discord sync won't revoke it.
        if not created and source == RoleSource.MANUAL and instance.source != RoleSource.MANUAL:
            instance.source = RoleSource.MANUAL
            instance.granted_by = granted_by
            await save_fields(instance, 'source', 'granted_by')
        return instance

    @staticmethod
    async def remove(user: User, role: Role) -> int:
        tenant_id = _tenant_id_for_role(role)
        return await UserRole.filter(user=user, role=role, tenant_id=tenant_id).delete()

    @staticmethod
    async def list_for_user(user: User) -> List[UserRole]:
        """Roles the user holds in the current tenant (excludes the global
        SUPER_ADMIN row — check ``AuthService.is_super_admin`` for that)."""
        return await UserRole.filter(user=user, tenant_id=current_tenant_id())

    @staticmethod
    async def list_for_user_by_source(user: User, source: RoleSource) -> List[UserRole]:
        return await UserRole.filter(
            user=user, source=source, tenant_id=current_tenant_id()
        )

    @staticmethod
    async def list_user_ids_by_source(source: RoleSource) -> List[int]:
        """Everyone holding a role from ``source`` in the current tenant."""
        ids = await UserRole.filter(
            source=source, tenant_id=current_tenant_id()
        ).distinct().values_list('user_id', flat=True)
        return cast(List[int], ids)

    @staticmethod
    async def user_ids_with_roles(roles: Iterable[Role]) -> Set[int]:
        """Everyone holding any of ``roles`` in the current tenant."""
        roles = [role for role in roles if role != Role.SUPER_ADMIN]
        if not roles:
            return set()
        ids = await UserRole.filter(
            role__in=roles, tenant_id=current_tenant_id(),
        ).values_list('user_id', flat=True)
        return set(cast(List[int], ids))

    @staticmethod
    async def any_with_role(role: Role) -> bool:
        """Whether anyone holds the role in the current tenant."""
        return await UserRole.filter(role=role, tenant_id=_tenant_id_for_role(role)).exists()

    @staticmethod
    async def list_users_with_role(role: Role, *, active_only: bool = False) -> List[User]:
        query = UserRole.filter(role=role, tenant_id=_tenant_id_for_role(role))
        if active_only:
            query = query.filter(user__is_active=True)
        rows = await query.prefetch_related('user')
        return [r.user for r in rows]
