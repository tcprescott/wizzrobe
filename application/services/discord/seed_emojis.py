"""Seed codes drawn as the bot's own Discord emojis.

The emojis are uploaded by hand to the bot's application (Developer Portal →
the app → Emojis), named ``<set>_<icon>`` (``alttpr_hookshot``, ``dk64_bongos``),
and matched by that name. Nothing is configured in Wizzrobe: an icon without
an emoji is written as its word, so a half-finished upload still reads.

Only games that show pictures on their file select get a set. The Super
Metroid family shows enemy names as words, which the text line already is.
"""

import asyncio
import logging
import re
import time
from typing import Optional

from discord.ext import commands

from application.services._seedgen_codes import ALTTPR_CODE_NAMES, DK64R_CODE_NAMES
from application.services.discord.bot_state import bot_ready

logger = logging.getLogger(__name__)

OOTR_CODE_NAMES = (
    'Deku Stick', 'Deku Nut', 'Bow', 'Slingshot', 'Fairy Ocarina', 'Bombchu',
    'Longshot', 'Boomerang', 'Lens of Truth', 'Beans', 'Megaton Hammer',
    'Bottled Fish', 'Bottled Milk', 'Mask of Truth', 'SOLD OUT', 'Cucco',
    'Mushroom', 'Saw', 'Frog', 'Master Sword', 'Mirror Shield', 'Kokiri Tunic',
    'Hover Boots', 'Silver Gauntlets', 'Gold Scale', 'Stone of Agony',
    'Skull Token', 'Heart Container', 'Boss Key', 'Compass', 'Map', 'Big Magic',
)

EMOJI_SETS: dict[str, tuple[str, tuple[str, ...]]] = {
    'alttpr': ('alttpr', ALTTPR_CODE_NAMES),
    'avianart': ('alttpr', ALTTPR_CODE_NAMES),
    'dk64r': ('dk64', DK64R_CODE_NAMES),
    'ootr': ('ootr', OOTR_CODE_NAMES),
}

# The roll waits on this, so a slow Discord costs the icons, not the DM.
FETCH_TIMEOUT_SECONDS = 3

REFRESH_SECONDS = 300

_emojis: dict[str, str] = {}
_fetched_at: Optional[float] = None


def emoji_name(prefix: str, icon: str) -> str:
    """The name the emoji for ``icon`` must be uploaded under."""
    slug = re.sub(r'[^a-z0-9]+', '_', icon.lower()).strip('_')
    return f'{prefix}_{slug}'[:32]


def expected_names() -> dict[str, list[str]]:
    """Every emoji name, by set, in the game's own order."""
    sets: dict[str, list[str]] = {}
    for prefix, names in EMOJI_SETS.values():
        sets.setdefault(prefix, [emoji_name(prefix, n) for n in names])
    return sets


async def _app_emojis(bot: Optional[commands.Bot]) -> dict[str, str]:
    global _emojis, _fetched_at
    if not bot_ready(bot):
        return _emojis
    if _fetched_at is not None and time.monotonic() - _fetched_at < REFRESH_SECONDS:
        return _emojis
    _fetched_at = time.monotonic()
    try:
        fetched = await asyncio.wait_for(bot.fetch_application_emojis(), FETCH_TIMEOUT_SECONDS)
    except Exception as e:
        # Keep the last good list: an outage should cost the icons at most.
        logger.info('Discord app emojis unavailable: %s', e)
        return _emojis
    _emojis = {e.name.lower(): str(e) for e in fetched}
    return _emojis


async def code_row(
    randomizer: str, code: Optional[list[str]], bot: Optional[commands.Bot],
) -> Optional[str]:
    """The code as one row of emojis, or ``None`` when none of it has one.

    An icon without an emoji keeps its word, so a missing upload is visible in
    the DM rather than a gap.
    """
    known = EMOJI_SETS.get(randomizer)
    if not code or known is None:
        return None
    emojis = await _app_emojis(bot)
    prefix = known[0]
    parts = [emojis.get(emoji_name(prefix, icon)) for icon in code]
    if not any(parts):
        return None
    return ' '.join(part or icon for part, icon in zip(parts, code, strict=True))


def reset_cache() -> None:
    global _emojis, _fetched_at
    _emojis, _fetched_at = {}, None
