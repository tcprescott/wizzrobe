"""The Ready for seed button on a player's check-in DM and opponent-ready nudge.

custom_id formats:
  'seed_ready:in:<match_id>'   mark ready (the last one releases the seed)
  'seed_ready:out:<match_id>'  take Ready back

The pressed message's button flips to its opposite, so a second press undoes
the first, and the reply is ephemeral.
"""

import logging
from typing import Any

import discord

from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction
from discordbot._tenant import match_scope

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'seed_ready'

MSG_UNEXPECTED_ERROR = (
    "Something went wrong marking you ready. Please try again, or use Ready for "
    "seed on My Schedule."
)


def make_seed_ready_view(match_id: int, ready: bool = False) -> discord.ui.View:
    """One button: Ready for seed, or Not ready once they are."""
    view = discord.ui.View(timeout=None)
    if ready:
        view.add_item(discord.ui.Button(
            label='Not ready', style=discord.ButtonStyle.secondary,
            custom_id=f'{CUSTOM_ID_PREFIX}:out:{match_id}',
        ))
    else:
        view.add_item(discord.ui.Button(
            label='Ready for seed', emoji='🎲', style=discord.ButtonStyle.success,
            custom_id=f'{CUSTOM_ID_PREFIX}:in:{match_id}',
        ))
    return view


def _parse(custom_id: str) -> tuple[str, int]:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] not in ('in', 'out'):
        raise DMInteractionError('Invalid interaction.')
    try:
        return parts[1], int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None


async def handle_seed_ready_interaction(interaction: discord.Interaction) -> None:
    from application.services import SeedReadyService, notification_links
    from application.services.discord.discord_service import _with_link_button

    async def handle(inter: discord.Interaction, parsed: tuple[str, int], user: Any, send: SendFn) -> None:
        action, match_id = parsed
        service = SeedReadyService()
        if action == 'in':
            result = await service.set_ready(match_id, user)
        else:
            result = await service.set_not_ready(match_id, user)
        if result.released:
            view = _with_link_button(
                discord.ui.View(timeout=None), await notification_links.player_match(match_id),
            )
        else:
            view = _with_link_button(
                make_seed_ready_view(match_id, ready=result.ready),
                await notification_links.player_match(match_id),
            )
        try:
            await inter.message.edit(view=view)  # type: ignore[union-attr]
        except Exception:
            logger.warning("Could not update seed_ready button (match_id=%s)", match_id)
        await send(result.message)

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=lambda parsed: match_scope(parsed[1]),
        not_found_message='That match no longer exists.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )
