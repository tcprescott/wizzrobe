"""Text of the DM a tournament's admins get when a match result comes in.
Sent by :mod:`application.services.result_alerts`."""

from datetime import datetime
from typing import NamedTuple, Optional, Sequence

from application.utils.discord_messages_opt_in import _clock, _ordinal


class Placing(NamedTuple):
    """One player's line on the board. ``rank`` is None for a player who didn't
    finish; ``seconds`` is a race time, ``score`` a bracket-site set score."""

    name: str
    rank: Optional[int]
    seconds: Optional[int] = None
    score: Optional[int] = None


def _detail(p: Placing) -> str:
    if p.seconds:
        return _clock(p.seconds)
    return '' if p.score is None else str(p.score)


def _ordered(placings: Sequence[Placing]) -> list:
    return sorted(placings, key=lambda p: (p.rank is None, p.rank or 0))


def result_line(placings: Sequence[Placing]) -> str:
    """``Alice beat Bob 2–1`` / ``Alice beat Bob in 1:42:13`` / ``Alice won``:
    the one-line form, for the plain-text copy and the roundup."""
    ordered = _ordered(placings)
    if not ordered or ordered[0].rank is None:
        return 'No one finished'
    winner, rest = ordered[0], ordered[1:]
    if len(ordered) != 2:
        time = f' in {_clock(winner.seconds)}' if winner.seconds else ''
        return f'**{winner.name}** won{time}'
    loser = rest[0]
    if winner.score is not None and loser.score is not None:
        return f'**{winner.name}** beat {loser.name} {winner.score}–{loser.score}'
    time = f' in {_clock(winner.seconds)}' if winner.seconds else ''
    return f'**{winner.name}** beat {loser.name}{time}'


def result_text(tournament: str, placings: Sequence[Placing], source: str) -> str:
    """The plain-text copy (web push, Telegram, email)."""
    return f'{result_line(placings)} in {tournament}. {source}.'


BOARD_MAX = 5


def result_board(placings: Sequence[Placing]) -> str:
    """Placings first, one line each: ``1st  **Alice** · 1:42:13``."""
    ordered = _ordered(placings)
    shown = ordered if len(ordered) <= BOARD_MAX else ordered[:3]
    lines = []
    for p in shown:
        detail = _detail(p)
        tail = f' · {detail}' if detail else ''
        if p.rank is None:
            lines.append(f"Didn't finish: {p.name}{tail}")
        elif p.rank == 1:
            lines.append(f'{_ordinal(p.rank)}  **{p.name}**{tail}')
        else:
            lines.append(f'{_ordinal(p.rank)}  {p.name}{tail}')
    if len(shown) < len(ordered):
        lines.append(f'…and {len(ordered) - len(shown)} more')
    return '\n'.join(lines)


def when_line(played: Optional[datetime], result_in: datetime) -> str:
    """``Played <t:…:f> · result in <t:…:t>``, Discord timestamps so each
    reader sees their own clock."""
    came_in = f'<t:{int(result_in.timestamp())}:t>'
    if played is None:
        return f'Result in {came_in}'
    return f'Played <t:{int(played.timestamp())}:f> · result in {came_in}'


def recorded_by(name: Optional[str]) -> str:
    return f'Recorded by {name}' if name else 'Recorded in Wizzrobe'


def forfeit_by(name: Optional[str]) -> str:
    return f'Forfeit recorded by {name}' if name else 'Forfeit recorded'


RACE_ROOM = 'From the race room'


def reported_on(site: str) -> str:
    return f'Reported on {site}'


def roundup_title(tournament: str, count: int, site: str) -> str:
    return f'🏁 {tournament} · {count} results from {site}'


def roundup_line(placings: Sequence[Placing], round_label: Optional[str]) -> str:
    tail = f' · {round_label}' if round_label else ''
    return f'{result_line(placings)}{tail}'


def roundup_text(tournament: str, count: int, site: str) -> str:
    return f'{count} {tournament} results came in from {site}.'
