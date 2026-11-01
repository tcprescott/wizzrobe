"""Whether the shared Discord client can make calls right now.

A process either holds the gateway connection (today's single process, or the
bot service) or only signs in for REST (the API service with
``DISCORD_GATEWAY=off``, which gets button presses over HTTP instead). DMs,
message edits and guild reads all go over REST, so both count as usable.
"""

from typing import Any, Optional

from application.utils.environment import discord_gateway_enabled


def bot_ready(bot: Optional[Any]) -> bool:
    if bot is None:
        return False
    if bot.is_ready():
        return True
    return not discord_gateway_enabled() and bot.user is not None and not bot.is_closed()
