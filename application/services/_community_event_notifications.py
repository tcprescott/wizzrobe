"""Discord DMs for community events.

Every sender here is best-effort: a Discord failure is logged and swallowed, so
it never undoes the save that prompted it. Each DM carries a link button to the
control it asks about (the review dialog for staff, the event page for hosts and
subscribers), and each is skipped for anyone who has turned DMs off.
"""

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from application.services import staff_request_dms
from application.services.notification_catalog import (
    EVENT_APPROVED,
    EVENT_CANCELLED,
    EVENT_MOVED,
    EVENT_POSTED_FOR_YOU,
    EVENT_REJECTED,
    FOLLOWERS,
    MEMBERS,
    NoticeTarget,
)
from models import CommunityEvent, CommunityEventStatus, Role, StaffRequestKind, User

logger = logging.getLogger(__name__)


def _wants_dm(user: Optional[User]) -> bool:
    return bool(user and user.discord_id)


def event_embed(event: CommunityEvent, *, title: str, color: int, community_name: Optional[str],
                description: str):
    from application.utils.discord_embeds import notification_embed, time_field
    from application.utils.discord_messages_community_events import literal

    return notification_embed(
        title=title,
        color=color,
        community_name=community_name,
        description=description,
        fields=[
            ('Event', literal(event.title), False),
            ('Starts', time_field(event.starts_at), False),
            ('Length', format_duration(event.duration_minutes), True),
            ('Where', literal(event.location), True),
        ],
    )


def format_duration(minutes: int) -> str:
    """``90`` → ``1h 30m``; ``60`` → ``1h``; ``45`` → ``45m``."""
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f'{hours}h {mins}m'
    return f'{hours}h' if hours else f'{mins}m'


async def _send(recipients: Iterable[User], message: str, embed, link, notice: NoticeTarget) -> None:
    from application.services.discord import DiscordService, discord_queue

    service = DiscordService()
    for user in recipients:
        if _wants_dm(user):
            discord_queue.enqueue(service.send_dm(int(user.discord_id), message, embed=embed, link=link, notice=notice))


async def notify_staff_of_submission(event: CommunityEvent, host: User) -> None:
    """Tell the community's staff a member's event is waiting for review."""
    try:
        from application.repositories.user_role_repository import UserRoleRepository
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import COLOR_JOIN_REQUEST
        from application.utils.discord_messages_community_events import submitted_to_staff_dm

        community = await TenantService.current_community_name()
        message = submitted_to_staff_dm(community, host.preferred_name, event.title)
        embed = event_embed(event, title='📅 Meetup to review', color=COLOR_JOIN_REQUEST,
                            community_name=community, description=message)
        link = await notification_links.admin_community_event(event.id)
        staff = [u for u in await UserRoleRepository.list_users_with_role(Role.STAFF, active_only=True) if u.id != host.id]
        await staff_request_dms.send(StaffRequestKind.COMMUNITY_EVENT, event.id, staff, message,
                                     embed=embed, link=link)
    except Exception:
        logger.exception('community event %s: staff notification failed', event.id)


async def mark_staff_dms_decided(event: CommunityEvent, actor: User, *, approved: bool,
                                 was_listed: bool = False) -> None:
    """Edit the review DMs every staff member got to say who decided it."""
    try:
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils import discord_messages_staff_requests as copy
        from application.utils.discord_embeds import COLOR_CANCELLED, COLOR_CONFIRMED

        community = await TenantService.current_community_name()
        when = event.reviewed_at or datetime.now(timezone.utc)
        name = actor.preferred_name

        def build(you: bool):
            if approved:
                title, desc = copy.event_approved(you, name, event.title, when)
            else:
                title, desc = copy.event_rejected(you, name, event.title, when,
                                                  event.review_note or '', was_listed)
            return event_embed(event, title=title, color=COLOR_CONFIRMED if approved else COLOR_CANCELLED,
                               community_name=community, description=desc)

        link = await notification_links.community_event(event.id, label='View the event')
        staff_request_dms.mark_handled(StaffRequestKind.COMMUNITY_EVENT, event.id, build, link,
                                       actor_id=actor.id)
    except Exception:
        logger.exception('community event %s: staff DM edit failed', event.id)


async def mark_staff_dms_cancelled(event: CommunityEvent, actor: User) -> None:
    """Edit the review DMs once the event is called off before anyone decided it."""
    try:
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils import discord_messages_staff_requests as copy
        from application.utils.discord_embeds import COLOR_FINISHED

        community = await TenantService.current_community_name()
        when = datetime.now(timezone.utc)

        def build(you: bool):
            title, desc = copy.event_cancelled(you, actor.preferred_name, event.title, when)
            return event_embed(event, title=title, color=COLOR_FINISHED,
                               community_name=community, description=desc)

        link = await notification_links.community_event(event.id, label='View the event')
        staff_request_dms.mark_handled(StaffRequestKind.COMMUNITY_EVENT, event.id, build, link,
                                       actor_id=actor.id)
    except Exception:
        logger.exception('community event %s: staff DM edit failed', event.id)


async def notify_host_of_decision(event: CommunityEvent, host: User, *, approved: bool,
                                  was_listed: bool = False) -> None:
    """Tell the host staff approved their event, or why they didn't."""
    try:
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import COLOR_CANCELLED, COLOR_CONFIRMED
        from application.utils.discord_messages_community_events import approved_dm, rejected_dm

        community = await TenantService.current_community_name()
        if approved:
            message = approved_dm(community, event.title, event.rsvp_enabled)
            title, color = '✅ Meetup approved', COLOR_CONFIRMED
            link = await notification_links.community_event(event.id)
        else:
            message = rejected_dm(community, event.title, event.review_note or '', was_listed)
            title, color = '📅 Meetup not listed', COLOR_CANCELLED
            link = await notification_links.community_event(event.id, label='Edit the event')
        embed = event_embed(event, title=title, color=color, community_name=community, description=message)
        await _send([host], message, embed, link, (EVENT_APPROVED if approved else EVENT_REJECTED).to(MEMBERS))
    except Exception:
        logger.exception('community event %s: host notification failed', event.id)


async def notify_host_of_handover(event: CommunityEvent, host: User, actor: User, *,
                                  new_event: bool) -> None:
    """Tell a member staff made them the host of ``event``."""
    try:
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import COLOR_CONFIRMED
        from application.utils.discord_messages_community_events import handed_over_dm

        community = await TenantService.current_community_name()
        message = handed_over_dm(community, actor.preferred_name, event.title, new_event=new_event,
                                 listed=event.status is CommunityEventStatus.APPROVED)
        embed = event_embed(event, title="📅 You're hosting a meetup", color=COLOR_CONFIRMED,
                            community_name=community, description=message)
        link = await notification_links.community_event(event.id)
        await _send([host], message, embed, link, EVENT_POSTED_FOR_YOU.to(MEMBERS))
    except Exception:
        logger.exception('community event %s: handover notification failed', event.id)


async def notify_subscribers(event: CommunityEvent, subscribers: Iterable[User], *,
                             cancelled: bool) -> None:
    """Tell subscribers an approved event moved, or is no longer happening."""
    try:
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import COLOR_CANCELLED, COLOR_RESCHEDULED, time_field
        from application.utils.discord_messages_community_events import cancelled_dm, time_changed_dm

        community = await TenantService.current_community_name()
        if cancelled:
            message = cancelled_dm(event.title)
            title, color = '🚫 Meetup cancelled', COLOR_CANCELLED
        else:
            message = time_changed_dm(event.title, time_field(event.starts_at))
            title, color = '🕑 Meetup moved', COLOR_RESCHEDULED
        embed = event_embed(event, title=title, color=color, community_name=community, description=message)
        link = await notification_links.community_event(event.id)
        await _send(subscribers, message, embed, link, (EVENT_CANCELLED if cancelled else EVENT_MOVED).to(FOLLOWERS))
    except Exception:
        logger.exception('community event %s: subscriber notification failed', event.id)
