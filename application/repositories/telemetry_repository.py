"""
Telemetry Repository - Data Access Layer

Writes and read/aggregation queries over TelemetryEvent. Pure data access: no
business logic, auth, or JSON encoding (the service owns those). Aggregations
are computed in the database (GROUP BY / COUNT) so the report never loads the
full high-volume table into memory.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from tortoise.expressions import Q
from tortoise.functions import Count, Max

from application.tenant_context import get_current_tenant_id
from models import TelemetryEvent


class TelemetryRepository:
    """Repository for TelemetryEvent data access."""

    @staticmethod
    async def create(
        *,
        category: str,
        event_type: str,
        user_id: Optional[int] = None,
        path: Optional[str] = None,
        session_id: Optional[str] = None,
        details: Optional[str] = None,
        target: Optional[str] = None,
        device: Optional[str] = None,
    ) -> TelemetryEvent:
        # Nullable tenant: stamped from the ambient context (the event's tenant
        # for the domain mirror, the request/page tenant for page views); None
        # marks a platform-level row.
        return await TelemetryEvent.create(
            tenant_id=get_current_tenant_id(),
            category=category,
            event_type=event_type,
            user_id=user_id,
            path=path,
            session_id=session_id,
            details=details,
            target=target,
            device=device,
        )

    @staticmethod
    async def bulk_create(rows: List[dict]) -> None:
        tenant_id = get_current_tenant_id()
        await TelemetryEvent.bulk_create(
            [TelemetryEvent(tenant_id=tenant_id, **row) for row in rows]
        )

    @staticmethod
    def _filtered(
        *,
        start: Optional[datetime],
        end: Optional[datetime],
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        path_contains: Optional[str] = None,
    ):
        # Scope every read/aggregate to the current tenant (None -> platform
        # rows). Single choke point for list/count/top_* reads.
        query = TelemetryEvent.filter(tenant_id=get_current_tenant_id())
        if start is not None:
            query = query.filter(created_at__gte=start)
        if end is not None:
            query = query.filter(created_at__lt=end)
        if category:
            query = query.filter(category=category)
        if event_type:
            query = query.filter(event_type=event_type)
        if user_id is not None:
            query = query.filter(user_id=user_id)
        if session_id:
            query = query.filter(session_id=session_id)
        if path_contains:
            query = query.filter(path__icontains=path_contains)
        return query

    @classmethod
    async def list(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        path_contains: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TelemetryEvent]:
        query = cls._filtered(
            start=start, end=end, category=category, event_type=event_type,
            user_id=user_id, session_id=session_id, path_contains=path_contains,
        )
        query = query.order_by('-created_at', '-id').offset(offset).limit(limit)
        return await query.prefetch_related('user')

    @classmethod
    async def count(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        path_contains: Optional[str] = None,
    ) -> int:
        return await cls._filtered(
            start=start, end=end, category=category, event_type=event_type,
            user_id=user_id, session_id=session_id, path_contains=path_contains,
        ).count()

    # --------------------------------------------------------------- aggregates

    @classmethod
    async def count_distinct_users(
        cls, *, start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> int:
        # COUNT(DISTINCT user_id) in the DB — do not transfer the whole distinct
        # set into Python just to len() it.
        rows = await cls._filtered(start=start, end=end).filter(
            user_id__isnull=False
        ).annotate(c=Count('user_id', distinct=True)).values_list('c', flat=True)
        return rows[0] if rows else 0

    @classmethod
    async def count_distinct_users_by_event_type(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
    ) -> Dict[str, int]:
        rows = await (
            cls._filtered(start=start, end=end, category=category)
            .filter(user_id__isnull=False)
            .annotate(c=Count('user_id', distinct=True))
            .group_by('event_type')
            .values('event_type', 'c')
        )
        return {row['event_type']: row['c'] for row in rows}

    @classmethod
    async def distinct_users_since(cls, start: datetime) -> Tuple[int, Dict[int, int]]:
        """Distinct signed-in users with any row since ``start``: overall, and per tenant id.

        tenant-scope: exempt — a platform-wide head count for Sentry Metrics,
        returning only numbers. A person active in two communities counts once
        in each and once in the total.
        """
        recent = TelemetryEvent.filter(created_at__gte=start, user_id__isnull=False)
        totals = await recent.annotate(c=Count('user_id', distinct=True)).values_list('c', flat=True)
        rows = await (
            TelemetryEvent.filter(created_at__gte=start, user_id__isnull=False, tenant_id__isnull=False)
            .annotate(c=Count('user_id', distinct=True))
            .group_by('tenant_id')
            .values('tenant_id', 'c')
        )
        total = int(totals[0]) if totals else 0  # type: ignore[call-overload]
        return total, {row['tenant_id']: row['c'] for row in rows}

    @classmethod
    async def count_distinct_sessions(
        cls, *, start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> int:
        # COUNT(DISTINCT session_id) in the DB — the session set is unbounded
        # over a long window, so never materialize it in Python.
        rows = await cls._filtered(start=start, end=end).filter(
            session_id__isnull=False
        ).annotate(c=Count('session_id', distinct=True)).values_list('c', flat=True)
        return rows[0] if rows else 0

    @classmethod
    async def top_paths(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
        limit: int = 15,
    ) -> List[dict]:
        """Most-viewed paths with view + distinct-user counts, busiest first."""
        rows = await (
            cls._filtered(start=start, end=end, category=category)
            .filter(path__isnull=False)
            .annotate(views=Count('id'), users=Count('user_id', distinct=True))
            .group_by('path')
            .order_by('-views')
            .limit(limit)
            .values('path', 'views', 'users')
        )
        return list(rows)

    @classmethod
    async def top_event_types(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[dict]:
        rows = await (
            cls._filtered(start=start, end=end)
            .annotate(count=Count('id'))
            .group_by('category', 'event_type')
            .order_by('-count')
            .limit(limit)
            .values('category', 'event_type', 'count')
        )
        return list(rows)

    @classmethod
    async def top_users(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 15,
    ) -> List[dict]:
        """Busiest identified users: event + distinct-session counts."""
        rows = await (
            cls._filtered(start=start, end=end)
            .filter(user_id__isnull=False)
            .annotate(events=Count('id'), sessions=Count('session_id', distinct=True))
            .group_by('user_id')
            .order_by('-events')
            .limit(limit)
            .values('user_id', 'events', 'sessions')
        )
        return list(rows)

    @classmethod
    async def top_controls(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Most-used controls: one row per (page, kind of use, control)."""
        rows = await (
            cls._filtered(start=start, end=end, category=category)
            .filter(target__isnull=False)
            .annotate(
                uses=Count('id'),
                users=Count('user_id', distinct=True),
                phone=Count('id', _filter=Q(device='phone')),
            )
            .group_by('path', 'event_type', 'target')
            .order_by('-uses')
            .limit(limit)
            .values('path', 'event_type', 'target', 'uses', 'users', 'phone')
        )
        return list(rows)

    @classmethod
    async def top_api_targets(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """One row per (surface, tool or route): calls, distinct users, failures."""
        rows = await (
            cls._filtered(start=start, end=end, category=category)
            .filter(target__isnull=False)
            .annotate(
                calls=Count('id'),
                users=Count('user_id', distinct=True),
                errors=Count('id', _filter=~Q(details__contains='"outcome": "ok"')),
            )
            .group_by('event_type', 'target')
            .order_by('-calls')
            .limit(limit)
            .values('event_type', 'target', 'calls', 'users', 'errors')
        )
        return list(rows)

    @classmethod
    async def top_api_users(
        cls,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """One row per (person, surface): calls, distinct tools/routes, last call."""
        rows = await (
            cls._filtered(start=start, end=end, category=category)
            .filter(user_id__isnull=False)
            .annotate(
                calls=Count('id'),
                targets=Count('target', distinct=True),
                last_call=Max('created_at'),
            )
            .group_by('user_id', 'event_type')
            .order_by('-calls')
            .limit(limit)
            .values('user_id', 'event_type', 'calls', 'targets', 'last_call')
        )
        return list(rows)

    @staticmethod
    async def delete_before(cutoff: datetime, batch: int = 5000) -> int:
        """Delete every row created before ``cutoff``, ``batch`` rows at a time.

        tenant-scope: exempt — retention is one platform-wide window. Batched so a
        first run over a year of backlog holds no long lock and yields to the event
        loop between batches.
        """
        deleted = 0
        while True:
            ids = await TelemetryEvent.filter(created_at__lt=cutoff).limit(batch).values_list('id', flat=True)
            if not ids:
                return deleted
            deleted += await TelemetryEvent.filter(id__in=list(ids)).delete()
            if len(ids) < batch:
                return deleted
            await asyncio.sleep(0)
