"""Posting in a community's own Discord channel (not a DM).

Mixed into ``DiscordService``; ``MockDiscordService`` takes the mock twin.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Union

import discord

from application.services.discord import dm_outbox
from application.services.discord.bot_state import bot_ready
from application.utils.discord_messages import DMLink

logger = logging.getLogger(__name__)


class ChannelPostMixin:
    _bot: Optional[discord.Client]

    async def list_text_channels(self, guild_id: int) -> Tuple[bool, Union[List[Dict[str, Union[int, str]]], str]]:
        """The server's text and announcement channels, in Discord's order: ``[{id, name}]``."""
        if self._bot is None or not bot_ready(self._bot):
            return False, dm_outbox.DM_BOT_OFFLINE
        try:
            guild = self._bot.get_guild(guild_id) or await self._bot.fetch_guild(guild_id)
            channels = await asyncio.wait_for(guild.fetch_channels(), timeout=10)
        except Exception as exc:
            logger.warning('listing channels of guild %s failed: %s', guild_id, exc)
            return False, "Couldn't read the Discord server's channels"
        text = sorted(
            (c for c in channels if isinstance(c, discord.TextChannel)),
            key=lambda c: (c.category.position if c.category else -1, c.position),
        )
        return True, [{'id': c.id, 'name': c.name} for c in text]

    async def post_to_channel(
        self,
        guild_id: int,
        channel_id: int,
        message: str,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        ping_role_id: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Post ``message`` (and ``embed``, link button) in a channel of ``guild_id``.

        Refuses a channel outside that server, so a community can only post in
        its own Discord. Mentions are limited to ``ping_role_id``.
        """
        if self._bot is None or not bot_ready(self._bot):
            return False, dm_outbox.DM_BOT_OFFLINE
        try:
            async def _send():
                channel = self._bot.get_channel(channel_id) or await self._bot.fetch_channel(channel_id)
                if getattr(getattr(channel, 'guild', None), 'id', None) != guild_id:
                    return None
                view = None
                if link is not None and link.url:
                    view = discord.ui.View(timeout=None)
                    view.add_item(discord.ui.Button(label=link.label, style=discord.ButtonStyle.link, url=link.url))
                kwargs: dict = {
                    'allowed_mentions': discord.AllowedMentions(
                        everyone=False, users=False,
                        roles=[discord.Object(id=ping_role_id)] if ping_role_id else False,
                    ),
                }
                if view is not None:
                    kwargs['view'] = view
                if embed is not None:
                    kwargs['embed'] = embed
                return await channel.send(message, **kwargs)

            sent = await asyncio.wait_for(_send(), dm_outbox.DM_SEND_TIMEOUT_SECONDS)
            if sent is None:
                return False, "That channel isn't in this community's Discord server."
            return True, "Message sent successfully."
        except asyncio.TimeoutError:
            return False, dm_outbox.DM_TIMED_OUT
        except discord.NotFound:
            return False, "That Discord channel doesn't exist any more."
        except discord.Forbidden:
            return False, "The bot can't post in that channel."
        except discord.HTTPException as e:
            logger.warning('Discord channel post to %s failed with HTTP %s: %s', channel_id, e.status, e)
            return False, f"Failed to post: {e!s}"
        except Exception as e:
            logger.exception('Discord channel post to %s crashed', channel_id)
            return False, f"Discord bot error: {e!s}"



class MockChannelPostMixin:
    async def list_text_channels(self, guild_id: int) -> Tuple[bool, Union[List[Dict[str, Union[int, str]]], str]]:
        return True, [
            {'id': 100000000000000001, 'name': 'daily-races'},
            {'id': 100000000000000003, 'name': 'qualifier-races'},
            {'id': 100000000000000004, 'name': 'general'},
        ]

    async def post_to_channel(
        self,
        guild_id: int,
        channel_id: int,
        message: str,
        embed: Optional["discord.Embed"] = None,
        link: Optional[DMLink] = None,
        ping_role_id: Optional[int] = None,
    ) -> Tuple[bool, str]:
        suffix = f" [embed: {embed.title}]" if embed is not None else ""
        if link is not None and link.url:
            suffix += f" [button: {link.label} -> {link.url}]"
        print(f"[MOCK Discord channel] {guild_id}/{channel_id}: {message}{suffix}")
        return True, "Message sent (mock)"
