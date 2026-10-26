"""Which game of a best-of set a match is, worked out from when it's played.

Players book a set's games one at a time and in any order, so the number a game
was booked under says nothing about when it's played. A game's number is its
place on the clock instead: games that have started come first, in the order
they started, then the rest by booked time. A started game's number never moves
again, because nothing can start before it any more.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Hashable, Iterable, List, Optional

_LAST = datetime.max.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class GameTimes:
    key: Hashable
    started_at: Optional[datetime]
    scheduled_at: Optional[datetime]
    # Breaks ties, and orders games with neither time (a cancelled native game).
    tiebreak: int = 0


def _aware(at: Optional[datetime]) -> datetime:
    if at is None:
        return _LAST
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def game_numbers(games: Iterable[GameTimes]) -> Dict[Hashable, int]:
    """``key -> game number`` (from 1) for one set's games."""
    ordered: List[GameTimes] = sorted(games, key=lambda g: (
        g.started_at is None,
        _aware(g.started_at),
        g.scheduled_at is None,
        _aware(g.scheduled_at),
        g.tiebreak,
    ))
    return {g.key: number for number, g in enumerate(ordered, start=1)}


def remaining_slots(best_of: int, booked: int, decided: bool) -> List[bool]:
    """One entry per game still to book, True where that game is only played if needed.

    A set needs ``best_of // 2 + 1`` games at least; every game past that is
    "if needed". A decided set has nothing left to book.
    """
    if decided or best_of <= 1:
        return []
    needed = best_of // 2 + 1
    return [number > needed for number in range(booked + 1, best_of + 1)]
