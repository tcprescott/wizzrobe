"""Old hard-preset DM buttons, kept answering after hard mode moved onto the match form.

Hard mode is a hidden Yes/No question on the match form now (migration 170).
DMs sent before that still carry "Play the harder preset" / "Back out"
buttons, and a dead button reads as a broken app, so a press says where the
question went and links to it. The view factory stays registered so a DM
queued under the old view before a deploy still sends.
"""

import logging

import discord

from application.utils.discord_messages import msg_unexpected_error
from discordbot._ack_common import (
    DMInteractionError,
    SendFn,
    run_dm_interaction,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'match_hard'


def make_hard_preset_view(match_id: int, opted_in: bool = False) -> discord.ui.View:
    """An empty view: the DM's link button carries the way to the question."""
    return discord.ui.View(timeout=None)


def _parse(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 3:
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None


async def handle_hard_preset_interaction(interaction: discord.Interaction) -> None:
    """A press on an old ``match_hard:in|out:<match_id>`` button: point at the match form."""
    from discordbot._tenant import match_scope

    async def resolve_tenant(match_id):
        return await match_scope(match_id)

    async def handle(inter: discord.Interaction, match_id, user, send: SendFn) -> None:
        from application.services import notification_links

        link = await notification_links.player_match_form(match_id)
        where = f' Answer it here: {link.url}' if link is not None else ''
        await send(
            "Hard mode is a question on this match's form now, under Before your match."
            f"{where}"
        )

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Match not found.',
        handle=handle,
        unexpected_error_message=msg_unexpected_error('open your match questions'),
    )
