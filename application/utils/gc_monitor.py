"""Time CPython's garbage collector, so a collection pause is named as one.

A collection stops the one event loop for as long as it runs, and it runs on
whichever allocation happens to tip a generation over its threshold. The loop
watchdog samples the loop thread's stack mid-stall, so without this it blames
that innocent allocation (a label's constructor, a dataclass ``__init__``)
instead of the collector (SGLMAN-20, SGLMAN-22).

:func:`install` hooks ``gc.callbacks``. The watchdog asks
:func:`seconds_between` how much of a stall was collection, and
:func:`snapshot` feeds the ``gc`` block of ``runtime_stats``.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

_RECENT = 200
_KEEP_SECONDS = 0.01


@dataclass
class Pass:
    started: float
    seconds: float
    generation: int
    collected: int


# RLock: a collection can start inside a section that already holds it, on the same thread.
_lock = threading.RLock()
_passes: Deque[Pass] = deque(maxlen=_RECENT)
_totals: Dict[int, List[float]] = {0: [0, 0.0, 0.0], 1: [0, 0.0, 0.0], 2: [0, 0.0, 0.0]}
_started: Optional[float] = None
_frozen: Optional[int] = None

# Don't raise: hourly thaws let production RSS climb 560 -> 907 MB and the thaw pass take 3.9s.
THAW_SECONDS = 600.0
_thaw_seconds = THAW_SECONDS
_refreeze_loop: Optional[asyncio.AbstractEventLoop] = None
_last_thaw = 0.0
_refreezes = 0
_thaws = 0


def _callback(phase: str, info: Dict[str, int]) -> None:
    global _started
    now = time.monotonic()
    if phase == 'start':
        _started = now
        return
    started, _started = _started, None
    if started is None:
        return
    seconds = now - started
    generation = info.get('generation', 0)
    with _lock:
        if seconds >= _KEEP_SECONDS:
            _passes.append(Pass(started, seconds, generation, info.get('collected', 0)))
        total = _totals.setdefault(generation, [0, 0.0, 0.0])
        total[0] += 1
        total[1] += seconds
        total[2] = max(total[2], seconds)


def install() -> None:
    if _callback not in gc.callbacks:
        gc.callbacks.append(_callback)


def uninstall() -> None:
    if _callback in gc.callbacks:
        gc.callbacks.remove(_callback)


def freeze() -> int:
    """Move every object alive now into the permanent generation.

    What exists after start-up (modules, classes, route tables, caches) lives
    for the life of the process, yet every full collection walks all of it
    again. Frozen objects are skipped, so a full pass only walks what the
    running app allocated since. Costs no memory; ``gc.unfreeze`` reverses it.
    """
    global _frozen
    gc.collect()
    gc.freeze()
    _frozen = gc.get_freeze_count()
    return _frozen


def _after_full_pass() -> None:
    global _last_thaw, _refreezes, _thaws
    now = time.monotonic()
    if now - _last_thaw >= _thaw_seconds:
        gc.unfreeze()
        _last_thaw = now
        _thaws += 1
    else:
        gc.freeze()
        _refreezes += 1


def _refreeze_callback(phase: str, info: Dict[str, int]) -> None:
    loop = _refreeze_loop
    if phase != 'stop' or info.get('generation') != 2 or loop is None:
        return
    # Not from inside the collection: the collector is still mid-pass and can run on any thread.
    try:
        loop.call_soon_threadsafe(_after_full_pass)
    except RuntimeError:
        pass


def refreeze_after_full_passes(loop: asyncio.AbstractEventLoop, thaw_seconds: float = THAW_SECONDS) -> None:
    """Freeze whatever survives each full collection, so the next one skips it.

    A full pass walks every live object, and with pages open that is mostly their
    element trees: about 1,070,000 objects for 49 pages in production on
    2026-10-22, 1.4-1.9s a pass, growing with every tab. Freezing the survivors
    right after a pass means the next one walks only what was allocated since. In
    a local 42-tab run that took passes from 0.66-0.84s to 0.06-0.11s.

    Frozen objects are still freed by reference counting; only a cycle that forms
    among them waits. :mod:`element_cycles` removes the one every deleted element
    used to leave, and once every ``thaw_seconds`` the next pass after a full
    one walks everything as before and frees whatever did collect.
    """
    global _refreeze_loop, _last_thaw, _thaw_seconds
    _refreeze_loop = loop
    _thaw_seconds = thaw_seconds
    _last_thaw = time.monotonic()
    if _refreeze_callback not in gc.callbacks:
        gc.callbacks.append(_refreeze_callback)


def stop_refreeze() -> None:
    global _refreeze_loop
    _refreeze_loop = None
    if _refreeze_callback in gc.callbacks:
        gc.callbacks.remove(_refreeze_callback)


def running_since() -> Optional[float]:
    """When the collection in progress started (monotonic), or ``None``."""
    return _started


def seconds_between(start: float, end: float) -> tuple[float, int]:
    """Collection time inside ``[start, end]`` (monotonic) and the oldest generation it touched."""
    total = 0.0
    generation = -1
    with _lock:
        passes = list(_passes)
    for p in passes:
        overlap = min(end, p.started + p.seconds) - max(start, p.started)
        if overlap > 0:
            total += overlap
            generation = max(generation, p.generation)
    return total, generation


def passes_after(started: float) -> List[Pass]:
    """Recorded passes (10ms and longer) that started after ``started`` (monotonic), oldest first."""
    with _lock:
        return [p for p in _passes if p.started > started]


def snapshot() -> Dict[str, object]:
    with _lock:
        totals = {g: list(t) for g, t in _totals.items()}
        slowest = sorted(_passes, key=lambda p: p.seconds, reverse=True)[:5]
    return {
        'installed': _callback in gc.callbacks,
        'thresholds': list(gc.get_threshold()),
        'pending': list(gc.get_count()),
        'frozen_objects': gc.get_freeze_count() if _frozen is not None else None,
        'refreeze': {'refreezes': _refreezes, 'thaws': _thaws},
        'by_generation': {
            str(g): {
                'passes': int(t[0]),
                'total_seconds': round(t[1], 3),
                'max_seconds': round(t[2], 3),
            }
            for g, t in sorted(totals.items())
        },
        'slowest_recent': [
            {
                'generation': p.generation,
                'seconds': round(p.seconds, 3),
                'collected': p.collected,
                'ago_seconds': round(time.monotonic() - p.started),
            }
            for p in slowest
        ],
    }
