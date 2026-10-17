"""Tests for BrowserMonitoringService — browser-side Sentry set on /platform.

What matters: only SUPER_ADMIN may change anything, every change is audited,
and the in-memory snapshot that page builds read follows each change, since
that snapshot is what decides whether a page loads the SDK and what it drops.
"""

import json

import pytest

from application.services import BrowserMonitoringService
from application.services.audit_service import AuditActions
from application.utils import browser_monitoring
from application.utils.browser_monitoring import MATCH_MESSAGE, MATCH_URL, Snapshot
from models import AuditLog, BrowserErrorIgnoreRule, Role, User, UserRole


@pytest.fixture
async def super_admin(db):
    su = await User.create(discord_id=3000, username='root')
    await UserRole.create(user=su, role=Role.SUPER_ADMIN, tenant=None)
    return su


@pytest.fixture
async def plain_user(db):
    return await User.create(discord_id=3001, username='nobody')


@pytest.fixture(autouse=True)
def _restore_snapshot():
    saved = browser_monitoring.current()
    yield
    browser_monitoring.replace(saved)


async def test_defaults_without_a_row(super_admin):
    snapshot = await BrowserMonitoringService().load()
    assert snapshot == Snapshot(enabled=True, replay_on_error=True, sample_rate=1.0, rules=())
    view = await BrowserMonitoringService().get_settings(super_admin)
    assert view.enabled and view.replay_on_error and view.sample_rate == 1.0


async def test_changes_require_super_admin(plain_user):
    service = BrowserMonitoringService()
    with pytest.raises(PermissionError):
        await service.update_settings(plain_user, enabled=False, replay_on_error=True, sample_rate=1.0)
    with pytest.raises(PermissionError):
        await service.add_rule(plain_user, 'Some error', MATCH_MESSAGE)
    with pytest.raises(PermissionError):
        await service.list_rules(plain_user)


async def test_update_settings_stores_audits_and_updates_snapshot(super_admin):
    await BrowserMonitoringService().update_settings(
        super_admin, enabled=False, replay_on_error=False, sample_rate=0.25)

    snapshot = browser_monitoring.current()
    assert (snapshot.enabled, snapshot.replay_on_error, snapshot.sample_rate) == (False, False, 0.25)
    view = await BrowserMonitoringService().get_settings(super_admin)
    assert view.updated_by == 'root'
    log = await AuditLog.get(action=AuditActions.BROWSER_MONITORING_UPDATED)
    assert json.loads(log.details)['previous']['enabled'] is True


async def test_update_settings_rejects_an_unoffered_share(super_admin):
    with pytest.raises(ValueError):
        await BrowserMonitoringService().update_settings(
            super_admin, enabled=True, replay_on_error=True, sample_rate=0.33)


async def test_add_and_remove_rule(super_admin):
    service = BrowserMonitoringService()
    await service.add_rule(super_admin, "  Cannot read properties of null (reading 'focus')  ", MATCH_MESSAGE)

    assert [r.pattern for r in browser_monitoring.current().rules] == [
        "Cannot read properties of null (reading 'focus')"]
    rows = await service.list_rules(super_admin)
    added = [r for r in rows if not r.builtin]
    assert not rows[0].builtin
    assert len(added) == 1 and added[0].created_by == 'root'
    assert any(r.builtin for r in rows)

    await service.remove_rule(super_admin, added[0].id)
    assert browser_monitoring.current().rules == ()
    assert await BrowserErrorIgnoreRule.all().count() == 0
    assert await AuditLog.filter(action=AuditActions.BROWSER_IGNORE_RULE_ADDED).count() == 1
    assert await AuditLog.filter(action=AuditActions.BROWSER_IGNORE_RULE_REMOVED).count() == 1


@pytest.mark.parametrize('pattern, match', [
    ('ab', MATCH_MESSAGE),                    # too short to be distinctive
    ('x' * 201, MATCH_MESSAGE),
    ('Some error', 'stack'),                  # unknown kind
    ('resizeobserver loop', MATCH_MESSAGE),   # already built in
    ('chrome-extension://', MATCH_URL),
])
async def test_add_rule_rejects(super_admin, pattern, match):
    with pytest.raises(ValueError):
        await BrowserMonitoringService().add_rule(super_admin, pattern, match)


async def test_add_rule_rejects_duplicates(super_admin):
    service = BrowserMonitoringService()
    await service.add_rule(super_admin, 'Some error', MATCH_MESSAGE)
    with pytest.raises(ValueError):
        await service.add_rule(super_admin, 'Some error', MATCH_MESSAGE)
