"""The Patreon REST surface: staff card, your link, the callback, the webhook, the flag."""

import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import pytest

from application.utils.mocks.mock_patreon import MOCK_SIGNING_KEY
from models import FeatureFlag, PatreonCampaign, Role, TenantFeatureFlag
from tests.api_helpers import build_api_app, client_for, create_user_token

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def mock_patreon_on(monkeypatch):
    monkeypatch.setenv('MOCK_PATREON', '1')


def _state(url: str) -> str:
    return parse_qs(urlparse(url).query)['state'][0]


async def test_connect_link_webhook_and_no_secrets_in_responses():
    app = build_api_app()
    _, staff_token = await create_user_token(username='staff', roles=[Role.STAFF])
    member, member_token = await create_user_token(username='member')

    async with client_for(app, member_token) as client:
        assert (await client.get('/api/settings/integrations/patreon')).status_code == 403
        assert (await client.post('/api/settings/integrations/patreon/connect')).status_code == 403

    async with client_for(app, staff_token) as client:
        r = await client.post('/api/settings/integrations/patreon/connect')
        assert r.status_code == 200, r.text
        url = r.json()['authorize_url']
    async with client_for(app) as anonymous:
        r = await anonymous.get('/api/patreon/callback', params={'state': _state(url), 'code': 'mock'})
        assert r.status_code == 200 and 'Patreon is connected' in r.text
        assert r.headers['cache-control'] == 'no-store'

    async with client_for(app, staff_token) as client:
        card = await client.get('/api/settings/integrations/patreon')
        assert card.json()['connected'] is True and card.json()['campaign_name'] == 'Mock Campaign'
        assert 'mock-access' not in card.text and MOCK_SIGNING_KEY not in card.text
        r = await client.put('/api/settings/integrations/patreon/tiers', json={'tiers': [
            {'tier_id': 't-hero', 'badge_label': 'Hero patron'}, {'tier_id': 't-supporter', 'badge_label': None}]})
        assert r.status_code == 200 and r.json()['tiers'][0]['badge_label'] == 'Hero patron'
        assert (await client.post('/api/settings/integrations/patreon/sync')).status_code == 202

    async with client_for(app, member_token) as client:
        url = (await client.post('/api/me/patreon/link')).json()['authorize_url']
    async with client_for(app) as anonymous:
        r = await anonymous.get('/api/patreon/callback', params={'state': _state(url), 'code': 'mock'})
        assert r.status_code == 200 and 'Patreon is linked' in r.text
        r = await anonymous.get('/api/patreon/callback', params={'state': _state(url), 'code': 'mock'})
        assert 'That sign-in has expired' in r.text
    async with client_for(app, member_token) as client:
        mine = (await client.get('/api/me/patreon')).json()
        assert mine['linked'] and mine['here']['is_patron'] and mine['here']['badge_label'] == 'Hero patron'
        assert 'token' not in json.dumps(mine).lower().replace('patreon', '')
        r = await client.patch('/api/me/patreon', json={'badge_hidden': True})
        assert r.json()['badge_hidden'] is True

    async with client_for(app, staff_token) as client:
        details = (await client.get(f'/api/users/{member.id}/details')).json()
        assert details['patron']['is_patron'] is True and details['patron']['badge_hidden'] is True
        rows = (await client.get('/api/admin/members')).json()['members']
        assert next(r for r in rows if r['id'] == member.id)['patron_badge'] is None

    campaign = await PatreonCampaign.get()
    body = json.dumps({'data': {'type': 'member', 'id': 'm-1', 'attributes': {'patron_status': 'former_patron'},
                                'relationships': {'user': {'data': {'type': 'user', 'id': 'p-1001'}}}}}).encode()
    good = hmac.new(MOCK_SIGNING_KEY.encode(), body, hashlib.md5).hexdigest()
    async with client_for(app) as anonymous:
        hook = f'/api/patreon-webhooks/{campaign.webhook_token}'
        assert (await anonymous.post(hook, content=body, headers={'X-Patreon-Signature': '0' * 32})).status_code == 403
        assert (await anonymous.post('/api/patreon-webhooks/nope', content=body)).status_code == 404
        ok = await anonymous.post(hook, content=body, headers={'X-Patreon-Signature': good, 'X-Patreon-Event': 'members:update'})
        assert ok.status_code == 200 and ok.json()['outcome'] == 'badge changed'

    async with client_for(app, member_token) as client:
        assert (await client.get('/api/me/patreon')).json()['here']['is_patron'] is False
        assert (await client.delete('/api/me/patreon')).status_code == 204
    async with client_for(app, staff_token) as client:
        assert (await client.delete('/api/settings/integrations/patreon')).status_code == 204
        assert (await client.get('/api/settings/integrations/patreon')).json()['connected'] is False


async def test_the_routes_404_with_the_flag_off():
    await TenantFeatureFlag.filter(flag=FeatureFlag.PATREON.value).update(enabled=False)
    _, token = await create_user_token(username='staff', roles=[Role.STAFF])
    async with client_for(build_api_app(), token) as client:
        assert (await client.get('/api/settings/integrations/patreon')).status_code == 404
        assert (await client.get('/api/me/patreon')).status_code == 404
