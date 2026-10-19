"""The test DM sent at check-in, and what it found out.

Every match call, crew ask and shift reminder at an in-person event reaches
people as a Discord DM, and a closed DM setting fails silently until someone
misses their match. Checking someone in is the one moment they're standing in
front of a volunteer, so the desk sends a short "you're checked in" DM then and
shows whether it landed. A refusal is fixed on the spot: they join the server
or open their DMs, and the desk sends it again.

The send goes through ``discord_queue`` like every other DM, so the result
arrives a moment later; it is written to the roster row and pushed to every
open desk on ``check_in_live``.
"""

import logging
from datetime import datetime, timezone

from application.events import check_in_live
from application.repositories import CheckInEntrantRepository
from models import CheckInDmStatus, CheckInEntrant

logger = logging.getLogger(__name__)


async def start_dm_check(entrant: CheckInEntrant, event_name: str) -> None:
    """Queue the test DM for ``entrant`` and mark it pending.

    No linked account, or an account with no Discord id, means there's no one
    to DM: nothing is recorded. Someone who turned DMs off in Wizzrobe is
    recorded as such without sending, since ``send_dm`` would honour it anyway.
    """
    user = entrant.user
    if user is None or not user.discord_id:
        return
    now = datetime.now(timezone.utc)
    if not user.dm_notifications:
        await _record(entrant, CheckInDmStatus.OPTED_OUT, now)
        return
    from application.services.notification_catalog import CHECKED_IN, MEMBERS
    from application.services.notification_settings_service import NotificationSettingsService
    from models import NotificationMode

    # Turned off in Admin → Notifications: no DM, so no result for the desk to show.
    if await NotificationSettingsService.mode_for(CHECKED_IN.to(MEMBERS)) is NotificationMode.OFF:
        return
    await _record(entrant, CheckInDmStatus.PENDING, now)

    from application.services import notification_links
    from application.services.discord import discord_queue
    from application.services.tenant_service import TenantService
    from application.utils.discord_embeds import COLOR_CONFIRMED, notification_embed
    from application.utils.discord_messages_check_in import checked_in_dm

    community = await TenantService.current_community_name()
    body = checked_in_dm(event_name)
    embed = notification_embed(
        title="✅ You're checked in", color=COLOR_CONFIRMED,
        community_name=community, description=body,
    )
    link = await notification_links.community_schedule(label='See the schedule')
    discord_queue.enqueue(_deliver(entrant.id, entrant.event_id, int(user.discord_id), body, embed, link))


async def _deliver(entrant_id: int, event_id: int, discord_id: int, body: str, embed, link) -> None:
    from application.services.discord import DiscordService
    from application.services.discord.discord_service import DM_UNREACHABLE
    from application.services.notification_catalog import CHECKED_IN, MEMBERS

    ok, message = await DiscordService().send_dm(
        discord_id, body, embed=embed, link=link, notice=CHECKED_IN.to(MEMBERS),
    )
    if ok:
        status = CheckInDmStatus.SENT
    elif message in DM_UNREACHABLE:
        status = CheckInDmStatus.BLOCKED
    else:
        logger.warning('Check-in test DM for entrant %s not sent: %s', entrant_id, message)
        status = CheckInDmStatus.FAILED
    await CheckInEntrantRepository.record_dm_status(entrant_id, status, datetime.now(timezone.utc))
    check_in_live.publish(event_id, entrant_id, check_in_live.CHANGED)


async def _record(entrant: CheckInEntrant, status: CheckInDmStatus, at: datetime) -> None:
    await CheckInEntrantRepository.record_dm_status(entrant.id, status, at)
    entrant.dm_status = status
    entrant.dm_checked_at = at
