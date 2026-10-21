"""Discord Message Templates: forfeits and drops

The DM copy for :mod:`application.services.match.match_forfeit` and the
tournament drop flow. A sibling of :mod:`application.utils.discord_messages`
on the same rule as the other siblings: the parent module stays under its
line budget, and all Discord text still lives in a builder.
"""

from typing import Optional

from application.utils.discord_messages import _match_info_lines


def forfeit_description(
    *, winner_name: str, forfeiter_name: str, reason_label: str, you: str = '',
) -> str:
    """The what-happened line of a forfeit DM, shared by its text and its card.

    ``you`` is ``'winner'`` or ``'forfeiter'`` for a player's own copy, '' for
    crew and watchers.
    """
    why = reason_label.lower()
    if you == 'winner':
        return (f"{forfeiter_name} forfeited ({why}), so you win this one and move on. "
                "Watch for your next match.")
    if you == 'forfeiter':
        return (f"Staff recorded a forfeit ({why}), so {winner_name} wins this one. "
                "Ask staff if that looks wrong.")
    return f"{forfeiter_name} forfeited ({why}), so {winner_name} wins. The match won't be played."


def forfeit_dm(
    tournament_name: str,
    *,
    winner_name: str,
    forfeiter_name: str,
    reason_label: str,
    you: str = '',
    player_names: Optional[list[str]] = None,
    scheduled_at_display: str = '',
    stage_name: str = '',
    bracket_line: str = '',
) -> str:
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        time_label='Was scheduled for',
        bracket_line=bracket_line,
    )
    block = ("\n".join(info) + "\n\n") if info else ''
    return (
        f"Your match in **{tournament_name}** was decided by forfeit.\n\n"
        f"{block}"
        f"{forfeit_description(winner_name=winner_name, forfeiter_name=forfeiter_name, reason_label=reason_label, you=you)}"
    )


def dropped_from_tournament_dm(
    tournament_name: str, *, reason_label: str, forfeited: int, called_off: int,
) -> str:
    """DM to a player staff dropped from a tournament."""
    parts = []
    if forfeited:
        parts.append(f"{forfeited} {'match goes' if forfeited == 1 else 'matches go'} to your opponents by forfeit")
    if called_off:
        parts.append(f"{called_off} {'match was' if called_off == 1 else 'matches were'} called off")
    matches = (' ' + ' and '.join(parts) + '.') if parts else ''
    return (
        f"Staff dropped you from **{tournament_name}** ({reason_label.lower()}).{matches} "
        "Ask staff if that looks wrong."
    )
