"""The app's Me screen: the settings list and each section's own routes."""

from datetime import datetime, timedelta, timezone

import pytest

from application.utils.timezone import today_local
from models import (
    FeatureFlag,
    TenantFeatureFlag,
    Tournament,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.api._bracket_api import token_for
from tests.api_helpers import client_for
from tests.factories import make_member, set_setting


def _soon(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def _flag_off(flag: FeatureFlag) -> None:
    await TenantFeatureFlag.filter(flag=flag.value).update(enabled=False)


class TestProfileList:
    async def test_lists_the_sections_this_community_has(self, db, app):
        alice = await make_member(discord_id=9501, username='alice', pronouns='she/her')
        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/me/profile')).json()
        assert body['preferred_name'] == 'alice'
        assert body['pronouns'] == 'she/her'
        keys = [s['key'] for s in body['sections']]
        assert keys[:3] == ['about', 'notifications', 'availability']
        for key in ('accommodation', 'emergency-contact', 'feedback', 'webhooks', 'apps', 'tokens'):
            assert key in keys
        assert 'staff-alerts' not in keys
        summaries = {s['key']: s['summary'] for s in body['sections']}
        assert summaries['availability'] == 'Free all event'
        assert summaries['emergency-contact'] == 'Not added'

    async def test_a_section_switched_off_drops_out(self, db, app):
        await _flag_off(FeatureFlag.EMERGENCY_CONTACTS)
        alice = await make_member(discord_id=9502, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            keys = [s['key'] for s in (await c.get('/api/me/profile')).json()['sections']]
            assert 'emergency-contact' not in keys
            assert (await c.get('/api/me/emergency-contact')).status_code == 404

    async def test_clock(self, db, app):
        alice = await make_member(discord_id=9503, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            body = (await c.patch('/api/me/clock', json={'timezone': 'Europe/London', 'hour_cycle': '24'})).json()
            assert (body['timezone'], body['hour_cycle']) == ('Europe/London', '24')
            body = (await c.patch('/api/me/clock', json={'hour_cycle': None})).json()
            assert (body['timezone'], body['hour_cycle']) == ('Europe/London', None)
            assert (await c.patch('/api/me/clock', json={'timezone': 'Mars/Base'})).status_code == 400

    async def test_matcherino_handle_saves_through_patch_me(self, db, app):
        alice = await make_member(discord_id=9504, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            assert (await c.patch('/api/users/me', json={'matcherino_username': 'alice#123456'})).status_code == 200
            assert (await c.get('/api/me/profile')).json()['matcherino_username'] == 'alice#123456'


class TestPersonalRecords:
    async def test_accommodation_round_trip(self, db, app):
        alice = await make_member(discord_id=9511, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            assert (await c.get('/api/me/accommodation')).json()['requested'] is False
            body = (await c.put('/api/me/accommodation', json={'requested': True, 'details': 'A chair, please'})).json()
            assert (body['requested'], body['status'], body['details']) == (True, 'new', 'A chair, please')
            body = (await c.put('/api/me/accommodation', json={'requested': False})).json()
            assert (body['requested'], body['status']) == (False, 'withdrawn')

    async def test_emergency_contact_round_trip(self, db, app):
        alice = await make_member(discord_id=9512, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            assert (await c.get('/api/me/emergency-contact')).json()['name'] is None
            body = (await c.put('/api/me/emergency-contact', json={
                'name': 'Jordan', 'relationship': 'sibling', 'phone': '+1 312 555 0142',
            })).json()
            assert body['name'] == 'Jordan'
            assert (await c.put('/api/me/emergency-contact', json={'name': '', 'phone': '1'})).status_code == 400
            assert (await c.delete('/api/me/emergency-contact')).status_code == 204
            assert (await c.get('/api/me/emergency-contact')).json()['name'] is None


class TestStaffAlerts:
    async def test_a_tournament_admin_can_switch_alerts(self, db, app):
        alice = await make_member(discord_id=9521, username='alice')
        t = await Tournament.create(name='Cup', is_active=True)
        await t.admins.add(alice)
        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/me/staff-alerts')).json()
            assert body['tournaments'] == [{'id': t.id, 'name': 'Cup', 'on': True}]
            body = (await c.put(f'/api/me/staff-alerts/{t.id}', json={'on': False})).json()
            assert body['tournaments'][0]['on'] is False
            keys = [s['key'] for s in (await c.get('/api/me/profile')).json()['sections']]
            assert 'staff-alerts' in keys

    async def test_others_get_404(self, db, app):
        bob = await make_member(discord_id=9522, username='bob')
        async with client_for(app, await token_for(bob)) as c:
            assert (await c.get('/api/me/staff-alerts')).status_code == 404


class TestChannels:
    async def test_reads_the_discord_switch(self, db, app):
        alice = await make_member(discord_id=9531, username='alice', dm_notifications=True)
        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/me/channels')).json()
        assert body['discord'] is True
        assert body['dm_notifications'] is True
        assert body['devices'] == []

    async def test_test_rejects_unknown_channels(self, db, app):
        alice = await make_member(discord_id=9532, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            assert (await c.post('/api/me/channels/test', json={'channels': ['pigeon']})).status_code == 400


class TestDeveloperTools:
    async def test_webhook_lifecycle_never_returns_the_url(self, db, app, monkeypatch):
        from application.services import personal_webhook_service as pws

        async def _public(*_a, **_k):
            return None
        monkeypatch.setattr(pws, 'ensure_public_host', _public, raising=False)
        alice = await make_member(discord_id=9541, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            listing = (await c.get('/api/me/webhooks')).json()
            assert listing['webhooks'] == []
            assert {a['key'] for a in listing['areas']} >= {'matches', 'crew'}
            resp = await c.post('/api/me/webhooks', json={
                'name': 'Mine', 'url': 'https://example.com/hooks/abc', 'event_types': ['match.created'],
            })
            assert resp.status_code == 201, resp.text
            created = resp.json()
            assert created['secret']
            hook = created['webhook']
            assert 'url' not in hook and hook['url_masked'].startswith('example.com')
            paused = (await c.put(f"/api/me/webhooks/{hook['id']}/active", json={'enabled': False})).json()
            assert paused['is_active'] is False
            assert (await c.delete(f"/api/me/webhooks/{hook['id']}")).status_code == 204

    async def test_register_an_app(self, db, app):
        alice = await make_member(discord_id=9542, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            resp = await c.post('/api/me/apps', json={
                'name': 'Overlay', 'client_type': 'confidential',
                'redirect_uris': ['https://example.com/callback'], 'allowed_scopes': ['profile'],
            })
            assert resp.status_code == 201, resp.text
            assert resp.json()['client_secret']
            assert [a['name'] for a in (await c.get('/api/me/apps')).json()] == ['Overlay']


class TestShiftCheckIn:
    @pytest.fixture
    async def shift(self, db):
        # Without dates the event is today alone, from 06:00, so a shift soon after midnight drops out.
        today = today_local()
        await set_setting(name='event_start_date', value=(today - timedelta(days=1)).isoformat())
        await set_setting(name='event_end_date', value=(today + timedelta(days=1)).isoformat())
        alice = await make_member(discord_id=9551, username='alice')
        pos = await VolunteerPosition.create(name='Desk')
        shift = await VolunteerShift.create(position=pos, starts_at=_soon(15), ends_at=_soon(135))
        assignment = await VolunteerAssignment.create(shift=shift, user=alice, auto_generated=False)
        return alice, assignment

    async def test_schedule_offers_im_here_then_undo(self, db, app, shift):
        alice, assignment = shift
        async with client_for(app, await token_for(alice)) as c:
            row = next(i for i in (await c.get('/api/me/schedule')).json()['items'] if i['kind'] == 'shift')
            assert row['shift_action'] == 'arrive'
            assert row['can_report_late'] is True
            assert row['can_release'] is True

            resp = await c.post(f'/api/volunteers/me/assignments/{assignment.id}/arrival')
            assert resp.status_code == 200 and resp.json()['checked_in_at']
            row = next(i for i in (await c.get('/api/me/schedule')).json()['items'] if i['kind'] == 'shift')
            assert row['checked_in'] is True and row['shift_action'] is None
            assert row['can_undo_check_in'] is True

            resp = await c.delete(f'/api/volunteers/me/assignments/{assignment.id}/arrival')
            assert resp.json()['checked_in_at'] is None

    async def test_running_late(self, db, app, shift):
        alice, assignment = shift
        async with client_for(app, await token_for(alice)) as c:
            assert (await c.post(
                f'/api/volunteers/me/assignments/{assignment.id}/running-late', json={'minutes': 15},
            )).status_code == 400
            assert (await c.post(
                f'/api/volunteers/me/assignments/{assignment.id}/running-late', json={'minutes': 20},
            )).status_code == 200
            row = next(i for i in (await c.get('/api/me/schedule')).json()['items'] if i['kind'] == 'shift')
            assert row['late_minutes'] == 20
