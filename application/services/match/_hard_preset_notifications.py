"""DMs for the hard-preset opt-in.

Three messages, each sent to a whole match at once:

* **invited** — the offer, when the match is scheduled. The only DM in the app
  whose button differs per recipient, so that it can carry each player's own
  next move without reporting anyone else's.
* **agreed / broken** — the moment everyone has opted in, and the moment a
  complete set breaks. Nothing is sent for an individual opt-in, because the
  feature turns on nobody hearing about those.
* **overridden** — staff choosing the match's settings themselves.

Free functions rather than a notifier class: each is a single fan-out with no
shared state, and they are enqueued on ``discord_queue`` from
``MatchHardPresetService``. Everything they need is passed in, because the queue
worker awaits them outside the enqueuing request's tenant scope.
"""

import logging
from datetime import datetime
from typing import Optional, Sequence

from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import (
    HARDER_SETTINGS_AGREED,
    HARDER_SETTINGS_DROPPED,
    HARDER_SETTINGS_OFFER,
    PLAYERS,
    SETTINGS_RELEASED,
    SETTINGS_SET_BY_STAFF,
)
from application.utils.discord_embeds import COLOR_RESCHEDULED, COLOR_SEED, match_embed
from application.utils.discord_messages import (
    hard_preset_agreed_dm,
    hard_preset_broken_dm,
    hard_preset_invite_dm,
    hard_preset_override_dm,
)
from models import User

logger = logging.getLogger(__name__)


async def notify_hard_preset_invite(
    *,
    match_id: int,
    tournament_name: str,
    preset_name: str,
    when: Optional[datetime],
    recipients: Sequence[User],
    opted_in_ids: Sequence[int],
) -> None:
    """Offer the harder preset to a match's players when it is scheduled.

    The one message that starts the whole thing, and the only DM in the app whose
    button differs per recipient: each player's carries their own next move, so
    nothing about anybody else's answer can be read off it. ``opted_in_ids``
    exists solely to pick that button and never reaches the text.

    Never raises; per-DM failures are logged and swallowed.
    """
    from application.services import notification_links

    try:
        discord_service = DiscordService()
        message = hard_preset_invite_dm(tournament_name, preset_name)
        embed = match_embed(
            title='⚡ Harder settings available',
            color=COLOR_SEED,
            description=message,
            tournament=tournament_name,
            player_names=[u.preferred_name for u in recipients],
            when=when,
        )
        link = await notification_links.player_hard_preset(match_id)
        already_in = set(opted_in_ids)
        for user in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm_with_hard_preset_buttons(
                user.discord_id, message, match_id, user.id in already_in,
                embed=embed, link=link, notice=HARDER_SETTINGS_OFFER.to(PLAYERS),
            )
            if not success:
                logger.warning(
                    "notify_hard_preset_invite DM failed for %s: %s", user.discord_id, err
                )
    except Exception:
        logger.exception("notify_hard_preset_invite unexpected error for match %s", match_id)


async def notify_hard_preset_agreement(
    *,
    match_id: int,
    tournament_name: str,
    preset_name: str,
    standard_preset_name: str,
    when: Optional[datetime],
    recipients: Sequence[User],
    agreed: bool,
    actor_name: str,
) -> None:
    """Tell a match's players that the harder preset is on, or off again.

    Never raises; per-DM failures are logged and swallowed.
    """
    from application.services import notification_links

    try:
        discord_service = DiscordService()
        if agreed:
            message = hard_preset_agreed_dm(tournament_name, preset_name)
            title = '🔒 Harder settings agreed'
            color = COLOR_SEED
        else:
            message = hard_preset_broken_dm(
                tournament_name, preset_name, standard_preset_name, actor_name,
            )
            title = '↩️ Back to the standard settings'
            # Not the stream gold on purpose: these players also get stream-candidate DMs.
            color = COLOR_RESCHEDULED
        embed = match_embed(
            title=title,
            color=color,
            description=message,
            tournament=tournament_name,
            player_names=[u.preferred_name for u in recipients],
            when=when,
        )
        link = await notification_links.player_match(match_id)
        for user in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=(HARDER_SETTINGS_AGREED if agreed else HARDER_SETTINGS_DROPPED).to(PLAYERS),
            )
            if not success:
                logger.warning(
                    "notify_hard_preset_agreement DM failed for %s: %s", user.discord_id, err
                )
    except Exception:
        logger.exception(
            "notify_hard_preset_agreement unexpected error for match %s", match_id
        )


async def notify_hard_preset_override(
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
        message = hard_preset_override_dm(tournament_name, preset_name, forced=forced)
        embed = match_embed(
            title='⚙️ Settings set by staff' if forced else '⚙️ Settings back in your hands',
            color=COLOR_SEED,
            description=message,
            tournament=tournament_name,
            player_names=[u.preferred_name for u in recipients],
            when=when,
        )
        # No settings link for a forced match: the dialog would refuse them.
        link = (
            await notification_links.player_match(match_id) if forced
            else await notification_links.player_hard_preset(match_id)
        )
        for user in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=(SETTINGS_SET_BY_STAFF if forced else SETTINGS_RELEASED).to(PLAYERS),
            )
            if not success:
                logger.warning(
                    "notify_hard_preset_override DM failed for %s: %s", user.discord_id, err
                )
    except Exception:
        logger.exception(
            "notify_hard_preset_override unexpected error for match %s", match_id
        )
