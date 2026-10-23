"""Round windows: when one stage or round of a bracket may be booked.

Pure and ORM-free. A window has up to four parts, each optional: the instant it
opens, the instant it closes, and a daily ``from``/``until`` that narrows every
day inside it ("semifinals Sat 18:00 to Sun 23:00, evenings only"). Opens and
closes are stored as UTC ISO strings; the daily hours are wall-clock ``HH:MM``
on the **community's** clock, the same clock its tournament hours are written in.

Stage keys name a stage independently of a site's round numbering, so staff can
set a window before the bracket exists (a two-stage Challonge tournament only
creates its elimination matches when the group stage finishes):

* ``group`` — a Challonge group stage; ``<phase_id>`` — a start.gg pool phase.
* ``e0``, ``e1``, … — single elimination, counted back from the final.
* ``gf`` — a double-elimination grand final (its reset included); ``w0``, ``w1``, …
  winners rounds counted back from the winners final; ``l0``, ``l1``, … losers
  rounds counted back from the losers final.

A start.gg elimination phase prefixes its keys with ``<phase_id>:``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from typing import Dict, Iterable, Optional, Set

GROUP_KEY = 'group'
GRAND_FINAL_KEY = 'gf'

_SE_NAMES = {0: 'Final', 1: 'Semifinals', 2: 'Quarterfinals'}
_WINNERS_NAMES = {0: 'Winners Final', 1: 'Winners Semifinals', 2: 'Winners Quarterfinals'}
_LOSERS_NAMES = {0: 'Losers Final', 1: 'Losers Semifinal'}


def _parse_instant(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed.astimezone(dt_timezone.utc)


def _parse_clock(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RoundWindow:
    opens: Optional[datetime] = None
    closes: Optional[datetime] = None
    daily_from: Optional[time] = None
    daily_until: Optional[time] = None

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> Optional['RoundWindow']:
        """Read a stored entry; native rounds call their halves ``scheduled_*``."""
        if not cfg:
            return None
        window = cls(
            opens=_parse_instant(cfg.get('opens_at') or cfg.get('scheduled_at')),
            closes=_parse_instant(cfg.get('closes_at') or cfg.get('scheduled_end')),
            daily_from=_parse_clock(cfg.get('daily_from')),
            daily_until=_parse_clock(cfg.get('daily_until')),
        )
        return None if window.is_empty else window

    @property
    def is_empty(self) -> bool:
        return not (self.opens or self.closes or self.has_daily_hours)

    @property
    def has_daily_hours(self) -> bool:
        return self.daily_from is not None and self.daily_until is not None

    def slot_fits(self, start: datetime, duration: timedelta, tz) -> bool:
        """Whether a match starting at ``start`` fits wholly inside the window."""
        end = start + duration
        if self.opens is not None and start < self.opens:
            return False
        if self.closes is not None and end > self.closes:
            return False
        if self.daily_from is not None and self.daily_until is not None:
            local = start.astimezone(tz)
            day_open = datetime.combine(local.date(), self.daily_from, tzinfo=tz)
            day_close = datetime.combine(local.date(), self.daily_until, tzinfo=tz)
            if day_close <= day_open:
                day_close += timedelta(days=1)
            if local < day_open or end.astimezone(tz) > day_close:
                return False
        return True

    def describe(self, tz) -> str:
        """``Book between Sat 6 pm and Sun 11 pm, evenings 6 to 11 pm`` and kin."""
        parts = []
        if self.opens and self.closes:
            parts.append(f'Book between {_day_time(self.opens, tz)} and {_day_time(self.closes, tz)}')
        elif self.closes:
            parts.append(f'Book by {_day_time(self.closes, tz)}')
        elif self.opens:
            parts.append(f'Opens {_day_time(self.opens, tz)}')
        if self.daily_from is not None and self.daily_until is not None:
            hours = f'{_clock(self.daily_from)} to {_clock(self.daily_until)}'
            parts.append(f'only {hours} each day' if not parts else f'{hours} each day')
        text = ', '.join(parts)
        return text[:1].upper() + text[1:]


def _clock(t: time) -> str:
    hour = t.hour % 12 or 12
    suffix = 'am' if t.hour < 12 else 'pm'
    return f'{hour} {suffix}' if t.minute == 0 else f'{hour}:{t.minute:02d} {suffix}'


def _day_time(dt: datetime, tz) -> str:
    local = dt.astimezone(tz)
    return f"{local.strftime('%a')} {_clock(local.time())}"


_CHALLONGE_GROUP_STATES = frozenset({'group_stages_underway', 'group_stages_finalized'})


def challonge_group_stage_running(remote_tournament: Optional[dict]) -> bool:
    """Whether a Challonge tournament is in its group stage.

    While it is, every match Challonge has is a group match: the elimination
    matches only exist once the final stage starts, and Challonge doesn't label
    group matches any other way.
    """
    remote = remote_tournament or {}
    return bool(remote.get('group_stage')) and remote.get('state') in _CHALLONGE_GROUP_STATES


def window_to_config(
    opens: Optional[datetime], closes: Optional[datetime],
    daily_from: Optional[str], daily_until: Optional[str],
) -> Optional[dict]:
    entry: dict = {}
    if opens is not None:
        entry['opens_at'] = opens.astimezone(dt_timezone.utc).isoformat()
    if closes is not None:
        entry['closes_at'] = closes.astimezone(dt_timezone.utc).isoformat()
    if daily_from and daily_until:
        entry['daily_from'] = daily_from
        entry['daily_until'] = daily_until
    return entry or None


# ---------------------------------------------------------------------------
# Stage keys
# ---------------------------------------------------------------------------

def winners_final_round(rounds: Iterable[int]) -> Optional[int]:
    """The double-elim winners final, read off the losers bracket's depth.

    Neither Challonge nor start.gg tells us which positive round is the grand
    final, but a losers bracket of ``L`` rounds always meets a winners bracket of
    ``ceil(L/2) + 1``; anything above that is the grand final (and its reset).
    """
    losers = max((abs(r) for r in rounds if r < 0), default=0)
    return math.ceil(losers / 2) + 1 if losers else None


def elimination_keys(
    rounds: Iterable[int], *, double: bool, grand_final_rounds: Optional[Set[int]] = None,
) -> Dict[int, str]:
    """``{round number: stage key}`` for one elimination bracket.

    Challonge files a single-elimination 3rd place match under round 0; it is
    played alongside the final, so it shares the final's key (and best-of).
    """
    given = set(rounds)
    rounds = sorted(r for r in given if r)
    if not rounds:
        return {}
    keys: Dict[int, str] = {}
    if not double:
        top = max(rounds)
        keys = {r: f'e{top - r}' for r in rounds if r > 0}
        if 0 in given and top > 0:
            keys[0] = 'e0'
        return keys
    gf_rounds = set(grand_final_rounds or ())
    wf = winners_final_round(rounds)
    if wf is None:
        wf = max((r for r in rounds if r > 0 and r not in gf_rounds), default=max(rounds))
    max_losers = max((abs(r) for r in rounds if r < 0), default=0)
    for r in rounds:
        if r > 0 and (r in gf_rounds or r > wf):
            keys[r] = GRAND_FINAL_KEY
        elif r > 0:
            keys[r] = f'w{wf - r}'
        else:
            keys[r] = f'l{max_losers - abs(r)}'
    return keys


def stage_label(key: str, *, round_number: Optional[int] = None) -> str:
    """The name a stage key reads as; ``round_number`` names deep early rounds."""
    if key == GROUP_KEY:
        return 'Group stage'
    if key == GRAND_FINAL_KEY:
        return 'Grand Finals'
    kind, depth = key[0], int(key[1:])
    if kind == 'e':
        return _SE_NAMES.get(depth) or (
            f'Round {round_number}' if round_number else f'Round of {2 ** (depth + 1)}'
        )
    if kind == 'w':
        return _WINNERS_NAMES.get(depth) or (
            f'Winners Round {round_number}' if round_number else f'Winners Round of {2 ** (depth + 1)}'
        )
    return _LOSERS_NAMES.get(depth) or (
        f'Losers Round {abs(round_number)}' if round_number else f'Losers Round (final - {depth})'
    )


def stage_sort_key(key: str) -> tuple:
    """Group stage first, then earliest elimination round first, finals last."""
    if key == GROUP_KEY:
        return (0, 0, 0)
    if key == GRAND_FINAL_KEY:
        return (2, 0, 0)
    kind, depth = key[0], int(key[1:])
    # Winners and losers interleave by depth; within a depth, losers play first.
    return (1, -depth, 0 if kind == 'l' else 1)


def placeholder_keys(*, double: bool) -> list:
    """The rows offered before a bracket's elimination matches exist."""
    if double:
        return [GRAND_FINAL_KEY, 'w0', 'w1', 'l0', 'l1']
    return ['e2', 'e1', 'e0']
