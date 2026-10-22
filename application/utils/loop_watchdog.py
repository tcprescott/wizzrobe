"""Detect a blocked event loop and report the code that blocked it.

Every page, socket, worker and bot handler shares one asyncio loop
(docs/scaling-roadmap.md), so one synchronous call that runs too long freezes
the app for everyone, and nothing logs it: the request that did it finishes
normally, and the ones it starved just look slow. Most UI work arrives over the
NiceGUI websocket, which Sentry tracing does not see at all.

A daemon thread posts a heartbeat onto the loop every :data:`_BEAT_SECONDS`.
When one is late by more than :data:`WARN_SECONDS` the thread samples the loop
thread's stack *while it is still blocked*, which is what names the culprit,
and logs the stall when it ends: WARNING (Sentry Logs) for a short one, ERROR
(a Sentry issue, grouped by the blocking frame) from :data:`ERROR_SECONDS`. A
stall still going after :data:`STUCK_SECONDS` is reported straight away, since
it may never end.

A stall that was mostly a garbage-collection pass is named as one
(:mod:`application.utils.gc_monitor`): the sampled stack then only shows the
allocation that happened to trigger the collector, so it would blame the
wrong line.

The thread also keeps a minute of heartbeat lag and process CPU for
:func:`snapshot`, which the super-admin ``runtime_stats`` MCP tool reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from types import FrameType
from typing import Deque, List, Optional, Tuple

import sentry_sdk

from application.utils import gc_monitor

logger = logging.getLogger(__name__)

WARN_SECONDS = 0.5
ERROR_SECONDS = 2.0
# A stall is the collector's when at least this share of it was a collection.
GC_SHARE = 0.5
GC_CULPRIT = 'garbage collection'
STUCK_SECONDS = 10.0
_BEAT_SECONDS = 0.25
_WINDOW_SECONDS = 60.0
_STACK_FRAMES = 25

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_THIS_FILE = os.path.abspath(__file__)


@dataclass
class Stall:
    started_at: float
    duration: float
    culprit: str
    stack: str
    gc_seconds: float = 0.0


@dataclass
class _Beat:
    sent: float
    seen: Optional[float] = None


def _is_app_frame(filename: str) -> bool:
    path = os.path.abspath(filename)
    return (
        path.startswith(_PROJECT_ROOT)
        and path != _THIS_FILE
        and 'site-packages' not in path
        and f'{os.sep}.venv{os.sep}' not in path
    )


def _describe(frame: Optional[FrameType]) -> Tuple[str, str]:
    """The innermost app frame (the culprit) and a formatted stack."""
    if frame is None:
        return 'unknown', ''
    summary = traceback.extract_stack(frame, limit=_STACK_FRAMES)
    culprit = 'library code'
    for entry in reversed(summary):
        if _is_app_frame(entry.filename):
            rel = os.path.relpath(entry.filename, _PROJECT_ROOT)
            culprit = f'{rel}:{entry.lineno} in {entry.name}'
            break
    return culprit, ''.join(traceback.format_list(summary))


class LoopWatchdog:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread_id: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lags: Deque[Tuple[float, float]] = deque()
        self._cpu: Deque[Tuple[float, float]] = deque()
        self._stalls: Deque[Stall] = deque(maxlen=20)
        self._stall_count = 0
        self.started_at: Optional[float] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        if self.running:
            return
        self._loop = loop or asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name='loop-watchdog', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        assert self._loop is not None
        while not self._stop.is_set():
            beat = _Beat(sent=time.monotonic())
            try:
                self._loop.call_soon_threadsafe(self._ack, beat)
            except RuntimeError:
                return
            self._wait_for(beat)
            self._stop.wait(_BEAT_SECONDS)

    @staticmethod
    def _ack(beat: _Beat) -> None:
        beat.seen = time.monotonic()

    def _wait_for(self, beat: _Beat) -> None:
        culprit, stack = '', ''
        reported_stuck = False
        while beat.seen is None and not self._stop.is_set():
            late = time.monotonic() - beat.sent
            if late >= WARN_SECONDS and not stack:
                culprit, stack = self._sample_loop_stack()
            if late >= STUCK_SECONDS and not reported_stuck:
                reported_stuck = True
                gc_started = gc_monitor.running_since()
                if gc_started is not None and gc_started <= beat.sent + late * (1 - GC_SHARE):
                    self._report(late, f'{GC_CULPRIT} (in progress)', stack, still_blocked=True)
                else:
                    self._report(late, culprit, stack, still_blocked=True)
            self._stop.wait(0.02)
        if beat.seen is None:
            return
        lag = beat.seen - beat.sent
        self._record(beat.seen, lag)
        if lag >= WARN_SECONDS:
            if not stack:
                culprit = 'unknown (stack not sampled)'
            gc_seconds, generation = gc_monitor.seconds_between(beat.sent, beat.seen)
            if gc_seconds >= lag * GC_SHARE:
                stack = f'Sampled mid-collection, at the allocation that set it off:\n{stack}'
                culprit = f'{GC_CULPRIT} (gen {generation}, {gc_seconds:.2f}s)'
            stall = Stall(
                started_at=time.time() - lag, duration=lag, culprit=culprit, stack=stack,
                gc_seconds=gc_seconds,
            )
            with self._lock:
                self._stalls.append(stall)
                self._stall_count += 1
            if not reported_stuck:
                self._report(lag, culprit, stack, still_blocked=False)

    def _sample_loop_stack(self) -> Tuple[str, str]:
        if self._loop_thread_id is None:
            return 'unknown', ''
        return _describe(sys._current_frames().get(self._loop_thread_id))

    def _record(self, now: float, lag: float) -> None:
        cpu = time.process_time()
        with self._lock:
            self._lags.append((now, lag))
            self._cpu.append((now, cpu))
            while self._lags and now - self._lags[0][0] > _WINDOW_SECONDS:
                self._lags.popleft()
            while self._cpu and now - self._cpu[0][0] > _WINDOW_SECONDS:
                self._cpu.popleft()

    def _report(self, seconds: float, culprit: str, stack: str, *, still_blocked: bool) -> None:
        verb = 'has been blocked for' if still_blocked else 'was blocked for'
        level = logging.ERROR if seconds >= ERROR_SECONDS else logging.WARNING
        with sentry_sdk.new_scope() as scope:
            # Every collection pause is one issue, whatever generation or length.
            group = GC_CULPRIT if culprit.startswith(GC_CULPRIT) else culprit
            scope.fingerprint = ['event-loop-blocked', group]
            scope.set_tag('loop_blocked_in', culprit[:200])
            logger.log(
                level, 'Event loop %s %.2fs in %s\n%s', verb, seconds, culprit, stack,
            )

    def snapshot(self) -> dict:
        with self._lock:
            lags = sorted(lag for _, lag in self._lags)
            cpu = list(self._cpu)
            stalls: List[Stall] = list(self._stalls)
            stall_count = self._stall_count
        cpu_percent: Optional[float] = None
        if len(cpu) >= 2 and cpu[-1][0] > cpu[0][0]:
            cpu_percent = round(100 * (cpu[-1][1] - cpu[0][1]) / (cpu[-1][0] - cpu[0][0]), 1)

        def pct(p: float) -> Optional[float]:
            if not lags:
                return None
            return round(lags[min(len(lags) - 1, int(p * len(lags)))] * 1000, 1)

        return {
            'running': self.running,
            'lag_ms_p50': pct(0.5),
            'lag_ms_p95': pct(0.95),
            'lag_ms_max': round(lags[-1] * 1000, 1) if lags else None,
            'cpu_percent': cpu_percent,
            'stalls_since_start': stall_count,
            'recent_stalls': [
                {
                    'started_at': s.started_at,
                    'seconds': round(s.duration, 2),
                    'culprit': s.culprit,
                    'gc_seconds': round(s.gc_seconds, 2),
                }
                for s in reversed(stalls)
            ],
        }


watchdog = LoopWatchdog()
