"""Helpers shared by the bracket admin's page and its three dialogs.

Datetime marshalling for the native ``datetime-local`` inputs, round labelling,
and the entry-id → display-name map the results and advance dialogs both need.
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from application.bracket_labels import distinct_rounds, match_label, round_display_names  # noqa: F401  (re-exported)
from application.utils.timezone import parse_local_datetime, to_local
from models import BracketFormat
from theme.brackets import entry_avatars

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
