"""Tests for the Sentry Metrics wiring.

Covers the on/off switch, the sampler's mapping of a ``runtime_stats`` snapshot
to gauges, the request middleware's route labels, and worker tick timings.
"""

import asyncio
from collections import deque
from typing import Any, ClassVar, Dict, List, Tuple

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from application.utils import gc_monitor, metrics_sampler, runtime_stats, sentry_metrics
from application.utils.background_loop import BackgroundLoop
from application.utils.coroutine_queue import CoroutineQueue, queue_depths
from middleware.request_metrics import RequestMetricsMiddleware


class _Recorder:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, float, Any, Any]] = []

    def _record(self, kind):
        def fn(name, value, unit=None, attributes=None):
            self.calls.append((kind, name, value, unit, attributes))
        return fn

    def __getattr__(self, kind):
        if kind in ('gauge', 'count', 'distribution'):
            return self._record(kind)
        raise AttributeError(kind)

    def named(self, name):
        return [c for c in self.calls if c[1] == name]


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(sentry_metrics, '_metrics', rec)
    monkeypatch.setattr(sentry_metrics, '_enabled', True)
    return rec


class TestSwitch:
    def test_disabled_sends_nothing(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(sentry_metrics, '_metrics', rec)
        monkeypatch.setattr(sentry_metrics, '_enabled', False)
        sentry_metrics.gauge('x', 1)
        sentry_metrics.count('x')
        sentry_metrics.distribution('x', 1)
        assert rec.calls == []

    @pytest.mark.parametrize('raw,expected', [('', True), ('on', True), ('off', False), ('false', False)])
    def test_env_switch(self, monkeypatch, raw, expected):
        monkeypatch.setenv('SENTRY_METRICS', raw)
        assert sentry_metrics.metrics_requested() is expected

    def test_none_values_are_skipped(self, recorder):
        sentry_metrics.gauge('x', None)
        sentry_metrics.distribution('x', None)
        assert recorder.calls == []

    def test_sdk_failure_is_swallowed(self, monkeypatch):
        class Boom:
            def gauge(self, *a, **k):
                raise RuntimeError('nope')
        monkeypatch.setattr(sentry_metrics, '_metrics', Boom())
        monkeypatch.setattr(sentry_metrics, '_enabled', True)
        sentry_metrics.gauge('x', 1)


class TestSampler:
    SNAPSHOT: ClassVar[Dict[str, Any]] = {
        'rss_mb': 512.5,
        'asyncio_tasks': 140,
        'event_loop': {'cpu_percent': 37.0, 'lag_ms_p50': 1.0, 'lag_ms_p95': 9.0,
                       'lag_ms_max': 40.0, 'stalls_since_start': 3},
        'clients': {'total': 80, 'connected': 72},
        'http_in_flight': 4,
        'db_pool': {'size': 10, 'idle': 7, 'max': 20},
        'queues': {'discord': 5},
        'watch_pages_by_tenant_id': {'1': {'live_viewers': 30, 'static_readers': 0,
                                           'arrivals_last_minute': 12}},
    }

    def test_maps_snapshot_to_gauges(self, recorder, monkeypatch):
        monkeypatch.setattr(metrics_sampler, '_last_stalls', None)
        metrics_sampler.emit(self.SNAPSHOT)
        values = {c[1]: c[2] for c in recorder.calls if c[0] == 'gauge' and not c[4]}
        assert values['process.cpu'] == 37.0
        assert values['process.memory_rss'] == 512.5
        assert values['loop.lag_p95'] == 9.0
        assert values['clients.connected'] == 72
        assert values['http.in_flight'] == 4
        assert values['db_pool.in_use'] == 3
        assert recorder.named('queue.depth') == [('gauge', 'queue.depth', 5, None, {'queue': 'discord'})]
        assert recorder.named('watch.live_viewers')[0][4] == {'tenant_id': '1'}
        assert recorder.named('loop.stalls') == []

    def test_stalls_count_the_delta(self, recorder, monkeypatch):
        monkeypatch.setattr(metrics_sampler, '_last_stalls', 1)
        metrics_sampler.emit(self.SNAPSHOT)
        assert [c[2] for c in recorder.named('loop.stalls')] == [2]

    def test_gc_pauses_and_seconds(self, recorder, monkeypatch):
        monkeypatch.setattr(metrics_sampler, '_last_gc_pass', 10.0)
        monkeypatch.setattr(metrics_sampler, '_last_gc_seconds', {'2': 1.0})
        monkeypatch.setattr(gc_monitor, '_passes', deque([
            gc_monitor.Pass(5.0, 0.4, 2, 0),
            gc_monitor.Pass(12.0, 2.5, 2, 900),
        ]))
        snapshot = {'gc': {'by_generation': {'0': {'total_seconds': 0.2}, '2': {'total_seconds': 3.6}}}}
        metrics_sampler.emit(snapshot)
        assert recorder.named('gc.pause') == [('distribution', 'gc.pause', 2.5, 'second', {'generation': '2'})]
        assert recorder.named('gc.seconds') == [('count', 'gc.seconds', 2.6, 'second', {'generation': '2'})]
        assert metrics_sampler._last_gc_pass == 12.0

        recorder.calls.clear()
        metrics_sampler.emit(snapshot)
        assert recorder.named('gc.pause') == []
        assert recorder.named('gc.seconds') == []

    async def test_real_snapshot_emits(self, recorder):
        metrics_sampler.emit(runtime_stats.collect())
        assert recorder.named('process.asyncio_tasks')


class TestRequestMiddleware:
    @staticmethod
    def _app():
        app = FastAPI()

        @app.get('/t/{slug}/thing')
        async def thing(slug: str):
            return {'slug': slug}

        @app.get('/_nicegui/asset')
        async def asset():
            return {}

        app.add_middleware(RequestMetricsMiddleware)
        return app

    async def test_labels_by_route_template(self, recorder):
        async with AsyncClient(transport=ASGITransport(app=self._app()), base_url='http://t') as c:
            await c.get('/t/sgl26/thing')
            await c.get('/nope')
        labels = [c[4] for c in recorder.named('http.requests')]
        assert {'route': '/t/{slug}/thing', 'method': 'GET', 'status': '2xx'} in labels
        assert {'route': 'unmatched', 'method': 'GET', 'status': '4xx'} in labels
        assert recorder.named('http.request_duration')[0][3] == 'millisecond'
        assert runtime_stats.collect()['http_in_flight'] == 0

    async def test_skips_nicegui_transport(self, recorder):
        async with AsyncClient(transport=ASGITransport(app=self._app()), base_url='http://t') as c:
            await c.get('/_nicegui/asset')
        assert recorder.calls == []


class TestWorkerAndQueue:
    async def test_tick_duration_and_failure(self, recorder):
        calls = 0

        async def tick():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('tick failed')

        loop = BackgroundLoop(tick, interval=0, name='probe')
        loop.start()
        for _ in range(50):
            if calls >= 2:
                break
            await asyncio.sleep(0.01)
        await loop.stop()
        durations = [c for c in recorder.named('worker.tick_duration') if c[4] == {'worker': 'probe'}]
        assert len(durations) >= 2
        assert recorder.named('worker.tick_failures')[0][4] == {'worker': 'probe'}

    def test_queue_depths_lists_live_queues(self):
        q = CoroutineQueue('probe-queue')

        async def noop():
            pass

        coro = noop()
        q.enqueue(coro)
        assert queue_depths()['probe-queue'] == 1
        q.discard_pending()


class TestOnlineUsers:
    def test_online_users_gauges(self, recorder):
        metrics_sampler.emit({'online_users': {'total': 3, 'by_community': {'sgl26': 2, 'alttpr': 1}}})
        assert [c[2] for c in recorder.named('users.online')] == [3]
        by = {c[4]['community']: c[2] for c in recorder.named('users.online_by_community')}
        assert by == {'sgl26': 2, 'alttpr': 1}

    def test_counts_distinct_signed_in_people_with_a_live_tab(self, monkeypatch):
        from types import SimpleNamespace

        from nicegui import Client, app

        def tab(session, path, connected=True):
            request = SimpleNamespace(session={'id': session}, url=SimpleNamespace(path=path))
            return SimpleNamespace(has_socket_connection=connected, request=request)

        monkeypatch.setattr(Client, 'instances', {
            'a': tab('s1', '/t/sgl26/home'),
            'b': tab('s1', '/t/alttpr/home'),
            'c': tab('s2', '/t/sgl26/admin'),
            'd': tab('s3', '/t/sgl26/home'),
            'e': tab('s4', '/t/sgl26/home', connected=False),
            'f': tab('s5', '/platform'),
        })
        monkeypatch.setattr(app.storage, '_users', {
            's1': {'user_id': 1}, 's2': {'discord_id': '22'}, 's3': {},
            's4': {'user_id': 4}, 's5': {'user_id': 5},
        })
        assert runtime_stats._online_users() == {
            'total': 3, 'by_community': {'alttpr': 1, 'sgl26': 2},
        }
