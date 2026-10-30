"""Tests for TelemetryService: capture paths, aggregations, and Staff gating.

Exercises the real aggregation SQL (GROUP BY / COUNT DISTINCT) against the
in-memory SQLite ``db`` fixture, plus the best-effort capture guarantees and
the Staff-only read boundary.
"""

import json

import pytest

from application.events import Event, EventType
from application.repositories.telemetry_repository import TelemetryRepository
from application.services.telemetry_service import (
    TelemetryCategory,
    TelemetryEventType,
    TelemetryService,
)
from models import Role, TelemetryEvent, User, UserRole


async def _staff(discord_id: int = 1001, username: str = 'staff') -> User:
    user = await User.create(discord_id=discord_id, username=username)
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _plain(discord_id: int = 2002, username: str = 'nobody') -> User:
    return await User.create(discord_id=discord_id, username=username)


class TestRecordEvent:
    async def test_mirrors_domain_event_with_actor(self, db):
        actor = await _plain()
        await TelemetryService().record_event(
            Event.create(EventType.MATCH_CREATED, {'match_id': 5}, actor)
        )
        rows = await TelemetryEvent.all()
        assert len(rows) == 1
        row = rows[0]
        assert row.category == TelemetryCategory.DOMAIN
        assert row.event_type == EventType.MATCH_CREATED
        assert row.user_id == actor.id
        assert row.path is None
        details = json.loads(row.details)
        assert details['match_id'] == 5
        assert details['actor_username'] == 'nobody'

    async def test_system_event_without_actor(self, db):
        await TelemetryService().record_event(
            Event.create(EventType.MATCH_STARTED, {'match_id': 9})
        )
        row = (await TelemetryEvent.all())[0]
        assert row.user_id is None
        assert row.event_type == EventType.MATCH_STARTED

    async def test_disabled_is_noop(self, db, monkeypatch):
        monkeypatch.setenv('TELEMETRY_ENABLED', 'false')
        await TelemetryService().record_event(Event.create(EventType.MATCH_CREATED, {}))
        assert await TelemetryEvent.all().count() == 0


class TestTrackPageView:
    async def test_resolves_user_and_records(self, db):
        user = await _plain(discord_id=555, username='viewer')
        await TelemetryService().track_page_view(
            path='/admin', discord_id='555', username='viewer',
            session_id='sess-1', params={'tab': 'Reports'},
        )
        row = (await TelemetryEvent.all())[0]
        assert row.category == TelemetryCategory.PAGE
        assert row.event_type == TelemetryEventType.PAGE_VIEW
        assert row.user_id == user.id
        assert row.path == '/admin'
        assert row.session_id == 'sess-1'
        details = json.loads(row.details)
        assert details['tab'] == 'Reports'
        assert details['actor_username'] == 'viewer'

    async def test_unknown_discord_id_records_anonymous(self, db):
        await TelemetryService().track_page_view(path='/home', discord_id='999999')
        row = (await TelemetryEvent.all())[0]
        assert row.user_id is None
        assert row.path == '/home'

    async def test_disabled_is_noop(self, db, monkeypatch):
        monkeypatch.setenv('TELEMETRY_ENABLED', '0')
        await TelemetryService().track_page_view(path='/home')
        assert await TelemetryEvent.all().count() == 0


class TestTrackInteraction:
    async def test_records_interaction(self, db):
        user = await _plain(discord_id=777, username='clicker')
        await TelemetryService().track_interaction(
            event_type=TelemetryEventType.REPORT_VIEWED, path='audit',
            discord_id='777', session_id='s9',
        )
        row = (await TelemetryEvent.all())[0]
        assert row.category == TelemetryCategory.INTERACTION
        assert row.event_type == 'report.viewed'
        assert row.path == 'audit'
        assert row.user_id == user.id
        assert row.session_id == 's9'


class TestAggregations:
    async def test_top_paths_counts_views_and_distinct_users(self, db):
        staff = await _staff()
        u1 = await _plain(3001, 'a')
        u2 = await _plain(3002, 'b')
        for uid in (u1.id, u1.id, u2.id):
            await TelemetryRepository.create(
                category='page', event_type='page.view', path='/admin', user_id=uid,
            )
        await TelemetryRepository.create(
            category='page', event_type='page.view', path='/home', user_id=u1.id,
        )
        top = {r['path']: r for r in await TelemetryService().top_paths(staff)}
        assert top['/admin']['views'] == 3
        assert top['/admin']['users'] == 2
        assert top['/home']['views'] == 1
        assert top['/home']['users'] == 1

    async def test_top_paths_excludes_non_page_categories(self, db):
        staff = await _staff()
        await TelemetryRepository.create(
            category='domain', event_type='match.created', path=None, user_id=staff.id,
        )
        assert await TelemetryService().top_paths(staff) == []

    async def test_top_event_types(self, db):
        staff = await _staff()
        await TelemetryRepository.create(category='domain', event_type='match.created', user_id=staff.id)
        await TelemetryRepository.create(category='domain', event_type='match.created', user_id=staff.id)
        await TelemetryRepository.create(category='interaction', event_type='report.viewed', path='audit', user_id=staff.id)
        by_evt = {r['event_type']: r for r in await TelemetryService().top_event_types(staff)}
        assert by_evt['match.created']['count'] == 2
        assert by_evt['match.created']['category'] == 'domain'
        assert by_evt['report.viewed']['count'] == 1

    async def test_top_users_resolves_names_and_sessions(self, db):
        staff = await _staff()
        u1 = await _plain(4001, 'alice')
        await TelemetryRepository.create(category='page', event_type='page.view', path='/x', user_id=u1.id, session_id='s1')
        await TelemetryRepository.create(category='page', event_type='page.view', path='/y', user_id=u1.id, session_id='s1')
        await TelemetryRepository.create(category='page', event_type='page.view', path='/z', user_id=u1.id, session_id='s2')
        rows = await TelemetryService().top_users(staff)
        alice = next(r for r in rows if r['user_id'] == u1.id)
        assert alice['events'] == 3
        assert alice['sessions'] == 2
        assert alice['user'] == 'alice'

    async def test_engagement_summary(self, db):
        staff = await _staff()
        u1 = await _plain(5001, 'a')
        await TelemetryRepository.create(category='page', event_type='page.view', path='/a', user_id=u1.id, session_id='s1')
        await TelemetryRepository.create(category='interaction', event_type='report.viewed', path='audit', user_id=u1.id, session_id='s1')
        await TelemetryRepository.create(category='domain', event_type='match.created', user_id=None)
        summary = await TelemetryService().engagement_summary(staff)
        assert summary['total_events'] == 3
        assert summary['unique_users'] == 1
        assert summary['unique_sessions'] == 1
        assert summary['page_views'] == 1


class TestListAndCount:
    async def test_filters(self, db):
        staff = await _staff()
        await TelemetryRepository.create(category='page', event_type='page.view', path='/admin', user_id=staff.id)
        await TelemetryRepository.create(category='domain', event_type='match.created', user_id=staff.id)
        svc = TelemetryService()
        assert await svc.count_events(staff) == 2
        assert await svc.count_events(staff, category='page') == 1
        assert await svc.count_events(staff, path_contains='adm') == 1
        rows = await svc.list_events(staff, category='domain')
        assert len(rows) == 1
        assert rows[0].event_type == 'match.created'


class TestStaffGating:
    async def test_reads_require_staff(self, db):
        plain = await _plain()
        svc = TelemetryService()
        with pytest.raises(PermissionError):
            await svc.list_events(plain)
        with pytest.raises(PermissionError):
            await svc.count_events(plain)
        with pytest.raises(PermissionError):
            await svc.engagement_summary(plain)
        with pytest.raises(PermissionError):
            await svc.top_paths(plain)
        with pytest.raises(PermissionError):
            await svc.top_event_types(plain)
        with pytest.raises(PermissionError):
            await svc.top_users(plain)

    async def test_staff_reads_allowed(self, db):
        staff = await _staff()
        assert await TelemetryService().count_events(staff) == 0


class TestTrackTabView:
    async def test_records_hub_and_tab(self, db):
        user = await _plain(discord_id=777, username='admin')
        await TelemetryService().track_tab_view(
            path='/admin/schedule', tab='schedule', discord_id='777', session_id='s',
        )
        row = (await TelemetryEvent.all())[0]
        assert row.category == TelemetryCategory.INTERACTION
        assert row.event_type == TelemetryEventType.TAB_VIEW
        assert row.path == '/admin/schedule'
        assert row.target == 'schedule'
        assert row.user_id == user.id


class TestTrackUiBatch:
    async def test_keeps_known_events_and_drops_the_rest(self, db):
        await _plain(discord_id=778, username='clicker')
        kept = await TelemetryService().track_ui_batch(
            [
                {'type': 'ui.click', 'target': 'Add match', 'path': '/admin/schedule',
                 'kind': 'button', 'dialog': None, 'device': 'phone', 'width': 390},
                {'type': 'ui.dialog_closed', 'target': 'Edit match', 'via': 'Cancel',
                 'path': '/admin/schedule', 'device': 'laptop'},
                {'type': 'drop.table', 'target': 'x'},
                {'type': 'ui.click', 'target': '   '},
                {'type': 'ui.click', 'target': {'nested': 1}},
                'not a dict',
            ],
            discord_id='778', session_id='s',
        )
        assert kept == 2
        rows = await TelemetryEvent.all().order_by('id')
        assert [r.target for r in rows] == ['Add match', 'Edit match']
        assert rows[0].device == 'phone'
        assert rows[1].device is None
        details = json.loads(rows[0].details)
        assert details['kind'] == 'button'
        assert details['width'] == '390'
        assert 'dialog' not in details
        assert json.loads(rows[1].details)['via'] == 'Cancel'

    async def test_bounds_batch_and_text(self, db):
        kept = await TelemetryService().track_ui_batch(
            [{'type': 'ui.click', 'target': 'x' * 500, 'path': '/p'}] * 80,
        )
        assert kept == 50
        row = await TelemetryEvent.first()
        assert len(row.target) == 200

    async def test_not_a_list_is_ignored(self, db):
        assert await TelemetryService().track_ui_batch({'type': 'ui.click'}) == 0
        assert await TelemetryEvent.all().count() == 0

    async def test_disabled_is_noop(self, db, monkeypatch):
        monkeypatch.setenv('TELEMETRY_ENABLED', 'false')
        await TelemetryService().track_ui_batch([{'type': 'ui.click', 'target': 'a'}])
        assert await TelemetryEvent.all().count() == 0

    async def test_top_controls_groups_by_page_and_control(self, db):
        staff = await _staff()
        await _plain(discord_id=779, username='other')
        svc = TelemetryService()
        click = {'type': 'ui.click', 'target': 'Add match', 'path': '/admin/schedule'}
        await svc.track_ui_batch([{**click, 'device': 'phone'}, click], discord_id='1001')
        await svc.track_ui_batch([click], discord_id='779')
        await svc.track_ui_batch([{**click, 'path': '/home/schedule'}], discord_id='779')
        await svc.track_page_view(path='/admin', discord_id='1001')
        rows = await svc.top_controls(staff)
        assert rows[0] == {
            'path': '/admin/schedule', 'event_type': 'ui.click', 'target': 'Add match',
            'uses': 3, 'users': 2, 'phone': 1,
        }
        assert len(rows) == 2

    async def test_top_controls_filters_to_one_person(self, db):
        staff = await _staff()
        other = await _plain(discord_id=779, username='other')
        svc = TelemetryService()
        click = {'type': 'ui.click', 'target': 'Add match', 'path': '/admin/schedule'}
        await svc.track_ui_batch([click, click], discord_id='1001')
        await svc.track_ui_batch([click, {**click, 'target': 'Roll seed'}], discord_id='779')
        rows = await svc.top_controls(staff, user_id=other.id)
        assert {(r['target'], r['uses'], r['users']) for r in rows} == {
            ('Add match', 1, 1), ('Roll seed', 1, 1),
        }
        assert [r['user_id'] for r in await svc.top_users(staff, user_id=staff.id)] == [staff.id]

    async def test_top_controls_is_staff_only(self, db):
        with pytest.raises(PermissionError):
            await TelemetryService().top_controls(await _plain())
