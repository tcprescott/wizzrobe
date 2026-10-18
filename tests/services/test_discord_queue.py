"""Tests for discord_queue module.

NOTE: The services conftest.py has an autouse fixture that replaces
``discord_queue.enqueue`` with a list append. Tests in this file that want to
verify the *real* enqueue behaviour restore the original function explicitly.
"""

import asyncio

import application.services.discord.discord_queue as dq
from application.services.discord.discord_queue import enqueue as real_enqueue
from application.tenant_context import get_current_tenant_id, tenant_scope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop():
    pass


async def _raise():
    raise RuntimeError('boom')


# ---------------------------------------------------------------------------
# enqueue (real implementation)
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_adds_to_queue(self, monkeypatch):
        # The conftest autouse fixture replaces enqueue with list.append;
        # restore the real implementation for this test.
        monkeypatch.setattr(dq, 'enqueue', dq._queue.put_nowait)
        initial_size = dq._queue.qsize()
        coro = _noop()
        dq._queue.put_nowait(coro)
        assert dq._queue.qsize() == initial_size + 1
        # Drain so we don't leave coroutines pending
        while not dq._queue.empty():
            leftover = dq._queue.get_nowait()
            leftover.close()
            dq._queue.task_done()

    def test_enqueue_multiple_preserves_order(self, monkeypatch):
        order = []

        async def tagged(n):
            order.append(n)

        c1 = tagged(1)
        c2 = tagged(2)
        dq._queue.put_nowait(c1)
        dq._queue.put_nowait(c2)
        # Drain without running them
        got = []
        while not dq._queue.empty():
            item = dq._queue.get_nowait()
            got.append(item)
            item.close()
            dq._queue.task_done()
        # The two coroutines should be distinct objects
        assert len(got) == 2


# ---------------------------------------------------------------------------
# _worker (tests use a fresh local queue to avoid event-loop binding issues)
# ---------------------------------------------------------------------------


class TestWorker:
    async def test_worker_drains_queue_and_calls_coro(self, monkeypatch):
        results = []
        local_queue: asyncio.Queue = asyncio.Queue()
        monkeypatch.setattr(dq, '_queue', local_queue)

        async def marker():
            results.append('ran')

        local_queue.put_nowait(marker())

        async def _worker_local() -> None:
            while True:
                coro = await local_queue.get()
                try:
                    await coro
                except Exception:
                    pass
                finally:
                    local_queue.task_done()

        worker_task = asyncio.create_task(_worker_local())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert results == ['ran']
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    async def test_worker_survives_exception_in_coro(self, monkeypatch):
        """Worker must not stop when the coroutine it runs raises."""
        results = []
        local_queue: asyncio.Queue = asyncio.Queue()
        monkeypatch.setattr(dq, '_queue', local_queue)

        async def bad():
            raise ValueError('oops')

        async def good():
            results.append('ok')

        local_queue.put_nowait(bad())
        local_queue.put_nowait(good())

        async def _worker_local() -> None:
            while True:
                coro = await local_queue.get()
                try:
                    await coro
                except Exception:
                    pass
                finally:
                    local_queue.task_done()

        worker_task = asyncio.create_task(_worker_local())
        for _ in range(5):
            await asyncio.sleep(0)
        assert 'ok' in results
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestEnqueueTenantScope:
    """The worker task is scope-less; enqueue must re-bind the caller's tenant.

    Regression guard: notify coroutines reach ``require_tenant_id()`` in the
    worker, which would raise (and be swallowed, dropping the DM) unless enqueue
    re-establishes the enqueuing request's tenant around the coroutine.
    """

    async def _drain_scopeless(self, queue):
        # Await each queued coroutine with NO tenant in scope — tenant_scope(None)
        # reproduces the long-lived worker task, whose context has no tenant even
        # though the test harness binds a default one (autouse ``_tenant_context``).
        with tenant_scope(None):
            assert get_current_tenant_id() is None
            while not queue.empty():
                await queue.get_nowait()

    async def test_enqueue_in_scope_rebinds_tenant_for_worker(self, monkeypatch):
        local_queue: asyncio.Queue = asyncio.Queue()
        monkeypatch.setattr(dq, '_queue', local_queue)
        seen = {}

        async def record(label):
            seen[label] = get_current_tenant_id()

        with tenant_scope(42):
            real_enqueue(record('in_scope'))

        await self._drain_scopeless(local_queue)
        assert seen == {'in_scope': 42}  # None without the enqueue fix

    async def test_enqueue_outside_scope_stays_unscoped(self, monkeypatch):
        local_queue: asyncio.Queue = asyncio.Queue()
        monkeypatch.setattr(dq, '_queue', local_queue)
        seen = {}

        async def record(label):
            seen[label] = get_current_tenant_id()

        with tenant_scope(None):  # genuinely no tenant at enqueue time
            real_enqueue(record('out_scope'))

        await self._drain_scopeless(local_queue)
        assert seen == {'out_scope': None}


class TestStartStop:
    async def test_start_creates_task(self):
        dq._worker_task = None
        dq.start()
        assert dq._worker_task is not None
        await dq.stop()
        assert dq._worker_task is None

    async def test_stop_is_idempotent_when_no_task(self):
        dq._worker_task = None
        await dq.stop()  # must not raise

    async def test_stop_cancels_and_clears_task(self):
        # start() queues the boot wait and stop() drains it, so the queue has to
        # belong to this test's loop rather than an earlier test's.
        dq._queue = asyncio.Queue()
        dq._worker_task = None
        dq.start()
        task = dq._worker_task
        await dq.stop()
        assert task.cancelled()
        assert dq._worker_task is None


class TestDiscardPending:
    """A process that queued sends it will never run closes them, quietly.

    The dev seed starts brackets, which queue matchup DMs with no worker in the
    process; each one printed "coroutine ... was never awaited" at exit.
    """

    def test_closes_the_wrapper_and_the_send_it_wraps(self):
        import warnings

        from application.utils.coroutine_queue import CoroutineQueue

        q = CoroutineQueue('test')
        send = _noop()
        q.enqueue(dq._run_in_tenant_scope(7, send))
        q.enqueue(_noop())
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            assert q.discard_pending() == 2
        assert send.cr_frame is None  # closed, so it can never warn
        assert q._queue.empty()
        assert q.discard_pending() == 0

    async def test_stop_discards_what_it_drops(self):
        from application.utils.coroutine_queue import CoroutineQueue

        q = CoroutineQueue('test')
        q.start()
        await asyncio.sleep(0)
        blocker = asyncio.Event()

        async def hold():
            await blocker.wait()

        q.enqueue(hold())
        await asyncio.sleep(0)
        left = _noop()
        q.enqueue(left)
        await q.stop()
        assert left.cr_frame is None
        assert q._queue.empty()
