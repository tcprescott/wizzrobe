"""
Volunteer shift reminder loop.

A lightweight background worker (modeled on ``discord_queue``) that periodically
finds upcoming volunteer assignments within the configured lead time, enqueues a
reminder DM with an acknowledge button, and stamps ``reminder_sent_at`` so each
assignment is only reminded once.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, NamedTuple

from application.services.notification_catalog import SHIFT_REMINDER, SHIFT_STARTING, VOLUNTEERS
from application.tenant_context import tenant_scope
from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop
from models import User

logger = logging.getLogger(__name__)

# How often the loop wakes up to look for due reminders.
TICK_SECONDS = 60

# The reminder lead time is now a per-tenant SystemConfiguration, but the loop
# iterates assignments across all tenants in one cross-tenant scan. So it scans a
# generous fixed window up front, then re-checks each assignment against ITS
# tenant's configured lead. A tenant lead beyond this bound is not silently
# dropped — the per-assignment re-check logs a warning — but such assignments are
# only reminded once they enter the window. Seven days sits comfortably above any
# realistic volunteer-reminder lead (typically minutes to hours).
MAX_LEAD_MINUTES = 7 * 24 * 60

# The shift-start DM goes out only this soon after the start; see _start_dms.
START_DM_GRACE = timedelta(minutes=30)

# How far apart two shifts' end and start may be and still count as a handover.
HANDOVER_SLACK = timedelta(minutes=15)


class Handover(NamedTuple):
    """Who a volunteer takes over from at the start, and hands over to at the end."""

    relieving: List[User]
    relieved_by: List[User]


async def handover_for(assignment) -> Handover:
    """The handover around this assignment's shift, on the same position.

    Someone also on this shift is left out of both lists: they keep working
    alongside, so they are not being relieved and are not relieving anyone.
    """
    from application.repositories import VolunteerAssignmentRepository

    shift = assignment.shift
    staying = {
        a.user_id for a in await VolunteerAssignmentRepository.list_for_shift(shift.id)  # type: ignore[attr-defined]
        if not a.auto_generated and a.called_off_at is None
    }
    staying.add(assignment.user_id)  # type: ignore[attr-defined]

    async def _people(at: datetime, *, ending: bool) -> List[User]:
        rows = await VolunteerAssignmentRepository.ending_or_starting_near(
            shift.position_id, at, HANDOVER_SLACK, ending=ending,
        )
        people: Dict[int, User] = {}
        for row in rows:
            if row.shift_id == shift.id or row.user_id in staying:  # type: ignore[attr-defined]
                continue
            people.setdefault(row.user_id, row.user)  # type: ignore[attr-defined]
        return list(people.values())

    return Handover(
        relieving=await _people(shift.starts_at, ending=True),
        relieved_by=await _people(shift.ends_at, ending=False),
    )


def _mention(user: User) -> str:
    import discord

    return f'<@{user.discord_id}>' if user.discord_id else discord.utils.escape_markdown(user.preferred_name)


async def _handover_lines(assignment) -> tuple[str, str]:
    """The handover line for the embed (mentions) and for the text (plain names).

    Never raises: the assignment is already stamped, so a failure here must not
    cost the volunteer the DM itself.
    """
    import discord

    from application.utils.discord_messages_volunteer import handover_text

    try:
        handover = await handover_for(assignment)
    except Exception:
        logger.exception('Handover lookup failed for assignment %s', getattr(assignment, 'id', None))
        return '', ''
    at = f'<t:{int(assignment.shift.ends_at.timestamp())}:t>'

    def _plain(user: User) -> str:
        return discord.utils.escape_markdown(user.preferred_name)

    return (
        handover_text([_mention(u) for u in handover.relieving],
                      [_mention(u) for u in handover.relieved_by], at),
        handover_text([_plain(u) for u in handover.relieving],
                      [_plain(u) for u in handover.relieved_by], at),
    )


async def _scoped_dm(tenant_id: int, coro) -> None:
    """Await a DM-send coroutine with its tenant bound so deep links resolve."""
    with tenant_scope(tenant_id):
        await coro


async def _tick() -> None:
    from application.repositories import VolunteerAssignmentRepository
    from application.services import notification_links
    from application.services.discord import discord_queue
    from application.services.discord.discord_service import DiscordService, discord_bot_down
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.system_config_service import SystemConfigService
    from application.services.tenant_service import TenantService
    from application.utils.discord_embeds import time_field, volunteer_embed
    from application.utils.discord_messages_volunteer import (
        volunteer_reminder_description,
        volunteer_reminder_dm,
    )
    from models import FeatureFlag

    if discord_bot_down():
        # Reminders are stamped before their DM is queued; wait for the bot so
        # they go out after a reconnect rather than being stamped and lost.
        return

    now = datetime.now(timezone.utc)
    await _start_dms(now)
    # Cross-tenant scan over a wide window (due_for_reminder is intentionally
    # unscoped); each assignment is re-checked against its own tenant's lead.
    window_end = now + timedelta(minutes=MAX_LEAD_MINUTES)
    due = await VolunteerAssignmentRepository.due_for_reminder(now, window_end)
    if not due:
        return

    discord_service = DiscordService()
    # Read once per community per tick: the scan window holds every assignment
    # of the next day, and a setting read per row was most of the tick's queries.
    leads: Dict[int, int] = {}

    async def _remind(assignment) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            return  # tenant has Volunteers disabled
        tenant_id = assignment.tenant_id
        if tenant_id not in leads:
            leads[tenant_id] = await SystemConfigService.get_volunteer_reminder_lead_minutes()
            if leads[tenant_id] > MAX_LEAD_MINUTES:
                logger.warning(
                    'Tenant %s volunteer_reminder_lead_minutes=%s exceeds the %s-minute '
                    'scan window; assignments further out are reminded only once they '
                    'enter the window.',
                    tenant_id, leads[tenant_id], MAX_LEAD_MINUTES,
                )
        lead = leads[tenant_id]
        shift = assignment.shift
        # Not yet within this tenant's lead window — leave un-stamped so a
        # later tick reminds it once it enters the window.
        if shift.starts_at > now + timedelta(minutes=lead):
            return
        user = assignment.user
        # Stamp first so a delivery failure (or a restart) doesn't re-fire.
        # Only this column: the row was loaded at the top of the tick, and a full
        # save would put back a null over an acknowledgment or check-in made since.
        assignment.reminder_sent_at = now
        await VolunteerAssignmentRepository.save(assignment, update_fields=['reminder_sent_at'])

        discord_id = getattr(user, 'discord_id', None)
        if not discord_id:
            return
        acknowledged = assignment.acknowledged_at is not None
        position_name = shift.position.name if shift.position else ''
        handover_embed, handover_plain = await _handover_lines(assignment)
        message = volunteer_reminder_dm(
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
            acknowledged=acknowledged,
            handover=handover_plain,
        )
        description = volunteer_reminder_description(acknowledged)
        if shift.label:
            description = f'**{shift.label}**\n{description}'
        embed = volunteer_embed(
            title='⏰ Volunteer shift reminder',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description=description,
            handover=handover_embed,
        )
        # The reminder is when someone finds out they can't make it, so it
        # carries the way out: My Shifts, with this shift's give-back dialog open.
        link = await notification_links.volunteer_release(assignment.id)
        if acknowledged:
            send = discord_service.send_dm(int(discord_id), message, embed=embed, link=link, notice=SHIFT_REMINDER.to(VOLUNTEERS))
        else:
            send = discord_service.send_dm_with_volunteer_acknowledgment_button(
                int(discord_id), message, assignment.id, embed=embed, link=link, notice=SHIFT_REMINDER.to(VOLUNTEERS),
            )
        # Close the DM over the tenant scope so its deep links / web-push
        # mirror resolve under the right tenant when the worker runs it.
        discord_queue.enqueue(_scoped_dm(tenant_id, send))

    await for_each_tenant_scoped(
        due,
        _remind,
        tenant_id_of=lambda assignment: assignment.tenant_id,  # type: ignore[attr-defined]
        event_id_of=lambda assignment: assignment.shift.event_id,
        logger=logger,
        describe=lambda assignment: f'assignment {getattr(assignment, "id", None)}',
    )


async def _start_dms(now: datetime) -> None:
    """DM each volunteer whose shift just started and who hasn't said they're here.

    Shifts that started more than ``START_DM_GRACE`` ago are left alone, so a
    deploy or an outage doesn't send a burst of stale "your shift is starting"
    DMs for shifts already well under way.
    """
    from application.repositories import VolunteerAssignmentRepository
    from application.services import notification_links
    from application.services.discord import discord_queue
    from application.services.discord.discord_service import DiscordService
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.tenant_service import TenantService
    from application.utils.discord_embeds import time_field, volunteer_embed
    from application.utils.discord_messages_volunteer import (
        VOLUNTEER_SHIFT_START_DESCRIPTION,
        volunteer_shift_start_dm,
    )
    from models import FeatureFlag

    due = await VolunteerAssignmentRepository.due_for_start_dm(now - START_DM_GRACE, now)
    if not due:
        return
    discord_service = DiscordService()

    async def _send(assignment) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            return
        # Stamp only this column first, for the same reasons as the reminder.
        assignment.start_dm_sent_at = now
        await VolunteerAssignmentRepository.save(assignment, update_fields=['start_dm_sent_at'])
        discord_id = getattr(assignment.user, 'discord_id', None)
        if not discord_id:
            return
        shift = assignment.shift
        position_name = shift.position.name if shift.position else ''
        handover_embed, handover_plain = await _handover_lines(assignment)
        message = volunteer_shift_start_dm(
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
            handover=handover_plain,
        )
        description = VOLUNTEER_SHIFT_START_DESCRIPTION
        if shift.label:
            description = f'**{shift.label}**\n{description}'
        embed = volunteer_embed(
            title='📍 Your shift is starting',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description=description,
            handover=handover_embed,
        )
        link = await notification_links.volunteer_my_shifts()
        send = discord_service.send_dm_with_volunteer_arrival_buttons(
            int(discord_id), message, assignment.id, embed=embed, link=link, notice=SHIFT_STARTING.to(VOLUNTEERS),
        )
        discord_queue.enqueue(_scoped_dm(assignment.tenant_id, send))

    await for_each_tenant_scoped(
        due,
        _send,
        tenant_id_of=lambda assignment: assignment.tenant_id,  # type: ignore[attr-defined]
        event_id_of=lambda assignment: assignment.shift.event_id,
        logger=logger,
        describe=lambda assignment: f'start DM for assignment {getattr(assignment, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'volunteer reminder', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
