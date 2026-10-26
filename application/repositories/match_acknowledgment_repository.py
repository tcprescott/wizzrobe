"""
Match Acknowledgment Repository - Data Access Layer

Handles all database operations for MatchAcknowledgment model.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from application.event_writes import guarded_delete
from application.repositories._tenant import current_tenant_id, scoped
from models import Match, MatchAcknowledgment, User


class MatchAcknowledgmentRepository:
    """Repository for match acknowledgment data access."""

    @staticmethod
    async def list_for_match(match: Match) -> List[MatchAcknowledgment]:
        return await scoped(MatchAcknowledgment.filter(match=match)).prefetch_related('user')

    @staticmethod
    async def list_for_matches(match_ids: List[int]) -> Dict[int, List[MatchAcknowledgment]]:
        if not match_ids:
            return {}
        rows = await scoped(MatchAcknowledgment.filter(match_id__in=match_ids)).prefetch_related('user')
        result: Dict[int, List[MatchAcknowledgment]] = {mid: [] for mid in match_ids}
        for row in rows:
            result.setdefault(row.match_id, []).append(row)
        return result

    @staticmethod
    async def get(match: Match, user: User) -> Optional[MatchAcknowledgment]:
        return await MatchAcknowledgment.get_or_none(match=match, user=user, tenant_id=current_tenant_id())

    @staticmethod
    async def upsert(
        match: Match,
        user: User,
        *,
        acknowledged: bool,
        auto: bool,
    ) -> MatchAcknowledgment:
        acknowledged_at = datetime.now(timezone.utc) if acknowledged else None
        ack, _ = await MatchAcknowledgment.update_or_create(
            tenant_id=current_tenant_id(),
            match=match,
            user=user,
            defaults={
                'acknowledged_at': acknowledged_at,
                'auto_acknowledged': auto if acknowledged else False,
            },
        )
        return ack

    @staticmethod
    async def reconcile(match: Match, users: List[User]) -> tuple[int, int]:
        """Make the rows match ``users`` without rewriting the answers already given.

        The non-destructive counterpart to ``upsert`` + ``delete_for_match``,
        which together restart the question: this creates a row for a player who
        has none and drops rows for people no longer in the match, and touches
        nothing else. Returns ``(created, removed)``.
        """
        by_id = {u.id: u for u in users}
        existing = set(await scoped(
            MatchAcknowledgment.filter(match=match),
        ).values_list('user_id', flat=True))

        created = 0
        for uid, user in by_id.items():
            if uid in existing:
                continue
            await MatchAcknowledgment.create(
                tenant_id=current_tenant_id(), match=match, user=user,
                acknowledged_at=None, auto_acknowledged=False,
            )
            created += 1

        stale = existing - set(by_id)
        removed = 0
        if stale:
            removed = await guarded_delete(scoped(
                MatchAcknowledgment.filter(match=match, user_id__in=list(stale)),
            ))
        return created, removed

    @staticmethod
    async def delete_for_match(match: Match) -> int:
        return await guarded_delete(scoped(MatchAcknowledgment.filter(match=match)))

    @staticmethod
    async def delete_for_user(match: Match, user: User) -> None:
        await guarded_delete(scoped(MatchAcknowledgment.filter(match=match, user=user)))
