"""The DM telling a player their opponent is ready for the seed.

Enqueued on ``discord_queue`` by ``SeedReadyService``. Everything it needs is
passed in, because the queue worker awaits it outside the enqueuing request's
tenant scope.
"""

import logging
from datetime import datetime
from typing import Optional, Sequence

import discord

from application.services import notification_links
from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import OPPONENT_SEED_READY, PLAYERS
from application.utils.discord_embeds import COLOR_SEED, match_embed

logger = logging.getLogger(__name__)

#: How a player's check-in DM ends on a tournament where players release the seed.
SEED_READY_PROMPT = (
    "Tap **Ready for seed** once you're set up. The seed is sent to everyone "
    "in the match when you're all ready."
)


def _safe(text: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))


def opponent_seed_ready_dm(ready_name: str, tournament_name: str) -> str:
    """A player still to tap Ready, told another player in their match has.
    The name is the other player's own, so it is escaped."""
    return (
        f"**{_safe(ready_name)}** is ready for the seed in your **{tournament_name}** match.\n\n"
        "Tap **Ready for seed** when you are and it's sent to you all."
    )


async def notify_opponent_seed_ready(
    *,
    match_id: int,
    tournament_name: str,
    community_name: str,
    ready_name: str,
    player_names: Sequence[str],
    when: Optional[datetime],
    stage_name: Optional[str],
    recipient_ids: Sequence[int],
) -> None:
    """DM each player still to tap Ready, with the button. Never raises."""
    try:
        message = opponent_seed_ready_dm(ready_name, tournament_name)
        embed = match_embed(
            title=f'🎲 {discord.utils.escape_mentions(ready_name)} is ready for the seed',
            color=COLOR_SEED,
            description="Tap **Ready for seed** when you are and it's sent to you all.",
            tournament=tournament_name, community_name=community_name,
            player_names=list(player_names), when=when, stage_name=stage_name,
        )
        link = await notification_links.player_match(match_id)
        discord_service = DiscordService()
        for discord_id in recipient_ids:
            success, err = await discord_service.send_dm_with_seed_ready_button(
                discord_id, message, match_id, embed=embed, link=link,
                notice=OPPONENT_SEED_READY.to(PLAYERS),
            )
            if not success:
                logger.warning("notify_opponent_seed_ready DM failed for %s: %s", discord_id, err)
    except Exception:
        logger.exception("notify_opponent_seed_ready unexpected error for match %s", match_id)
