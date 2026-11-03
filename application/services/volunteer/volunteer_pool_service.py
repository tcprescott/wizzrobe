"""
Volunteer Pool Service - Business Logic Layer

The assignable-volunteer pool used by the coordinator UI, the auto-scheduler,
hours and the export. Membership comes from roles alone: Staff, or Volunteer
held in the event being scheduled.
"""

from typing import List

from application.repositories import UserRoleRepository
from models import Role, User

ASSIGNABLE_ROLES = (Role.VOLUNTEER, Role.STAFF)


class VolunteerPoolService:
    """Who coordinators can schedule: VOLUNTEER or STAFF role holders."""

    async def assignable_user_ids(self) -> set[int]:
        """Ids of every STAFF holder **in the current tenant**, and every
        VOLUNTEER in the event in scope.

        Roles are per-tenant (``UserRole.tenant``), so scoping to the active
        tenant keeps another community's volunteers out of this coordinator's
        roster and auto-scheduler pool; last year's volunteers stay out of this
        year's until someone grants them the role again.
        """
        return await UserRoleRepository.user_ids_with_roles(ASSIGNABLE_ROLES)

    async def assignable_volunteers(self) -> List[User]:
        """The pool as users, by name."""
        user_ids = await self.assignable_user_ids()
        if not user_ids:
            return []
        users = await User.filter(id__in=user_ids)
        return sorted(users, key=lambda u: u.preferred_name.lower())
