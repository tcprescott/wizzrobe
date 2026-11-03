"""Human-readable names for a match, for user-facing web copy.

The Discord side already refuses to print primary keys — see the module
docstring of :mod:`application.utils.discord_messages`, which states the rule
and identifies a match by its players, its time and its stage. The web copy was
the only place still talking in database ids ("sign up as a commentator for
match ID 17"), so the same identification lives here, built from the row the
surface already has rather than from a fresh query.

Pure formatting: takes strings and dicts, touches no ORM, so both the
presentation layer and the services can use it.
"""

import re
from typing import Dict, List, Optional, Sequence, Tuple, Union

# A decided entrant with no account rides in bracket_placeholders too, so test against these shapes.
_UNDECIDED = re.compile(r'TBD|(?:Winner|Loser) of .+|.+ or .+')

MatchupPart = Dict[str, Union[str, bool]]


def and_join(words: Sequence[str]) -> str:
    """Words as prose: "a", "a and b", "a, b and c"."""
    words = list(words)
    if len(words) <= 1:
        return ''.join(words)
    return ', '.join(words[:-1]) + ' and ' + words[-1]


def players_label(player_names: Optional[Sequence[str]]) -> str:
    """Human-readable list of players: "A vs B" for two, comma-joined otherwise."""
    if not player_names:
        return ''
    names = list(player_names)
    if len(names) == 2:
        return ' vs '.join(names)
    return ', '.join(names)


def is_undecided_side(label: str) -> bool:
    """A bracket placeholder ("TBD", "Winner of C", "Andy or Benny"), not a decided player."""
    return bool(_UNDECIDED.fullmatch((label or '').strip()))


def matchup_parts(
    player_names: Sequence[str],
    placeholders: Sequence[str] = (),
    *,
    max_names: Optional[int] = None,
) -> List[MatchupPart]:
    """:func:`players_label`'s text as ``{'text', 'tbd'}`` pieces, so a renderer
    can set the sides a bracket hasn't decided apart from real players.

    ``placeholders`` follow the players, the way ``Match.bracket_placeholders``
    does. With ``max_names``, names past it are counted ("+3 more") instead,
    and the first two are joined with "vs" whatever the count.
    """
    sides = [(n, False) for n in player_names] + [(p, is_undecided_side(p)) for p in placeholders]
    return sides_parts(sides, max_names=max_names)


def sides_parts(sides: Sequence[Tuple[str, bool]], *, max_names: Optional[int] = None) -> List[MatchupPart]:
    """:func:`matchup_parts` for ``(name, undecided)`` pairs already in roster order."""
    shown = sides if max_names is None else sides[:max_names]
    sep = ' vs ' if len(sides) == 2 or max_names is not None else ', '
    parts: List[MatchupPart] = []
    for i, (text, tbd) in enumerate(shown):
        if i:
            parts.append({'text': sep, 'tbd': False})
        parts.append({'text': text, 'tbd': tbd})
    if len(shown) < len(sides):
        parts.append({'text': f' +{len(sides) - len(shown)} more', 'tbd': False})
    return parts


def has_undecided(parts: Sequence[MatchupPart]) -> bool:
    return any(p['tbd'] for p in parts)


def match_label(
    *,
    player_names: Optional[Sequence[str]] = None,
    title: str = '',
    tournament: str = '',
    scheduled_at: str = '',
    with_context: bool = True,
) -> str:
    """Name a match the way the person looking at it would.

    ``with_context`` appends the tournament and scheduled time in parentheses —
    wanted where the reader has to *decide* (a confirmation dialog), redundant
    in a toast that answers a click they just made.

    Falls back to ``'this match'`` rather than to an id: a match with no players
    and no schedule has nothing to identify it by, and the reader clicked the
    row it came from.
    """
    players = players_label(player_names)
    name = title if title and title != players else players
    context = [part for part in (tournament, scheduled_at) if part] if with_context else []

    if not name:
        return ', '.join(context) if context else 'this match'
    if context:
        return f"{name} ({', '.join(context)})"
    return name


def match_model_label(match, *, with_context: bool = True) -> str:
    """:func:`match_label` for a loaded ``Match``, for the lifecycle dialogs.

    Its sibling below reads a board row; this one reads the model the dialog
    already loaded (with ``players__user`` prefetched). Same rule either way —
    an id in a sentence is a defect wherever a user can read it — so a dialog
    that says "Start match #17?" is one that has not been through here.

    Falls back to the plain id only when the match has no roster, no title and
    no time, which is the one case where there is genuinely nothing else to say.
    """
    from application.utils.timezone import format_local_display

    # Defensive: an unprefetched match gets a thinner label, never an exception mid-dialog.
    try:
        players = [
            p.user.preferred_name for p in match.players
            if getattr(p, 'user', None) is not None
        ]
    except Exception:
        players = []
    try:
        tournament = match.tournament.name if match.tournament is not None else ''
    except Exception:
        tournament = ''

    label = match_label(
        player_names=players,
        title=getattr(match, 'title', '') or '',
        tournament=tournament,
        scheduled_at=format_local_display(getattr(match, 'scheduled_at', None)),
        with_context=with_context,
    )
    return f'match #{match.id}' if label == 'this match' else label


def match_row_label(row: dict, *, with_context: bool = True) -> str:
    """:func:`match_label` for a match-table row dict (``MatchDisplayService``)."""
    players = [p.get('name', '') for p in (row.get('players') or []) if p.get('name')]
    return match_label(
        player_names=players,
        title=row.get('title') or '',
        tournament=row.get('tournament') or '',
        scheduled_at=row.get('scheduled_at') or '',
        with_context=with_context,
    )


def challonge_round_label(round_: Optional[int]) -> str:
    """Name a Challonge round the way the native bracket does.

    Challonge numbers losers rounds as negatives, so a raw ``Round -1`` reached
    players. Group stages and an unknown round (a freshly mirrored row) have no
    number to show and get an empty string.
    """
    if not round_:
        return ''
    return f'Losers round {abs(round_)}' if round_ < 0 else f'Round {round_}'


def entrant_name(entrant) -> str:
    """A bracket-site entrant (Challonge participant, start.gg entrant) as players
    know them here: the linked member's Wizzrobe name, else the site's own name.

    ``entrant.user`` must be prefetched.
    """
    if entrant is None:
        return 'TBD'
    user = entrant.user
    if user is not None:
        return user.preferred_name
    return entrant.name or 'TBD'
