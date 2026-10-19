"""Text of the opt-in DMs: messages nobody gets until they turn them on under
Profile → Notifications. Sent by :mod:`application.services.opt_in_notifications`."""

from typing import Optional


def opponent_acknowledged_dm(name: str, tournament: str) -> str:
    return f"{name} acknowledged your {tournament} match, so they've seen the time."


def station_assigned_dm(tournament: str, station: str) -> str:
    return f'Your {tournament} match is on station **{station}**.'


def bracket_won_dm(opponent: str, round_name: str, tournament: str) -> str:
    return f'You beat {opponent} in {round_name} of {tournament}. You move on.'


def bracket_lost_dm(opponent: str, round_name: str, tournament: str, *, out: bool) -> str:
    then = "You're out of the bracket." if out else "You're still in, in the losers' bracket."
    return f'{opponent} beat you in {round_name} of {tournament}. {then}'


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
