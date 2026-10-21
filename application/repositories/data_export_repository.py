"""Data access for :class:`DataExport` — background export jobs and their files."""

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import DataExport, DataExportKind, DataExportStatus

# Every column but the two file blobs: lists and status polls never need them,
# and a ready export's files are most of the row.
_SUMMARY_FIELDS = (
    'id', 'tenant_id', 'requested_by_id', 'kind', 'title', 'params', 'status',
    'progress_done', 'progress_total', 'error', 'filename', 'media_type',
    'size_bytes', 'created_at', 'started_at', 'finished_at',
)
_PENDING = (DataExportStatus.QUEUED, DataExportStatus.RUNNING)


class DataExportRepository(TenantScopedRepository[DataExport]):
    model = DataExport

    @classmethod
    async def create_queued(cls, **fields: Any) -> DataExport:
        return await DataExport.create(
            tenant_id=current_tenant_id(), status=DataExportStatus.QUEUED, **fields,
        )

    @classmethod
    async def create_ready(cls, **fields: Any) -> DataExport:
        now = datetime.now(timezone.utc)
        return await DataExport.create(
            tenant_id=current_tenant_id(), status=DataExportStatus.READY,
            started_at=now, finished_at=now, **fields,
        )

    @classmethod
    async def summary(cls, export_id: int) -> Optional[DataExport]:
        """One export without its files, scoped to this tenant."""
        return await scoped(DataExport.filter(id=export_id)).only(*_SUMMARY_FIELDS).first()

    @classmethod
    async def list_for_requester(cls, user_id: int, limit: int = 20) -> List[DataExport]:
        return await scoped(
            DataExport.filter(requested_by_id=user_id)
        ).only(*_SUMMARY_FIELDS).order_by('-created_at', '-id').limit(limit)

    @classmethod
    async def count_pending_for_requester(cls, user_id: int) -> int:
        return await scoped(
            DataExport.filter(requested_by_id=user_id, status__in=_PENDING)
        ).count()

    @classmethod
    async def count_queued_since(cls, user_id: int, since: datetime) -> int:
        """Background exports ``user_id`` asked for since ``since`` (logged table CSVs excluded)."""
        return await scoped(
            DataExport.filter(requested_by_id=user_id, created_at__gte=since)
            .exclude(kind=DataExportKind.TABLE_CSV)
        ).count()

    @classmethod
    async def count_pending_in_tenant(cls) -> int:
        return await scoped(DataExport.filter(status__in=_PENDING)).count()

    @classmethod
    async def set_progress(cls, export_id: int, done: int, total: int) -> None:
        await scoped(DataExport.filter(id=export_id)).update(
            progress_done=done, progress_total=total,
        )

    @classmethod
    async def finish(cls, export_id: int, **fields: Any) -> None:
        await scoped(DataExport.filter(id=export_id)).update(
            finished_at=datetime.now(timezone.utc), **fields,
        )

    # ------------------------------------------------------------------
    # Worker-facing queries — deliberately cross-tenant
    # ------------------------------------------------------------------

    @classmethod
    async def next_queued_all(cls) -> Optional[DataExport]:
        """The oldest queued export in any community, without its files.

        Deliberately unscoped: the worker owns the one global queue, then enters
        the export's own ``tenant_scope`` to build it.
        """
        return await DataExport.filter(
            status=DataExportStatus.QUEUED,
        ).only(*_SUMMARY_FIELDS).order_by('created_at', 'id').first()

    @classmethod
    async def claim(cls, export_id: int) -> bool:
        """Move a queued export to RUNNING, once. Unscoped: the worker's claim on its own queue."""
        updated = await DataExport.filter(
            id=export_id, status=DataExportStatus.QUEUED,
        ).update(status=DataExportStatus.RUNNING, started_at=datetime.now(timezone.utc))
        return bool(updated)

    @classmethod
    async def fail_interrupted_all(cls, older_than: timedelta, error: str) -> int:
        """Fail RUNNING exports started before the cutoff, in every community (cross-tenant).

        A restart mid-build leaves a row RUNNING that nothing will finish.
        """
        cutoff = datetime.now(timezone.utc) - older_than
        return await DataExport.filter(
            status=DataExportStatus.RUNNING, started_at__lt=cutoff,
        ).update(status=DataExportStatus.FAILED, error=error, finished_at=datetime.now(timezone.utc))

    @classmethod
    async def prune_all(cls, older_than: timedelta) -> int:
        """Delete exports created before the cutoff in every community (cross-tenant)."""
        cutoff = datetime.now(timezone.utc) - older_than
        return await DataExport.filter(created_at__lt=cutoff).delete()
