"""
Match Hard Preset Opt-In Repository - Data Access Layer

Handles all database operations for the MatchHardPresetOptIn model.

Every method here is capable of naming who opted in, because agreement is
computed from exactly that. The secrecy rule lives one layer up, in
``MatchHardPresetService``: it is the only caller, and it never hands a set of
user ids to a surface. Adding a caller elsewhere would defeat the feature.
"""

from typing import Dict, List, Set, Tuple

from application.event_writes import guarded_delete
from application.repositories._tenant import current_tenant_id, scoped
from models import Match, MatchHardPresetOptIn, User


class MatchHardPresetRepository:
    """Repository for hard-preset opt-in data access."""

    @staticmethod
    async def has_opted_in(match_id: int, user_id: int) -> bool:
        return await scoped(
            MatchHardPresetOptIn.filter(match_id=match_id, user_id=user_id)
        ).exists()

    @staticmethod
    async def user_ids_for_match(match_id: int) -> Set[int]:
        """Everyone holding an opt-in row on this match.

        Used only to compare against the match's player set. Never rendered.
        """
        rows = await scoped(
            MatchHardPresetOptIn.filter(match_id=match_id)
        ).values_list('user_id', flat=True)
        # flat=True yields scalars; the stub types values_list as tuples.
        return set(rows)  # type: ignore[arg-type]

    @staticmethod
    async def opted_in_match_ids(user: User, match_ids: List[int]) -> Set[int]:
        """Which of ``match_ids`` this viewer has opted into, in one query.

        Bounded by the board's own rows rather than fetching the viewer's whole
        history, and returns only the viewer's own rows — the board has no
        business knowing about anyone else's.
        """
        if not match_ids:
            return set()
        rows = await scoped(
            MatchHardPresetOptIn.filter(user=user, match_id__in=list(match_ids))
        ).values_list('match_id', flat=True)
        return set(rows)  # type: ignore[arg-type]

    @staticmethod
    async def user_ids_by_match(match_ids: List[int]) -> Dict[int, Set[int]]:
        """``{match_id: {user_id, ...}}`` for a whole board in one query.

        Feeds the unanimity check for every visible row at once. As with
        :meth:`user_ids_for_match`, the ids are compared against rosters and
        never rendered.
        """
        if not match_ids:
            return {}
        rows = await scoped(
            MatchHardPresetOptIn.filter(match_id__in=list(match_ids))
        ).values_list('match_id', 'user_id')
        by_match: Dict[int, Set[int]] = {}
        for match_id, user_id in rows:
            by_match.setdefault(match_id, set()).add(user_id)
        return by_match

    @staticmethod
    async def get_or_create(match: Match, user: User) -> Tuple[MatchHardPresetOptIn, bool]:
        return await MatchHardPresetOptIn.get_or_create(
            tenant_id=current_tenant_id(), match=match, user=user,
        )

    @staticmethod
    async def delete_by_match_and_user(match: Match, user: User) -> bool:
        deleted = await guarded_delete(scoped(MatchHardPresetOptIn.filter(match=match, user=user)))
        return bool(deleted)

    @staticmethod
    async def delete_for_user_in_match(match_id: int, user_id: int) -> bool:
        """Drop one player's row when they leave the match's roster."""
        deleted = await guarded_delete(scoped(
            MatchHardPresetOptIn.filter(match_id=match_id, user_id=user_id)
        ))
        return bool(deleted)
