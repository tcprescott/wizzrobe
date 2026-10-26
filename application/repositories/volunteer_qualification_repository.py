"""
VolunteerQualification Repository - Data Access Layer

Which positions a volunteer is qualified to fill.
"""

from typing import Dict, List, Set

from tortoise.transactions import in_transaction

from application.event_writes import guarded_delete
from application.repositories._tenant import current_tenant_id, scoped
from models import User, VolunteerQualification


class VolunteerQualificationRepository:
    """Repository for volunteer position qualifications."""

    @staticmethod
    async def qualified_position_ids(user: User) -> Set[int]:
        ids = await scoped(VolunteerQualification.filter(user=user)).values_list('position_id', flat=True)
        return set(ids)

    @staticmethod
    async def set_for_user(user: User, position_ids: List[int]) -> None:
        """Replace all qualifications for ``user`` with the given position IDs."""
        async with in_transaction():
            await guarded_delete(scoped(VolunteerQualification.filter(user=user)))
            for pid in position_ids:
                await VolunteerQualification.create(tenant_id=current_tenant_id(), user_id=user.id, position_id=pid)

    @staticmethod
    async def position_ids_by_user(user_ids: List[int]) -> Dict[int, Set[int]]:
        """user_id -> the positions they are qualified for, in this tenant only.

        Users are global, so an unscoped read would hand one community's
        qualifications to another's auto-scheduler.
        """
        rows = await scoped(VolunteerQualification.filter(user_id__in=user_ids)).values_list(
            'user_id', 'position_id',
        )
        out: Dict[int, Set[int]] = {}
        for uid, pid in rows:
            out.setdefault(uid, set()).add(pid)
        return out

    @staticmethod
    async def qualified_user_ids_for_position(position_id: int) -> Set[int]:
        ids = await scoped(VolunteerQualification.filter(position_id=position_id)).values_list('user_id', flat=True)
        return set(ids)

    @staticmethod
    async def list_all() -> List[VolunteerQualification]:
        return await scoped(VolunteerQualification.all()).prefetch_related('user', 'position')
