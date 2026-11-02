"""The app's Settings panes over REST: staff read and save, everyone else is refused."""

from models import Event, EventStatus
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID


async def test_appearance_saves_colours_and_warns_on_contrast(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        before = (await c.get('/api/settings/appearance')).json()
        assert before['presets'] and before['logo_url'] is None
        r = await c.put('/api/settings/appearance', json={'primary': '#FFFF00'})
        assert r.status_code == 200
        body = r.json()
        assert body['colours']['primary'] == '#ffff00'
        assert body['colours']['accent'] == before['defaults']['accent']
        assert body['issues'].get('primary'), 'yellow buttons fail AA on white'
        bad = await c.put('/api/settings/appearance', json={'primary': 'yellow'})
        assert bad.status_code == 400
        check = await c.post('/api/settings/appearance/contrast', json={'colours': {'primary': '#ffff00'}})
        assert check.json()['issues']['primary']


async def test_joining_and_reminders_round_trip(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        joining = (await c.get('/api/settings/joining')).json()
        assert joining['guild_linked'] is False
        assert 'discord' not in [m['value'] for m in joining['modes']], 'no linked server, no Discord-only mode'
        r = await c.put('/api/settings/joining', json={'join_mode': 'request', 'discord_invite_url': 'discord.gg/abc123'})
        assert r.status_code == 200
        assert r.json()['join_mode'] == 'request'
        assert r.json()['discord_invite_url'].startswith('https://discord.gg/')
        assert (await c.put('/api/settings/joining', json={'join_mode': 'nope'})).status_code == 400

        reminders = (await c.get('/api/settings/profile-reminders')).json()
        check = reminders['reminders'][0]['check']
        r = await c.put('/api/settings/profile-reminders', json={'rules': [{'check': check, 'dismissible': False, 'note': 'Please'}]})
        saved = {x['check']: x for x in r.json()['reminders']}
        assert saved[check]['enabled'] and not saved[check]['dismissible'] and saved[check]['note'] == 'Please'


async def test_watch_volunteers_meetups_and_clock(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        watch = (await c.get('/api/settings/watch')).json()
        assert watch['mode'] == 'live' and watch['audience']['admissions_per_minute'] > 0
        r = await c.put('/api/settings/watch', json={'mode': 'static', 'static_signed_out': True, 'auto_static': False, 'threshold': 50})
        assert r.json()['mode'] == 'static' and r.json()['threshold'] == 50
        assert (await c.put('/api/settings/watch', json={**r.json(), 'threshold': 0})).status_code == 400

        r = await c.put('/api/settings/volunteers', json={'reminder_lead_minutes': 45})
        assert r.json() == {'reminder_lead_minutes': 45}
        r = await c.put('/api/settings/volunteers', json={'reminder_lead_minutes': None})
        assert r.json() == {'reminder_lead_minutes': None}

        r = await c.put('/api/settings/meetups', json={'require_approval': False, 'reminder_lead_minutes': 15})
        assert r.json() == {'require_approval': False, 'reminder_lead_minutes': 15}

        r = await c.put('/api/settings/timezone', json={'mode': 'pinned', 'name': 'Europe/Berlin'})
        assert r.json()['mode'] == 'pinned' and r.json()['name'] == 'Europe/Berlin'
        assert (await c.put('/api/settings/timezone', json={'mode': 'pinned', 'name': 'Mars/Base'})).status_code == 400


async def test_features_toggle(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        features = (await c.get('/api/settings/features')).json()['features']
        flag = next(f for f in features if f['available'] and f['enabled'])['flag']
        r = await c.put(f'/api/settings/features/{flag}', json={'enabled': False})
        assert next(f for f in r.json()['features'] if f['flag'] == flag)['enabled'] is False
        assert (await c.put('/api/settings/features/nope', json={'enabled': True})).status_code == 400


async def test_connected_apps_lists_nothing_yet(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.get('/api/settings/apps')
    assert r.status_code == 200 and r.json() == []


async def test_players_cant_read_or_save_settings(db, app):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/settings/appearance')).status_code == 403
        assert (await c.put('/api/settings/watch', json={'mode': 'static', 'static_signed_out': False, 'auto_static': True, 'threshold': 5})).status_code == 403


async def test_event_summary(db, app):
    _, raw = await staff_token()
    event = await Event.filter(tenant_id=DEFAULT_TEST_TENANT_ID, status=EventStatus.LIVE).first()
    assert event is not None
    async with client_for(app, raw) as c:
        r = await c.get(f'/api/events/{event.slug}/summary')
    assert r.status_code == 200
    assert set(r.json()) == {'tournaments', 'matches', 'open_matches', 'future_shifts', 'open_race_rooms'}
