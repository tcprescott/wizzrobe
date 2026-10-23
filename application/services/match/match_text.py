"""Normalising the staff-written title and description a match carries."""

from typing import Optional

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
