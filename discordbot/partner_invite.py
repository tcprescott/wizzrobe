"""
Discord commentary partner invite: Decline and Accept on the partner's DM.

Accepting signs the partner up as commentator through the ordinary crew signup,
so staff still approve them, and the reply says so.
"""

import discord

from application.utils.discord_messages import msg_unexpected_error
from application.utils.discord_messages_crew import partner_invite_reply
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction

CUSTOM_ID_PREFIX = 'partner_invite'


def make_partner_invite_view(invite_id: int) -> discord.ui.View:
    """Create a Discord View with Decline and Accept for a commentary partner invite."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Decline',
        style=discord.ButtonStyle.secondary,
        custom_id=f'{CUSTOM_ID_PREFIX}:decline:{invite_id}',
    ))
    view.add_item(discord.ui.Button(
        label='Accept',
        style=discord.ButtonStyle.success,
        custom_id=f'{CUSTOM_ID_PREFIX}:accept:{invite_id}',
    ))
    return view


def _parse(custom_id: str) -> tuple[bool, int]:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] not in ('accept', 'decline'):
        raise DMInteractionError('Invalid interaction.')
    try:
        return parts[1] == 'accept', int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid invite ID.') from None


async def handle_partner_invite_interaction(interaction: discord.Interaction) -> None:
    """
    Handle Accept or Decline on a commentary partner invite DM.

    custom_id format: 'partner_invite:<accept|decline>:<invite_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import CommentaryInviteService
    from discordbot._tenant import commentary_invite_scope

    async def resolve_tenant(parsed: tuple[bool, int]):
        return await commentary_invite_scope(parsed[1])

    async def handle(_interaction, parsed, user, send: SendFn) -> None:
        accept, invite_id = parsed
        invite = await CommentaryInviteService().respond(invite_id, user, accept)
        await send(partner_invite_reply(accept, invite.inviter.preferred_name))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='That invite is no longer around.',
        handle=handle,
        unexpected_error_message=msg_unexpected_error('answer the invite'),
    )
