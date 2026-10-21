"""Text of the opt-in DMs: messages nobody gets until they turn them on under
Profile → Notifications. Sent by :mod:`application.services.opt_in_notifications`."""

from typing import Optional, Sequence, Tuple


def opponent_acknowledged_dm(name: str, tournament: str) -> str:
    return f"{name} acknowledged your {tournament} match, so they've seen the time."


def station_assigned_dm(tournament: str, station: str) -> str:
    return f'Your {tournament} match is on station **{station}**.'



def added_to_tournament_dm(tournament: str, by: str) -> str:
    return f'{by} added you to **{tournament}**.'


def removed_from_tournament_dm(tournament: str, by: str) -> str:
    return f'{by} took you out of **{tournament}**. Ask staff if that looks wrong.'


def player_withdrew_dm(player: str, tournament: str) -> str:
    return f'{player} withdrew from **{tournament}**.'


def request_withdrawn_dm(requester: str, tournament: str, cancel: bool) -> str:
    what = 'cancel' if cancel else 'reschedule'
    return f'{requester} withdrew their request to {what} your {tournament} match. Nothing changes.'


def match_flagged_dm(by: str, tournament: str) -> str:
    return f'{by} flagged a {tournament} match for review.'


def seed_failed_dm(tournament: str, error: Optional[str]) -> str:
    error = (error or '').strip()
    if error and error[-1] not in '.!?':
        error += '.'
    reason = f' The randomizer said: {error}' if error else ''
    return f"A seed for a {tournament} match didn't roll.{reason} Roll it again from the match."


def stream_offer_dm(by: str, tournament: str) -> str:
    return f'{by} offered to stream a {tournament} match.'


def volunteer_arrived_dm(volunteer: str, position: str) -> str:
    return f"{volunteer} is here for their {position} shift."


def qualifier_opened_dm(name: str) -> str:
    return f'**{name}** is open. You can start your run now.'


def qualifier_closed_dm(name: str) -> str:
    return f'**{name}** has closed. No more runs can be started.'


def _clock(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}'


def _ordinal(n: int) -> str:
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def match_result_dm(tournament: str, placings: Sequence[Tuple[str, Optional[int], Optional[int]]]) -> str:
    """``placings`` is (name, finish_rank, finish_time seconds), best first; a
    player with no rank didn't finish."""
    ranked = [p for p in placings if p[1]]
    unranked = [p for p in placings if not p[1]]
    if len(placings) == 2 and len(ranked) >= 1:
        winner = ranked[0]
        loser = next(p for p in placings if p is not winner)
        time = f' in {_clock(winner[2])}' if winner[2] else ''
        return f'**{winner[0]}** beat {loser[0]}{time} in {tournament}.'
    lines = [f'{tournament} result:']
    for name, rank, seconds in ranked:
        time = f' ({_clock(seconds)})' if seconds else ''
        lines.append(f'{_ordinal(rank)}: **{name}**{time}')  # type: ignore[arg-type]
    lines += [f"Didn't finish: {name}" for name, _, _ in unranked]
    return '\n'.join(lines)


def bracket_started_dm(tournament: str) -> str:
    return f"**{tournament}**'s bracket has started."


def event_listed_dm(title: str, host: str) -> str:
    return f'New community event from {host}: **{title}**.'
