"""The stage reminder DM's Tell staff I'm late button, for the match's players.

custom_id formats:
  'match_late:<match_id>'                ask how late (ephemeral choice)
  'match_late_min:<match_id>:<minutes>'  record it and tell staff on duty
"""

from typing import Any

import discord

from application.utils.discord_messages_volunteer import LATE_CHOICES, late_label
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction
from discordbot._tenant import match_tenant_id

LATE_PREFIX = 'match_late'
LATE_MINUTES_PREFIX = 'match_late_min'

MSG_UNEXPECTED_ERROR = (
    "Something went wrong telling staff. Please try again, or use Tell staff "
    "I'm late on My Schedule."
)


def make_player_late_view(match_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Tell staff I'm late", style=discord.ButtonStyle.secondary,
        custom_id=f'{LATE_PREFIX}:{match_id}',
    ))
    return view


def _parse_id(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 2:
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[1])
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None


def _parse_late(custom_id: str) -> tuple[int, int]:
    parts = custom_id.split(':')
    if len(parts) != 3:
        raise DMInteractionError('Invalid interaction.')
    try:
        match_id, minutes = int(parts[1]), int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid interaction.') from None
    if minutes not in LATE_CHOICES:
        raise DMInteractionError('Invalid interaction.')
    return match_id, minutes


async def _run(interaction: discord.Interaction, *, log_label: str, parse, handle) -> None:
    await run_dm_interaction(
        interaction,
        log_label=log_label,
        parse=parse,
        resolve_tenant=lambda parsed: match_tenant_id(parsed[0] if isinstance(parsed, tuple) else parsed),
        not_found_message='That match no longer exists.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )


async def handle_player_late_interaction(interaction: discord.Interaction) -> None:
    """Offer the how-late choice as an ephemeral row of buttons."""
    async def handle(inter: discord.Interaction, match_id: int, user: Any, send: SendFn) -> None:
        view = discord.ui.View(timeout=None)
        for minutes in LATE_CHOICES:
            view.add_item(discord.ui.Button(
                label=late_label(minutes), style=discord.ButtonStyle.primary,
                custom_id=f'{LATE_MINUTES_PREFIX}:{match_id}:{minutes}',
            ))
        await inter.followup.send('About how late will you be?', view=view, ephemeral=True)

    await _run(interaction, log_label=LATE_PREFIX, parse=_parse_id, handle=handle)


async def handle_player_late_minutes_interaction(interaction: discord.Interaction) -> None:
    from application.services import PlayerLateService

    async def handle(inter: discord.Interaction, parsed: tuple[int, int], user: Any, send: SendFn) -> None:
        match_id, minutes = parsed
        await PlayerLateService().report_late(match_id, user, minutes)
        await send(f"Thanks. Staff on duty know you're running late "
                   f"({late_label(minutes).lower()}).")

    await _run(interaction, log_label=LATE_MINUTES_PREFIX, parse=_parse_late, handle=handle)
