"""
Discord reschedule-agreement interaction handler and view factory.

Renders an Agree button on the DM sent to the other player when someone asks
staff to move or call off their shared match. Pressing it records agreement and
nothing else: staff still decide, and the confirmation says so, because a button
that reads like a decision and turns out to be advice is worse than no button.
"""

import discord

from application.utils.discord_messages import MSG_UNEXPECTED_ERROR
from application.utils.discord_messages_reschedule import reschedule_agree_confirmation
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction

CUSTOM_ID_PREFIX = 'reschedule_agree'


def make_reschedule_agree_view(request_id: int) -> discord.ui.View:
    """Create a Discord View with an Agree button for a reschedule request."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Agree',
        style=discord.ButtonStyle.success,
        custom_id=f'{CUSTOM_ID_PREFIX}:agree:{request_id}',
    ))
    return view


def _parse(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] != 'agree':
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid request ID.') from None


async def handle_reschedule_agree_interaction(interaction: discord.Interaction) -> None:
    """
    Handle an Agree button press from a Discord DM.

    custom_id format: 'reschedule_agree:agree:<request_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import MatchRescheduleService
    from discordbot._tenant import reschedule_request_scope

    async def resolve_tenant(request_id: int):
        return await reschedule_request_scope(request_id)

    async def handle(_interaction, request_id: int, user, send: SendFn) -> None:
        request = await MatchRescheduleService().record_opponent_agreement(request_id, user)
        await send(reschedule_agree_confirmation(request.requested_by.preferred_name))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='That request is no longer around.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )
