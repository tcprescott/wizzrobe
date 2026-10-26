"""
Discord volunteer shift acknowledgment interaction handler and view factory.
"""

import logging

import discord

from application.utils.discord_messages_volunteer import volunteer_ack_confirmation
from discordbot._ack_common import (
    DMInteractionError,
    SendFn,
    make_acknowledged_view,
    run_dm_interaction,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'volunteer_ack'

MSG_UNEXPECTED_ERROR = (
    "Something went wrong acknowledging your shift. Please try again, or "
    "acknowledge it from the website."
)


def make_volunteer_acknowledgment_view(assignment_id: int) -> discord.ui.View:
    """Create a Discord View with an Acknowledge button for a volunteer shift."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Acknowledge',
        style=discord.ButtonStyle.success,
        custom_id=f'{CUSTOM_ID_PREFIX}:{assignment_id}',
    ))
    return view


def _parse(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 2:
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[1])
    except ValueError:
        raise DMInteractionError('Invalid assignment ID.') from None


async def handle_volunteer_acknowledgment_interaction(interaction: discord.Interaction) -> None:
    """Handle a volunteer_ack button press from a Discord DM.

    custom_id format: 'volunteer_ack:<assignment_id>'
    """
    from application.services import VolunteerScheduleService
    from discordbot._tenant import assignment_scope

    async def resolve_tenant(assignment_id: int):
        return await assignment_scope(assignment_id)

    async def handle(inter: discord.Interaction, assignment_id: int, user, send: SendFn) -> None:
        assignment = await VolunteerScheduleService().acknowledge(assignment_id, user)
        position_name = (
            assignment.shift.position.name
            if assignment.shift and assignment.shift.position else 'volunteer'
        )

        try:
            await inter.message.edit(view=make_acknowledged_view(CUSTOM_ID_PREFIX))
        except Exception:
            logger.warning("Could not disable volunteer_ack button (assignment_id=%s)", assignment_id)

        await send(volunteer_ack_confirmation(position_name))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Assignment not found.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )
