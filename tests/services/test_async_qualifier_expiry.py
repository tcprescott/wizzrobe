"""Run expiry: an abandoned qualifier run is warned, then forfeited.

Left open, a drawn-but-never-submitted run holds its permalink assignment and the
player's pool slot forever, and keeps widening the window in which any finish time
is claimable (``classify_claim`` measures against the draw).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from application.repositories import AsyncQualifierRunRepository
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import (
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    Role,
    User,
    UserRole,
)

pytestmark = pytest.mark.anyio


async def _staff() -> User:
    u = await User.create(discord_id=910001, username='staffy')
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _open_qualifier(service, staff, **config):
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Q', opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=30),
        config=config or None,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    return q, pool


async def _drawn_run(service, staff, player, *, age: timedelta, **config):
    q, pool = await _open_qualifier(service, staff, **config)
    await service.add_permalinks_bulk(staff, pool.id, urls=['https://seed.test/u1'])
    run = await service.start_run(player, q.id, pool.id)
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - age,
    )
    return q, await AsyncQualifierRun.get(id=run.id)


def _qualifier(config=None) -> SimpleNamespace:
    """The only attribute the pure rule helpers read off a qualifier."""
    return SimpleNamespace(config=config)


class TestRules:
    def test_default_limit_is_twelve_hours(self):
        assert rules.run_time_limit(_qualifier()) == timedelta(hours=12)

    def test_config_overrides_the_limit(self):
        assert rules.run_time_limit(
            _qualifier({'run_time_limit_hours': 3})
        ) == timedelta(hours=3)

    def test_deadline_is_none_for_an_unstarted_run(self):
        assert rules.run_deadline(_qualifier(), None) is None

    def test_naive_started_at_is_read_as_utc(self):
        naive = datetime(2026, 7, 31, 12, 0, 0)
        assert rules.run_deadline(
            _qualifier({'run_time_limit_hours': 1}), naive
        ) == datetime(2026, 7, 31, 13, 0, 0, tzinfo=timezone.utc)


class TestExpireRun:
    async def test_forfeits_and_marks_expired(self, db):
        service = AsyncQualifierService()
        staff = await _staff()
        player = await User.create(discord_id=910010, username='p1')
        _q, run = await _drawn_run(service, staff, player, age=timedelta(hours=13))

        expired = await service.expire_run(run)

        assert expired.status == AsyncQualifierRunStatus.FORFEIT
        assert expired.score == 0.0
        assert expired.expired_at is not None
        assert expired.finished_at is not None

    async def test_is_idempotent(self, db):
        service = AsyncQualifierService()
        staff = await _staff()
        player = await User.create(discord_id=910011, username='p2')
        _q, run = await _drawn_run(service, staff, player, age=timedelta(hours=13))

        first = await service.expire_run(run)
        again = await service.expire_run(first)

        assert again.expired_at == first.expired_at

    async def test_a_submitted_run_is_untouched(self, db):
        service = AsyncQualifierService()
        staff = await _staff()
        player = await User.create(discord_id=910012, username='p3')
        _q, run = await _drawn_run(service, staff, player, age=timedelta(hours=2))
        await service.submit_run(player, run.id, elapsed_seconds=3600)

        run = await AsyncQualifierRun.get(id=run.id)
        result = await service.expire_run(run)

        assert result.status == AsyncQualifierRunStatus.FINISHED
        assert result.expired_at is None


class TestWarning:
    async def test_stamps_once(self, db):
        service = AsyncQualifierService()
        staff = await _staff()
        player = await User.create(discord_id=910013, username='p4')
        _q, run = await _drawn_run(service, staff, player, age=timedelta(hours=11, minutes=30))

        deadline = datetime.now(timezone.utc) + timedelta(minutes=30)
        warned = await service.warn_run_expiring(run, deadline)
        assert warned.expiry_warned_at is not None

        first_stamp = warned.expiry_warned_at
        again = await service.warn_run_expiring(warned, deadline)
        assert again.expiry_warned_at == first_stamp


class TestWorkerScan:
    async def test_finds_in_progress_runs_across_tenants(self, db):
        service = AsyncQualifierService()
        staff = await _staff()
        player = await User.create(discord_id=910014, username='p5')
        _q, run = await _drawn_run(service, staff, player, age=timedelta(hours=1))

        found = await AsyncQualifierRunRepository.list_in_progress_all()

        assert [r.id for r in found] == [run.id]

    async def test_excludes_terminal_runs(self, db):
        service = AsyncQualifierService()
        staff = await _staff()
        player = await User.create(discord_id=910015, username='p6')
        _q, run = await _drawn_run(service, staff, player, age=timedelta(hours=13))
        await service.expire_run(run)

        assert await AsyncQualifierRunRepository.list_in_progress_all() == []
