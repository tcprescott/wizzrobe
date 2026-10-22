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

import gc
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

_RECENT = 200
# Young-generation passes run hundreds of times a second under load and take
# microseconds; only keep the ones long enough to matter in a stall.
_KEEP_SECONDS = 0.01


@dataclass
class Pass:
    started: float
    seconds: float
    generation: int
    collected: int


# An RLock: a collection can start inside a section that already holds it, on the
# same thread, and the callback must not deadlock against itself.
_lock = threading.RLock()
_passes: Deque[Pass] = deque(maxlen=_RECENT)
_totals: Dict[int, List[float]] = {0: [0, 0.0, 0.0], 1: [0, 0.0, 0.0], 2: [0, 0.0, 0.0]}
_started: Optional[float] = None
_frozen: Optional[int] = None


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


def snapshot() -> Dict[str, object]:
    with _lock:
        totals = {g: list(t) for g, t in _totals.items()}
        slowest = sorted(_passes, key=lambda p: p.seconds, reverse=True)[:5]
    return {
        'installed': _callback in gc.callbacks,
        'thresholds': list(gc.get_threshold()),
        'pending': list(gc.get_count()),
        'frozen_objects': _frozen,
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
