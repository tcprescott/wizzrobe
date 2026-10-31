"""DMs to the people who staff the volunteer schedule.

A volunteer giving a shift back and a volunteer running late both have to reach
whoever is covering the roster: every ``VOLUNTEER_COORDINATOR`` plus the admin
on duty (all ``STAFF`` when nobody is). Both land on the schedule on the day of
the shift.
"""

import discord

from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import Notice
from application.services.timezone_service import TimezoneService
from application.utils.timezone import to_local
from models import VolunteerShift


async def notify_coordinators(
    shift: VolunteerShift, message: str, embed: discord.Embed, *, link_label: str,
    notice: Notice,
) -> None:
    """Queue the DM to the coordinators and whoever is on duty. Never raises on delivery."""
    from application.services import notification_links
    from application.services.staff_alert_service import StaffAlertService

    recipients = await StaffAlertService.tagged_volunteer_alert_recipients()
    if not recipients:
        return

    # The community's clock, not the reader's: the roster day belongs to the schedule.
    day = None
    if shift.starts_at:
        local = to_local(shift.starts_at, await TimezoneService.tenant_timezone_name())
        day = local.date() if local else None
    link = await notification_links.admin_volunteer_schedule(day, label=link_label)
    discord_service = DiscordService()
    for coordinator, why in recipients:
        discord_id = getattr(coordinator, 'discord_id', None)
        if not discord_id:
            continue
        discord_queue.enqueue(
            discord_service.send_dm(int(discord_id), message, embed=embed, link=link, notice=notice.to(why))
        )
