"""The settings footnote every catalogued notification ends with."""

import copy
from typing import NamedTuple

import discord

from application.utils.discord_messages import DMLink


class SettingsFootnote(NamedTuple):
    """The "change how you get these" line at the bottom of a catalogued DM.

    It names the message, so the reader knows which row to change, and links
    Profile → Notifications. Discord shows it inside the card, email in its
    footer and Telegram on the last line; device notifications go without it,
    having no room and already opening the message's own action.
    """

    title: str
    link: DMLink

    @property
    def lead(self) -> str:
        return f'Change how you get “{self.title}” messages:'

    def markdown(self) -> str:
        return f'{self.lead} [{self.link.label}]({self.link.url})'

    def plain(self) -> str:
        return f'{self.lead} {self.link.url}'

    def with_message(self, message: str) -> str:
        """``message`` with the footnote as small text under it (no embed)."""
        return f'{message}\n-# {self.lead} [{self.link.label}](<{self.link.url}>)'

    def with_embed(self, embed: discord.Embed) -> discord.Embed:
        """A copy of ``embed`` with the footnote as its last line, above the footer."""
        # Embed.copy() is shallow and shares the fields list, so adding to it
        # stacked one footnote per recipient onto a card reused across a fan-out.
        card = discord.Embed.from_dict(copy.deepcopy(embed.to_dict()))
        card.add_field(name='\u200b', value=self.markdown(), inline=False)
        return card
