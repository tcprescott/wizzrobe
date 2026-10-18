"""
Community event reminder loop.

DMs each subscriber of an approved community event shortly before it starts,
then stamps ``reminder_sent_at`` so they hear once. Moving the event clears the
stamp (``CommunityEventService.update``) so the reminder fires again at the new
time. The lead time is a per-community setting, so the scan runs over a fixed
generous window and each subscription is re-checked against its own
community's lead, as the volunteer shift reminder does.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict

from application.tenant_context import tenant_scope
from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60

#: The widest lead any community can usefully set. A longer lead still works;
#: those subscribers are reminded once the event enters this window.
MAX_LEAD_MINUTES = 24 * 60


async def _scoped_dm(tenant_id: int, coro) -> None:
    """Await a DM-send coroutine with its tenant bound so deep links resolve."""
    with tenant_scope(tenant_id):
        await coro


async def _tick() -> None:
    from application.repositories import CommunityEventSubscriptionRepository
    from application.services import notification_links
    from application.services._community_event_notifications import event_embed
    from application.services.discord import discord_queue
    from application.services.discord.discord_service import DiscordService, discord_bot_down
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.system_config_service import SystemConfigService
    from application.services.tenant_service import TenantService
    from application.utils.discord_embeds import COLOR_CHECKED_IN, time_field
    from application.utils.discord_messages_community_events import reminder_dm
    from models import FeatureFlag

    if discord_bot_down():
        # Reminders are stamped before their DM is queued; wait for the bot so
        # they go out after a reconnect rather than being stamped and lost.
        return

    now = datetime.now(timezone.utc)
    # Cross-tenant scan; each subscription is acted on inside its own tenant.
    due = await CommunityEventSubscriptionRepository.due_for_reminder(
        now, now + timedelta(minutes=MAX_LEAD_MINUTES),
    )
    if not due:
        return

    discord_service = DiscordService()
    # Read once per community per tick, not once per subscription.
    leads: Dict[int, int] = {}

    async def _remind(subscription) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.COMMUNITY_EVENTS):
            return
        event = subscription.event
        if subscription.tenant_id not in leads:
            leads[subscription.tenant_id] = await SystemConfigService.get_community_event_reminder_lead_minutes()
        lead = leads[subscription.tenant_id]
        if event.starts_at > now + timedelta(minutes=lead):
            return
        # Stamp first so a delivery failure (or a restart) doesn't re-send.
        await CommunityEventSubscriptionRepository.mark_reminded(subscription, now)

        user = subscription.user
        if not user.discord_id:
            return
        message = reminder_dm(event.title, time_field(event.starts_at), event.location)
        embed = event_embed(
            event, title='⏰ Community event starting soon', color=COLOR_CHECKED_IN,
            community_name=await TenantService.current_community_name(), description=message,
        )
        link = await notification_links.community_event(event.id)
        send = discord_service.send_dm(int(user.discord_id), message, embed=embed, link=link)
        discord_queue.enqueue(_scoped_dm(subscription.tenant_id, send))

    await for_each_tenant_scoped(
        due,
        _remind,
        tenant_id_of=lambda subscription: subscription.tenant_id,
        logger=logger,
        describe=lambda subscription: f'community event subscription {getattr(subscription, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'community event reminder', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
