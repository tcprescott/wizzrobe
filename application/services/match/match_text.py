"""Normalising the staff-written title and description a match carries."""

from typing import Any, Dict, Optional, Sequence

TITLE_MAX = 255
DESCRIPTION_MAX = 4000


def clean_title(value: Optional[str]) -> Optional[str]:
    """Trimmed title, or None for a blank one. Raises on one too long to store."""
    text = (value or '').strip()
    if len(text) > TITLE_MAX:
        raise ValueError(f"Keep the title to {TITLE_MAX} characters.")
    return text or None


def clean_description(value: Optional[str]) -> Optional[str]:
    """Trimmed markdown description, or None for a blank one."""
    text = (value or '').strip()
    if len(text) > DESCRIPTION_MAX:
        raise ValueError(f"Keep the description to {DESCRIPTION_MAX} characters.")
    return text or None


def require_roster_or_title(
    player_ids: Sequence[int], title: Optional[str], *,
    from_bracket: bool = False, placeholders: Optional[Sequence[str]] = None,
) -> None:
    """Refuse a new match the schedule would have no way to name.

    A placeholder is staff holding a slot before anyone knows who plays: no
    players, and the title stands in for their names on every schedule. A
    bracket booking names its sides with ``placeholders`` instead.
    """
    if player_ids:
        return
    if from_bracket:
        if not placeholders:
            raise ValueError("Add at least one player before creating the match.")
    elif not title:
        raise ValueError("Give the match a title, or add a player, so the schedule can say what it is.")


def text_updates(title: Optional[str], description: Optional[str]) -> Dict[str, Any]:
    """The edit's title/description columns: None leaves one alone, ``''`` clears it."""
    fields: Dict[str, Any] = {}
    if title is not None:
        fields['title'] = clean_title(title)
    if description is not None:
        fields['description'] = clean_description(description)
    return fields
