"""Dev seed: Reports → Exports (see application/services/exports/)."""

from datetime import datetime, timezone

from models import DataExport, DataExportKind, DataExportStatus, Tenant, Tournament, User

_CSV = b'Match,Tournament,Scheduled\n1,Wizzrobe Dev Tournament,2026-10-23 18:00\n'


async def seed_data_exports(tenant: Tenant, users: dict[str, User]) -> None:
    """staff_user's exports in each state: a logged table CSV, a failed one, one
    mid-build, and a queued tournament export the worker builds once the app is
    running. The mid-build row is failed as interrupted 15 minutes after seeding,
    just as a real one would be after a restart."""
    staff = users['staff_user']
    now = datetime.now(timezone.utc)
    ids = [t.id async for t in Tournament.filter(tenant=tenant).order_by('id').limit(3)]
    await DataExport.get_or_create(
        tenant=tenant, requested_by=staff, kind=DataExportKind.TABLE_CSV, title='match-ops.csv',
        defaults={
            'status': DataExportStatus.READY, 'filename': 'match-ops.csv', 'media_type': 'text/csv',
            'content': _CSV, 'size_bytes': len(_CSV), 'progress_done': 1, 'progress_total': 1,
            'started_at': now, 'finished_at': now,
        },
    )
    await DataExport.get_or_create(
        tenant=tenant, requested_by=staff, kind=DataExportKind.VOLUNTEERS,
        title='Volunteer data, Thu 22 Oct to Sun 25 Oct',
        defaults={
            'status': DataExportStatus.FAILED, 'progress_total': 1,
            'error': 'The server restarted while this export was building. Request it again.',
            'params': {}, 'started_at': now, 'finished_at': now,
        },
    )
    if ids:
        await DataExport.get_or_create(
            tenant=tenant, requested_by=staff, kind=DataExportKind.TOURNAMENTS,
            title=f'All {len(ids)} tournaments',
            defaults={
                'status': DataExportStatus.RUNNING, 'params': {'tournament_ids': None},
                'progress_done': 1, 'progress_total': len(ids), 'started_at': now,
            },
        )
        await DataExport.get_or_create(
            tenant=tenant, requested_by=staff, kind=DataExportKind.TOURNAMENTS,
            title=f'{len(ids)} tournaments',
            defaults={'params': {'tournament_ids': ids}, 'progress_total': len(ids)},
        )
