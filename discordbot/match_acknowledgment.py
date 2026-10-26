"""
Discord match acknowledgment interaction handler and view factory.
"""

import logging

import discord

from application.utils.discord_messages import (
    match_ack_confirmation,
    msg_unexpected_error,
)
from discordbot._ack_common import (
    DMInteractionError,
    SendFn,
    make_acknowledged_view,
    run_dm_interaction,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'match_ack'


def make_match_acknowledgment_view(match_id: int) -> discord.ui.View:
    """Create a Discord View with an Acknowledge button for a match."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Acknowledge',
        style=discord.ButtonStyle.success,
        custom_id=f'{CUSTOM_ID_PREFIX}:ack:{match_id}',
    ))
    return view


def _parse(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] != 'ack':
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None


async def handle_match_acknowledgment_interaction(interaction: discord.Interaction) -> None:
    """
    Handle a match_ack button press from a Discord DM.

    custom_id format: 'match_ack:ack:<match_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import MatchService
    from discordbot._tenant import match_scope

    async def resolve_tenant(match_id: int):
        return await match_scope(match_id)

    async def handle(inter: discord.Interaction, match_id: int, user, send: SendFn) -> None:
        match_service = MatchService()
        await match_service.acknowledge_match(match_id, user)
        player_names = await match_service.get_player_names(match_id)

        try:
            await inter.message.edit(view=make_acknowledged_view(CUSTOM_ID_PREFIX))
        except Exception:
            logger.warning("Could not disable match_ack button (match_id=%s)", match_id)

        await send(match_ack_confirmation(player_names))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Match not found.',
        handle=handle,
        unexpected_error_message=msg_unexpected_error('acknowledge'),
    )
