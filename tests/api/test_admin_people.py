"""The app's people admin screens: Members, Meetups, Check-in Roster and Equipment."""

from datetime import datetime, timedelta, timezone

from application.services.audit_service import AuditActions
from models import (
    AccommodationRequest,
    AuditLog,
    CheckInEvent,
    CommunityEvent,
    CommunityEventStatus,
    EmergencyContact,
    Equipment,
    Role,
)
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member, make_user


def _later(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


async def _staff():
    user, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    return user, raw


class TestMembers:
    async def test_board_lists_members_with_roles_and_counts(self, db, app):
        _, raw = await _staff()
        await make_member(discord_id=8101, username='kirby', pronouns='they/them')
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/members')).json()
        rows = {r['username']: r for r in body['members']}
        assert rows['kirby']['pronouns'] == 'they/them'
        assert [r['role'] for r in rows['boss']['roles']] == ['staff']
        assert body['join_requests'] == 0
        assert set(body['switches']) == {'accommodations', 'emergency_contacts', 'challonge'}

    async def test_non_staff_are_refused(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/members')).status_code == 403
            assert (await c.get('/api/admin/members/addable')).status_code == 403

    async def test_addable_lists_only_non_members(self, db, app):
        _, raw = await _staff()
        await make_user(discord_id=8102, username='outsider')
        await make_member(discord_id=8103, username='insider')
        async with client_for(app, raw) as c:
            names = {a['username'] for a in (await c.get('/api/admin/members/addable')).json()}
        assert 'outsider' in names
        assert 'insider' not in names

    async def test_opening_an_emergency_contact_is_logged(self, db, app):
        _, raw = await _staff()
        member = await make_member(discord_id=8104, username='sam')
        await EmergencyContact.create(user=member, name='Alex', phone='555-0100')
        async with client_for(app, raw) as c:
            body = (await c.post(f'/api/admin/members/{member.id}/emergency-contact')).json()
        assert (body['found'], body['name'], body['phone']) == (True, 'Alex', '555-0100')
        assert await AuditLog.filter(action=AuditActions.EMERGENCY_CONTACT_VIEWED).count() == 1

    async def test_an_outsiders_contact_is_not_found(self, db, app):
        _, raw = await _staff()
        outsider = await make_user(discord_id=8105, username='outsider')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/admin/members/{outsider.id}/emergency-contact')).status_code == 404

    async def test_ada_requests_read_and_update(self, db, app):
        _, raw = await _staff()
        member = await make_member(discord_id=8106, username='juno')
        request = await AccommodationRequest.create(user=member, details='Seat near the stage')
        async with client_for(app, raw) as c:
            rows = (await c.get('/api/admin/members/ada-requests')).json()
            assert [(r['username'], r['details'], r['status']) for r in rows] == [('juno', 'Seat near the stage', 'new')]
            body = (await c.put(
                f'/api/admin/members/ada-requests/{request.id}',
                json={'status': 'arranged', 'staff_notes': 'Row A'},
            )).json()
            assert (body['status'], body['staff_notes']) == ('arranged', 'Row A')
            assert (await c.put(
                f'/api/admin/members/ada-requests/{request.id}', json={'status': 'withdrawn'},
            )).status_code == 400
            board = (await c.get('/api/admin/members')).json()
        juno = next(r for r in board['members'] if r['username'] == 'juno')
        assert juno['ada']['label'] == 'Arranged'

    async def test_message_a_member(self, db, app, mocker):
        _, raw = await _staff()
        member = await make_member(discord_id=8107, username='lee')
        send = mocker.patch(
            'application.services.discord.discord_service.DiscordService.send_dm', return_value=(True, ''),
        )
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/admin/members/{member.id}/message', json={'message': ' Hi! '})).status_code == 200
            assert (await c.post(f'/api/admin/members/{member.id}/message', json={'message': '   '})).status_code == 400
        send.assert_awaited_once_with(8107, 'Hi!')
        assert await AuditLog.filter(action=AuditActions.USER_MESSAGED).count() == 1


class TestMeetups:
    async def test_review_queue(self, db, app, mocker):
        mocker.patch('application.services.discord.discord_service.DiscordService.send_dm', return_value=(True, ''))
        _, raw = await _staff()
        host = await make_member(discord_id=8201, username='tavi')
        meetup = await CommunityEvent.create(
            title='Trivia night', starts_at=_later(24), duration_minutes=120, host=host,
            status=CommunityEventStatus.PENDING,
        )
        async with client_for(app, raw) as c:
            rows = (await c.get('/api/admin/meetups')).json()['meetups']
            assert [(r['title'], r['host_name'], r['status']) for r in rows] == [('Trivia night', 'tavi', 'pending')]
            assert (await c.post(f'/api/admin/meetups/{meetup.id}/reject', json={'reason': ''})).status_code == 422
            body = (await c.post(f'/api/admin/meetups/{meetup.id}/reject', json={'reason': 'Needs a room'})).json()
            assert (body['status'], body['review_note']) == ('rejected', 'Needs a room')
            assert (await c.post(f'/api/admin/meetups/{meetup.id}/approve')).json()['status'] == 'approved'

    async def test_players_are_refused(self, db, app):
        _, raw = await create_user_token(username='plain', roles=[])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/meetups')).status_code == 403


class TestCheckIn:
    async def test_board_and_event_crud(self, db, app):
        _, raw = await _staff()
        async with client_for(app, raw) as c:
            created = (await c.post('/api/admin/check-in/events', json={
                'name': 'SGL badges', 'comp_roles': ['staff'], 'comp_volunteers': True,
            })).json()
            assert (created['status'], created['comp_roles']) == ('draft', ['staff'])
            board = (await c.get('/api/admin/check-in')).json()
            assert [e['name'] for e in board['events']] == ['SGL badges']
            assert board['login']['configured'] is False
            assert any(o['value'] == 'staff' for o in board['comp_role_options'])
            updated = (await c.put(f"/api/admin/check-in/events/{created['id']}", json={
                'name': 'SGL badges', 'status': 'open', 'sync_interval_minutes': 10,
            })).json()
            assert (updated['status'], updated['sync_interval_minutes']) == ('open', 10)
            assert (await c.get(f"/api/admin/check-in/events/{created['id']}/tiers")).json() == []
            assert (await c.delete(f"/api/admin/check-in/events/{created['id']}")).status_code == 204
        assert await CheckInEvent.all().count() == 0

    async def test_desk_volunteers_cannot_set_up_events(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/check-in')).status_code == 403
            assert (await c.post('/api/admin/check-in/events', json={'name': 'x'})).status_code == 403


class TestEquipment:
    async def test_people_and_labels(self, db, app):
        boss, raw = await _staff()
        await make_member(discord_id=8301, username='owner')
        await make_user(discord_id=8302, username='stranger')
        asset = await Equipment.create(asset_number=1, name='Retron 5')
        async with client_for(app, raw) as c:
            board = (await c.get('/api/admin/equipment')).json()
            assert [a['name'] for a in board['assets']] == ['Retron 5']
            mine = {p['username'] for p in (await c.get('/api/admin/equipment/people')).json()}
            everyone = {p['username'] for p in (await c.get('/api/admin/equipment/people?everyone=true')).json()}
            sheet = (await c.get(f'/api/admin/equipment/labels?ids={asset.id}&ids=999999')).json()
        assert 'owner' in mine and 'stranger' not in mine
        assert 'stranger' in everyone
        assert [a['name'] for a in sheet['assets']] == ['Retron 5']
        assert sheet['assets'][0]['qr'].startswith('data:image/svg+xml')

    async def test_players_are_refused(self, db, app):
        _, raw = await create_user_token(username='plain', roles=[])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/equipment/people')).status_code == 403
