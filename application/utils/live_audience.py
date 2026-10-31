"""Who is on the Watch page right now, and whether it should be static.

The app admits roughly ten new live tabs a second (docs/scaling-roadmap.md);
holding them afterwards is cheap. So the number that matters during a rush is
arrivals, not viewers: a Twitch raid can bring a few hundred people in seconds,
faster than anyone on staff could react. This counts both, per tenant, and
trips the page to its static rendering when arrivals pass the threshold staff
set, for :data:`TRIP_MINUTES` after the last minute that did.

Process-local by design, like the page cache: the app runs one worker, and the
counts describe this process's sockets, which are the ones that cost anything.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Set

TRIP_MINUTES = 10
_WINDOW_SECONDS = 60.0


@dataclass
class _Audience:
    arrivals: Deque[float] = field(default_factory=deque)
    static_reads: Deque[float] = field(default_factory=deque)
    live: Set[str] = field(default_factory=set)
    peak: int = 0
    peak_at: Optional[datetime] = None
    tripped_until: float = 0.0
    tripped_at: Optional[datetime] = None


_audiences: Dict[int, _Audience] = {}


def _get(tenant_id: int) -> _Audience:
    return _audiences.setdefault(tenant_id, _Audience())


def _prune(stamps: Deque[float], now: float) -> int:
    while stamps and now - stamps[0] > _WINDOW_SECONDS:
        stamps.popleft()
    return len(stamps)


def record_arrival(tenant_id: int, threshold: Optional[int] = None) -> None:
    """Someone opened ``/live``. Trips static when ``threshold`` is passed."""
    audience = _get(tenant_id)
    now = time.monotonic()
    audience.arrivals.append(now)
    if threshold and _prune(audience.arrivals, now) > threshold:
        if audience.tripped_until <= now:
            audience.tripped_at = datetime.now(timezone.utc)
        audience.tripped_until = now + TRIP_MINUTES * 60


def record_static_read(tenant_id: int) -> None:
    """A static reader refetched the page, which it does once a minute."""
    audience = _get(tenant_id)
    audience.static_reads.append(time.monotonic())


def viewer_joined(tenant_id: int, client_id: str) -> None:
    audience = _get(tenant_id)
    audience.live.add(client_id)
    if len(audience.live) > audience.peak:
        audience.peak = len(audience.live)
        audience.peak_at = datetime.now(timezone.utc)


def viewer_left(tenant_id: int, client_id: str) -> None:
    _get(tenant_id).live.discard(client_id)


def is_tripped(tenant_id: int) -> bool:
    audience = _audiences.get(tenant_id)
    return bool(audience and audience.tripped_until > time.monotonic())


def clear_trip(tenant_id: int) -> None:
    audience = _get(tenant_id)
    audience.tripped_until = 0.0
    audience.tripped_at = None


@dataclass(frozen=True)
class AudienceSnapshot:
    live_viewers: int
    static_readers: int
    arrivals_last_minute: int
    peak: int
    peak_at: Optional[datetime]
    tripped: bool
    tripped_at: Optional[datetime]


def snapshot(tenant_id: int) -> AudienceSnapshot:
    audience = _get(tenant_id)
    now = time.monotonic()
    tripped = audience.tripped_until > now
    return AudienceSnapshot(
        live_viewers=len(audience.live),
        static_readers=_prune(audience.static_reads, now),
        arrivals_last_minute=_prune(audience.arrivals, now),
        peak=audience.peak,
        peak_at=audience.peak_at,
        tripped=tripped,
        tripped_at=audience.tripped_at if tripped else None,
    )


def tenant_ids() -> List[int]:
    """Communities whose Watch page has had a visitor since this process started."""
    return list(_audiences)


def reset() -> None:
    """Forget everything (tests)."""
    _audiences.clear()
