"""Sent Message Repository - Data Access Layer

One row per DM Wizzrobe sent in a community: what the recipient got and how
each channel went. Backs the staff Sent messages report.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from tortoise.expressions import Q
from tortoise.functions import Count
from tortoise.queryset import QuerySet

from models import SentMessage, SentMessageStatus

from ._tenant import current_tenant_id, scoped


class SentMessageRepository:
    """Data access for :class:`SentMessage`."""

    @staticmethod
    async def create(**fields: Any) -> SentMessage:
        return await SentMessage.create(tenant_id=current_tenant_id(), **fields)

    @staticmethod
    async def update(message_id: int, tenant_id: int, **fields: Any) -> int:
        # Explicit tenant: the web-push mirror reports back from the dispatch
        # worker, which runs outside any tenant scope.
        return await scoped(SentMessage.filter(id=message_id), tenant_id).update(**fields)

    @staticmethod
    def _filtered(
        start: Optional[datetime],
        end: Optional[datetime],
        statuses: Optional[List[SentMessageStatus]] = None,
        search: Optional[str] = None,
        recipient_id: Optional[int] = None,
    ) -> QuerySet:
        qs = scoped(SentMessage.all())
        if start is not None:
            qs = qs.filter(created_at__gte=start)
        if end is not None:
            qs = qs.filter(created_at__lt=end)
        if statuses:
            qs = qs.filter(status__in=statuses)
        if recipient_id is not None:
            qs = qs.filter(recipient_id=recipient_id)
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(body__icontains=search)
                | Q(recipient__display_name__icontains=search)
                | Q(recipient__username__icontains=search)
                | Q(discord_id=search)
            )
        return qs

    @staticmethod
    async def list(
        start: Optional[datetime],
        end: Optional[datetime],
        statuses: Optional[List[SentMessageStatus]] = None,
        search: Optional[str] = None,
        recipient_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SentMessage]:
        qs = SentMessageRepository._filtered(start, end, statuses, search, recipient_id)
        return await (
            qs.order_by('-created_at', '-id').offset(offset).limit(limit).prefetch_related('recipient')
        )

    @staticmethod
    async def count(
        start: Optional[datetime],
        end: Optional[datetime],
        statuses: Optional[List[SentMessageStatus]] = None,
        search: Optional[str] = None,
        recipient_id: Optional[int] = None,
    ) -> int:
        return await SentMessageRepository._filtered(start, end, statuses, search, recipient_id).count()

    @staticmethod
    async def get(message_id: int) -> Optional[SentMessage]:
        return await scoped(SentMessage.filter(id=message_id)).prefetch_related('recipient').first()

    @staticmethod
    async def totals(start: Optional[datetime], end: Optional[datetime]) -> Dict[str, int]:
        """Counts by status, plus ``silent``, over the window, in two grouped queries."""
        qs = SentMessageRepository._filtered(start, end)
        rows = await qs.annotate(n=Count('id')).group_by('status').values('status', 'n')
        totals = {str(getattr(r['status'], 'value', r['status'])): r['n'] for r in rows}
        totals['silent'] = await SentMessageRepository._filtered(start, end).filter(silent=True).count()
        return totals
