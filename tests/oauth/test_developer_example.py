"""The downloadable example client speaks the protocol the server actually serves.

The script talks to a real site over urllib, so the test drives its own
building blocks (PKCE pair, authorize URL, the token and revoke forms) through
the test app. A change on either side that breaks the published example fails
here rather than in a developer's terminal.
"""

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from application.services import OAuthGrantService
from application.services.oauth_grant_service import issuer
from models import OAuthClientType
from tests.api_helpers import create_community_member
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.oauth.conftest import client, make_app, query

pytestmark = pytest.mark.usefixtures('db')

EXAMPLE = Path(__file__).resolve().parents[2] / 'static' / 'developers' / 'wizzrobe_app_example.py'


def _example():
    spec = importlib.util.spec_from_file_location('wizzrobe_app_example', EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_example_signs_in_refreshes_and_disconnects():
    ex = _example()
    owner = await create_community_member(username='dev')
    member = await create_community_member(username='player')
    app, secret = await make_app(owner, client_type=OAuthClientType.PUBLIC, redirect_uris=(ex.REDIRECT_URI,))
    assert secret is None

    verifier, challenge = ex.pkce_pair()
    url = urlsplit(ex.authorize_url('http://test', app.client_id, challenge, 'st4te', 'profile schedule:read'))
    assert url.path == '/oauth/authorize'

    async with client() as http:
        start = await http.get(f'{url.path}?{url.query}')
        assert start.status_code == 302, start.text
        txn = query(start.headers['location'])['request']
        redirect = await OAuthGrantService().approve(txn, member, DEFAULT_TEST_TENANT_ID, ['profile'])
        reply = query(redirect)
        assert reply['state'] == 'st4te'
        assert reply['iss'] == issuer()

        tokens = (await http.post('/oauth/token', data=ex.code_exchange_fields(reply['code'], verifier, app.client_id))).json()
        assert tokens['scope'] == 'profile'
        async with client(tokens['access_token']) as as_app:
            me = await as_app.get('/api/users/me')
        assert me.status_code == 200 and me.json()['username'] == 'player'

        refreshed = await http.post('/oauth/token', data=ex.refresh_fields(tokens['refresh_token'], app.client_id))
        assert refreshed.status_code == 200, refreshed.text
        new = refreshed.json()
        revoked = await http.post('/oauth/revoke', data=ex.revoke_fields(new['refresh_token'], app.client_id))
        assert revoked.status_code == 200

    async with client(new['access_token']) as as_app:
        assert (await as_app.get('/api/users/me')).status_code == 401


def test_the_example_is_published_where_help_links_it():
    article = (Path(__file__).resolve().parents[2] / 'application' / 'help' / 'content' / 'building-apps.md').read_text()
    assert '/static/developers/wizzrobe_app_example.py' in article
