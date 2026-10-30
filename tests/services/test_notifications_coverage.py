"""Coverage tests for WebhookService and WebPushService error/edge branches.

Complements tests/services/test_webhook_service.py and
tests/services/test_web_push_service.py by exercising the untested delivery,
retry, SSRF-guard, config, and prune paths. No real network, Discord, or push
service is contacted — httpx and the encryption/config layer are stubbed.
"""

import socket

import httpx
import pytest

from application.events import Event, EventType
from application.services.web_push_service import WebPushService
from application.services.webhook_service import WebhookService
from application.utils.web_push import generate_vapid_keys
from models import AuditLog, Role, User, UserRole, Webhook, WebhookDelivery, WebPushSubscription
from tests.test_web_push_protocol import RFC_AUTH, RFC_UA_PUBLIC

ENDPOINT = 'https://push.example.net/send/abc123'


async def _staff() -> User:
    user = await User.create(discord_id=1, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _plain() -> User:
    return await User.create(discord_id=2, username='nobody')


from tests.factories import make_user as _user


async def _subscribed_user(discord_id: int = 1) -> User:
    user = await _user(discord_id)
    await WebPushSubscription.create(user=user, endpoint=ENDPOINT, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
    return user

async def _deliver(user: User, title: str = 't', body: str = 'b') -> int:
    """Push to every device ``user`` subscribed, through the shared delivery path."""
    subscriptions = await WebPushSubscription.filter(user=user)
    return await WebPushService()._send_to_subscriptions(subscriptions, title=title, body=body)


@pytest.fixture
def no_sleep(monkeypatch):
    async def _noop(_seconds):
        return None
    monkeypatch.setattr('application.services.webhook_service.asyncio.sleep', _noop)


class WebhookFakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class WebhookFakeClient:
    """Stand-in for httpx.AsyncClient used as an async context manager.

    Each scripted action is either an int status code to return or an Exception
    instance to raise from ``post`` (to exercise the ``httpx.HTTPError`` branch).
    """

    def __init__(self, actions):
        self._actions = list(actions)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None, extensions=None):
        self.calls.append({
            'url': url, 'content': content, 'headers': headers, 'extensions': extensions,
        })
        action = self._actions[len(self.calls) - 1]
        if isinstance(action, Exception):
            raise action
        return WebhookFakeResponse(action)


class TestCreateValidation:
    async def test_create_rejects_empty_name(self, db):
        actor = await _staff()
        with pytest.raises(ValueError, match='name is required'):
            await WebhookService().create_webhook(
                actor, name='   ', url='https://example.com', event_types=['*'],
            )

    async def test_url_without_host_rejected(self, db):
        with pytest.raises(ValueError, match='include a host'):
            await WebhookService()._validate_url('https://')


class TestUpdateValidation:
    async def test_update_name_and_url(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        updated = await WebhookService().update_webhook(
            actor, webhook.id, name='  renamed  ', url='https://new.example/hook',
        )
        assert updated.name == 'renamed'
        assert updated.url == 'https://new.example/hook'

    async def test_update_empty_name_raises(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        with pytest.raises(ValueError, match='name is required'):
            await WebhookService().update_webhook(actor, webhook.id, name='   ')

    async def test_update_rejects_bad_url(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        with pytest.raises(ValueError, match='https'):
            await WebhookService().update_webhook(actor, webhook.id, url='ftp://bad.example')

    async def test_update_no_changes_is_noop(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        before = await AuditLog.filter(action='webhook.updated').count()
        result = await WebhookService().update_webhook(actor, webhook.id)
        assert result.id == webhook.id
        assert await AuditLog.filter(action='webhook.updated').count() == before


class TestSsrfGuard:
    """The production-only SSRF guard (_ensure_public_host) via _validate_url."""

    async def test_private_host_rejected_in_production(self, db, monkeypatch):
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 0))],
        )
        with pytest.raises(ValueError, match='public address'):
            await WebhookService()._validate_url('https://internal.example.com/hook')

    async def test_loopback_host_rejected_in_production(self, db, monkeypatch):
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))],
        )
        with pytest.raises(ValueError, match='public address'):
            await WebhookService()._validate_url('https://localhost.trick.example/hook')

    async def test_unresolvable_host_rejected_in_production(self, db, monkeypatch):
        def _boom(*a, **k):
            raise socket.gaierror('name resolution failed')
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr('application.utils.ssrf.socket.getaddrinfo', _boom)
        with pytest.raises(ValueError, match='Could not resolve'):
            await WebhookService()._validate_url('https://nope.invalid/hook')

    async def test_public_host_allowed_in_production(self, db, monkeypatch):
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))],
        )
        await WebhookService()._validate_url('https://example.com/hook')

    async def test_create_in_production_with_public_host(self, db, monkeypatch):
        actor = await _staff()
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))],
        )
        webhook = await WebhookService().create_webhook(
            actor, name='prod', url='https://example.com/hook', event_types=['*'],
        )
        assert webhook.id is not None


class TestDeliveryErrorBranches:
    async def test_http_error_then_success_records_final_status(self, db, monkeypatch, no_sleep):
        webhook = await Webhook.create(
            name='w', url='https://example.com/hook', secret='shhh', event_types=['*'],
        )
        fake = WebhookFakeClient([httpx.HTTPError('boom'), httpx.HTTPError('boom2'), 200])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))

        assert len(fake.calls) == 3
        delivery = await WebhookDelivery.get(webhook=webhook)
        assert delivery.success is True
        assert delivery.response_status == 200
        assert delivery.attempt_count == 3
        assert delivery.error is None
        assert delivery.delivered_at is not None

    async def test_all_attempts_raise_records_error_and_null_status(self, db, monkeypatch, no_sleep):
        webhook = await Webhook.create(
            name='w', url='https://example.com/hook', secret='shhh', event_types=['*'],
        )
        fake = WebhookFakeClient([
            httpx.HTTPError('down-1'), httpx.HTTPError('down-2'), httpx.HTTPError('down-3'),
        ])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))

        assert len(fake.calls) == WebhookService.MAX_ATTEMPTS
        delivery = await WebhookDelivery.get(webhook=webhook)
        assert delivery.success is False
        assert delivery.response_status is None
        assert delivery.error == 'down-3'
        assert delivery.delivered_at is None


class TestDeliverySsrfRevalidation:
    async def test_delivery_blocked_when_host_resolves_private_in_production(self, db, monkeypatch):
        webhook = await Webhook.create(
            name='w', url='https://public-then-internal.example/hook',
            secret='shhh', event_types=['*'],
        )
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(0, 0, 0, '', ('169.254.169.254', 0))],
        )
        fake = WebhookFakeClient([200])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))

        assert fake.calls == []
        delivery = await WebhookDelivery.get(webhook=webhook)
        assert delivery.success is False
        assert delivery.attempt_count == 0
        assert 'SSRF' in (delivery.error or '')
        assert delivery.delivered_at is None

    async def test_delivery_connects_to_the_address_that_passed(self, db, monkeypatch):
        """httpx must not resolve the name again: connect to the checked IP and
        carry the hostname as Host and as the TLS server name."""
        webhook = await Webhook.create(
            name='w', url='https://hooks.example:8443/in?x=1',
            secret='shhh', event_types=['*'],
        )
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(0, 0, 0, '', ('93.184.216.34', 0))],
        )
        fake = WebhookFakeClient([200])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))

        [call] = fake.calls
        assert call['url'] == 'https://93.184.216.34:8443/in?x=1'
        assert call['headers']['Host'] == 'hooks.example:8443'
        assert call['extensions'] == {'sni_hostname': 'hooks.example'}

    async def test_a_rebind_between_retries_is_caught(self, db, monkeypatch, no_sleep):
        """Each retry re-checks: public on the first try, internal on the next."""
        webhook = await Webhook.create(
            name='w', url='https://rebind.example/hook', secret='shhh', event_types=['*'],
        )
        monkeypatch.setattr('application.services.webhook_service.is_production', lambda: True)
        answers = iter(['93.184.216.34', '127.0.0.1'])
        monkeypatch.setattr(
            'application.utils.ssrf.socket.getaddrinfo',
            lambda *a, **k: [(0, 0, 0, '', (next(answers), 0))],
        )
        fake = WebhookFakeClient([500, 200])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))

        assert [c['url'] for c in fake.calls] == ['https://93.184.216.34/hook']
        delivery = await WebhookDelivery.get(webhook=webhook)
        assert delivery.success is False
        assert delivery.attempt_count == 1
        assert 'SSRF' in (delivery.error or '')


class TestListWebhooks:
    async def test_list_webhooks_returns_all_for_staff(self, db):
        actor = await _staff()
        await Webhook.create(name='a', url='https://a.example', secret='s', event_types=['*'])
        await Webhook.create(name='b', url='https://b.example', secret='s', event_types=['*'])
        webhooks = await WebhookService().list_webhooks(actor)
        assert {w.name for w in webhooks} == {'a', 'b'}

    async def test_list_webhooks_requires_staff(self, db):
        actor = await _plain()
        with pytest.raises(PermissionError):
            await WebhookService().list_webhooks(actor)


class TestListDeliveries:
    async def test_list_deliveries_returns_rows(self, db):
        actor = await _staff()
        webhook = await Webhook.create(name='w', url='https://x.example', secret='s', event_types=['*'])
        await WebhookDelivery.create(
            webhook=webhook, event_type=EventType.MATCH_CREATED, payload='{}',
            response_status=200, attempt_count=1, success=True,
        )
        deliveries = await WebhookService().list_deliveries(actor, webhook.id)
        assert len(deliveries) == 1
        assert deliveries[0].event_type == EventType.MATCH_CREATED

    async def test_list_deliveries_requires_staff(self, db):
        actor = await _plain()
        webhook = await Webhook.create(name='w', url='https://x.example', secret='s', event_types=['*'])
        with pytest.raises(PermissionError):
            await WebhookService().list_deliveries(actor, webhook.id)

    async def test_list_deliveries_missing_webhook_raises(self, db):
        actor = await _staff()
        with pytest.raises(ValueError, match='not found'):
            await WebhookService().list_deliveries(actor, 424242)


@pytest.fixture
def vapid_env(monkeypatch):
    private_key, _ = generate_vapid_keys()
    monkeypatch.setenv('VAPID_PRIVATE_KEY', private_key)
    monkeypatch.setenv('VAPID_SUBJECT', 'mailto:ops@example.com')
    return private_key


@pytest.fixture
def no_vapid_env(monkeypatch):
    monkeypatch.delenv('VAPID_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('VAPID_SUBJECT', raising=False)


class PushFakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = f'status {status_code}'


class PushFakeClient:
    """Stand-in for the shared push httpx.AsyncClient; returns codes or raises."""

    def __init__(self, status_codes=None, raise_exc=None):
        self._status_codes = list(status_codes or [])
        self.raise_exc = raise_exc
        self.calls = []

    async def post(self, url, content=None, headers=None):
        self.calls.append({'url': url, 'content': content, 'headers': headers})
        if self.raise_exc is not None:
            raise self.raise_exc
        return PushFakeResponse(self._status_codes[len(self.calls) - 1])


@pytest.fixture
def fake_client(monkeypatch):
    def _install(status_codes=None, *, raise_exc=None):
        fake = PushFakeClient(status_codes, raise_exc)
        monkeypatch.setattr('application.services.web_push_service._get_http_client', lambda: fake)
        return fake
    return _install


class TestConfigResolution:
    def test_invalid_private_key_disables_and_warns(self, monkeypatch, caplog):
        monkeypatch.setenv('VAPID_PRIVATE_KEY', 'invalid-vapid-key-value-xyz')
        monkeypatch.setenv('VAPID_SUBJECT', 'mailto:ops@example.com')
        with caplog.at_level('WARNING', logger='application.services.web_push_service'):
            assert WebPushService.is_configured() is False
        assert any('Invalid VAPID_PRIVATE_KEY' in r.message for r in caplog.records)


class TestHttpClientLifecycle:
    async def test_get_reuses_then_close_resets(self):
        import application.services.web_push_service as mod

        await mod.aclose_http_client()
        client = mod._get_http_client()
        assert isinstance(client, httpx.AsyncClient)
        assert client.is_closed is False
        assert mod._get_http_client() is client

        await mod.aclose_http_client()
        assert mod._http_client is None

        fresh = mod._get_http_client()
        assert fresh is not client
        await mod.aclose_http_client()

    async def test_aclose_when_already_none_is_safe(self):
        import application.services.web_push_service as mod
        await mod.aclose_http_client()
        assert mod._http_client is None
        await mod.aclose_http_client()
        assert mod._http_client is None


class TestSubscribeValidation:
    async def test_rejects_too_long_endpoint(self, db):
        user = await _user()
        long_endpoint = 'https://push.example.net/' + ('a' * 1100)
        with pytest.raises(ValueError, match='too long'):
            await WebPushService().subscribe(user, endpoint=long_endpoint, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)

    async def test_rejects_wrong_length_p256dh(self, db):
        user = await _user()
        # 'AAAA' decodes cleanly but to 3 bytes, not the required 65.
        with pytest.raises(ValueError, match='malformed'):
            await WebPushService().subscribe(user, endpoint=ENDPOINT, p256dh='AAAA', auth=RFC_AUTH)

    async def test_rejects_wrong_length_auth(self, db):
        user = await _user()
        # Valid p256dh, but auth decodes to 3 bytes rather than 16.
        with pytest.raises(ValueError, match='malformed'):
            await WebPushService().subscribe(user, endpoint=ENDPOINT, p256dh=RFC_UA_PUBLIC, auth='AAAA')


class TestRemoveSubscription:
    async def test_remove_own_subscription_deletes_and_audits(self, db):
        owner = await _subscribed_user(discord_id=1)
        subscription = (await WebPushService().list_subscriptions(owner))[0]
        await WebPushService().remove_subscription(owner, subscription.id)
        assert await WebPushSubscription.all().count() == 0
        assert await AuditLog.filter(action='web_push.unsubscribed').count() == 1

    async def test_remove_missing_subscription_raises(self, db):
        user = await _user()
        with pytest.raises(ValueError, match='not found'):
            await WebPushService().remove_subscription(user, 999999)


class TestNotifyGating:
    async def test_send_to_subscriptions_zero_when_config_none(self, db, no_vapid_env):
        assert await WebPushService()._send_to_subscriptions([], title='t', body='b') == 0


class TestPlainText:
    def test_truncates_overlong_body(self):
        text = WebPushService()._plain_text('x' * 600)
        assert len(text) == WebPushService.MAX_BODY_LENGTH
        assert text.endswith('…')

    def test_strips_only_bold_markdown(self):
        assert WebPushService()._plain_text('**Bold** keep__me') == 'Bold keep__me'


class TestDeliveryPruneAndErrors:
    async def test_encrypt_valueerror_prunes_subscription(self, db, vapid_env, fake_client, monkeypatch):
        fake = fake_client([])
        user = await _subscribed_user(discord_id=99)

        def _boom(*args, **kwargs):
            raise ValueError('bad p256dh point')
        monkeypatch.setattr('application.services.web_push_service.protocol.encrypt_payload', _boom)

        delivered = await _deliver(user)
        assert delivered == 0
        assert fake.calls == []
        assert await WebPushSubscription.all().count() == 0

    async def test_http_error_keeps_subscription(self, db, vapid_env, fake_client):
        fake = fake_client(raise_exc=httpx.HTTPError('push service unreachable'))
        user = await _subscribed_user(discord_id=99)

        delivered = await _deliver(user)
        assert delivered == 0
        assert len(fake.calls) == 1
        assert await WebPushSubscription.all().count() == 1

    async def test_server_error_status_keeps_subscription(self, db, vapid_env, fake_client):
        fake = fake_client([500])
        user = await _subscribed_user(discord_id=99)

        delivered = await _deliver(user)
        assert delivered == 0
        assert len(fake.calls) == 1
        assert await WebPushSubscription.all().count() == 1
