"""racetime.gg's bot websocket, as plain data: what comes in and what goes out.

Pure functions, no I/O, so the transport stays a thin socket loop and every
judgement about the wire format is testable on a dict.

A room sends ``race.data`` (the whole race, on every change) and
``chat.message``. What the bot sends back is built in
:mod:`application.utils.clients.racetime_room_actions`, where the services
that decide to speak can reach it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from models import RaceRoomStatus
from racetimebot.transport import EntrantStatus, RaceEntrant, RaceRoomEvent, RoomChat

_ROOM_STATUS = {
    'open': RaceRoomStatus.OPEN,
    'invitational': RaceRoomStatus.OPEN,
    'pending': RaceRoomStatus.OPEN,
    'in_progress': RaceRoomStatus.IN_PROGRESS,
    'finished': RaceRoomStatus.FINISHED,
    'cancelled': RaceRoomStatus.CANCELLED,
}

_ENTRANT_STATUS = {
    'done': EntrantStatus.DONE,
    'dnf': EntrantStatus.DID_NOT_FINISH,
    'dq': EntrantStatus.DISQUALIFIED,
}

# ISO 8601 durations as racetime writes them: ``P0DT01H42M10.231407S``.
_DURATION = re.compile(
    r'^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$'
)

TERMINAL = frozenset({RaceRoomStatus.FINISHED, RaceRoomStatus.CANCELLED})


def duration_ms(value: Optional[str]) -> Optional[int]:
    """Milliseconds in a racetime duration, or ``None`` for blank or unreadable."""
    if not value:
        return None
    found = _DURATION.match(value.strip())
    if found is None:
        return None
    parts = found.groupdict()
    seconds = (
        int(parts['d'] or 0) * 86400 + int(parts['h'] or 0) * 3600
        + int(parts['m'] or 0) * 60 + float(parts['s'] or 0)
    )
    return round(seconds * 1000)


def _status_value(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get('value')
    return str(raw or '')


def _user_id(user: Any) -> str:
    return str(user.get('id') or '') if isinstance(user, dict) else ''


def _user_name(user: Any) -> str:
    if not isinstance(user, dict):
        return ''
    return str(user.get('name') or user.get('full_name') or user.get('id') or '')


def _when(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def room_status(race: Dict[str, Any]) -> RaceRoomStatus:
    """The room's lifecycle state, with an unknown value read as still open."""
    return _ROOM_STATUS.get(_status_value(race.get('status')), RaceRoomStatus.OPEN)


def entrant(raw: Dict[str, Any]) -> RaceEntrant:
    finish_ms = duration_ms(raw.get('finish_time'))
    place = raw.get('place')
    return RaceEntrant(
        user_id=_user_id(raw.get('user')),
        display_name=_user_name(raw.get('user')),
        status=_ENTRANT_STATUS.get(_status_value(raw.get('status')), EntrantStatus.IN_PROGRESS),
        finish_time=finish_ms // 1000 if finish_ms is not None else None,
        place=place if isinstance(place, int) else None,
        finish_ms=finish_ms,
    )


def race_event(race: Dict[str, Any], *, slug: str, category: str, kind: str = 'race') -> RaceRoomEvent:
    """A ``race.data`` payload (or the room's ``/data``) as a :class:`RaceRoomEvent`."""
    return RaceRoomEvent(
        slug=slug,
        category=category,
        status=room_status(race),
        entrants=[entrant(e) for e in race.get('entrants') or [] if isinstance(e, dict)],
        raw=race,
        kind=kind,
        monitor_ids=[uid for uid in (_user_id(m) for m in race.get('monitors') or []) if uid],
        started_at=_when(race.get('started_at')),
    )


def chat(message: Dict[str, Any]) -> Optional[RoomChat]:
    """A ``chat.message`` payload's message, or ``None`` when it has no text."""
    text = message.get('message_plain') or message.get('message') or ''
    if not isinstance(text, str) or not text.strip():
        return None
    user = message.get('user')
    return RoomChat(
        user_id=_user_id(user),
        display_name=_user_name(user) or str(message.get('bot') or ''),
        text=text.strip(),
        is_bot=bool(message.get('is_bot') or message.get('bot')),
        is_system=bool(message.get('is_system')),
    )


def entrant_ids(event: RaceRoomEvent) -> List[str]:
    return [e.user_id for e in event.entrants if e.user_id]
