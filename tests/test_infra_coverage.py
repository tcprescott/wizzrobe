"""Coverage for the in-process infra: dispatch worker, match_live, bus, error handlers.

These modules are pure plumbing (no DB), so most tests here run without the ``db``
fixture. The dispatch-queue tests drive the real async worker deterministically with
``asyncio`` primitives (Events, ``Queue.join``) rather than sleeps.
"""

import asyncio
import logging
import types
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from application.events import Event, EventType, dispatch_queue, match_live
from application.events import bus as event_bus
from middleware.error_handlers import (
    _current_user_best_effort,
    log_unhandled_error,
    register_error_handlers,
)


class TestDispatchQueue:
    @pytest.fixture(autouse=True)
    async def _reset(self):
        """Give every test a fresh, loop-local queue and a stopped worker."""
        dispatch_queue._queue = asyncio.Queue()
        dispatch_queue._worker_task = None
        yield
        task = dispatch_queue._worker_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        dispatch_queue._worker_task = None
        while not dispatch_queue._queue.empty():
            leftover = dispatch_queue._queue.get_nowait()
            if asyncio.iscoroutine(leftover):
                leftover.close()

    async def test_enqueue_puts_coroutine_on_the_queue(self):
        async def noop():
            return None

        coro = noop()
        dispatch_queue.enqueue(coro)
        assert dispatch_queue._queue.qsize() == 1
        # drain + close so it is never left un-awaited
        queued = dispatch_queue._queue.get_nowait()
        assert queued is coro
        queued.close()

    async def test_worker_runs_enqueued_coroutines(self):
        ran = asyncio.Event()

        async def job():
            ran.set()

        dispatch_queue.start()
        assert dispatch_queue._worker_task is not None
        dispatch_queue.enqueue(job())
        await asyncio.wait_for(ran.wait(), timeout=1)
        await dispatch_queue.stop()
        assert dispatch_queue._worker_task is None

    async def test_failing_subscriber_does_not_kill_worker(self, caplog):
        """A raising coroutine is logged and swallowed; later work still runs."""
        results = []

        async def boom():
            raise RuntimeError('bad subscriber')

        async def ok():
            results.append('ok')

        with caplog.at_level(logging.ERROR, logger='application.events.dispatch_queue'):
            dispatch_queue.start()
            dispatch_queue.enqueue(boom())
            dispatch_queue.enqueue(ok())
            await asyncio.wait_for(dispatch_queue._queue.join(), timeout=1)

        assert results == ['ok']
        assert any('event dispatch worker error' in r.getMessage() for r in caplog.records)
        await dispatch_queue.stop()

    async def test_stop_is_noop_when_never_started(self):
        dispatch_queue._worker_task = None
        await dispatch_queue.stop()
        assert dispatch_queue._worker_task is None

    async def test_stop_warns_when_items_remain_queued(self, caplog):
        """Stopping with a backlog logs a warning and drops the queued work."""

        async def idle():
            await asyncio.sleep(60)

        dispatch_queue._worker_task = asyncio.get_running_loop().create_task(idle())

        leftover = []
        for _ in range(3):
            async def job():
                return None

            coro = job()
            leftover.append(coro)
            dispatch_queue.enqueue(coro)

        with caplog.at_level(logging.WARNING, logger='application.events.dispatch_queue'):
            await dispatch_queue.stop()

        assert dispatch_queue._worker_task is None
        assert any('still queued' in r.getMessage() for r in caplog.records)
        for coro in leftover:
            coro.close()

    async def test_stop_after_drain_does_not_warn(self, caplog):
        ran = asyncio.Event()

        async def job():
            ran.set()

        dispatch_queue.start()
        dispatch_queue.enqueue(job())
        await asyncio.wait_for(ran.wait(), timeout=1)
        await asyncio.wait_for(dispatch_queue._queue.join(), timeout=1)

        with caplog.at_level(logging.WARNING, logger='application.events.dispatch_queue'):
            await dispatch_queue.stop()

        assert not any('still queued' in r.getMessage() for r in caplog.records)


class TestMatchEvents:
    @pytest.fixture(autouse=True)
    def _clean_subscribers(self):
        match_live._subscribers.clear()
        yield
        match_live._subscribers.clear()

    def test_subscribe_delivers_to_callback(self):
        seen = []
        match_live.subscribe(lambda mid, ct: seen.append((mid, ct)))
        match_live.publish(5, match_live.CREATED)
        assert seen == [(5, 'created')]

    def test_publish_defaults_to_changed(self):
        seen = []
        match_live.subscribe(lambda mid, ct: seen.append((mid, ct)))
        match_live.publish(9)
        assert seen == [(9, match_live.CHANGED)]

    def test_unsubscribe_stops_delivery(self):
        seen = []
        token = match_live.subscribe(lambda mid, ct: seen.append(mid))
        match_live.unsubscribe(token)
        match_live.publish(1, match_live.DELETED)
        assert seen == []

    def test_unsubscribe_unknown_token_is_noop(self):
        match_live.unsubscribe(9999)

    def test_multiple_subscribers_each_notified(self):
        a, b = [], []
        match_live.subscribe(lambda mid, ct: a.append(mid))
        match_live.subscribe(lambda mid, ct: b.append(mid))
        match_live.publish(7)
        assert a == [7] and b == [7]

    def test_raising_subscriber_is_isolated(self):
        seen = []

        def boom(_mid, _ct):
            raise RuntimeError('bad match subscriber')

        match_live.subscribe(boom)
        match_live.subscribe(lambda mid, ct: seen.append(mid))
        match_live.publish(3)
        assert seen == [3]


class TestEventBusEnqueueFailure:
    @pytest.fixture(autouse=True)
    def _clean_bus(self):
        event_bus._sync_subscribers.clear()
        event_bus._async_subscribers.clear()
        yield
        event_bus._sync_subscribers.clear()
        event_bus._async_subscribers.clear()

    def test_enqueue_failure_is_swallowed(self, monkeypatch, caplog):
        """If the dispatch worker rejects the coroutine, publish still returns."""

        def raising_enqueue(_coro):
            raise RuntimeError('queue is down')

        monkeypatch.setattr('application.events.dispatch_queue.enqueue', raising_enqueue)
        # A plain (non-coroutine) return, so a rejected enqueue leaves no un-awaited coroutine.
        event_bus.subscribe_async(lambda _e: 'sentinel')

        with caplog.at_level(logging.ERROR, logger='application.events.bus'):
            event_bus.publish(Event.create(EventType.MATCH_CREATED, {}))

        assert any('failed to enqueue' in r.getMessage() for r in caplog.records)

    def test_async_subscriber_not_matching_is_skipped(self, monkeypatch):
        captured = []
        monkeypatch.setattr('application.events.dispatch_queue.enqueue', captured.append)
        event_bus.subscribe_async(lambda _e: None, event_types=[EventType.MATCH_STARTED])
        event_bus.publish(Event.create(EventType.MATCH_CREATED, {}))
        assert captured == []


class _FakeStorage:
    def __init__(self):
        self.user = {}


class _FakeApp:
    """Stands in for the NiceGUI ``app`` singleton: records registered handlers."""

    def __init__(self):
        self.storage = _FakeStorage()
        self.captured = {}

    def exception_handler(self, code):
        def deco(fn):
            self.captured[code] = fn
            return fn

        return deco

    def on_page_exception(self, fn):
        self.captured['page'] = fn
        return fn


class TestCurrentUserBestEffort:
    async def test_returns_none_when_storage_unavailable(self):
        assert await _current_user_best_effort() is None

    async def test_returns_user_on_success(self, monkeypatch):
        fake_app = _FakeApp()
        fake_app.storage.user = {'discord_id': 7}
        monkeypatch.setattr('middleware.error_handlers.app', fake_app)

        sentinel = object()

        async def fake_get(storage):
            assert storage == {'discord_id': 7}
            return sentinel

        monkeypatch.setattr(
            'application.services.auth_service.get_user_from_session', fake_get
        )
        assert await _current_user_best_effort() is sentinel


class TestRegisteredHandlers:
    @pytest.fixture
    def handlers(self, monkeypatch):
        fake_app = _FakeApp()
        monkeypatch.setattr('middleware.error_handlers.app', fake_app)
        register_error_handlers(FastAPI())
        assert 404 in fake_app.captured
        assert 'page' in fake_app.captured
        return fake_app

    async def test_not_found_returns_json_for_api_route(self, handlers):
        not_found = handlers.captured[404]

        def some_endpoint():
            return None

        scope = {
            'type': 'http',
            'method': 'GET',
            'path': '/api/thing',
            'headers': [],
            'endpoint': some_endpoint,
        }
        exc = StarletteHTTPException(status_code=404, detail='Not Found')
        response = await not_found(Request(scope), exc)

        assert response.status_code == 404
        assert b'Not Found' in bytes(response.body)

    async def test_not_found_renders_themed_page_for_ui_route(self, monkeypatch, handlers):
        not_found = handlers.captured[404]

        calls = {}

        class FakeClient:
            def __init__(self, page, request=None):
                calls['page'] = page
                calls['request'] = request

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def build_response(self, request, status):
                calls['status'] = status
                return Response(status_code=status)

        monkeypatch.setattr('middleware.error_handlers.Client', FakeClient)
        monkeypatch.setattr(
            'middleware.error_handlers.ui', types.SimpleNamespace(page=lambda arg: object())
        )
        monkeypatch.setattr(
            'middleware.error_handlers.render_error_page',
            lambda **kw: calls.__setitem__('render', kw),
        )

        async def fake_get(storage):
            return None

        monkeypatch.setattr(
            'application.services.auth_service.get_user_from_session', fake_get
        )

        scope = {'type': 'http', 'method': 'GET', 'path': '/missing', 'headers': []}
        exc = StarletteHTTPException(status_code=404)
        response = await not_found(Request(scope), exc)

        assert response.status_code == 404
        assert calls['status'] == 404
        assert calls['render']['status_code'] == 404
        assert calls['render']['headline'] == 'Page not found'

    def test_page_exception_handler_dev_shows_traceback(self, monkeypatch, handlers):
        page_handler = handlers.captured['page']

        monkeypatch.setattr('middleware.error_handlers.sentry_sdk', MagicMock())
        monkeypatch.setattr('middleware.error_handlers.is_production', lambda: False)
        rendered = {}
        monkeypatch.setattr(
            'middleware.error_handlers.render_error_page',
            lambda **kw: rendered.update(kw),
        )

        try:
            raise RuntimeError('kaboom in a page')
        except RuntimeError as exc:
            page_handler(exc)

        assert rendered['status_code'] == 500
        assert rendered['error_id']
        assert rendered['traceback_text'] is not None
        assert 'RuntimeError' in rendered['traceback_text']
        assert 'kaboom in a page' in rendered['traceback_text']

    def test_page_exception_handler_production_hides_traceback(self, monkeypatch, handlers):
        page_handler = handlers.captured['page']

        monkeypatch.setattr('middleware.error_handlers.sentry_sdk', MagicMock())
        monkeypatch.setattr('middleware.error_handlers.is_production', lambda: True)
        rendered = {}
        monkeypatch.setattr(
            'middleware.error_handlers.render_error_page',
            lambda **kw: rendered.update(kw),
        )

        page_handler(RuntimeError('boom'))

        assert rendered['status_code'] == 500
        assert rendered['error_id']
        assert rendered['traceback_text'] is None


class TestLogUnhandledErrorExtra:
    def test_sentry_failure_is_swallowed(self, monkeypatch):
        sentry = MagicMock()
        sentry.set_tag.side_effect = RuntimeError('sentry offline')
        monkeypatch.setattr('middleware.error_handlers.sentry_sdk', sentry)
        error_id = log_unhandled_error(RuntimeError('x'), '/p')
        assert isinstance(error_id, str) and len(error_id) == 36
