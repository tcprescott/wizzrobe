"""Helpers shared by the bracket admin's page and its three dialogs.

Datetime marshalling for the native ``datetime-local`` inputs, round labelling,
and the entry-id → display-name map the results and advance dialogs both need.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from application.services.bracket_engines.round_names import round_label
from application.utils.timezone import parse_local_datetime, to_local
from models import BracketFormat, BracketMatch
from theme.brackets import detect_finals, entry_avatars

ELIM_FORMATS = (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM)


def iso_to_local_input(iso: Optional[str]) -> str:
    """Stored UTC ISO → a ``datetime-local`` value on the display clock, for prefill."""
    if not iso:
        return ''
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return to_local(dt).strftime('%Y-%m-%dT%H:%M')


def local_input_to_iso(value: Optional[str]) -> Optional[str]:
    """A ``datetime-local`` value on the display clock → stored UTC ISO, or None if blank."""
    if not value or 'T' not in value:
        return None
    date_str, time_str = value.split('T', 1)
    return parse_local_datetime(date_str, time_str[:5]).isoformat()


def distinct_rounds(matches: List[BracketMatch]) -> List[int]:
    """Rounds present, ordered winners (asc) then losers (by magnitude)."""
    positive = sorted({m.round for m in matches if m.round >= 0})
    negative = sorted({m.round for m in matches if m.round < 0}, key=abs)
    return positive + negative


def round_display_names(
    matches: List[BracketMatch], fmt: BracketFormat
) -> Dict[int, str]:
    """``round number → the name the public bracket gives that round``.

    The admin used to label these "Round 1 / Round 2 / Round 3" while the headers
    they configure render "Quarterfinals / Semifinals / Final" — the same rounds
    under two vocabularies, in one product. This routes both through
    ``round_label``, which is where the naming rule already lived.
    """
    rounds = distinct_rounds(matches)
    if not rounds:
        return {}
    double = fmt == BracketFormat.DOUBLE_ELIM
    grand_final, reset = detect_finals(matches) if double else (None, None)
    gf_round = grand_final.round if grand_final is not None else None
    reset_round = reset.round if reset is not None else None
    finals = {r for r in (gf_round, reset_round) if r is not None}
    max_winners = max(
        (r for r in rounds if r > 0 and r not in finals), default=None,
    )
    max_losers = max((abs(r) for r in rounds if r < 0), default=None)
    if fmt not in (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM):
        return {r: f'Round {r}' for r in rounds}
    return {
        r: round_label(
            r,
            is_grand_final=r == gf_round,
            is_reset=r == reset_round,
            max_winners_round=max_winners,
            max_losers_magnitude=max_losers,
            double_elim=double,
        )
        for r in rounds
    }


def match_label(match: BracketMatch, round_names: Dict[int, str]) -> str:
    """One matchup's human coordinates: its round name, group and board.

    Never the raw ``position``: round-robin positions are offset by group to keep
    the uniqueness constraint, so group 2 board 1 is stored as ``100001`` and
    printing it reads as data corruption to the one surface — round robin — that
    has no visual bracket to fall back on.
    """
    name = round_names.get(match.round, f'Round {match.round}')
    if match.group_number:
        board = match.position - (match.group_number - 1) * 100000
        return f'Group {match.group_number} · {name} · Board {board}'
    return f'{name} · Match {match.position}'


async def entry_display_maps(
    service, bracket_id: int, tournament_id: int
) -> Tuple[Dict[int, str], Dict[int, str]]:
    """``(names, avatar_urls)`` per entry, off one entrant load.

    The caller supplies the tenant scope. Paired because every surface that shows
    a name shows the disc beside it, and loading the roster twice to build them
    separately is the only way to get that wrong.
    """
    entrants = await service.list_entrants(tournament_id)
    entries = await service.list_entries(bracket_id)
    by_entrant = {en.id: en.display_name for en in entrants}
    names = {e.id: by_entrant.get(e.entrant_id, f'Entry {e.id}') for e in entries}
    return names, entry_avatars(entrants, entries)


async def entry_name_map(service, bracket_id: int, tournament_id: int) -> Dict[int, str]:
    """entry_id → entrant display name (the caller supplies the tenant scope)."""
    names, _ = await entry_display_maps(service, bracket_id, tournament_id)
    return names
