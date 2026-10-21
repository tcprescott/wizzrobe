"""DM text the way its reader saw it, for pages that show a sent DM again."""

import re
from datetime import datetime, timezone

from application.utils.timezone import format_local_display

# Discord renders these itself; a page has to do it for them.
_DISCORD_TIME = re.compile(r'<t:(\d{1,12})(?::[tTdDfFR])?>')
_EMPHASIS = re.compile(r'\*\*|__')
_MASKED_LINK = re.compile(r'\[([^\]]+)\]\(<?[^)>\s]+>?\)')
_SUBTEXT = re.compile(r'^-# ', re.MULTILINE)


def readable_dm_text(text: str) -> str:
    """``text`` with Discord timestamps in the viewer's clock and the markup dropped."""
    def _time(match: re.Match) -> str:
        return format_local_display(datetime.fromtimestamp(int(match.group(1)), timezone.utc))
    text = _DISCORD_TIME.sub(_time, text or '')
    text = _MASKED_LINK.sub(r'\1', text)
    return _SUBTEXT.sub('', _EMPHASIS.sub('', text))
