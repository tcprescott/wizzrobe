"""The nightly job that deletes engagement telemetry older than 13 months."""

from datetime import datetime, timedelta, timezone

from application.services import telemetry_retention
from models import TelemetryEvent, Tenant


async def _row(tenant, ago, now):
    ev = await TelemetryEvent.create(tenant=tenant, category='page', event_type='page.view')
    await TelemetryEvent.filter(id=ev.id).update(created_at=now - ago)
    return ev.id


async def test_deletes_only_rows_older_than_the_window_in_every_community(db):
    now = datetime.now(timezone.utc)
    a = await Tenant.create(name='Alpha', slug='alpha')
    b = await Tenant.create(name='Beta', slug='beta')
    keep = [
        await _row(a, timedelta(days=30), now),
        await _row(b, telemetry_retention.RETENTION - timedelta(days=1), now),
    ]
    await _row(a, telemetry_retention.RETENTION + timedelta(days=1), now)
    await _row(b, timedelta(days=800), now)

    assert await telemetry_retention.prune(now) == 2
    assert sorted(await TelemetryEvent.all().values_list('id', flat=True)) == sorted(keep)


async def test_batches_until_the_backlog_is_gone(db):
    from application.repositories.telemetry_repository import TelemetryRepository

    now = datetime.now(timezone.utc)
    t = await Tenant.create(name='Alpha', slug='alpha')
    for _ in range(7):
        await _row(t, timedelta(days=500), now)
    await _row(t, timedelta(days=1), now)

    assert await TelemetryRepository.delete_before(now - telemetry_retention.RETENTION, batch=3) == 7
    assert await TelemetryEvent.all().count() == 1


async def test_retention_is_thirteen_months():
    assert timedelta(days=395) <= telemetry_retention.RETENTION <= timedelta(days=397)
