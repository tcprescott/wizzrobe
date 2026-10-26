"""
UserRole Repository - Data Access Layer

Handles database operations for the userrole join table.

Roles are per-tenant, with one exception: ``SUPER_ADMIN`` is a global platform
role whose row carries ``tenant=NULL``. ``_tenant_id_for_role`` encodes that
invariant so every method scopes normal roles to the current tenant while
SUPER_ADMIN operations target the global (NULL-tenant) row.

Inside a community, the on-site roles (``Role.is_per_event``) are held in one
event and carry its ``event_id``; the rest carry NULL. Reads take an
:data:`EventChoice` saying which events' grants count, always alongside the
community-wide ones. tenant-scope and event-scope: exempt — every read here is
tenant-filtered by hand and event-filtered through :func:`role_event_q`, which
also counts the community-wide rows an ``event_scoped`` read would drop.
"""

from enum import Enum
from typing import Iterable, List, Optional, Set, Union, cast

from tortoise.expressions import Q

from application.event_context import get_current_event_id, require_event_id
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id
from models import EventStatus, Role, RoleSource, User, UserRole


class RoleScope(Enum):
    """Which events' grants a role read counts, besides the community-wide ones."""

    #: The live events: a community page (Equipment), or no event in scope.
    LIVE = 'live'
    #: Live and draft events: what a person still holds, for the membership rule.
    OPEN = 'open'
    #: Every event, archived ones too: the full record, for display.
    EVERY = 'every'


#: An event id, a :class:`RoleScope`, or None for the event in scope (and
#: :attr:`RoleScope.LIVE` when there is none).
EventChoice = Union[int, RoleScope, None]


def role_event_q(event: EventChoice = None) -> Q:
    """The filter that keeps community-wide grants plus those in ``event``."""
    if event is None:
        event = get_current_event_id() or RoleScope.LIVE
    community = Q(event_id=None)
    if event is RoleScope.EVERY:
        return Q()
    if event is RoleScope.LIVE:
        return community | Q(event__status=EventStatus.LIVE)
    if event is RoleScope.OPEN:
        return community | Q(event__status__in=[EventStatus.LIVE, EventStatus.DRAFT])
    return community | Q(event_id=event)


def _tenant_id_for_role(role: Role) -> Optional[int]:
    return None if role == Role.SUPER_ADMIN else current_tenant_id()


def event_id_for_role(role: Role, event_id: Optional[int] = None) -> Optional[int]:
    """The event a grant of ``role`` is written in: none for a community-wide
    role, else ``event_id``, else the event in scope (raising if there is none)."""
    if not role.is_per_event:
        return None
    return event_id if event_id is not None else require_event_id()


class UserRoleRepository:
    """Repository for UserRole data access."""

    @staticmethod
    async def add(
        user: User,
        role: Role,
        granted_by: Optional[User] = None,
        source: RoleSource = RoleSource.MANUAL,
        *,
        event_id: Optional[int] = None,
    ) -> UserRole:
        tenant_id = _tenant_id_for_role(role)
        instance, created = await UserRole.get_or_create(
            user=user, role=role, tenant_id=tenant_id, event_id=event_id_for_role(role, event_id),
            defaults={'granted_by': granted_by, 'source': source},
        )
        # A manual grant pins the role so a later Discord sync won't revoke it.
        if not created and source == RoleSource.MANUAL and instance.source != RoleSource.MANUAL:
            instance.source = RoleSource.MANUAL
            instance.granted_by = granted_by
            await save_fields(instance, 'source', 'granted_by')
        return instance

    @staticmethod
    async def remove(user: User, role: Role, *, event_id: Optional[int] = None) -> int:
        tenant_id = _tenant_id_for_role(role)
        rows = await UserRole.filter(
            user=user, role=role, tenant_id=tenant_id, event_id=event_id_for_role(role, event_id),
        )
        # One by one, so the archive guard sees each row: an archived event's
        # grants stay as they were.
        for row in rows:
            await row.delete()
        return len(rows)

    @staticmethod
    async def list_for_user(user: User, event: EventChoice = RoleScope.EVERY) -> List[UserRole]:
        """Grants the user holds in the current tenant (excludes the global
        SUPER_ADMIN row — check ``AuthService.is_super_admin`` for that)."""
        return await UserRole.filter(role_event_q(event), user=user, tenant_id=current_tenant_id())

    @staticmethod
    async def list_for_user_by_source(
        user: User, source: RoleSource, event: EventChoice = RoleScope.EVERY,
    ) -> List[UserRole]:
        return await UserRole.filter(
            role_event_q(event), user=user, source=source, tenant_id=current_tenant_id()
        )

    @staticmethod
    async def list_user_ids_by_source(source: RoleSource, event: EventChoice = RoleScope.EVERY) -> List[int]:
        """Everyone holding a role from ``source`` in the current tenant."""
        ids = await UserRole.filter(
            role_event_q(event), source=source, tenant_id=current_tenant_id()
        ).distinct().values_list('user_id', flat=True)
        return cast(List[int], ids)

    @staticmethod
    async def user_ids_with_roles(roles: Iterable[Role], event: EventChoice = None) -> Set[int]:
        """Everyone holding any of ``roles`` in the current tenant and ``event``."""
        roles = [role for role in roles if role != Role.SUPER_ADMIN]
        if not roles:
            return set()
        ids = await UserRole.filter(
            role_event_q(event), role__in=roles, tenant_id=current_tenant_id(),
        ).values_list('user_id', flat=True)
        return set(cast(List[int], ids))

    @staticmethod
    async def event_ids_for_user(user: User) -> Set[int]:
        """The events the user holds a per-event grant in, in the current tenant."""
        ids = await UserRole.filter(
            user=user, tenant_id=current_tenant_id(), event_id__isnull=False,
        ).values_list('event_id', flat=True)
        return set(cast(List[int], ids))

    @staticmethod
    async def any_with_role(role: Role) -> bool:
        """Whether anyone holds the role in the current tenant, in any event (cross-event)."""
        return await UserRole.filter(role=role, tenant_id=_tenant_id_for_role(role)).exists()

    @staticmethod
    async def list_users_with_role(
        role: Role, *, active_only: bool = False, event: EventChoice = None,
    ) -> List[User]:
        """Holders of ``role``: community-wide, or in ``event`` (default: the one in scope)."""
        query = UserRole.filter(role_event_q(event), role=role, tenant_id=_tenant_id_for_role(role))
        if active_only:
            query = query.filter(user__is_active=True)
        rows = await query.prefetch_related('user')
        users: dict[int, User] = {}
        for row in rows:
            users.setdefault(row.user.id, row.user)
        return list(users.values())
