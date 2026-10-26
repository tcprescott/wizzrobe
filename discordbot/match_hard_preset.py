"""Discord hard-preset opt-in interaction handler and view factory.

The DM carries exactly one button, chosen from the reader's own current answer:
"Play <preset>" for someone who is out, "Back out" for someone who is in. It
never carries both, and it never renders a state, because a message that could
show "waiting on your opponent" would be the leak the whole feature exists to
prevent.

Replies are ephemeral for the same reason, and the pressed message's button is
swapped for its opposite so a second press is the way back rather than a repeat.
"""

import logging

import discord

from application.utils.discord_messages import (
    hard_preset_opt_in_confirmation,
    hard_preset_withdraw_confirmation,
    msg_unexpected_error,
)
from discordbot._ack_common import (
    DMInteractionError,
    SendFn,
    run_dm_interaction,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'match_hard'


def make_hard_preset_view(match_id: int, opted_in: bool = False) -> discord.ui.View:
    """A single button: opt in, or back out, whichever the reader can do next."""
    view = discord.ui.View(timeout=None)
    if opted_in:
        view.add_item(discord.ui.Button(
            label='Back out',
            style=discord.ButtonStyle.secondary,
            custom_id=f'{CUSTOM_ID_PREFIX}:out:{match_id}',
        ))
    else:
        view.add_item(discord.ui.Button(
            label='Play the harder preset',
            style=discord.ButtonStyle.primary,
            custom_id=f'{CUSTOM_ID_PREFIX}:in:{match_id}',
        ))
    return view


def _parse(custom_id: str) -> tuple:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] not in ('in', 'out'):
        raise DMInteractionError('Invalid interaction.')
    try:
        return parts[1], int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None


async def handle_hard_preset_interaction(interaction: discord.Interaction) -> None:
    """
    Handle a match_hard button press from a Discord DM.

    custom_id format: 'match_hard:in:<match_id>' / 'match_hard:out:<match_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import MatchHardPresetService
    from discordbot._tenant import match_scope

    async def resolve_tenant(parsed):
        _, match_id = parsed
        return await match_scope(match_id)

    async def handle(inter: discord.Interaction, parsed, user, send: SendFn) -> None:
        action, match_id = parsed
        service = MatchHardPresetService()

        if action == 'in':
            state = await service.opt_in(match_id, user)
            message = hard_preset_opt_in_confirmation(
                state.preset_name, everyone_in=state.everyone_in,
            )
        else:
            state = await service.withdraw(match_id, user)
            message = hard_preset_withdraw_confirmation(state.preset_name)

        # Rebuild the link button alongside the swapped action button: the DM
        # stays the live surface after a press, and ``send_dm`` composed the two
        # together, so editing in the bare action view would strip the route to
        # the web control this message exists to offer.
        from application.services import notification_links
        from application.services.discord.discord_service import _with_link_button

        view = _with_link_button(
            make_hard_preset_view(match_id, opted_in=state.opted_in),
            await notification_links.player_hard_preset(match_id),
        )
        try:
            await inter.message.edit(view=view)  # type: ignore[union-attr]
        except Exception:
            logger.warning("Could not update match_hard button (match_id=%s)", match_id)

        await send(message)

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Match not found.',
        handle=handle,
        unexpected_error_message=msg_unexpected_error('change your settings'),
    )
