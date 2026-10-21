"""DM text the way its reader saw it, for pages that show a sent DM again."""

import re

from application.utils.timezone import readable_discord_times

# Discord renders these itself; a page has to do it for them.
_EMPHASIS = re.compile(r'\*\*|__')
_MASKED_LINK = re.compile(r'\[([^\]]+)\]\(<?[^)>\s]+>?\)')
_SUBTEXT = re.compile(r'^-# ', re.MULTILINE)


def readable_dm_text(text: str) -> str:
    """``text`` with Discord timestamps in the viewer's clock and the markup dropped."""
    text = _MASKED_LINK.sub(r'\1', readable_discord_times(text or ''))
    return _SUBTEXT.sub('', _EMPHASIS.sub('', text))
