"""/api/notification-settings: the per-recipient settings and the combined Staff choice."""

from models import Role
from tests.api_helpers import client_for, create_user_token


async def test_list_carries_section_and_staff_choice(db, app):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        body = (await c.get('/api/notification-settings')).json()
    comment = [r for r in body if r['notice'] == 'player_comment']
    assert {r['section'] for r in comment} == {'Comments and staff alerts'}
    assert {r['staff_choice'] for r in comment} == {'ping_all'}
    scheduled = [r for r in body if r['notice'] == 'match_scheduled']
    assert {r['staff_choice'] for r in scheduled} == {None}


async def test_staff_set_the_staff_choice(db, app):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        resp = await c.put('/api/notification-settings/player_comment/staff-alerts', json={'choice': 'ping_on_duty'})
        assert resp.status_code == 200
        assert {r['recipient']: r['mode'] for r in resp.json()} == {
            'on_duty': 'ping', 'admins': 'off', 'staff': 'ping',
        }
        # The per-recipient route is untouched by the new one.
        one = await c.put('/api/notification-settings/player_comment/admins', json={'mode': 'ping'})
        assert one.status_code == 200 and one.json()['staff_choice'] == 'ping_all'
        bad = await c.put('/api/notification-settings/join_request/staff-alerts', json={'choice': 'ping_on_duty'})
        assert bad.status_code == 400


async def test_non_staff_are_refused(db, app):
    _, raw = await create_user_token(username='plain', roles=[Role.PROCTOR])
    async with client_for(app, raw) as c:
        resp = await c.put('/api/notification-settings/player_comment/staff-alerts', json={'choice': 'off'})
    assert resp.status_code == 403
