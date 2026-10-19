"""Frozen wire contract for the webhooks an outside automation consumes.

sgl26's "SG Schedule Update" webhook posts to SpeedGaming, whose automation was
written against what these six events looked like on 2026-10-07. A red test here
means a change would reach that automation. Don't update the expected values to
make it pass: call the change out to Thomas in the PR so the author can be told
first. Adding a new event type is fine; renaming, dropping or reshaping one of
these, the envelope, the headers or the signature is not.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from application.events import Event, EventType, event_bus
from application.services.crew_service import CrewService
from application.services.match.match_service import MatchService
from application.services.webhook_service import WebhookService
from models import Commentator, Stage
from tests.services._match_service_setup import make_player, make_staff, make_tournament

pytestmark = pytest.mark.usefixtures("stub_discord_queue")

CONSUMED_EVENTS = {
    'match.rescheduled': EventType.MATCH_RESCHEDULED,
    'match.stage_assigned': EventType.MATCH_STAGE_ASSIGNED,
    'match.stage_cleared': EventType.MATCH_STAGE_CLEARED,
    'match.deleted': EventType.MATCH_DELETED,
    'match.cancelled': EventType.MATCH_CANCELLED,
    'crew.approval_changed': EventType.CREW_APPROVAL_CHANGED,
}


@pytest.fixture
async def service():
    return MatchService()


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


async def _match(service, actor, **kwargs):
    t = await make_tournament()
    alice = await make_player("alice", "Alice")
    bob = await make_player("bob", "Bob")
    return await service.create_match(
        tournament_id=t.id, scheduled_date="2025-01-15", scheduled_time="14:30",
        player_ids=[alice.id, bob.id], actor=actor, **kwargs,
    )


def _only(captured_events, event_type):
    matching = [e for e in captured_events if e.event_type == event_type]
    assert len(matching) == 1, f"expected one {event_type}, got {len(matching)}"
    return matching[0]


def test_event_names_unchanged():
    for wire_name, constant in CONSUMED_EVENTS.items():
        assert constant == wire_name
    assert set(CONSUMED_EVENTS) <= set(EventType.ALL)


def test_envelope_unchanged():
    event = Event(
        event_type='match.deleted', payload={'match_id': 1}, actor_id=2,
        actor_username='staff', tenant_id=3,
        occurred_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )
    assert event.to_wire() == {
        'event_type': 'match.deleted',
        'occurred_at': '2026-10-07T00:00:00+00:00',
        'tenant_id': 3,
        'actor_id': 2,
        'actor_username': 'staff',
        'data': {'match_id': 1},
    }


def test_headers_and_signature_unchanged():
    headers = WebhookService.build_delivery_headers(
        event_type='match.deleted', delivery_id='d', timestamp='1700000000', signature='abc',
    )
    assert headers == {
        'Content-Type': 'application/json',
        'User-Agent': 'wizzrobe-webhook',
        'X-Wizzrobe-Event': 'match.deleted',
        'X-Wizzrobe-Delivery': 'd',
        'X-Wizzrobe-Timestamp': '1700000000',
        'X-Wizzrobe-Signature': 'sha256=abc',
    }
    assert WebhookService.MAX_ATTEMPTS == 3
    assert WebhookService.DELIVERY_TIMEOUT == 10.0


async def test_delivered_body_is_signed_over_timestamp_dot_body(monkeypatch):
    sent = {}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, _url, *, content, headers, **_kwargs):
            sent['body'], sent['headers'] = content, headers

            class _Response:
                status_code = 204
            return _Response()

    monkeypatch.setattr('application.services.webhook_service.httpx.AsyncClient', _Client)
    svc = object.__new__(WebhookService)

    class _Deliveries:
        async def create(self, **_kwargs):
            return None
    svc.delivery_repository = _Deliveries()

    webhook = type('W', (), {'id': 1, 'url': 'http://localhost/hook', 'secret': 'shh', 'tenant_id': 1})()
    event = Event(
        event_type='match.deleted', payload={'match_id': 1, 'tournament_id': 2},
        tenant_id=1, occurred_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )
    await svc._deliver_one(webhook, event)

    body = sent['body']
    assert body == json.dumps(event.to_wire(), default=str, sort_keys=True)
    timestamp = sent['headers']['X-Wizzrobe-Timestamp']
    expected = hmac.new(b'shh', f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    assert sent['headers']['X-Wizzrobe-Signature'] == f'sha256={expected}'


async def test_match_rescheduled_payload(service, db, captured_events):
    actor = await make_staff()
    match = await _match(service, actor)

    await service.update_match(
        match_id=match.id, scheduled_date="2025-01-16", scheduled_time="15:00", actor=actor,
    )

    event = _only(captured_events, EventType.MATCH_RESCHEDULED)
    assert event.payload == {
        'match_id': match.id, 'tournament_id': match.tournament_id,
        'changed_fields': ['scheduled_at'],
    }


async def test_match_stage_assigned_and_cleared_payloads(service, db, captured_events):
    actor = await make_staff()
    match = await _match(service, actor)
    stage = await Stage.create(name="Stage A")

    await service.assign_stage(match.id, stage.id, actor=actor)
    await service.assign_stage(match.id, None, actor=actor)

    assert _only(captured_events, EventType.MATCH_STAGE_ASSIGNED).payload == {
        'match_id': match.id, 'stage_id': stage.id, 'tournament_id': match.tournament_id,
    }
    assert _only(captured_events, EventType.MATCH_STAGE_CLEARED).payload == {
        'match_id': match.id, 'stage_id': None, 'tournament_id': match.tournament_id,
    }


async def test_match_deleted_payload(service, db, captured_events):
    actor = await make_staff()
    match = await _match(service, actor)

    await service.delete_match(match.id, actor=actor)

    assert _only(captured_events, EventType.MATCH_DELETED).payload == {
        'match_id': match.id, 'tournament_id': match.tournament_id,
    }


async def test_match_cancelled_payload(service, db, captured_events):
    actor = await make_staff()
    match = await _match(service, actor)

    await service.cancel_match(match.id, actor=actor, reason='called off')

    assert _only(captured_events, EventType.MATCH_CANCELLED).payload == {
        'match_id': match.id, 'tournament_id': match.tournament_id, 'reason': 'called off',
    }


async def test_crew_approval_changed_payload(service, db, captured_events):
    actor = await make_staff()
    match = await _match(service, actor)
    volunteer = await make_player("comm", "Comm")
    crew = await Commentator.create(match=match, user=volunteer, approved=False)

    await CrewService().update_crew_approval(crew, 'commentator', approved=True, actor=actor)

    assert _only(captured_events, EventType.CREW_APPROVAL_CHANGED).payload == {
        'match_id': match.id, 'crew_type': 'commentator',
        'user_id': volunteer.id, 'approved': True,
    }
