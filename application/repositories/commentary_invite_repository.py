"""
Commentary Invite Repository - Data Access Layer

Handles all database operations for the CommentaryInvite model.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from application.event_writes import guarded_update
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import CommentaryInvite, CommentaryInviteStatus, Match, User


class CommentaryInviteRepository:
    """Repository for commentary partner invites."""

    @staticmethod
    async def create(match: Match, inviter: User, invitee: User) -> CommentaryInvite:
        return await CommentaryInvite.create(
            match=match, inviter=inviter, invitee=invitee, tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def get_by_id(invite_id: int) -> Optional[CommentaryInvite]:
        return await scoped(CommentaryInvite.filter(id=invite_id)).prefetch_related(
            'inviter', 'invitee', 'match', 'match__tournament', 'match__stage',
        ).first()

    @staticmethod
    async def open_for_inviter(match_id: int, inviter_id: int) -> List[CommentaryInvite]:
        """The inviter's invites on this match still waiting on an answer."""
        return await scoped(CommentaryInvite.filter(
            match_id=match_id, inviter_id=inviter_id, status=CommentaryInviteStatus.PENDING,
        )).prefetch_related('invitee', 'inviter', 'match')

    @staticmethod
    async def pending_for_inviter(match_id: int, inviter_id: int) -> bool:
        """Whether the inviter is already waiting on someone's answer for this match."""
        return await scoped(CommentaryInvite.filter(
            match_id=match_id, inviter_id=inviter_id, status=CommentaryInviteStatus.PENDING,
        )).exists()

    @staticmethod
    async def pending_for_pair(match_id: int, invitee_id: int) -> bool:
        return await scoped(CommentaryInvite.filter(
            match_id=match_id, invitee_id=invitee_id, status=CommentaryInviteStatus.PENDING,
        )).exists()

    @staticmethod
    async def count_sent_since(inviter_id: int, since: datetime) -> int:
        return await scoped(CommentaryInvite.filter(inviter_id=inviter_id, created_at__gte=since)).count()

    @staticmethod
    async def pending_for_invitee(invitee_id: int) -> List[CommentaryInvite]:
        return await scoped(CommentaryInvite.filter(
            invitee_id=invitee_id, status=CommentaryInviteStatus.PENDING,
        )).prefetch_related(
            'inviter', 'match', 'match__tournament', 'match__players__user',
        ).order_by('created_at')

    @staticmethod
    async def pending_by_match(match_ids: List[int]) -> Dict[int, List[CommentaryInvite]]:
        """``{match_id: [pending invite, ...]}`` for a whole board in one query."""
        if not match_ids:
            return {}
        rows = await scoped(CommentaryInvite.filter(
            match_id__in=match_ids, status=CommentaryInviteStatus.PENDING,
        )).prefetch_related('inviter', 'invitee').order_by('created_at')
        by_match: Dict[int, List[CommentaryInvite]] = {}
        for row in rows:
            by_match.setdefault(row.match_id, []).append(row)
        return by_match

    @staticmethod
    async def set_status(invite: CommentaryInvite, status: CommentaryInviteStatus) -> CommentaryInvite:
        invite.status = status
        invite.responded_at = datetime.now(timezone.utc)
        await save_fields(invite, 'status', 'responded_at')
        return invite

    @staticmethod
    async def record_dm(invite_id: int, channel_id: int, message_id: int) -> None:
        await guarded_update(scoped(CommentaryInvite.filter(id=invite_id)), dm_channel_id=channel_id, dm_message_id=message_id)

    @staticmethod
    async def move_to_match(from_match_id: int, to_match_id: int) -> int:
        """Carry every invite on a crew hold over to the booking that replaces it."""
        return await guarded_update(scoped(CommentaryInvite.filter(match_id=from_match_id)), match_id=to_match_id)
