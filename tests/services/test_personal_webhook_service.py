"""Tests for PersonalWebhookService: who can create, what each owner is told, delivery."""

import hashlib
import hmac
import json
from typing import ClassVar

import pytest

from application.events import Event, EventType
from application.services import personal_webhook_service as pws
from application.services.personal_webhook_service import (
    MAX_PER_USER,
    PersonalWebhookService,
    is_discord_url,
    masked_url,
)
from application.tenant_context import tenant_scope
from models import (
    AuditLog,
    Commentator,
    FeatureFlag,
    Match,
    MatchPlayers,
    PersonalWebhook,
    PersonalWebhookOutbox,
    TenantFeatureFlag,
    TenantMembership,
    User,
    VolunteerPosition,
    VolunteerShift,
)
from tests.factories import make_member, make_user
from tests.services._match_service_setup import make_tournament

DISCORD = 'https://discord.com/api/webhooks/123456789/abcdefTOKEN'
JSON_URL = 'https://example.com/hook'


@pytest.fixture(autouse=True)
def _reset_limits():
    pws.reset_rate_limits()
    yield
    pws.reset_rate_limits()


async def _member(discord_id: int, name: str) -> User:
    return await make_member(discord_id=discord_id, username=name, display_name=name)


async def _match(*players: User, **fields) -> Match:
    tournament = await make_tournament(name='ALTTPR Main')
    match = await Match.create(tournament=tournament, comment='staff only note', **fields)
    for p in players:
        await MatchPlayers.create(match=match, user=p)
    return match


async def _hook(owner: User, events, *, only_mine=True, url=JSON_URL) -> PersonalWebhook:
    webhook, _ = await PersonalWebhookService().create(
        owner, name='Mine', url=url, event_types=list(events), only_mine=only_mine,
    )
    return await PersonalWebhook.get(id=webhook.id).prefetch_related('user')


def _event(event_type, **payload) -> Event:
    return Event.create(event_type, payload)


class TestCreate:
    async def test_member_creates_json_webhook_and_gets_secret_once(self, db):
        alice = await _member(1, 'alice')
        webhook, secret = await PersonalWebhookService().create(
            alice, name='Overlay', url=JSON_URL, event_types=[EventType.MATCH_STARTED],
        )
        assert secret and secret == (await PersonalWebhook.get(id=webhook.id)).secret
        assert webhook.only_mine is True
        assert await AuditLog.filter(action='personal_webhook.created').count() == 1

    async def test_discord_webhook_has_no_secret_to_show(self, db):
        alice = await _member(1, 'alice')
        _, secret = await PersonalWebhookService().create(
            alice, name='Pings', url=DISCORD, event_types=[EventType.MATCH_STARTED],
        )
        assert secret is None

    async def test_non_member_refused(self, db):
        outsider = await make_user(5, 'outsider')
        with pytest.raises(PermissionError):
            await PersonalWebhookService().create(
                outsider, name='x', url=JSON_URL, event_types=[EventType.MATCH_STARTED],
            )

    async def test_cap_per_member(self, db):
        alice = await _member(1, 'alice')
        for i in range(MAX_PER_USER):
            await PersonalWebhookService().create(
                alice, name=f'h{i}', url=JSON_URL, event_types=[EventType.MATCH_STARTED],
            )
        with pytest.raises(ValueError, match='Delete one'):
            await PersonalWebhookService().create(
                alice, name='one more', url=JSON_URL, event_types=[EventType.MATCH_STARTED],
            )

    @pytest.mark.parametrize('url, message', [
        ('ftp://example.com/x', 'https'),
        ('https://user:pw@example.com/x', 'username'),
        ('https://discord.com/channels/1/2', 'Discord webhook'),
    ])
    async def test_bad_urls(self, db, url, message):
        alice = await _member(1, 'alice')
        with pytest.raises(ValueError, match=message):
            await PersonalWebhookService().create(
                alice, name='x', url=url, event_types=[EventType.MATCH_STARTED],
            )

    async def test_staff_only_events_are_not_subscribable(self, db):
        alice = await _member(1, 'alice')
        with pytest.raises(ValueError, match='at least one'):
            await PersonalWebhookService().create(
                alice, name='x', url=JSON_URL,
                event_types=[EventType.MATCH_COMMENT_ADDED, EventType.MATCH_FLAGGED_FOR_REVIEW],
            )

    async def test_feature_off_refuses(self, db):
        alice = await _member(1, 'alice')
        await TenantFeatureFlag.filter(flag=FeatureFlag.PERSONAL_WEBHOOKS.value).update(enabled=False)
        with pytest.raises(ValueError):
            await PersonalWebhookService().list_mine(alice)

    async def test_cannot_touch_someone_elses(self, db):
        alice = await _member(1, 'alice')
        bob = await _member(2, 'bob')
        webhook = await _hook(alice, [EventType.MATCH_STARTED])
        with pytest.raises(ValueError):
            await PersonalWebhookService().delete(bob, webhook.id)
        assert await PersonalWebhookService().list_mine(bob) == []


class TestVisibility:
    async def test_own_match_is_sent_with_allowlisted_fields_only(self, db):
        alice = await _member(1, 'alice')
        bob = await _member(2, 'bob')
        match = await _match(alice, bob)
        webhook = await _hook(alice, [EventType.MATCH_STARTED])

        view = await PersonalWebhookService().view_for(webhook, _event(
            EventType.MATCH_STARTED, match_id=match.id, secret_staff_field='x',
        ))

        assert view is not None and view.about_me
        body = json.dumps(view.data)
        assert 'staff only note' not in body
        assert 'secret_staff_field' not in body
        assert [p['name'] for p in view.data['match']['players']] == ['alice', 'bob']

    async def test_someone_elses_match_needs_only_mine_off(self, db):
        alice = await _member(1, 'alice')
        bob = await _member(2, 'bob')
        carol = await _member(3, 'carol')
        match = await _match(bob, carol)
        event = _event(EventType.MATCH_STARTED, match_id=match.id)

        mine = await _hook(alice, [EventType.MATCH_STARTED], only_mine=True)
        everything = await _hook(alice, [EventType.MATCH_STARTED], only_mine=False)

        assert await PersonalWebhookService().view_for(mine, event) is None
        view = await PersonalWebhookService().view_for(everything, event)
        assert view is not None and view.about_me is False

    async def test_approved_crew_counts_as_about_me(self, db):
        alice = await _member(1, 'alice')
        bob = await _member(2, 'bob')
        match = await _match(bob)
        await Commentator.create(match=match, user=alice, approved=True)
        webhook = await _hook(alice, [EventType.MATCH_STARTED])
        view = await PersonalWebhookService().view_for(
            webhook, _event(EventType.MATCH_STARTED, match_id=match.id),
        )
        assert view is not None and view.about_me

    async def test_mine_only_event_about_someone_else_never_sent(self, db):
        alice = await _member(1, 'alice')
        bob = await _member(2, 'bob')
        match = await _match(alice)
        webhook = await _hook(alice, [EventType.CREW_APPROVAL_CHANGED], only_mine=False)
        event = _event(
            EventType.CREW_APPROVAL_CHANGED, match_id=match.id, user_id=bob.id,
            crew_type='commentator', approved=True,
        )
        assert await PersonalWebhookService().view_for(webhook, event) is None

    async def test_own_crew_approval(self, db):
        alice = await _member(1, 'alice')
        match = await _match(await _member(2, 'bob'))
        webhook = await _hook(alice, [EventType.CREW_APPROVAL_CHANGED])
        view = await PersonalWebhookService().view_for(webhook, _event(
            EventType.CREW_APPROVAL_CHANGED, match_id=match.id, user_id=alice.id,
            crew_type='commentator', approved=True,
        ))
        assert view is not None
        assert view.data['crew'] == {'role': 'commentator', 'approved': True}

    async def test_owner_who_left_hears_nothing(self, db):
        alice = await _member(1, 'alice')
        match = await _match(alice)
        webhook = await _hook(alice, [EventType.MATCH_STARTED])
        await TenantMembership.filter(user_id=alice.id).delete()
        assert await PersonalWebhookService().view_for(
            webhook, _event(EventType.MATCH_STARTED, match_id=match.id),
        ) is None

    async def test_match_in_another_community_is_invisible(self, two_tenants):
        _, tenant_b = two_tenants
        alice = await _member(1, 'alice')
        webhook = await _hook(alice, [EventType.MATCH_STARTED], only_mine=False)
        with tenant_scope(tenant_b.id):
            other = await _match(await make_user(9, 'stranger'))
        assert await PersonalWebhookService().view_for(
            webhook, _event(EventType.MATCH_STARTED, match_id=other.id),
        ) is None

    async def test_own_volunteer_shift(self, db):
        from datetime import datetime, timedelta, timezone

        alice = await _member(1, 'alice')
        position = await VolunteerPosition.create(name='Proctor')
        start = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
        shift = await VolunteerShift.create(position=position, starts_at=start, ends_at=start + timedelta(hours=2))
        webhook = await _hook(alice, [EventType.VOLUNTEER_ASSIGNED])
        view = await PersonalWebhookService().view_for(webhook, _event(
            EventType.VOLUNTEER_ASSIGNED, assignment_id=1, shift_id=shift.id, user_id=alice.id,
        ))
        assert view is not None and view.data['shift']['position'] == 'Proctor'
        assert '<t:' in view.lines[1]


class _FakeClient:
    sent: ClassVar[list] = []
    status = 204

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, *, content, headers, **_kwargs):
        _FakeClient.sent.append({'url': url, 'body': content, 'headers': headers})

        class _Response:
            status_code = _FakeClient.status
        return _Response()


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.sent = []
    _FakeClient.status = 204
    monkeypatch.setattr(pws.httpx, 'AsyncClient', _FakeClient)

    async def _no_sleep(*_a):
        return None
    monkeypatch.setattr(pws.asyncio, 'sleep', _no_sleep)
    return _FakeClient


class TestDelivery:
    async def test_discord_body_never_pings(self, db, fake_http):
        alice = await _member(1, 'alice')
        evil = await _member(2, '@everyone')
        match = await _match(alice, evil)
        webhook = await _hook(alice, [EventType.MATCH_STARTED], url=DISCORD)
        event = _event(EventType.MATCH_STARTED, match_id=match.id)
        view = await PersonalWebhookService().view_for(webhook, event)

        await PersonalWebhookService()._deliver(webhook, event.event_type, view, event.occurred_at)

        body = json.loads(fake_http.sent[0]['body'])
        assert body['allowed_mentions'] == {'parse': []}
        assert body['embeds'][0]['title'].startswith('Started')
        stored = await PersonalWebhook.get(id=webhook.id)
        assert stored.delivered_count == 1 and stored.consecutive_failures == 0

    async def test_json_body_is_signed(self, db, fake_http):
        alice = await _member(1, 'alice')
        match = await _match(alice)
        webhook = await _hook(alice, [EventType.MATCH_STARTED])
        event = _event(EventType.MATCH_STARTED, match_id=match.id)
        view = await PersonalWebhookService().view_for(webhook, event)

        await PersonalWebhookService()._deliver(webhook, event.event_type, view, event.occurred_at)

        sent = fake_http.sent[0]
        payload = json.loads(sent['body'])
        assert payload['event_type'] == EventType.MATCH_STARTED and payload['about_me'] is True
        timestamp = sent['headers']['X-Wizzrobe-Timestamp']
        expected = hmac.new(webhook.secret.encode(), f"{timestamp}.{sent['body']}".encode(), hashlib.sha256)
        assert sent['headers']['X-Wizzrobe-Signature'] == f'sha256={expected.hexdigest()}'

    async def test_deleted_discord_webhook_pauses(self, db, fake_http):
        fake_http.status = 404
        alice = await _member(1, 'alice')
        match = await _match(alice)
        webhook = await _hook(alice, [EventType.MATCH_STARTED], url=DISCORD)
        event = _event(EventType.MATCH_STARTED, match_id=match.id)
        view = await PersonalWebhookService().view_for(webhook, event)

        await PersonalWebhookService()._deliver(webhook, event.event_type, view, event.occurred_at)

        stored = await PersonalWebhook.get(id=webhook.id)
        assert stored.is_active is False and 'no longer exists' in stored.paused_reason
        assert len(fake_http.sent) == 1

    async def test_deliver_event_skips_when_feature_off(self, db, monkeypatch):
        alice = await _member(1, 'alice')
        match = await _match(alice)
        await _hook(alice, [EventType.MATCH_STARTED])
        queued = []
        monkeypatch.setattr(pws.webhook_queue, 'enqueue', lambda coro: queued.append(coro.close()))
        await TenantFeatureFlag.filter(flag=FeatureFlag.PERSONAL_WEBHOOKS.value).update(enabled=False)
        await PersonalWebhookService().deliver_event(_event(EventType.MATCH_STARTED, match_id=match.id))
        assert queued == []

    async def test_deliver_event_queues_for_subscribers_only(self, db, monkeypatch):
        alice = await _member(1, 'alice')
        bob = await _member(2, 'bob')
        match = await _match(alice)
        await _hook(alice, [EventType.MATCH_STARTED])
        await _hook(bob, [EventType.MATCH_STARTED])
        await _hook(alice, [EventType.MATCH_FINISHED])
        queued = []
        monkeypatch.setattr(pws.webhook_queue, 'enqueue', lambda coro: queued.append(coro.close()))
        await PersonalWebhookService().deliver_event(_event(EventType.MATCH_STARTED, match_id=match.id))
        assert len(queued) == 1


class TestOutbox:
    async def test_a_message_is_saved_before_it_is_sent_and_removed_after(self, db, fake_http, monkeypatch):
        alice = await _member(1, 'alice')
        match = await _match(alice)
        await _hook(alice, [EventType.MATCH_STARTED])
        queued = []
        monkeypatch.setattr(pws.webhook_queue, 'enqueue', queued.append)
        await PersonalWebhookService().deliver_event(_event(EventType.MATCH_STARTED, match_id=match.id))

        row = await PersonalWebhookOutbox.get()
        assert row.view['data']['match']['id'] == match.id and not fake_http.sent
        await queued.pop()
        assert fake_http.sent[0]['headers']['X-Wizzrobe-Delivery'] == row.delivery_uuid
        assert not await PersonalWebhookOutbox.exists()

    async def test_the_sweeper_sends_what_a_restart_left_and_drops_paused(self, db, fake_http, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from application.services import webhook_outbox_worker

        alice = await _member(1, 'alice')
        match = await _match(alice)
        live = await _hook(alice, [EventType.MATCH_STARTED])
        paused = await _hook(alice, [EventType.MATCH_STARTED])
        monkeypatch.setattr(pws.webhook_queue, 'enqueue', lambda coro: coro.close())
        await PersonalWebhookService().deliver_event(_event(EventType.MATCH_STARTED, match_id=match.id))
        await PersonalWebhook.filter(id=paused.id).update(is_active=False)
        await PersonalWebhookOutbox.all().update(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))

        await webhook_outbox_worker._tick()

        assert len(fake_http.sent) == 1
        assert (await PersonalWebhook.get(id=live.id)).delivered_count == 1
        assert not await PersonalWebhookOutbox.exists()


def test_url_helpers():
    assert is_discord_url(DISCORD)
    assert not is_discord_url('https://evil.example/api/webhooks/1/2')
    assert 'abcdefTOKEN' not in masked_url(DISCORD)
