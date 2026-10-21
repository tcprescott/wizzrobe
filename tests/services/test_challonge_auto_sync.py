"""Challonge auto-sync: the per-community interval and which brackets come due."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services.audit_service import AuditActions
from application.services.challonge_service import ChallongeService
from models import AuditLog, Role, TenantMembership, Tournament, User


@pytest.fixture
async def staff(db):
    boss = await User.create(discord_id=7600, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


@pytest.fixture
def synced(monkeypatch):
    service = ChallongeService()
    calls: list = []

    async def fake_sync(tournament, actor, force=False):
        calls.append(tournament.id)
        return {}

    monkeypatch.setattr(service, '_sync_tournament', fake_sync)
    return service, calls


async def _linked(name: str, *, state=None, last=None, active=True) -> Tournament:
    return await Tournament.create(
        name=name, challonge_tournament_id=name.lower(), challonge_state=state,
        challonge_last_synced_at=last, is_active=active,
    )


async def test_off_by_default(db):
    assert await ChallongeService().auto_sync_minutes() == 0


async def test_staff_set_the_interval_and_it_is_audited(staff):
    service = ChallongeService()
    await service.set_auto_sync_minutes(60, staff)
    assert await service.auto_sync_minutes() == 60
    row = await AuditLog.get(action=AuditActions.CHALLONGE_AUTO_SYNC_UPDATED)
    details = json.loads(row.details)
    assert (details['minutes_before'], details['minutes']) == (0, 60)


async def test_rejects_an_interval_not_on_offer(staff):
    with pytest.raises(ValueError):
        await ChallongeService().set_auto_sync_minutes(7, staff)


async def test_only_staff_change_it(db):
    player = await User.create(discord_id=7601, username='player')
    await TenantMembership.create(user=player, tenant_id=1)
    with pytest.raises(PermissionError):
        await ChallongeService().set_auto_sync_minutes(60, player)


async def test_estimate_counts_unfinished_brackets(staff):
    await _linked('A', state='underway')
    await _linked('B')
    await _linked('Done', state='complete')
    await _linked('Old', state='underway', active=False)
    estimate = await ChallongeService().auto_sync_estimate(60)
    assert estimate.tournaments == 2
    assert estimate.requests_per_day == 2 * 2 * 24


async def test_syncs_nothing_while_off(staff, synced):
    service, calls = synced
    await _linked('A', state='underway')
    assert await service.sync_due_brackets(staff) == 0
    assert calls == []


async def test_syncs_only_brackets_that_are_due(staff, synced):
    service, calls = synced
    await service.set_auto_sync_minutes(60, staff)
    now = datetime.now(timezone.utc)
    stale = await _linked('Stale', state='underway', last=now - timedelta(minutes=61))
    never = await _linked('Never')
    await _linked('Fresh', state='underway', last=now - timedelta(minutes=10))
    await _linked('Done', state='complete', last=now - timedelta(days=1))
    assert await service.sync_due_brackets(staff) == 2
    assert sorted(calls) == sorted([stale.id, never.id])
