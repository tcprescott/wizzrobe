"""
Triforce Text Repository - Data Access Layer

Handles all database operations for the TriforceText model.
"""

from datetime import datetime, timezone
from typing import List, Optional

from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import Tournament, TriforceText, User

APPROVAL_STATUSES = ('pending', 'approved', 'rejected')


def _status_to_approved_filter(status: Optional[str]) -> object:
    """Translate a status string into the ``approved`` column filter."""
    if status is None:
        return _UNSET
    if status == 'pending':
        return None
    if status == 'approved':
        return True
    if status == 'rejected':
        return False
    raise ValueError(f"Unknown triforce text status: {status!r}")


_UNSET = object()


class TriforceTextRepository:
    """Repository for triforce text data access."""

    @staticmethod
    async def get_by_id(text_id: int) -> Optional[TriforceText]:
        return await TriforceText.get_or_none(
            id=text_id, tenant_id=current_tenant_id()
        ).prefetch_related(
            'tournament', 'user', 'approved_by'
        )

    @staticmethod
    async def list_by_tournament(
        tournament: Tournament,
        status: Optional[str] = None,
    ) -> List[TriforceText]:
        """List texts for a tournament.

        ``status`` may be ``None`` (all), ``'pending'``, ``'approved'``, or
        ``'rejected'``.
        """
        query = scoped(TriforceText.filter(tournament=tournament))
        approved = _status_to_approved_filter(status)
        if approved is not _UNSET:
            query = query.filter(approved=approved)
        return await query.order_by('-created_at').prefetch_related('user', 'approved_by')

    @staticmethod
    async def list_by_tournament_and_user(
        tournament: Tournament, user: User
    ) -> List[TriforceText]:
        return await scoped(TriforceText.filter(
            tournament=tournament, user=user
        )).order_by('-created_at')

    @staticmethod
    async def list_approved(tournament: Tournament) -> List[TriforceText]:
        return await scoped(TriforceText.filter(tournament=tournament, approved=True))

    @staticmethod
    async def list_approved_user_buckets(tournament: Tournament) -> List[Optional[int]]:
        """Distinct user-id buckets that have at least one approved text.

        Includes ``None`` as a bucket when there are approved texts whose
        submitter has since been deleted (FK set to NULL). Treating NULL
        as its own bucket keeps those texts in rotation for balanced
        selection.
        """
        rows = await scoped(TriforceText.filter(
            tournament=tournament, approved=True
        )).distinct().values_list('user_id', flat=True)
        return list(rows)

    @staticmethod
    async def list_approved_by_user(
        tournament: Tournament, user_id: Optional[int]
    ) -> List[TriforceText]:
        return await scoped(TriforceText.filter(
            tournament=tournament, approved=True, user_id=user_id
        ))

    @staticmethod
    async def create(
        tournament: Tournament,
        user: Optional[User],
        text: str,
        author: Optional[str],
    ) -> TriforceText:
        return await TriforceText.create(
            tenant_id=current_tenant_id(),
            tournament=tournament,
            user=user,
            text=text,
            author=author,
            approved=None,
        )

    @staticmethod
    async def update(triforce_text: TriforceText, **changes) -> TriforceText:
        await triforce_text.update_from_dict(changes)
        await save_fields(triforce_text, *changes)
        return triforce_text

    @staticmethod
    async def set_moderation(
        triforce_text: TriforceText, approved: bool, actor: User
    ) -> TriforceText:
        return await TriforceTextRepository.update(
            triforce_text,
            approved=approved,
            approved_by=actor,
            approved_at=datetime.now(timezone.utc),
        )

    @staticmethod
    async def delete(triforce_text: TriforceText) -> None:
        await triforce_text.delete()
