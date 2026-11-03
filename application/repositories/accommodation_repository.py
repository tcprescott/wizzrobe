"""
Accommodation Repository - Data Access Layer

Handles database operations for ADA accommodation requests.
"""

from typing import Iterable, List, Optional, Set

from tortoise.expressions import Q

from application.repositories._base import EventScopedRepository
from application.repositories._event import current_event_id, event_scoped
from application.repositories._tenant import current_tenant_id
from models import AccommodationRequest, AccommodationStatus, User


class AccommodationRepository(EventScopedRepository[AccommodationRequest]):
    """Repository for accommodation requests, one per person per event."""

    model = AccommodationRequest

    @staticmethod
    async def get_by_id(obj_id: int) -> Optional[AccommodationRequest]:
        return await AccommodationRequest.get_or_none(
            id=obj_id, tenant_id=current_tenant_id(),
        ).prefetch_related('user')

    @staticmethod
    async def get_for_user(user: User) -> Optional[AccommodationRequest]:
        return await AccommodationRequest.get_or_none(
            user_id=user.id, tenant_id=current_tenant_id(), event_id=current_event_id(),
        )

    @staticmethod
    async def list_by_status(statuses: Iterable[AccommodationStatus]) -> List[AccommodationRequest]:
        """Requests in any of ``statuses``, oldest first so the queue reads in order asked."""
        return await event_scoped(
            AccommodationRequest.filter(status__in=list(statuses))
        ).order_by('created_at').prefetch_related('user')

    @staticmethod
    async def list_for_users(
        user_ids: Iterable[int], status: AccommodationStatus,
    ) -> List[AccommodationRequest]:
        return await event_scoped(
            AccommodationRequest.filter(user_id__in=list(user_ids), status=status)
        )

    @staticmethod
    async def user_ids_with_status(statuses: Iterable[AccommodationStatus]) -> Set[int]:
        rows = await event_scoped(
            AccommodationRequest.filter(status__in=list(statuses))
        ).values_list('user_id', flat=True)
        return {int(r) for r in rows}  # type: ignore[call-overload]

    @staticmethod
    async def count_needing_action() -> int:
        """New and Acknowledged requests, plus Arranged ones changed since."""
        return await event_scoped(
            AccommodationRequest.filter(
                Q(status__in=[AccommodationStatus.NEW, AccommodationStatus.ACKNOWLEDGED])
                | Q(status=AccommodationStatus.ARRANGED, changed_since_arranged=True)
            )
        ).count()
