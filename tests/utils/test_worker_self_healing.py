"""Workers and queues survive a hung or cancelled job, restart if they die, and
the watchdog reports one that has gone quiet."""

import asyncio
import logging

import pytest

from application.utils import background_loop, coroutine_queue, worker_watchdog
from application.utils.background_loop import BackgroundLoop
from application.utils.coroutine_queue import CoroutineQueue


async def _until(predicate, seconds: float = 2.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError('condition not reached')


@pytest.fixture
def fast_restarts(monkeypatch):
    monkeypatch.setattr(background_loop, '_RESTART_FIRST_SECONDS', 0.01)
    monkeypatch.setattr(background_loop, '_RESTART_MAX_SECONDS', 0.01)


class TestBackgroundLoop:
    async def test_a_hung_tick_is_cancelled_and_the_loop_carries_on(self, caplog):
        calls = []

        async def tick():
            calls.append(1)
            if len(calls) == 1:
                await asyncio.Event().wait()

        loop = BackgroundLoop(tick, interval=0.01, name='zz hung worker', tick_timeout=0.05)
        loop.start()
        try:
            await _until(lambda: len(calls) >= 3)
        finally:
            await loop.stop()
        stats = loop.stats()
        assert stats['timeouts'] == 1
        assert stats['failures'] == 1
        assert 'zz hung worker tick ran past' in caplog.text

    async def test_a_cancellation_leaking_out_of_a_tick_does_not_end_the_loop(self, caplog):
        calls = []

        async def tick():
            calls.append(1)
            if len(calls) == 1:
                raise asyncio.CancelledError()

        loop = BackgroundLoop(tick, interval=0.01, name='zz cancelled worker')
        loop.start()
        try:
            await _until(lambda: len(calls) >= 3)
            assert loop.stats()['running'] is True
        finally:
            await loop.stop()
        assert loop.stats()['failures'] == 1
        assert loop.stats()['restarts'] == 0
        assert 'cancelled from inside' in caplog.text

    async def test_a_loop_killed_from_outside_restarts(self, fast_restarts, caplog):
        calls = []

        async def tick():
            calls.append(1)

        loop = BackgroundLoop(tick, interval=0.01, name='zz killed worker')
        loop.start()
        try:
            await _until(lambda: len(calls) >= 1)
            loop._task.cancel()
            seen = len(calls)
            await _until(lambda: len(calls) > seen + 1)
        finally:
            await loop.stop()
        assert loop.stats()['restarts'] == 1
        assert 'zz killed worker loop stopped unexpectedly' in caplog.text

    async def test_stop_is_not_a_crash(self, caplog):
        loop = BackgroundLoop(lambda: asyncio.sleep(0), interval=0.01, name='zz stopped worker')
        loop.start()
        await asyncio.sleep(0.03)
        await loop.stop()
        await asyncio.sleep(0.03)
        assert loop.stats()['restarts'] == 0
        assert loop.stats()['enabled'] is False
        assert 'stopped unexpectedly' not in caplog.text

    async def test_stall_reason(self):
        loop = BackgroundLoop(lambda: asyncio.sleep(0), interval=60, name='zz stall worker', tick_timeout=100)
        assert loop.stall_reason() is None
        loop.start()
        try:
            await _until(lambda: loop._last_finished is not None)
            finished = loop._last_finished
            assert loop.stall_reason(finished + 100) is None
            assert 'no tick for' in loop.stall_reason(finished + 60 + background_loop.STALL_GRACE_SECONDS + 1)
            loop._tick_began = finished
            assert loop.stall_reason(finished + 150) is None
            assert 'did not stop' in loop.stall_reason(finished + 100 + background_loop.STALL_GRACE_SECONDS + 1)
        finally:
            loop._tick_began = None
            await loop.stop()


class TestCoroutineQueue:
    async def test_a_hung_item_is_cancelled_and_the_queue_moves_on(self, monkeypatch, caplog):
        monkeypatch.setattr(coroutine_queue, 'ITEM_TIMEOUT_SECONDS', 0.05)
        q = CoroutineQueue('zz hung queue')
        done = []

        async def hang():
            await asyncio.Event().wait()

        async def ok():
            done.append(1)

        q.start()
        try:
            q.enqueue(hang())
            q.enqueue(ok())
            await _until(lambda: done)
        finally:
            await q.stop()
        assert q.stats()['timeouts'] == 1
        assert 'ran past' in caplog.text

    async def test_a_cancellation_leaking_out_of_an_item_does_not_end_the_worker(self, caplog):
        q = CoroutineQueue('zz cancelled queue')
        done = []

        async def leak():
            raise asyncio.CancelledError()

        async def ok():
            done.append(1)

        q.start()
        try:
            q.enqueue(leak())
            q.enqueue(ok())
            await _until(lambda: done)
            assert q.stats()['running'] is True
        finally:
            await q.stop()
        assert q.stats()['restarts'] == 0
        assert 'cancelled from inside' in caplog.text

    async def test_a_worker_killed_from_outside_restarts(self, fast_restarts):
        q = CoroutineQueue('zz killed queue')
        done = []

        async def ok():
            done.append(1)

        q.start()
        try:
            await asyncio.sleep(0.01)
            q._worker_task.cancel()
            await asyncio.sleep(0.05)
            q.enqueue(ok())
            await _until(lambda: done)
        finally:
            await q.stop()
        assert q.stats()['restarts'] == 1


class TestWatchdog:
    async def test_reports_a_stall_once_and_then_the_recovery(self, caplog):
        caplog.set_level(logging.WARNING)
        loop = BackgroundLoop(lambda: asyncio.sleep(0), interval=60, name='zz watched worker')
        loop.start()
        try:
            await _until(lambda: loop._last_finished is not None)
            late = loop._last_finished + 60 + background_loop.STALL_GRACE_SECONDS + 1
            assert 'zz watched worker' in worker_watchdog.check(late)
            worker_watchdog.check(late)
            assert caplog.text.count('Background worker zz watched worker is stalled') == 1
            assert 'zz watched worker' not in worker_watchdog.check(loop._last_finished)
            assert 'Background worker zz watched worker is running again' in caplog.text
        finally:
            await loop.stop()
            await worker_watchdog.stop()
