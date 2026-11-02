"""The app's admin menu read: who sees which areas, panes and setup steps."""

from models import Role
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token


async def test_staff_get_the_admin_menu(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.get('/api/admin/nav')
    assert r.status_code == 200
    body = r.json()
    slugs = [a['slug'] for a in body['areas']]
    assert {'match-schedule', 'members', 'events', 'settings', 'event-settings'} <= set(slugs)
    schedule = next(a for a in body['areas'] if a['slug'] == 'match-schedule')
    assert schedule['group'] == 'Run the event' and schedule['half'] == 'event'
    assert 'schedule' in schedule['aliases']
    assert [p['slug'] for p in body['settings_panes']][:3] == ['general', 'members', 'watch']
    assert body['event_settings_panes'][0]['slug'] == 'dates'
    assert body['setup'], 'staff see the setup checklist'


async def test_a_broadcast_tech_sees_only_their_areas(db, app):
    _, raw = await create_user_token(username='tech', roles=[Role.STREAM_MANAGER])
    async with client_for(app, raw) as c:
        body = (await c.get('/api/admin/nav')).json()
    assert [a['slug'] for a in body['areas']] [:2] == ['match-schedule', 'stages']
    assert 'members' not in [a['slug'] for a in body['areas']]
    assert body['settings_panes'] == [] and body['setup'] == []


async def test_a_player_gets_nothing(db, app):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        r = await c.get('/api/admin/nav')
    assert r.status_code == 200
    assert r.json()['areas'] == []
