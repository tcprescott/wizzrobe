"""
Match Stream Volunteer Repository - Data Access Layer

Handles all database operations for the MatchStreamVolunteer model.
"""

from typing import Dict, List, Optional, Tuple

from application.event_writes import guarded_delete
from application.repositories._tenant import current_tenant_id, scoped
from models import Match, MatchStreamVolunteer, User


class MatchStreamVolunteerRepository:
    """Repository for stream-volunteer data access."""

    @staticmethod
    async def get_by_match(match: Match) -> List[MatchStreamVolunteer]:
        return await scoped(MatchStreamVolunteer.filter(match=match)).prefetch_related('user')

    @staticmethod
    async def get_by_match_and_user(match: Match, user: User) -> Optional[MatchStreamVolunteer]:
        return await MatchStreamVolunteer.get_or_none(
            match=match, user=user, tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def get_by_user(user: User) -> List[MatchStreamVolunteer]:
        return await scoped(MatchStreamVolunteer.filter(user=user))

    @staticmethod
    async def get_match_ids_for_user(user: User) -> List[int]:
        rows = await scoped(
            MatchStreamVolunteer.filter(user=user)
        ).values_list('match_id', flat=True)
        # flat=True yields scalars; the stub types values_list as tuples.
        return list(rows)  # type: ignore[arg-type]

    @staticmethod
    async def is_volunteer(match_id: int, user_id: int) -> bool:
        return await scoped(
            MatchStreamVolunteer.filter(match_id=match_id, user_id=user_id)
        ).exists()

    @staticmethod
    async def names_by_match(match_ids: List[int]) -> Dict[int, List[str]]:
        """``{match_id: [volunteer name, ...]}`` for a whole board in one query.

        The board renders one line per row, so the alternative is a query per
        row. Empty ``match_ids`` short-circuits rather than fetching the
        tenant's entire volunteer history.
        """
        if not match_ids:
            return {}
        rows = await scoped(
            MatchStreamVolunteer.filter(match_id__in=match_ids)
        ).prefetch_related('user').order_by('created_at')
        by_match: Dict[int, List[str]] = {}
        for row in rows:
            by_match.setdefault(row.match_id, []).append(row.user.preferred_name)
        return by_match

    @staticmethod
    async def get_or_create(match: Match, user: User) -> Tuple[MatchStreamVolunteer, bool]:
        return await MatchStreamVolunteer.get_or_create(
            tenant_id=current_tenant_id(), match=match, user=user,
        )

    @staticmethod
    async def delete_by_match_and_user(match: Match, user: User) -> bool:
        deleted = await guarded_delete(scoped(MatchStreamVolunteer.filter(match=match, user=user)))
        return bool(deleted)
