"""
Discord unwatch interaction handler and view factory.

Renders an Unwatch button on lifecycle DMs sent to match watchers so they
can opt out of further notifications without leaving Discord.
"""

import discord

from application.utils.discord_messages import MSG_UNEXPECTED_ERROR, unwatch_confirmation
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction

CUSTOM_ID_PREFIX = 'match_watch'


def make_unwatch_view(match_id: int) -> discord.ui.View:
    """Create a Discord View with an Unwatch button for a match."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Unwatch',
        style=discord.ButtonStyle.danger,
        custom_id=f'{CUSTOM_ID_PREFIX}:unwatch:{match_id}',
    ))
    return view


def _parse(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] != 'unwatch':
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None


async def handle_unwatch_interaction(interaction: discord.Interaction) -> None:
    """
    Handle an Unwatch button press from a Discord DM.

    custom_id format: 'match_watch:unwatch:<match_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import MatchService, MatchWatcherService
    from discordbot._tenant import match_scope

    async def resolve_tenant(match_id: int):
        return await match_scope(match_id)

    async def handle(_interaction, match_id: int, user, send: SendFn) -> None:
        removed = await MatchWatcherService().unwatch(match_id, user)
        player_names = await MatchService().get_player_names(match_id)
        await send(unwatch_confirmation(player_names, was_watching=removed))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Match not found.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )
