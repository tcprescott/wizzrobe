"""When a race the bot ran waits for staff instead of recording itself.

Pure functions over a finished race, so every rule is testable without a
database. :func:`hold_for` runs the checks in :class:`~models.RaceRoomHoldReason`
order and returns the first that applies, or ``None`` for a clean result that
records and reports on its own.

A clean result is one nobody could reasonably dispute: every player linked,
every runner a player, everyone finished, nobody called a fair play pause, and
no two finishers within the tournament's close-finish threshold. Anything else
is a person's call, so the race stops at "held" and waits on the Check result
dialog.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Dict, List, Optional, Sequence

from application.utils.duration import format_hms
from application.utils.racetime_entrants import is_scored_finish, unmatched_handle
from models import RaceRoomHoldReason
from racetimebot.transport import EntrantStatus, RaceEntrant


@dataclass(frozen=True)
class RacePlayer:
    """One of the match's players, as the hold checks need them."""

    name: str
    racetime_user_id: Optional[str]


@dataclass(frozen=True)
class Hold:
    reason: RaceRoomHoldReason
    detail: str


HOLD_LABELS = {
    RaceRoomHoldReason.FPA: 'FPA called',
    RaceRoomHoldReason.UNMATCHED: "Runner not matched",
    RaceRoomHoldReason.FORFEIT: 'Forfeit',
    RaceRoomHoldReason.CLOSE_FINISH: 'Close finish',
}


def hold_label(reason: Optional[str]) -> str:
    """The row chip's words for a hold reason, ``''`` for none."""
    if not reason:
        return ''
    try:
        return HOLD_LABELS[RaceRoomHoldReason(reason)]
    except ValueError:
        return 'Held'


def gap_label(ms: int) -> str:
    """A finish gap in the precision that makes it readable: ``0.4s``, ``3s``."""
    if ms < 10_000:
        return f'{ms / 1000:.1f}s'.replace('.0s', 's')
    return f'{round(ms / 1000)}s'


def hold_for(
    players: Sequence[RacePlayer],
    entrants: Sequence[RaceEntrant],
    *,
    fpa_calls: Sequence[Dict[str, Any]] = (),
    close_finish_ms: int = 1000,
) -> Optional[Hold]:
    """The first reason this race can't record itself, or ``None``."""
    if fpa_calls:
        call = fpa_calls[0]
        at = format_hms(call.get('race_seconds'), dash='')
        who = call.get('name') or 'A runner'
        more = f' (and {len(fpa_calls) - 1} more)' if len(fpa_calls) > 1 else ''
        return Hold(
            RaceRoomHoldReason.FPA,
            f'{who} called a fair play pause' + (f' at {at}' if at else '') + more + '.',
        )

    by_id = {p.racetime_user_id: p for p in players if p.racetime_user_id}
    unlinked = [p.name for p in players if not p.racetime_user_id]
    if unlinked:
        return Hold(
            RaceRoomHoldReason.UNMATCHED,
            f"{_names(unlinked)} {'has' if len(unlinked) == 1 else 'have'} no racetime account linked.",
        )
    strangers = [unmatched_handle(e) for e in entrants if e.user_id not in by_id]
    if strangers:
        return Hold(
            RaceRoomHoldReason.UNMATCHED,
            f"racetime runner {_names(strangers)} isn't a player in this match.",
        )
    raced = {e.user_id for e in entrants}
    absent = [p.name for p in by_id.values() if p.racetime_user_id not in raced]
    if absent:
        return Hold(
            RaceRoomHoldReason.UNMATCHED,
            f"{_names(absent)} didn't join the race.",
        )

    unfinished = [e for e in entrants if not is_scored_finish(e)]
    if unfinished:
        first = unfinished[0]
        name = by_id[first.user_id].name
        verb = {
            EntrantStatus.DISQUALIFIED: 'was disqualified',
            EntrantStatus.DONE: 'finished with no time',
        }.get(first.status, 'forfeited')
        return Hold(RaceRoomHoldReason.FORFEIT, f'{name} {verb}.')

    if close_finish_ms > 0:
        timed = sorted(
            (e for e in entrants if _ms(e) is not None), key=lambda e: _ms(e) or 0,
        )
        for ahead, behind in pairwise(timed):
            gap = (_ms(behind) or 0) - (_ms(ahead) or 0)
            if gap <= close_finish_ms:
                return Hold(
                    RaceRoomHoldReason.CLOSE_FINISH,
                    f'{by_id[ahead.user_id].name} and {by_id[behind.user_id].name} '
                    f'finished {gap_label(gap)} apart.',
                )
    return None


def entrant_snapshot(entrants: Sequence[RaceEntrant]) -> List[Dict[str, Any]]:
    """What racetime reported, kept on the room so the Check result dialog can show it."""
    return [
        {
            'user_id': e.user_id,
            'name': e.display_name,
            'status': e.status.value,
            'place': e.place,
            'finish_ms': _ms(e),
        }
        for e in entrants
    ]


def _ms(entrant: RaceEntrant) -> Optional[int]:
    if entrant.finish_ms is not None:
        return entrant.finish_ms
    return entrant.finish_time * 1000 if entrant.finish_time is not None else None


def _names(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 2:
        return ' and '.join(names)
    return ', '.join(names[:-1]) + f' and {names[-1]}'
