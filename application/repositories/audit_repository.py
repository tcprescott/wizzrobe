"""
Audit Repository - Data Access Layer

Filtered/paginated queries over AuditLog.
"""

from collections import Counter
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from tortoise import connections
from tortoise.expressions import Q
from tortoise.queryset import QuerySet

from application.repositories._tenant import current_tenant_id, scoped
from application.timezone_context import get_zone
from application.utils.timezone import to_local
from models import AuditLog


class AuditRepository:
    """Repository for AuditLog data access.

    Reads are scoped to the current tenant and raise without one (AuditLog.tenant
    is nullable, so an unscoped ``tenant_id=None`` filter would read the
    NULL-tenant platform rows instead of failing).
    """

    @staticmethod
    def _filtered(
        *,
        start: Optional[datetime],
        end: Optional[datetime],
        user_id: Optional[int],
        action_contains: Optional[str],
        action_prefixes: Optional[Sequence[str]],
        actions_in: Optional[Sequence[str]],
    ) -> QuerySet:
        query = scoped(AuditLog.all())
        if start is not None:
            query = query.filter(created_at__gte=start)
        if end is not None:
            query = query.filter(created_at__lte=end)
        if user_id is not None:
            query = query.filter(user_id=user_id)
        if action_prefixes is not None:
            area = Q(*[Q(action__startswith=p) for p in action_prefixes], join_type='OR') \
                if action_prefixes else Q(id__in=[])
            query = query.filter(area)
        if action_contains or actions_in:
            text = Q(action__icontains=action_contains) if action_contains else Q(id__in=[])
            if actions_in:
                text = text | Q(action__in=list(actions_in))
            query = query.filter(text)
        return query

    @staticmethod
    async def list(
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        user_id: Optional[int] = None,
        action_contains: Optional[str] = None,
        action_prefixes: Optional[Sequence[str]] = None,
        actions_in: Optional[Sequence[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditLog]:
        query = AuditRepository._filtered(
            start=start, end=end, user_id=user_id, action_contains=action_contains,
            action_prefixes=action_prefixes, actions_in=actions_in,
        )
        query = query.order_by('-created_at', '-id').offset(offset).limit(limit)
        return await query.prefetch_related('user')

    @staticmethod
    async def count(
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        user_id: Optional[int] = None,
        action_contains: Optional[str] = None,
        action_prefixes: Optional[Sequence[str]] = None,
        actions_in: Optional[Sequence[str]] = None,
    ) -> int:
        return await AuditRepository._filtered(
            start=start, end=end, user_id=user_id, action_contains=action_contains,
            action_prefixes=action_prefixes, actions_in=actions_in,
        ).count()

    @staticmethod
    async def daily_category_counts(
        start: datetime, end: datetime, zone_name: str,
    ) -> Dict[Tuple[str, date], int]:
        """Rows per (action namespace, local day in ``zone_name``), ``start``..``end`` inclusive.

        The namespace is the part of ``verb.object`` before the dot. A year of an
        active community is a few hundred thousand rows; Postgres groups them in
        ~0.1s, where hydrating each row in Python took seconds of event-loop time.
        """
        tenant_id = current_tenant_id()
        client = connections.get('default')
        if client.capabilities.dialect == 'postgres':
            rows = await client.execute_query_dict(
                'SELECT split_part(coalesce(action, \'other\'), \'.\', 1) AS category, '
                '(created_at AT TIME ZONE $1)::date AS day, count(*) AS n '
                'FROM auditlog WHERE tenant_id = $2 AND created_at >= $3 AND created_at <= $4 '
                'GROUP BY 1, 2',
                [zone_name, tenant_id, start, end],
            )
            return {((r['category'] or 'other'), r['day']): r['n'] for r in rows}
        zone = get_zone(zone_name)
        logs = await scoped(AuditLog.filter(created_at__gte=start, created_at__lte=end)) \
            .values_list('action', 'created_at')
        return dict(Counter(
            ((action or 'other').split('.', 1)[0], to_local(created_at, zone).date())  # type: ignore[union-attr]
            for action, created_at in logs
        ))
