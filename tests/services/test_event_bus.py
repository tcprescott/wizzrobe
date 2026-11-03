"""Tests for the in-process event bus (application/events)."""

import pytest

from application.events import Event, EventType
from application.events import bus as event_bus


@pytest.fixture(autouse=True)
def _clean_bus():
    """Isolate the module-global subscriber registries per test."""
    event_bus._sync_subscribers.clear()
    event_bus._async_subscribers.clear()
    yield
    event_bus._sync_subscribers.clear()
    event_bus._async_subscribers.clear()


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Capture coroutines handed to the dispatch worker without running them."""
    captured = []
    monkeypatch.setattr('application.events.dispatch_queue.enqueue', captured.append)
    yield captured
    for coro in captured:
        coro.close()


class TestSyncSubscribers:
    def test_receives_published_event_inline(self):
        seen = []
        event_bus.subscribe_sync(seen.append)
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {'match_id': 1}))
        assert len(seen) == 1
        assert seen[0].event_type == EventType.MATCH_CREATED
        assert seen[0].payload == {'match_id': 1}

    def test_event_type_filter_excludes_others(self):
        seen = []
        event_bus.subscribe_sync(seen.append, event_types=[EventType.MATCH_STARTED])
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {}))
        assert seen == []
        event_bus.publish(Event.create(EventType.MATCH_STARTED, {}))
        assert len(seen) == 1

    def test_unsubscribe_stops_delivery(self):
        seen = []
        token = event_bus.subscribe_sync(seen.append)
        event_bus.unsubscribe(token)
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {}))
        assert seen == []

    def test_raising_subscriber_is_isolated(self):
        seen = []

        def boom(_event):
            raise RuntimeError("bad subscriber")

        event_bus.subscribe_sync(boom)
        event_bus.subscribe_sync(seen.append)
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {}))
        assert len(seen) == 1


class TestAsyncSubscribers:
    def test_matching_event_enqueues_coroutine(self, stub_dispatch):
        async def handler(_event):
            return None

        event_bus.subscribe_async(handler, event_types=[EventType.MATCH_CREATED])
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {'match_id': 7}))
        assert len(stub_dispatch) == 1

    def test_non_matching_event_not_enqueued(self, stub_dispatch):
        async def handler(_event):
            return None

        event_bus.subscribe_async(handler, event_types=[EventType.MATCH_STARTED])
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {}))
        assert stub_dispatch == []


class TestEventType:
    def test_is_valid(self):
        assert EventType.is_valid(EventType.MATCH_CREATED)
        assert EventType.is_valid('*')
        assert not EventType.is_valid('not.a.real.event')

    def test_all_contains_only_namespaced_names(self):
        assert EventType.MATCH_CREATED in EventType.ALL
        assert all('.' in name for name in EventType.ALL)


class TestEvent:
    def test_to_wire_shape(self):
        event = Event.create(EventType.MATCH_CREATED, {'match_id': 3})
        wire = event.to_wire()
        assert wire['event_type'] == EventType.MATCH_CREATED
        assert wire['data'] == {'match_id': 3}
        assert 'occurred_at' in wire

    def test_create_snapshots_actor(self):
        class FakeUser:
            id = 42
            username = 'alice'

        event = Event.create(EventType.MATCH_CREATED, {}, FakeUser())
        assert event.actor_id == 42
        assert event.actor_username == 'alice'
