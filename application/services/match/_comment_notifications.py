"""DMs about match comments: to players from staff, and to staff from a player.

Enqueued on ``discord_queue`` by ``MatchCommentService``. Everything it needs is
passed in, because the queue worker awaits it outside the enqueuing request's
tenant scope.
"""

import logging
from datetime import datetime
from typing import Optional, Sequence, Tuple

from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import (
    PLAYER_COMMENT,
    PLAYERS,
    STAFF_COMMENT,
    NoticeTarget,
)
from application.utils.discord_embeds import COLOR_RESCHEDULED, match_embed
from application.utils.discord_messages import (
    match_comment_dm,
    player_comment_staff_dm,
    player_late_staff_dm,
)
from models import NotificationRecipient, User

logger = logging.getLogger(__name__)

# Discord caps an embed description at 4096 characters; the rest of the comment
# is one tap away on the match.
_BODY_LIMIT = 1500


async def notify_match_comment(
    *,
    match_id: int,
    tournament_name: str,
    author_name: str,
    body: str,
    when: Optional[datetime],
    player_names: Sequence[str],
    recipients: Sequence[User],
) -> None:
    """DM each recipient the comment. Never raises; failures are logged."""
    from application.services import notification_links

    try:
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT].rstrip() + '…'
        message = match_comment_dm(author_name, tournament_name, body)
        embed = match_embed(
            title='💬 Staff comment on your match',
            color=COLOR_RESCHEDULED,
            description=message,
            tournament=tournament_name,
            player_names=list(player_names),
            when=when,
        )
        link = await notification_links.player_match_comments(match_id)
        discord_service = DiscordService()
        for user in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=STAFF_COMMENT.to(PLAYERS),
            )
            if not success:
                logger.warning("notify_match_comment DM failed for %s: %s", user.discord_id, err)
    except Exception:
        logger.exception("notify_match_comment unexpected error for match %s", match_id)


async def notify_staff_of_player_comment(
    *,
    match_id: int,
    tournament_name: str,
    author_name: str,
    body: str,
    when: Optional[datetime],
    player_names: Sequence[str],
    recipients: Sequence[Tuple[User, NotificationRecipient]],
) -> None:
    """DM the match's alert recipients a player's comment. Never raises."""
    from application.services import notification_links

    try:
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT].rstrip() + '…'
        message = player_comment_staff_dm(author_name, tournament_name, body)
        embed = match_embed(
            title='💬 Player comment',
            color=COLOR_RESCHEDULED,
            description=message,
            tournament=tournament_name,
            player_names=list(player_names),
            when=when,
        )
        link = await notification_links.admin_match_comments(match_id)
        discord_service = DiscordService()
        for user, why in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=PLAYER_COMMENT.to(why),
            )
            if not success:
                logger.warning("notify_staff_of_player_comment DM failed for %s: %s", user.discord_id, err)
    except Exception:
        logger.exception("notify_staff_of_player_comment unexpected error for match %s", match_id)


async def notify_staff_of_player_late(
    *,
    match_id: int,
    tournament_name: str,
    player_name: str,
    minutes: int,
    note: Optional[str],
    when: Optional[datetime],
    player_names: Sequence[str],
    recipients: Sequence[Tuple[User, NoticeTarget]],
) -> None:
    """DM each recipient, under its notice, that a player is running late. Never raises."""
    from application.services import notification_links

    try:
        message = player_late_staff_dm(player_name, tournament_name, minutes, note)
        embed = match_embed(
            title='🕒 Player running late',
            color=COLOR_RESCHEDULED,
            description=message,
            tournament=tournament_name,
            player_names=list(player_names),
            when=when,
        )
        link = await notification_links.admin_match(match_id)
        discord_service = DiscordService()
        for user, notice in recipients:
            if not user.discord_id:
                continue
            success, err = await discord_service.send_dm(
                user.discord_id, message, embed=embed, link=link, notice=notice,
            )
            if not success:
                logger.warning("notify_staff_of_player_late DM failed for %s: %s", user.discord_id, err)
    except Exception:
        logger.exception("notify_staff_of_player_late unexpected error for match %s", match_id)
