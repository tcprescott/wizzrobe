"""The app's Integrations pane over REST: which cards show, the sign-in cards, webhooks, keys and the AI provider."""

from datetime import datetime, timedelta, timezone

from models import ChallongeConnection, Role, Tenant, Tournament
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token


async def test_cards_by_role(db, app):
    _, staff = await staff_token()
    async with client_for(app, staff) as c:
        assert (await c.get('/api/settings/integrations')).json() == {
            'challonge': True, 'startgg': True, 'webhooks': True, 'randomizer_keys': True, 'ai': True,
        }
    _, presets = await create_user_token(username='presets', roles=[Role.PRESET_MANAGER])
    async with client_for(app, presets) as c:
        body = (await c.get('/api/settings/integrations')).json()
        assert body == {'challonge': False, 'startgg': False, 'webhooks': False, 'randomizer_keys': True, 'ai': False}
        assert (await c.get('/api/settings/integrations/webhooks')).status_code == 403
        assert len((await c.get('/api/settings/integrations/randomizer-keys')).json()) == 3
    _, player = await create_user_token(username='player')
    async with client_for(app, player) as c:
        assert (await c.get('/api/settings/integrations')).status_code == 403
        assert (await c.get('/api/settings/integrations/randomizer-keys')).status_code == 403


async def test_challonge_card(db, app):
    tenant = await Tenant.get(id=1)
    await ChallongeConnection.create(
        tenant=tenant, access_token='a', refresh_token='r', challonge_username='sgl_brackets',
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    await Tournament.create(name='Linked', challonge_tournament_id='123', is_active=True)
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        body = (await c.get('/api/settings/integrations/challonge')).json()
        assert body['connected'] is True and body['username'] == 'sgl_brackets'
        assert body['linked_tournaments'] == 1 and body['auto_sync_minutes'] == 0
        assert 15 in body['auto_sync_choices']

        assert (await c.put('/api/settings/integrations/challonge/auto-sync', json={'minutes': 30})).status_code == 204
        body = (await c.get('/api/settings/integrations/challonge')).json()
        assert body['auto_sync_minutes'] == 30 and body['auto_sync_brackets'] == 1
        assert body['auto_sync_requests_per_day'] == 96
        assert (await c.put('/api/settings/integrations/challonge/auto-sync', json={'minutes': 7})).status_code == 400

        assert (await c.post('/api/settings/integrations/challonge/disconnect')).status_code == 204
        assert (await c.get('/api/settings/integrations/challonge')).json()['connected'] is False


async def test_startgg_card_unconnected(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        body = (await c.get('/api/settings/integrations/startgg')).json()
        assert body['connected'] is False and body['linked'] == []


async def test_webhooks_with_health_and_options(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.post('/api/webhooks', json={
            'name': 'SG Schedule Update', 'url': 'https://hooks.example.org/sg', 'event_types': ['match.rescheduled'],
        })
        assert r.status_code == 201
        rows = (await c.get('/api/settings/integrations/webhooks')).json()
        assert rows[0]['name'] == 'SG Schedule Update'
        assert rows[0]['delivered'] == 0 and rows[0]['failed'] == 0 and rows[0]['last_sent_at'] is None
        options = (await c.get('/api/settings/integrations/webhook-options')).json()
        assert options['event_types'][0] == '*' and 'match.rescheduled' in options['event_types']
        assert options['events']


async def test_randomizer_keys_round_trip(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        assert (await c.put('/api/settings/integrations/randomizer-keys/ootr/api_key', json={'value': 'secret-1'})).status_code == 204
        rows = {r['randomizer']: r for r in (await c.get('/api/settings/integrations/randomizer-keys')).json()}
        assert rows['ootr']['configured'] is True and 'value' not in rows['ootr']
        assert (await c.put('/api/settings/integrations/randomizer-keys/nope/api_key', json={'value': 'x'})).status_code in (400, 404)
        assert (await c.delete('/api/settings/integrations/randomizer-keys/ootr/api_key')).status_code == 204
        rows = {r['randomizer']: r for r in (await c.get('/api/settings/integrations/randomizer-keys')).json()}
        assert rows['ootr']['configured'] is False


async def test_ai_provider_round_trip(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        body = (await c.get('/api/settings/integrations/ai')).json()
        assert body['configured'] is False and body['default_models']['anthropic']
        missing_key = await c.put('/api/settings/integrations/ai', json={
            'provider': 'anthropic', 'model': 'claude-x', 'daily_limit': 20,
        })
        assert missing_key.status_code == 400
        r = await c.put('/api/settings/integrations/ai', json={
            'provider': 'anthropic', 'model': 'claude-x', 'api_key': 'sk-test', 'daily_limit': 20,
        })
        assert r.status_code == 200 and r.json()['configured'] is True and r.json()['key_readable'] is True
        assert 'api_key' not in r.json()
        assert (await c.delete('/api/settings/integrations/ai')).status_code == 204
        assert (await c.get('/api/settings/integrations/ai')).json()['configured'] is False
