"""What the iOS and Android store apps need from the server: push tokens over
REST, the /.well-known association files, and CORS for their webviews."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from api.app_association import router as association_router
from application.services import OAuthAppService
from models import DevicePushToken, Role
from tests.api._bracket_api import token_for
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member

ANDROID_TOKEN = 'fcm-token:APA91b' + 'x' * 140


@pytest.fixture
def fcm_env(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT', json.dumps({
        'project_id': 'p', 'client_email': 'e@p.iam.gserviceaccount.com', 'private_key': key,
    }))
    for name in ('APNS_KEY', 'APNS_KEY_ID', 'APNS_TEAM_ID', 'APNS_TOPIC'):
        monkeypatch.delenv(name, raising=False)


class TestNativeDevices:
    async def test_register_list_and_remove(self, db, app, fcm_env):
        alice = await make_member(discord_id=9601, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/me/channels')).json()
            assert body['native_push_platforms'] == ['ios', 'android']
            assert body['native_devices'] == []

            r = await c.post('/api/me/channels/native-devices', json={
                'platform': 'android', 'token': ANDROID_TOKEN, 'device_name': 'Pixel 9',
            })
            assert r.status_code == 200, r.text
            devices = r.json()['native_devices']
            assert [(d['platform'], d['device_name']) for d in devices] == [('android', 'Pixel 9')]
            assert r.json()['registered_device_id'] == devices[0]['id']

            r = await c.delete(f"/api/me/channels/native-devices/{devices[0]['id']}")
            assert r.status_code == 200
            assert r.json()['native_devices'] == []

    async def test_unconfigured_platform_is_404(self, db, app, monkeypatch):
        monkeypatch.delenv('FCM_SERVICE_ACCOUNT', raising=False)
        monkeypatch.delenv('APNS_KEY', raising=False)
        alice = await make_member(discord_id=9602, username='alice')
        async with client_for(app, await token_for(alice)) as c:
            r = await c.post('/api/me/channels/native-devices', json={'platform': 'ios', 'token': 'ab' * 32})
        assert r.status_code == 404

    async def test_cannot_remove_someone_elses_phone(self, db, app, fcm_env):
        alice = await make_member(discord_id=9603, username='alice')
        bob = await make_member(discord_id=9604, username='bob')
        row = await DevicePushToken.create(user=alice, platform='android', token=ANDROID_TOKEN)
        async with client_for(app, await token_for(bob)) as c:
            assert (await c.delete(f'/api/me/channels/native-devices/{row.id}')).status_code == 404
        assert await DevicePushToken.filter(id=row.id).exists()


def _association_client() -> AsyncClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(association_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


class TestAssociationFiles:
    async def test_404_until_configured(self, monkeypatch):
        for name in ('APP_ID', 'APPLE_TEAM_ID', 'ANDROID_CERT_SHA256'):
            monkeypatch.delenv(name, raising=False)
        async with _association_client() as c:
            assert (await c.get('/.well-known/apple-app-site-association')).status_code == 404
            assert (await c.get('/.well-known/assetlinks.json')).status_code == 404

    async def test_ios_claims_community_links_only(self, monkeypatch):
        monkeypatch.setenv('APP_ID', 'org.example.wizzrobe')
        monkeypatch.setenv('APPLE_TEAM_ID', 'ABCDE12345')
        async with _association_client() as c:
            r = await c.get('/.well-known/apple-app-site-association')
        assert r.status_code == 200
        assert r.headers['content-type'] == 'application/json'
        details = r.json()['applinks']['details'][0]
        assert details['appIDs'] == ['ABCDE12345.org.example.wizzrobe']
        assert details['components'] == [{'/': '/t/*'}]

    async def test_android_lists_valid_fingerprints(self, monkeypatch):
        good = ':'.join(['AB'] * 32)
        monkeypatch.setenv('APP_ID', 'org.example.wizzrobe')
        monkeypatch.setenv('ANDROID_CERT_SHA256', f'{good.lower()}, not-a-fingerprint')
        async with _association_client() as c:
            r = await c.get('/.well-known/assetlinks.json')
        target = r.json()[0]['target']
        assert target['package_name'] == 'org.example.wizzrobe'
        assert target['sha256_cert_fingerprints'] == [good]

    async def test_bad_team_id_stays_off(self, monkeypatch):
        monkeypatch.setenv('APP_ID', 'org.example.wizzrobe')
        monkeypatch.setenv('APPLE_TEAM_ID', 'nope')
        async with _association_client() as c:
            assert (await c.get('/.well-known/apple-app-site-association')).status_code == 404


class TestNativeWebviewCors:
    async def test_first_party_app_with_an_app_scheme_opens_the_webview_origins(self, db):
        admin, _ = await create_user_token(username='platform', roles=[Role.SUPER_ADMIN])
        await OAuthAppService().register_first_party(
            admin, client_id='wizzrobe-app',
            redirect_uris=['https://app.example.org/auth/callback', 'org.example.wizzrobe:/auth/callback'],
        )
        origins = await OAuthAppService().browser_origins()
        assert {'capacitor://localhost', 'https://localhost', 'https://app.example.org'} <= origins

    async def test_web_only_first_party_app_does_not(self, db):
        admin, _ = await create_user_token(username='platform', roles=[Role.SUPER_ADMIN])
        await OAuthAppService().register_first_party(
            admin, client_id='wizzrobe-app', redirect_uris=['https://app.example.org/auth/callback'],
        )
        assert 'capacitor://localhost' not in await OAuthAppService().browser_origins()
