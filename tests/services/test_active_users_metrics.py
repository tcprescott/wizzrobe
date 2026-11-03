"""Active signed-in user counts sent to Sentry Metrics."""

from datetime import datetime, timedelta, timezone

import pytest

from application.repositories.telemetry_repository import TelemetryRepository
from application.services import active_users_metrics
from application.utils import sentry_metrics
from models import TelemetryEvent, Tenant, User


@pytest.fixture
def gauges(monkeypatch):
    sent = []

    class Rec:
        def gauge(self, name, value, unit=None, attributes=None):
            sent.append((name, value, attributes))

    monkeypatch.setattr(sentry_metrics, '_metrics', Rec())
    monkeypatch.setattr(sentry_metrics, '_enabled', True)
    return sent


async def _seed():
    now = datetime.now(timezone.utc)
    a = await Tenant.create(name='Alpha', slug='alpha')
    b = await Tenant.create(name='Beta', slug='beta')
    u1 = await User.create(discord_id=1, username='one')
    u2 = await User.create(discord_id=2, username='two')
    u3 = await User.create(discord_id=3, username='three')

    async def row(user, tenant, ago):
        ev = await TelemetryEvent.create(
            user=user, tenant=tenant, category='page', event_type='page.view')
        await TelemetryEvent.filter(id=ev.id).update(created_at=now - ago)

    await row(u1, a, timedelta(minutes=5))
    await row(u1, a, timedelta(minutes=6))
    await row(u1, b, timedelta(minutes=7))
    await row(u2, a, timedelta(minutes=30))
    await row(u3, b, timedelta(hours=5))
    await row(u3, None, timedelta(days=3))
    await TelemetryEvent.create(tenant=a, category='domain', event_type='match.created')
    return now, a, b


async def test_distinct_users_since(db):
    now, a, b = await _seed()
    assert await TelemetryRepository.distinct_users_since(now - timedelta(minutes=15)) == (1, {a.id: 1, b.id: 1})
    assert await TelemetryRepository.distinct_users_since(now - timedelta(hours=1)) == (2, {a.id: 2, b.id: 1})
    assert await TelemetryRepository.distinct_users_since(now - timedelta(hours=24)) == (3, {a.id: 2, b.id: 2})


async def test_emits_each_window_overall_and_per_community(db, gauges):
    now, _, _ = await _seed()
    await active_users_metrics.emit_active_users(now)
    got = {(name, (attrs or {}).get('community')): value for name, value, attrs in gauges}
    assert got[('users.active_15m', None)] == 1
    assert got[('users.active_1h', None)] == 2
    assert got[('users.active_24h', None)] == 3
    assert got[('users.active_1h_by_community', 'alpha')] == 2
    assert got[('users.active_24h_by_community', 'beta')] == 2
