"""
Discord crew acknowledgment interaction handler and view factory.
"""

import logging

import discord

from application.utils.discord_messages import msg_unexpected_error
from application.utils.discord_messages_crew import crew_ack_confirmation
from discordbot._ack_common import (
    DMInteractionError,
    SendFn,
    make_acknowledged_view,
    run_dm_interaction,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'crew_ack'


def make_crew_acknowledgment_view(crew_type: str, crew_id: int) -> discord.ui.View:
    """Create a Discord View with an Acknowledge button for a crew assignment."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Acknowledge',
        style=discord.ButtonStyle.success,
        custom_id=f'{CUSTOM_ID_PREFIX}:{crew_type}:{crew_id}',
    ))
    return view


def _parse(custom_id: str) -> tuple[str, int]:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] not in ('commentator', 'tracker'):
        raise DMInteractionError('Invalid interaction.')
    crew_type = parts[1]
    try:
        crew_id = int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid crew ID.') from None
    return crew_type, crew_id


async def handle_crew_acknowledgment_interaction(interaction: discord.Interaction) -> None:
    """
    Handle a crew_ack button press from a Discord DM.

    custom_id format: 'crew_ack:<crew_type>:<crew_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import CrewService, MatchService
    from discordbot._tenant import crew_scope

    async def resolve_tenant(parsed: tuple[str, int]):
        crew_type, crew_id = parsed
        return await crew_scope(crew_id, crew_type)

    async def handle(inter: discord.Interaction, parsed, user, send: SendFn) -> None:
        crew_type, crew_id = parsed
        crew_member = await CrewService().acknowledge_crew_assignment(crew_id, crew_type, user)
        player_names = await MatchService().get_player_names(crew_member.match_id)

        try:
            await inter.message.edit(view=make_acknowledged_view(CUSTOM_ID_PREFIX))
        except Exception:
            logger.warning("Could not disable crew_ack button (crew_id=%s)", crew_id)

        await send(crew_ack_confirmation(crew_type, player_names))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Crew assignment not found.',
        handle=handle,
        unexpected_error_message=msg_unexpected_error('acknowledge'),
    )
