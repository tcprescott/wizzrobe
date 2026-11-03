"""The privacy policy, from ``privacy.md`` beside this module."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from application.content import Block, parse_blocks

__all__ = ['PRIVACY_PATH', 'PRIVACY_UPDATED', 'privacy_blocks']

PRIVACY_PATH = Path(__file__).parent / 'privacy.md'

# Bump with any change to what the policy says.
PRIVACY_UPDATED = date(2026, 10, 17)


@lru_cache(maxsize=1)
def privacy_blocks() -> tuple[Block, ...]:
    return tuple(parse_blocks(PRIVACY_PATH.read_text(encoding='utf-8')))
