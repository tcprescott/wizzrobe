"""Tests for the event-loop watchdog and the runtime snapshot built on it."""

import asyncio
import gc
import logging
import time

import pytest

from application.utils import gc_monitor, runtime_stats
from application.utils import loop_watchdog as wd
from application.utils.background_loop import BackgroundLoop, worker_stats


def _block_the_loop(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pass


@pytest.fixture
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(wd, 'WARN_SECONDS', 0.1)
    monkeypatch.setattr(wd, 'ERROR_SECONDS', 0.3)
    monkeypatch.setattr(wd, '_BEAT_SECONDS', 0.02)
    dog = wd.LoopWatchdog()
    yield dog
    dog.stop()


async def _settle(dog: wd.LoopWatchdog, stalls: int) -> None:
    for _ in range(100):
        if dog.snapshot()['stalls_since_start'] >= stalls:
            return
        await asyncio.sleep(0.02)


async def _blocked_record(caplog) -> logging.LogRecord:
    """The watchdog counts a stall before it logs it, from its own thread, so
    the record can land a moment after ``_settle`` returns."""
    for _ in range(100):
        record = next((r for r in caplog.records if 'Event loop was blocked' in r.getMessage()), None)
        if record is not None:
            return record
        await asyncio.sleep(0.02)
    raise AssertionError('the watchdog never logged the block')


class TestLoopWatchdog:
    async def test_a_quiet_loop_records_lag_but_no_stall(self, fast_watchdog):
        fast_watchdog.start()
        await asyncio.sleep(0.2)
        snap = fast_watchdog.snapshot()
        assert snap['running']
        assert snap['lag_ms_p95'] is not None
        assert snap['stalls_since_start'] == 0

    async def test_a_block_names_the_function_that_did_it(self, fast_watchdog, caplog):
        fast_watchdog.start()
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.WARNING, logger=wd.__name__):
            _block_the_loop(0.2)
            await _settle(fast_watchdog, 1)
            record = await _blocked_record(caplog)
        stall = fast_watchdog.snapshot()['recent_stalls'][0]
        assert '_block_the_loop' in stall['culprit']
        assert 'tests/utils/test_loop_watchdog.py' in stall['culprit']
        assert record.levelno == logging.WARNING

    async def test_a_long_block_is_an_error(self, fast_watchdog, caplog):
        fast_watchdog.start()
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.WARNING, logger=wd.__name__):
            _block_the_loop(0.4)
            await _settle(fast_watchdog, 1)
            record = await _blocked_record(caplog)
        assert record.levelno == logging.ERROR

    async def test_start_is_idempotent_and_stop_ends_the_thread(self, fast_watchdog):
        fast_watchdog.start()
        thread = fast_watchdog._thread
        fast_watchdog.start()
        assert fast_watchdog._thread is thread
        fast_watchdog.stop()
        assert not fast_watchdog.running


class TestWorkerStats:
    async def test_ticks_are_timed_and_failures_counted(self):
        calls = []

        async def tick():
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('boom')

        loop = BackgroundLoop(tick, interval=0.01, name='zz test worker')
        loop.start()
        for _ in range(100):
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.01)
        await loop.stop()
        stats = next(s for s in worker_stats() if s['name'] == 'zz test worker')
        assert stats['ticks'] >= 3
        assert stats['failures'] == 1
        assert stats['last_tick_seconds'] is not None
        assert stats['running'] is False


class TestRuntimeSnapshot:
    async def test_collect_never_raises_and_has_every_section(self):
        snap = runtime_stats.collect()
        for key in ('uptime_seconds', 'rss_mb', 'clients', 'db_pool', 'event_loop', 'workers'):
            assert key in snap
        assert snap['asyncio_tasks'] >= 1


@pytest.fixture
def gc_watch():
    gc_monitor.install()
    yield
    gc_monitor.uninstall()


def _fake_collection(seconds: float, generation: int = 2) -> None:
    """A collection as the watchdog sees one: the loop thread stuck between the
    collector's start and stop callbacks."""
    gc_monitor._callback('start', {'generation': generation})
    _block_the_loop(seconds)
    gc_monitor._callback('stop', {'generation': generation, 'collected': 7, 'uncollectable': 0})


class TestGarbageCollectionStalls:
    async def test_a_collection_pause_is_named_as_one_not_as_the_line_it_interrupted(
        self, fast_watchdog, gc_watch, caplog,
    ):
        fast_watchdog.start()
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.WARNING, logger=wd.__name__):
            _fake_collection(0.4)
            await _settle(fast_watchdog, 1)
            record = await _blocked_record(caplog)
        stall = fast_watchdog.snapshot()['recent_stalls'][0]
        assert stall['culprit'].startswith('garbage collection (gen 2, ')
        assert stall['gc_seconds'] >= 0.3
        assert 'garbage collection' in record.getMessage()
        assert record.levelno == logging.ERROR

    async def test_ordinary_slow_code_keeps_its_own_culprit(self, fast_watchdog, gc_watch):
        fast_watchdog.start()
        await asyncio.sleep(0.05)
        _block_the_loop(0.2)
        await _settle(fast_watchdog, 1)
        stall = fast_watchdog.snapshot()['recent_stalls'][0]
        assert '_block_the_loop' in stall['culprit']
        assert stall['gc_seconds'] < 0.1

    def test_real_collections_are_timed_by_generation(self, gc_watch):
        before = gc_monitor.snapshot()['by_generation']['2']['passes']
        gc.collect()
        snap = gc_monitor.snapshot()
        assert snap['installed']
        assert snap['by_generation']['2']['passes'] == before + 1
        assert len(snap['thresholds']) == 3

    def test_overlap_counts_only_collection_time_inside_the_window(self):
        with gc_monitor._lock:
            gc_monitor._passes.append(gc_monitor.Pass(started=100.0, seconds=2.0, generation=2, collected=0))
        try:
            assert gc_monitor.seconds_between(101.0, 105.0) == (1.0, 2)
            assert gc_monitor.seconds_between(103.0, 105.0) == (0.0, -1)
        finally:
            with gc_monitor._lock:
                gc_monitor._passes.pop()

    async def test_runtime_stats_has_a_gc_section(self):
        assert 'by_generation' in runtime_stats.collect()['gc']

    def test_freeze_moves_live_objects_out_of_collection(self):
        try:
            frozen = gc_monitor.freeze()
            assert frozen > 0
            assert gc.get_freeze_count() == frozen
            assert gc_monitor.snapshot()['frozen_objects'] == frozen
        finally:
            gc.unfreeze()

    async def test_the_mcp_tool_schema_keeps_the_gc_block(self, gc_watch):
        from mcpserver.schemas import RuntimeStats

        gc.collect()
        stats = RuntimeStats.model_validate(runtime_stats.collect())
        assert stats.gc.installed
        assert stats.gc.by_generation['2']['passes'] >= 1
