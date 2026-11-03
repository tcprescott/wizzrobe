"""
VolunteerRoleMapping Repository - Data Access Layer

Volunteer-position-to-app-role mappings.
"""

from typing import List, Optional

from application.repositories._base import EventScopedRepository
from application.repositories._event import event_scoped
from application.repositories._tenant import current_tenant_id, scoped
from models import Role, VolunteerRoleMapping


class VolunteerRoleMappingRepository(EventScopedRepository[VolunteerRoleMapping]):
    """Repository for VolunteerRoleMapping data access. A mapping follows its position's event."""

    model = VolunteerRoleMapping

    @classmethod
    async def get_by_id(cls, obj_id: int) -> Optional[VolunteerRoleMapping]:
        return await VolunteerRoleMapping.get_or_none(
            id=obj_id, tenant_id=current_tenant_id(),
        ).prefetch_related('position')

    @staticmethod
    async def list_all() -> List[VolunteerRoleMapping]:
        return await event_scoped(VolunteerRoleMapping.all()).prefetch_related('position').order_by(
            'position__display_order', 'position__name', 'app_role',
        )

    @staticmethod
    async def exists(position_id: int, app_role: Role) -> bool:
        return await scoped(
            VolunteerRoleMapping.filter(position_id=position_id, app_role=app_role)
        ).exists()
