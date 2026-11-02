"""NativePushService: store-app push tokens, APNs + FCM delivery, and pruning."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from application.errors import NotFoundError
from application.services import native_push_service as mod
from application.services.native_push_service import NativePushService
from application.services.web_push_service import WebPushService
from models import AuditLog, DevicePushToken
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures('db')

IOS_TOKEN = 'a1' * 32
ANDROID_TOKEN = 'fcm-token:APA91b' + 'x' * 140


def _pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()


@pytest.fixture(autouse=True)
def _fresh_config(monkeypatch):
    for name in ('APNS_KEY', 'APNS_KEY_ID', 'APNS_TEAM_ID', 'APNS_TOPIC', 'APNS_SANDBOX', 'FCM_SERVICE_ACCOUNT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mod, '_apns_token', ('', 0.0))
    monkeypatch.setattr(mod, '_fcm_token', ('', 0.0))
    yield


@pytest.fixture
def apns_env(monkeypatch):
    monkeypatch.setenv('APNS_KEY', _pem(ec.generate_private_key(ec.SECP256R1())))
    monkeypatch.setenv('APNS_KEY_ID', 'KEY1234567')
    monkeypatch.setenv('APNS_TEAM_ID', 'TEAM123456')
    monkeypatch.setenv('APNS_TOPIC', 'org.example.wizzrobe')


@pytest.fixture
def fcm_env(monkeypatch):
    account = {
        'project_id': 'wiz-test',
        'client_email': 'push@wiz-test.iam.gserviceaccount.com',
        'private_key': _pem(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        'token_uri': 'https://oauth2.example.com/token',
    }
    monkeypatch.setenv('FCM_SERVICE_ACCOUNT', json.dumps(account))


class FakeResponse:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.is_closed = False

    async def post(self, url, content=None, json=None, data=None, headers=None):
        self.calls.append({'url': url, 'content': content, 'json': json, 'data': data, 'headers': headers})
        return self._responses.pop(0)


def _install(monkeypatch, responses) -> FakeClient:
    fake = FakeClient(responses)
    monkeypatch.setattr(mod, '_client', lambda: fake)
    return fake


class TestRegister:
    async def test_needs_a_configured_platform(self):
        user = await make_user(1)
        with pytest.raises(NotFoundError):
            await NativePushService().register(user, platform='ios', token=IOS_TOKEN)

    async def test_ios_goes_through_fcm_when_only_fcm_is_set(self, fcm_env):
        assert NativePushService.configured_platforms() == ['ios', 'android']

    async def test_apns_alone_serves_ios_only(self, apns_env):
        assert NativePushService.configured_platforms() == ['ios']
        user = await make_user(1)
        with pytest.raises(NotFoundError):
            await NativePushService().register(user, platform='android', token=ANDROID_TOKEN)

    async def test_rejects_unknown_platforms_and_junk_tokens(self, fcm_env):
        user = await make_user(1)
        with pytest.raises(ValueError):
            await NativePushService().register(user, platform='windows', token='abc')
        with pytest.raises(ValueError):
            await NativePushService().register(user, platform='android', token='has space')

    async def test_a_token_moves_to_whoever_signs_in_next(self, fcm_env):
        alice, bob = await make_user(1), await make_user(2)
        service = NativePushService()
        first = await service.register(alice, platform='android', token=ANDROID_TOKEN, device_name='Pixel 9')
        second = await service.register(bob, platform='android', token=ANDROID_TOKEN, device_name='Pixel 9')
        assert first.id == second.id
        assert await DevicePushToken.filter(user=bob).count() == 1
        assert await DevicePushToken.filter(user=alice).count() == 0
        assert await AuditLog.filter(action='device_push.registered').count() == 2

    async def test_remove_only_your_own(self, fcm_env):
        alice, bob = await make_user(1), await make_user(2)
        row = await NativePushService().register(alice, platform='android', token=ANDROID_TOKEN)
        with pytest.raises(NotFoundError):
            await NativePushService().remove(bob, row.id)
        await NativePushService().remove(alice, row.id)
        assert await DevicePushToken.all().count() == 0


class TestApns:
    async def test_sends_an_alert_with_the_link(self, apns_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='ios', token=IOS_TOKEN)
        fake = _install(monkeypatch, [FakeResponse(200)])

        sent = await NativePushService().send_to_discord_id(1, title='Wizzrobe', body='Hi', navigate='https://w.example/t/x/home')

        assert sent == 1
        call = fake.calls[0]
        assert call['url'] == f'https://api.push.apple.com/3/device/{IOS_TOKEN}'
        assert call['headers']['apns-topic'] == 'org.example.wizzrobe'
        assert call['headers']['authorization'].startswith('bearer ')
        payload = json.loads(call['content'])
        assert payload['aps']['alert'] == {'title': 'Wizzrobe', 'body': 'Hi'}
        assert payload['aps']['sound'] == 'default'
        assert payload['navigate'] == 'https://w.example/t/x/home'
        assert (await DevicePushToken.get(token=IOS_TOKEN)).last_used_at is not None

    async def test_silent_drops_the_sound(self, apns_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='ios', token=IOS_TOKEN)
        fake = _install(monkeypatch, [FakeResponse(200)])
        await NativePushService().send_to_discord_id(1, title='t', body='b', silent=True)
        assert 'sound' not in json.loads(fake.calls[0]['content'])['aps']

    async def test_a_sandbox_token_is_retried_on_the_sandbox(self, apns_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='ios', token=IOS_TOKEN)
        fake = _install(monkeypatch, [FakeResponse(400, {'reason': 'BadDeviceToken'}), FakeResponse(200)])
        assert await NativePushService().send_to_discord_id(1, title='t', body='b') == 1
        assert fake.calls[1]['url'].startswith('https://api.sandbox.push.apple.com/')

    async def test_unregistered_tokens_are_pruned(self, apns_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='ios', token=IOS_TOKEN)
        _install(monkeypatch, [FakeResponse(410, {'reason': 'Unregistered'})])
        assert await NativePushService().send_to_discord_id(1, title='t', body='b') == 0
        assert await DevicePushToken.all().count() == 0

    async def test_a_server_error_keeps_the_token(self, apns_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='ios', token=IOS_TOKEN)
        _install(monkeypatch, [FakeResponse(503, {'reason': 'ServiceUnavailable'})])
        assert await NativePushService().send_to_discord_id(1, title='t', body='b') == 0
        assert await DevicePushToken.all().count() == 1


class TestFcm:
    async def test_gets_a_token_then_sends(self, fcm_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='android', token=ANDROID_TOKEN)
        fake = _install(monkeypatch, [
            FakeResponse(200, {'access_token': 'ya29.test', 'expires_in': 3600}),
            FakeResponse(200, {'name': 'projects/wiz-test/messages/1'}),
        ])

        sent = await NativePushService().send_to_discord_id(1, title='Wizzrobe', body='Hi', navigate='https://w.example/t/x')

        assert sent == 1
        assert fake.calls[0]['url'] == 'https://oauth2.example.com/token'
        assert fake.calls[0]['data']['grant_type'] == 'urn:ietf:params:oauth:grant-type:jwt-bearer'
        send = fake.calls[1]
        assert send['url'] == 'https://fcm.googleapis.com/v1/projects/wiz-test/messages:send'
        assert send['headers']['Authorization'] == 'Bearer ya29.test'
        message = send['json']['message']
        assert message['token'] == ANDROID_TOKEN
        assert message['data'] == {'navigate': 'https://w.example/t/x'}
        assert message['android']['notification']['channel_id'] == 'messages'

    async def test_silent_uses_the_quiet_channel(self, fcm_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='android', token=ANDROID_TOKEN)
        fake = _install(monkeypatch, [FakeResponse(200, {'access_token': 'a', 'expires_in': 3600}), FakeResponse(200)])
        await NativePushService().send_to_discord_id(1, title='t', body='b', silent=True)
        assert fake.calls[1]['json']['message']['android']['notification']['channel_id'] == 'quiet'

    async def test_unregistered_tokens_are_pruned(self, fcm_env, monkeypatch):
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='android', token=ANDROID_TOKEN)
        gone = {'error': {'status': 'NOT_FOUND', 'details': [{'errorCode': 'UNREGISTERED'}]}}
        _install(monkeypatch, [FakeResponse(200, {'access_token': 'a', 'expires_in': 3600}), FakeResponse(404, gone)])
        assert await NativePushService().send_to_discord_id(1, title='t', body='b') == 0
        assert await DevicePushToken.all().count() == 0


class TestDmMirror:
    async def test_the_dm_mirror_reaches_store_apps_without_vapid(self, fcm_env, monkeypatch):
        monkeypatch.delenv('VAPID_PRIVATE_KEY', raising=False)
        user = await make_user(1)
        await DevicePushToken.create(user=user, platform='android', token=ANDROID_TOKEN)
        fake = _install(monkeypatch, [FakeResponse(200, {'access_token': 'a', 'expires_in': 3600}), FakeResponse(200)])

        await WebPushService().mirror_dm(1, '**Match** soon', navigate='https://w.example/t/x')

        assert fake.calls[1]['json']['message']['notification'] == {'title': 'Wizzrobe', 'body': 'Match soon'}
