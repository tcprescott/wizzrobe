"""Argument parsing shared by every tool that takes a time window.

MCP arguments arrive as JSON, so a timestamp is a string until someone parses
it. Doing that per module produced the same three-line ``try/fromisoformat``
in four files; centralising it also makes the *message* uniform, which matters
more than the code: the model recovers from a refusal by reading it, and one
consistent phrasing is one thing to learn.
"""

from datetime import datetime
from typing import Literal, Optional, Tuple


def parse_ts(label: str, value: Optional[str]) -> Optional[datetime]:
    """Parse one optional ISO 8601 argument, naming the field when it is wrong."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{label} must be an ISO 8601 timestamp') from exc


def window(start: str, end: str) -> Tuple[datetime, datetime]:
    """Parse a required ``[start, end]`` pair of ISO 8601 timestamps."""
    try:
        start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError as exc:
        raise ValueError('start and end must be ISO 8601 timestamps') from exc
    # Refuse a reversed window: the services clamp it to empty, which reads as "no data".
    try:
        reversed_window = end_dt < start_dt
    except TypeError as exc:
        raise ValueError('start and end must both carry a UTC offset, or neither') from exc
    if reversed_window:
        raise ValueError('end must not be before start.')
    return start_dt, end_dt


# A Literal on purpose: the service treats every value but 'month' as a week.
Bucket = Literal['week', 'month']


def clamp(value: int, *, low: int, high: int) -> int:
    """Clamp a caller-supplied row count into a range the surface can afford."""
    return max(low, min(value, high))


def choose(dimension: str, options: dict):
    """Resolve a string dimension to its handler, or explain the valid set.

    An error the model can act on beats one it can only report, so the refusal
    names every accepted value rather than just rejecting this one.
    """
    chosen = options.get(dimension)
    if chosen is None:
        raise ValueError(
            f"Unknown dimension '{dimension}'. Valid: {', '.join(options)}"
        )
    return chosen
