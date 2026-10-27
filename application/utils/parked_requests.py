"""Housekeeping for in-process stores of parked authorization requests.

``/oauth/authorize`` and the MCP server's authorize step both park a request
until the person answers the consent screen. Anyone can start one without
signing in, so the store needs a ceiling as well as an expiry: past it the
oldest go first, which costs a person mid-consent a restart rather than
letting a flood grow the single worker's memory.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Protocol, TypeVar

MAX_PARKED = 5_000


class _Parked(Protocol):
    created_at: datetime


P = TypeVar('P', bound=_Parked)


def prune_parked(store: Dict[str, P], ttl: timedelta, *, room_for: int = 0) -> None:
    """Drop expired entries, then the oldest until ``room_for`` more fit under the ceiling."""
    cutoff = datetime.now(timezone.utc) - ttl
    for key in [k for k, v in store.items() if v.created_at < cutoff]:
        del store[key]
    overflow = len(store) + room_for - MAX_PARKED
    if overflow > 0:
        for key in sorted(store, key=lambda k: store[k].created_at)[:overflow]:
            del store[key]
