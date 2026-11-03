"""Staff DMs about a request someone has to decide, and the edit once someone has.

Every staff member gets the DM, so without the edit they all open the request
to check whether it still needs them. :func:`send` records each copy Discord
accepts, and :func:`mark_handled` rewrites every recorded copy to say who
decided it. The edit doesn't ping anyone; staff see it when they next look.

Both go through ``discord_queue``, whose one worker runs in order, so an edit
queued after a send always finds that send's record. Everything here is
best-effort: a Discord failure never undoes the decision that prompted it.
"""

import logging
from typing import Callable, Iterable, Optional, Tuple, Union

import discord

from application.repositories.staff_request_dm_repository import StaffRequestDMRepository
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import (
    ADA_REQUEST,
    BROADCASTER_SIGNUP,
    COMMENTATOR_SIGNUP,
    EVENT_TO_REVIEW,
    JOIN_REQUEST,
    RESCHEDULE_REQUEST,
    STAFF,
    TRACKER_SIGNUP,
    Notice,
)
from application.utils.discord_messages import DMLink
from models import NotificationRecipient, StaffRequestKind, User

logger = logging.getLogger(__name__)

CardBuilder = Callable[[bool], discord.Embed]

_NOTICES: dict[StaffRequestKind, Notice] = {
    StaffRequestKind.JOIN_REQUEST: JOIN_REQUEST,
    StaffRequestKind.ADA_REQUEST: ADA_REQUEST,
    StaffRequestKind.COMMUNITY_EVENT: EVENT_TO_REVIEW,
    StaffRequestKind.RESCHEDULE_REQUEST: RESCHEDULE_REQUEST,
    StaffRequestKind.COMMENTATOR_SIGNUP: COMMENTATOR_SIGNUP,
    StaffRequestKind.TRACKER_SIGNUP: TRACKER_SIGNUP,
    StaffRequestKind.BROADCASTER_SIGNUP: BROADCASTER_SIGNUP,
}


async def send(
    kind: StaffRequestKind,
    object_id: int,
    recipients: Iterable[Union[User, Tuple[User, NotificationRecipient]]],
    message: str,
    *,
    embed: discord.Embed,
    link: Optional[DMLink],
    notice: Optional[Notice] = None,
) -> None:
    """Queue the DM to each recipient, remembering every copy that lands.

    A new round (a resubmitted event, an ADA request asked again) starts over:
    the copies from an earlier round keep the outcome they were edited to.
    A bare user is told as all staff; a ``(user, recipient)`` pair (from
    ``StaffAlertService``) says which notification setting governs their copy.
    ``notice`` overrides the kind's own setting for a variant (a changed ADA
    request).
    """
    await StaffRequestDMRepository.forget(kind, object_id)
    service = DiscordService()
    notice = notice or _NOTICES[kind]
    for entry in recipients:
        user, why = entry if isinstance(entry, tuple) else (entry, STAFF)
        if user.discord_id:
            discord_queue.enqueue(service.send_dm(
                int(user.discord_id), message, embed=embed, link=link,
                on_sent=_recorder(kind, object_id, user.id), notice=notice.to(why),
            ))


def _recorder(kind: StaffRequestKind, object_id: int, user_id: int):
    async def record(channel_id: int, message_id: int) -> None:
        await StaffRequestDMRepository.record(kind, object_id, user_id, channel_id, message_id)
    return record


def mark_handled(
    kind: StaffRequestKind,
    object_id: int,
    build: CardBuilder,
    link: Optional[DMLink],
    *,
    actor_id: Optional[int] = None,
) -> None:
    """Queue the edit of every copy of the DM about this request.

    ``actor_id`` is whoever decided it, so their own copy reads "You". Later
    decisions (approved, then taken down) edit the same copies again.
    """
    discord_queue.enqueue(_edit_all(kind, object_id, build, link, actor_id))


def carry_over(
    kind: StaffRequestKind,
    old_id: int,
    new_id: int,
    build: CardBuilder,
    link: Optional[DMLink],
) -> None:
    """Queue moving the DMs about ``old_id`` onto ``new_id`` and editing them.

    For a request withdrawn and made again before anyone is told twice: the
    copies that now say "withdrawn" become the ask again instead of a fresh DM.
    """
    async def move_then_edit() -> None:
        await StaffRequestDMRepository.rebind(kind, old_id, new_id)
        await _edit_all(kind, new_id, build, link, None)

    discord_queue.enqueue(move_then_edit())


async def _edit_all(
    kind: StaffRequestKind,
    object_id: int,
    build: CardBuilder,
    link: Optional[DMLink],
    actor_id: Optional[int],
) -> None:
    service = DiscordService()
    for row in await StaffRequestDMRepository.list_for(kind, object_id):
        ok, reason = await service.edit_dm(
            row.channel_id, row.message_id, build(row.user_id == actor_id), link,
        )
        if not ok:
            logger.info('%s %s: staff DM %s not edited: %s', kind.value, object_id, row.message_id, reason)


def card(
    title_and_description: Tuple[str, str],
    *,
    color: int,
    community_name: Optional[str],
    fields=None,
) -> discord.Embed:
    """The edited card, in the same shape as every other notification."""
    from application.utils.discord_embeds import notification_embed

    title, description = title_and_description
    return notification_embed(
        title=title, color=color, community_name=community_name,
        description=description, fields=fields,
    )
