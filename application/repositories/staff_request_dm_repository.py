"""Staff Request DM Repository - Data Access Layer

The Discord message each staff member got about a pending request, kept so it
can be edited once someone decides the request.
"""

from typing import List

from models import StaffRequestDM, StaffRequestKind

from ._tenant import current_tenant_id, scoped


class StaffRequestDMRepository:
    """Data access for :class:`StaffRequestDM`."""

    @staticmethod
    async def record(
        kind: StaffRequestKind, object_id: int, user_id: int, channel_id: int, message_id: int,
    ) -> StaffRequestDM:
        return await StaffRequestDM.create(
            tenant_id=current_tenant_id(), kind=kind, object_id=object_id,
            user_id=user_id, channel_id=channel_id, message_id=message_id,
        )

    @staticmethod
    async def list_for(kind: StaffRequestKind, object_id: int) -> List[StaffRequestDM]:
        return await scoped(StaffRequestDM.filter(kind=kind, object_id=object_id)).order_by('id')

    @staticmethod
    async def forget(kind: StaffRequestKind, object_id: int) -> int:
        return await scoped(StaffRequestDM.filter(kind=kind, object_id=object_id)).delete()

    @staticmethod
    async def rebind(kind: StaffRequestKind, old_id: int, new_id: int) -> int:
        """Point the DMs about ``old_id`` at ``new_id``, for a request that came back."""
        return await scoped(StaffRequestDM.filter(kind=kind, object_id=old_id)).update(object_id=new_id)
