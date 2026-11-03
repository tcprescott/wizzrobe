"""Relay cursors over the schedule's ``(scheduled_at, id)`` keyset.

A cursor is opaque to clients; it's the last row's start time and id, so a
match booked into an earlier page while a client pages forward neither
repeats nor shifts what comes next.
"""

import base64
from datetime import datetime
from typing import Optional, Tuple


def encode_cursor(scheduled_at: datetime, match_id: int) -> str:
    raw = f'{scheduled_at.isoformat()}|{match_id}'
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: Optional[str]) -> Optional[Tuple[datetime, int]]:
    if cursor is None:
        return None
    try:
        at, _, match_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition('|')
        return datetime.fromisoformat(at), int(match_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("That cursor isn't one this API handed out.") from exc
