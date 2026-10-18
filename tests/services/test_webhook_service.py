"""Tests for WebhookService: CRUD/auth/validation and signed delivery."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from application.events import Event, EventType
from application.services.webhook_service import WebhookService
from models import AuditLog, Role, User, UserRole, Webhook, WebhookDelivery


async def _staff() -> User:
    user = await User.create(discord_id=1, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _plain() -> User:
    return await User.create(discord_id=2, username='nobody')


# ---------------------------------------------------------------------------
# CRUD, authorization, validation
# ---------------------------------------------------------------------------


class TestWebhookCrud:
    async def test_create_generates_secret_and_audits(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='overlay', url='https://example.com/hook',
            event_types=[EventType.MATCH_CREATED],
        )
        assert webhook.id is not None
        assert webhook.secret  # generated
        stored = await Webhook.get(id=webhook.id)
        assert stored.event_types == [EventType.MATCH_CREATED]
        assert await AuditLog.filter(action='webhook.created').count() == 1

    async def test_create_rejected_for_non_staff(self, db):
        actor = await _plain()
        with pytest.raises(PermissionError):
            await WebhookService().create_webhook(
                actor, name='x', url='https://example.com', event_types=['*'],
            )

    async def test_create_rejects_bad_event_type(self, db):
        actor = await _staff()
        with pytest.raises(ValueError, match='Unknown event type'):
            await WebhookService().create_webhook(
                actor, name='x', url='https://example.com', event_types=['bogus.event'],
            )

    async def test_create_rejects_empty_event_types(self, db):
        actor = await _staff()
        with pytest.raises(ValueError, match='at least one event type'):
            await WebhookService().create_webhook(
                actor, name='x', url='https://example.com', event_types=[],
            )

    async def test_create_rejects_non_https_url(self, db):
        actor = await _staff()
        with pytest.raises(ValueError, match='https'):
            await WebhookService().create_webhook(
                actor, name='x', url='ftp://example.com', event_types=['*'],
            )

    async def test_update_changes_fields(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        updated = await WebhookService().update_webhook(
            actor, webhook.id, is_active=False, event_types=[EventType.MATCH_STARTED],
        )
        assert updated.is_active is False
        assert updated.event_types == [EventType.MATCH_STARTED]

    async def test_delete_removes_row(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        await WebhookService().delete_webhook(actor, webhook.id)
        assert await Webhook.filter(id=webhook.id).count() == 0

    async def test_regenerate_secret_changes_it(self, db):
        actor = await _staff()
        webhook = await WebhookService().create_webhook(
            actor, name='x', url='https://example.com', event_types=['*'],
        )
        old_secret = webhook.secret
        new_secret = await WebhookService().regenerate_secret(actor, webhook.id)
        assert new_secret != old_secret
        assert (await Webhook.get(id=webhook.id)).secret == new_secret

    async def test_get_missing_raises(self, db):
        actor = await _staff()
        with pytest.raises(ValueError, match='not found'):
            await WebhookService().get_webhook(actor, 9999)


# ---------------------------------------------------------------------------
# Delivery: signing, logging, retries
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeClientFactory:
    """Stand-in for httpx.AsyncClient that records posts and returns scripted codes."""

    def __init__(self, status_codes):
        self._status_codes = list(status_codes)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None, extensions=None):
        self.calls.append({'url': url, 'content': content, 'headers': headers})
        return FakeResponse(self._status_codes[len(self.calls) - 1])


@pytest.fixture
def no_sleep(monkeypatch):
    async def _noop(_seconds):
        return None
    monkeypatch.setattr('application.services.webhook_service.asyncio.sleep', _noop)


class TestDelivery:
    async def test_successful_delivery_signs_and_logs(self, db, monkeypatch):
        webhook = await Webhook.create(
            name='w', url='https://example.com/hook', secret='shhh',
            event_types=[EventType.MATCH_CREATED],
        )
        fake = FakeClientFactory([200])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        event = Event.create(EventType.MATCH_CREATED, {'match_id': 5})
        await WebhookService()._deliver_one(webhook, event)

        # One POST, HMAC over "{timestamp}.{body}" verifies against the secret.
        assert len(fake.calls) == 1
        call = fake.calls[0]
        body = call['content']
        ts = call['headers']['X-Wizzrobe-Timestamp']
        expected = hmac.new(b'shhh', f'{ts}.{body}'.encode(), hashlib.sha256).hexdigest()
        assert call['headers']['X-Wizzrobe-Signature'] == f'sha256={expected}'
        assert call['headers']['X-Wizzrobe-Event'] == EventType.MATCH_CREATED
        assert json.loads(body)['data'] == {'match_id': 5}

        delivery = await WebhookDelivery.get(webhook=webhook)
        assert delivery.success is True
        assert delivery.response_status == 200
        assert delivery.attempt_count == 1
        assert delivery.delivered_at is not None

    async def test_retries_then_gives_up_on_5xx(self, db, monkeypatch, no_sleep):
        webhook = await Webhook.create(
            name='w', url='https://example.com/hook', secret='shhh', event_types=['*'],
        )
        fake = FakeClientFactory([500, 502, 503])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)

        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))

        assert len(fake.calls) == WebhookService.MAX_ATTEMPTS
        delivery = await WebhookDelivery.get(webhook=webhook)
        assert delivery.success is False
        assert delivery.attempt_count == WebhookService.MAX_ATTEMPTS
        assert delivery.response_status == 503
        assert delivery.delivered_at is None

    async def test_deliver_event_enqueues_only_subscribed_active(self, db, monkeypatch):
        await Webhook.create(name='a', url='https://a.example', secret='s',
                             event_types=[EventType.MATCH_CREATED], is_active=True)
        await Webhook.create(name='b', url='https://b.example', secret='s',
                             event_types=[EventType.MATCH_STARTED], is_active=True)
        await Webhook.create(name='c', url='https://c.example', secret='s',
                             event_types=['*'], is_active=False)  # inactive
        await Webhook.create(name='d', url='https://d.example', secret='s',
                             event_types=['*'], is_active=True)  # wildcard

        enqueued = []
        monkeypatch.setattr(
            'application.services.webhook_service.webhook_queue.enqueue',
            lambda coro: (enqueued.append(coro), coro.close()),
        )
        await WebhookService().deliver_event(Event.create(EventType.MATCH_CREATED, {}))
        # 'a' (subscribed) and 'd' (wildcard) match; 'b' wrong event, 'c' inactive.
        assert len(enqueued) == 2


# ---------------------------------------------------------------------------
# In-app format reference (drift guard against the delivery code)
# ---------------------------------------------------------------------------


class TestFormatReference:
    def test_event_list_matches_registry(self):
        ref = WebhookService.format_reference()
        flat = [name for group in ref['events'].values() for name in group]
        assert sorted(flat) == sorted(EventType.ALL)
        assert ref['wildcard'] == EventType.WILDCARD

    def test_header_names_match_builder(self):
        ref = WebhookService.format_reference()
        documented = [h['name'] for h in ref['headers']]
        built = list(WebhookService.build_delivery_headers(
            event_type='x', delivery_id='d', timestamp='t', signature='s',
        ).keys())
        assert documented == built

    def test_example_payload_matches_wire_shape(self):
        ref = WebhookService.format_reference()
        expected_keys = set(Event.create(EventType.MATCH_CREATED, {}).to_wire().keys())
        assert set(ref['example_payload'].keys()) == expected_keys

    async def test_delivery_sends_every_documented_header(self, db, monkeypatch):
        # A real delivery must send exactly the headers the reference documents —
        # including Content-Type and User-Agent, which the prose doc once omitted.
        webhook = await Webhook.create(
            name='w', url='https://x.example', secret='s', event_types=['*'],
        )
        fake = FakeClientFactory([200])
        monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', fake)
        await WebhookService()._deliver_one(webhook, Event.create(EventType.MATCH_CREATED, {}))
        sent = fake.calls[0]['headers']
        for header in WebhookService.format_reference()['headers']:
            assert header['name'] in sent
        assert sent['Content-Type'] == 'application/json'
        assert sent['User-Agent'] == 'wizzrobe-webhook'


# ---------------------------------------------------------------------------
# Delivery health on the list
# ---------------------------------------------------------------------------


class TestRecentHealth:
    """The 24h summary the webhooks list shows beside Active.

    The list used to carry the Active toggle and nothing else, so a webhook
    whose recent deliveries all failed looked exactly like a healthy one — the
    failures were recorded, and two clicks away behind the per-row history.
    """

    async def _webhook(self, name='hook'):
        return await Webhook.create(
            name=name, url='https://example.com/h', secret='s', event_types=['*'],
        )

    async def _delivery(self, webhook, *, success: bool, age=timedelta(0)):
        row = await WebhookDelivery.create(
            webhook=webhook, event_type='match.created', payload='{}',
            response_status=200 if success else 500, attempt_count=1, success=success,
        )
        if age:
            # auto_now_add ignores a passed value, so age the row after the fact.
            await WebhookDelivery.filter(id=row.id).update(
                created_at=datetime.now(timezone.utc) - age,
            )
        return row

    async def test_counts_both_outcomes_per_webhook(self, db):
        actor = await _staff()
        a, b = await self._webhook('a'), await self._webhook('b')
        await self._delivery(a, success=True)
        await self._delivery(a, success=False)
        await self._delivery(a, success=False)
        await self._delivery(b, success=True)

        health = await WebhookService().recent_health(actor)

        assert health[a.id]['ok'] == 1
        assert health[a.id]['failed'] == 2
        assert health[b.id] == {'ok': 1, 'failed': 0, 'last': health[b.id]['last']}

    async def test_a_webhook_with_no_recent_deliveries_is_absent(self, db):
        # Absent, not zeroed: the row renders "—" rather than claiming health.
        actor = await _staff()
        quiet = await self._webhook('quiet')
        assert quiet.id not in await WebhookService().recent_health(actor)

    async def test_deliveries_outside_the_window_do_not_count(self, db):
        """A webhook that broke last month must not wear a red badge forever."""
        actor = await _staff()
        hook = await self._webhook()
        await self._delivery(hook, success=False, age=WebhookService.HEALTH_WINDOW * 2)
        assert hook.id not in await WebhookService().recent_health(actor)

    async def test_non_staff_refused(self, db):
        with pytest.raises(PermissionError):
            await WebhookService().recent_health(await _plain())


class TestHealthCell:
    """The chip the row paints, from one webhook's counts."""

    def test_no_entry_is_blank_not_a_zero(self):
        from pages.admin_tabs.admin_webhooks import _health_cell

        assert _health_cell(None) == {'health': '', 'health_chip': ''}
        assert _health_cell({'ok': 0, 'failed': 0, 'last': None})['health'] == ''

    def test_all_delivered_reads_positive(self):
        from pages.admin_tabs.admin_webhooks import _health_cell

        cell = _health_cell({'ok': 3, 'failed': 0, 'last': None})
        assert cell['health'] == '3 delivered'
        assert cell['health_chip'] == 'wiz-chip--ok'

    def test_any_failure_leads_with_the_failure_count(self):
        from pages.admin_tabs.admin_webhooks import _health_cell

        # The number the operator is looking for goes first.
        cell = _health_cell({'ok': 1, 'failed': 2, 'last': None})
        assert cell['health'] == '2 of 3 failed'
        assert cell['health_chip'] == 'wiz-chip--cancelled'
