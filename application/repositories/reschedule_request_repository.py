"""
Reschedule Request Repository - Data Access Layer

Handles all database operations for the MatchRescheduleRequest model.
"""

from typing import Dict, List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import MatchRescheduleRequest, RescheduleRequestStatus, User

_FULL = ('requested_by', 'decided_by', 'match', 'match__tournament', 'match__players__user')


class RescheduleRequestRepository(TenantScopedRepository[MatchRescheduleRequest]):
    """Repository for player reschedule/cancellation requests."""

    model = MatchRescheduleRequest

    @classmethod
    async def get_by_id(cls, obj_id: int) -> Optional[MatchRescheduleRequest]:
        """Overridden to prefetch: every caller needs the match and requester."""
        return await MatchRescheduleRequest.get_or_none(
            id=obj_id, tenant_id=current_tenant_id(),
        ).prefetch_related(*_FULL)

    @staticmethod
    async def list_pending(
        tournament_ids: Optional[List[int]] = None,
        event_id: Optional[int] = None,
    ) -> List[MatchRescheduleRequest]:
        """The staff queue, oldest first — the one waiting longest is the one to answer.

        ``tournament_ids`` narrows it to the tournaments a viewer operates, the
        same way the schedule board narrows itself; ``event_id`` to the board's event.
        """
        qs = MatchRescheduleRequest.filter(status=RescheduleRequestStatus.PENDING)
        if tournament_ids is not None:
            qs = qs.filter(match__tournament_id__in=tournament_ids)
        if event_id is not None:
            qs = qs.filter(match__event_id=event_id)
        return await scoped(qs).prefetch_related(*_FULL).order_by('created_at')

    @staticmethod
    async def list_for_match(match_id: int) -> List[MatchRescheduleRequest]:
        return await scoped(
            MatchRescheduleRequest.filter(match_id=match_id)
        ).prefetch_related(*_FULL).order_by('-created_at')

    @staticmethod
    async def list_for_user(user: User, limit: int = 25) -> List[MatchRescheduleRequest]:
        """A player's own requests, newest first — their half of the loop."""
        return await scoped(
            MatchRescheduleRequest.filter(requested_by=user)
        ).prefetch_related(*_FULL).order_by('-created_at').limit(limit)

    @staticmethod
    async def list_pending_against(user_id: int) -> List[MatchRescheduleRequest]:
        """Open requests on matches ``user_id`` plays in that someone else raised.

        The opponent's half of the loop: what they have been asked to agree to.
        Oldest first, like the staff queue.
        """
        return await scoped(
            MatchRescheduleRequest.filter(
                status=RescheduleRequestStatus.PENDING,
                match__players__user_id=user_id,
            ).exclude(requested_by_id=user_id)
        ).distinct().prefetch_related(*_FULL).order_by('created_at')

    @staticmethod
    async def get_pending_for(match_id: int, user_id: int) -> Optional[MatchRescheduleRequest]:
        """This player's open request on this match, if they have one."""
        return await scoped(
            MatchRescheduleRequest.filter(
                match_id=match_id,
                requested_by_id=user_id,
                status=RescheduleRequestStatus.PENDING,
            )
        ).first()

    @staticmethod
    async def list_pending_for_match(match_id: int) -> List[MatchRescheduleRequest]:
        """Every open request on one match, whoever raised it.

        Approving one settles the match, so the service closes the others out
        rather than leaving a queue entry pointing at a time that already moved.
        """
        return await scoped(
            MatchRescheduleRequest.filter(
                match_id=match_id, status=RescheduleRequestStatus.PENDING,
            )
        ).prefetch_related('requested_by')

    @staticmethod
    async def pending_match_ids_for_user(user_id: int) -> List[int]:
        """Matches where this player already has an open request.

        The set the board subtracts, so a viewer never sees "Ask to reschedule"
        on a match they have already asked about.
        """
        rows = await scoped(
            MatchRescheduleRequest.filter(
                requested_by_id=user_id, status=RescheduleRequestStatus.PENDING,
            )
        ).values_list('match_id', flat=True)
        return list(rows)  # type: ignore[arg-type]

    @staticmethod
    async def pending_count() -> int:
        """How many are waiting — the number on the admin queue strip."""
        return await scoped(
            MatchRescheduleRequest.filter(status=RescheduleRequestStatus.PENDING)
        ).count()

    @staticmethod
    async def pending_match_ids() -> List[int]:
        """Match ids with an open request, for the strip's one-click filter."""
        rows = await scoped(
            MatchRescheduleRequest.filter(status=RescheduleRequestStatus.PENDING)
        ).values_list('match_id', flat=True)
        return sorted(set(rows))  # type: ignore[arg-type]

    @staticmethod
    async def pending_by_match(match_ids: List[int]) -> Dict[int, int]:
        """``{match_id: open request count}`` for a whole board in one query.

        The board badges one cell per row, so the alternative is a query per
        row. Empty ``match_ids`` short-circuits rather than counting the
        tenant's entire request history.
        """
        if not match_ids:
            return {}
        rows = await scoped(
            MatchRescheduleRequest.filter(
                match_id__in=match_ids, status=RescheduleRequestStatus.PENDING,
            )
        ).values_list('match_id', flat=True)
        counts: Dict[int, int] = {}
        for match_id in rows:
            counts[match_id] = counts.get(match_id, 0) + 1  # type: ignore[call-overload,index]
        return counts
