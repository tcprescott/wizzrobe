"""The DM telling a match's players that staff forced its settings, or handed them back.

Enqueued on ``discord_queue`` from ``MatchPresetOverrideService``. Everything it needs
is passed in, because the queue worker awaits it outside the enqueuing request's
tenant scope.
"""

import logging
from datetime import datetime
from typing import Optional, Sequence

from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import (
    PLAYERS,
    SETTINGS_RELEASED,
    SETTINGS_SET_BY_STAFF,
)
from application.utils.discord_embeds import COLOR_SEED, match_embed
from application.utils.discord_messages import (
    preset_override_dm,
)
from models import User

logger = logging.getLogger(__name__)


async def notify_preset_override(
    *,
    match_id: int,
    tournament_name: str,
    preset_name: str,
    when: Optional[datetime],
    recipients: Sequence[User],
    forced: bool,
) -> None:
    """Tell a match's players that staff chose its settings, or handed them back.

    Never raises; per-DM failures are logged and swallowed.
    """
    from application.services import notification_links

    try:
        discord_service = DiscordService()
        message = preset_override_dm(tournament_name, preset_name, forced=forced)
        embed = match_embed(
            title='⚙️ Settings set by staff' if forced else '⚙️ Settings back in your hands',
            color=COLOR_SEED,
            description=message,
            tournament=tournament_name,
            player_names=[u.preferred_name for u in recipients],
            when=when,
        )
        # A forced match has nothing left to answer; a released one sends players back to its questions.
        link = (
            await notification_links.player_match(match_id) if forced
            else await notification_links.player_match_form(match_id)
        )
        for user in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=(SETTINGS_SET_BY_STAFF if forced else SETTINGS_RELEASED).to(PLAYERS),
            )
            if not success:
                logger.warning(
                    "notify_preset_override DM failed for %s: %s", user.discord_id, err
                )
    except Exception:
        logger.exception(
            "notify_preset_override unexpected error for match %s", match_id
        )
