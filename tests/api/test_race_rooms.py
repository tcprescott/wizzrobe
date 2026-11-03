"""REST API tests for the race room endpoints (api/routers/race_rooms.py).

Covers the read endpoints (/open, /by-match/{id}), the write endpoints
(manual create, cancel, status), auth gating (staff/sync for reads/writes,
read-only tokens rejected on writes), and cross-tenant isolation — a token for
one tenant never sees or mutates another tenant's rooms.
"""


from application.tenant_context import tenant_scope
from models import (
    Match,
    RaceRoomStatus,
    RacetimeRoom,
    Role,
    Tenant,
    Tournament,
)
from tests.api_helpers import client_for, create_user_token, enable_all_features


async def _staff_token(username='staff'):
    return await create_user_token(username=username, roles=[Role.STAFF])


async def _make_room(*, slug, category='alttp', match=None, status=RaceRoomStatus.OPEN):
    return await RacetimeRoom.create(slug=slug, category=category, match=match, status=status)


class TestOpenList:
    async def test_list_open_success(self, db, app):
        _, raw = await _staff_token()
        await _make_room(slug='alttp/match-open')
        await _make_room(slug='alttp/match-done', status=RaceRoomStatus.FINISHED)
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-rooms/open')
            assert resp.status_code == 200
            slugs = {r['slug'] for r in resp.json()}
            assert slugs == {'alttp/match-open'}

    async def test_list_open_requires_auth(self, db, app):
        await _make_room(slug='alttp/match-noauth')
        async with client_for(app) as c:
            resp = await c.get('/api/race-rooms/open')
            assert resp.status_code == 401

    async def test_list_open_forbidden_for_plain_user(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-rooms/open')
            assert resp.status_code == 403


    async def test_a_sync_admin_lists_and_cancels_open_rooms(self, db, app):
        """Sync admins run the Racetime tab, so they read the list as well as cancel."""
        _, raw = await create_user_token(username='sync', roles=[Role.SYNC_ADMIN])
        room = await _make_room(slug='alttp/match-sync')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-rooms/open')
            assert resp.status_code == 200
            assert {r['slug'] for r in resp.json()} == {'alttp/match-sync'}
            resp = await c.post(f'/api/race-rooms/{room.id}/cancel', json={'reason': 'called off'})
            assert resp.status_code == 200

class TestByMatch:
    async def test_by_match_success(self, db, app):
        _, raw = await create_user_token(username='reader')
        tournament = await Tournament.create(name='Cup')
        match = await Match.create(tournament=tournament)
        await _make_room(slug='alttp/match-linked', match=match)
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/race-rooms/by-match/{match.id}')
            assert resp.status_code == 200
            body = resp.json()
            assert body['slug'] == 'alttp/match-linked'
            assert body['match_id'] == match.id

    async def test_by_match_match_not_found(self, db, app):
        _, raw = await create_user_token(username='reader')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/race-rooms/by-match/9999')
            assert resp.status_code == 404

    async def test_by_match_no_room(self, db, app):
        _, raw = await create_user_token(username='reader')
        tournament = await Tournament.create(name='Cup')
        match = await Match.create(tournament=tournament)
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/race-rooms/by-match/{match.id}')
            assert resp.status_code == 404


class TestCancel:
    async def test_cancel_success(self, db, app):
        _, raw = await _staff_token()
        room = await _make_room(slug='alttp/match-cancel')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/race-rooms/{room.id}/cancel', json={'reason': 'called off'})
            assert resp.status_code == 200
            assert resp.json()['status'] == RaceRoomStatus.CANCELLED.value
        refreshed = await RacetimeRoom.get(id=room.id)
        assert refreshed.status == RaceRoomStatus.CANCELLED

    async def test_cancel_read_only_token_forbidden(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF], read_only=True)
        room = await _make_room(slug='alttp/match-ro')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/race-rooms/{room.id}/cancel', json={})
            assert resp.status_code == 403

    async def test_cancel_forbidden_for_plain_user(self, db, app):
        _, raw = await create_user_token(username='plain')
        room = await _make_room(slug='alttp/match-plain')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/race-rooms/{room.id}/cancel', json={})
            assert resp.status_code == 403

    async def test_cancel_not_found(self, db, app):
        _, raw = await _staff_token()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-rooms/9999/cancel', json={})
            assert resp.status_code == 404


class TestSetStatus:
    async def test_set_status_success(self, db, app):
        _, raw = await _staff_token()
        room = await _make_room(slug='alttp/match-status')
        async with client_for(app, raw) as c:
            resp = await c.patch(
                f'/api/race-rooms/{room.id}/status',
                json={'status': RaceRoomStatus.IN_PROGRESS.value},
            )
            assert resp.status_code == 200
            assert resp.json()['status'] == RaceRoomStatus.IN_PROGRESS.value

    async def test_set_status_forbidden_for_plain_user(self, db, app):
        _, raw = await create_user_token(username='plain')
        room = await _make_room(slug='alttp/match-status-plain')
        async with client_for(app, raw) as c:
            resp = await c.patch(
                f'/api/race-rooms/{room.id}/status',
                json={'status': RaceRoomStatus.IN_PROGRESS.value},
            )
            assert resp.status_code == 403


class TestManualCreate:
    async def test_create_read_only_token_forbidden(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-rooms', json={'match_id': 1})
            assert resp.status_code == 403

    async def test_create_forbidden_for_plain_user(self, db, app):
        _, raw = await create_user_token(username='plain')
        tournament = await Tournament.create(name='Cup')
        match = await Match.create(tournament=tournament)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/race-rooms', json={'match_id': match.id})
            assert resp.status_code == 403

    async def test_create_requires_auth(self, db, app):
        async with client_for(app) as c:
            resp = await c.post('/api/race-rooms', json={'match_id': 1})
            assert resp.status_code == 401


# --- Cross-tenant isolation -----------------------------------------------

class TestTenantIsolation:
    async def test_open_list_and_by_id_are_tenant_isolated(self, db, app):
        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')

        with tenant_scope(a.id):
            tournament_a = await Tournament.create(name='A Cup')
            match_a = await Match.create(tournament=tournament_a)
            room_a = await _make_room(slug='alttp/a-match', match=match_a)
        with tenant_scope(b.id):
            await enable_all_features(b.id)
            _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])

        async with client_for(app, token_b) as c:
            open_resp = await c.get('/api/race-rooms/open')
            assert open_resp.status_code == 200
            assert room_a.slug not in {r['slug'] for r in open_resp.json()}

            bm_resp = await c.get(f'/api/race-rooms/by-match/{match_a.id}')
            assert bm_resp.status_code == 404

            cancel_resp = await c.post(f'/api/race-rooms/{room_a.id}/cancel', json={})
            assert cancel_resp.status_code == 404

            status_resp = await c.patch(
                f'/api/race-rooms/{room_a.id}/status',
                json={'status': RaceRoomStatus.CANCELLED.value},
            )
            assert status_resp.status_code == 404
