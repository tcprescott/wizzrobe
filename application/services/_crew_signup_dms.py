"""The crew signup DM to a match's crew owners, and its edits once it's settled.

Every owner (the tournament's admins and crew coordinators) gets the DM, so it
goes through :mod:`staff_request_dms`: each copy is remembered and rewritten
when the volunteer withdraws, someone approves them, or staff remove the
signup. Everything here is best-effort: a Discord failure never undoes the
change that prompted it.
"""

import logging
from typing import Callable, Optional, Tuple, Union

from application.services import notification_links
from application.services.tenant_service import TenantService
from application.utils import discord_messages_staff_requests as handled_copy
from application.utils.discord_embeds import COLOR_CREW, notification_embed, time_field
from application.utils.match_labels import players_label
from models import Commentator, StaffRequestKind, Tracker, User

logger = logging.getLogger(__name__)

SIGNUP_KIND = {
    'commentator': StaffRequestKind.COMMENTATOR_SIGNUP,
    'tracker': StaffRequestKind.TRACKER_SIGNUP,
}


async def crew_dm_parts(match):
    """Shared DM ingredients for every crew notification.

    Returns the message-builder kwargs and the embed field list — every crew
    notification describes the same assignment, so they identify it from the
    same fields.
    """
    players = await match.players.all().prefetch_related('user')
    player_names = [p.user.preferred_name for p in players] if players else []
    message_kwargs = {
        'match_title': match.title or None,
        'scheduled_at_display': time_field(match.scheduled_at),
        'stage_name': match.stage.name if match.stage else None,
        'player_names': player_names or None,
    }

    players_str = players_label(player_names)
    fields = [('Match', match.title or players_str, False)]
    if match.title and players_str:
        fields.append(('Players', players_str, True))
    fields.append(('Time', time_field(match.scheduled_at), False))
    if match.stage:
        fields.append(('Stage', match.stage.name, True))
    return message_kwargs, fields


async def mark_signup_dms(
    match,
    crew_member: Union[Commentator, Tracker],
    crew_type: str,
    color: int,
    card: Callable[[bool], Tuple[str, str]],
    *,
    actor_id: Optional[int] = None,
    link_label: str = 'Open the match',
) -> None:
    """Edit every owner's copy of the signup DM to say where it stands now.

    ``card(you)`` returns the ``(title, description)``; ``you`` is whether the
    reader decided it. ``crew_member`` may already be deleted.
    """
    from application.services import staff_request_dms

    try:
        _, fields = await crew_dm_parts(match)
        community = await TenantService.current_community_name()
        link = await notification_links.admin_match(match.id, label=link_label)

        def build(you: bool):
            title, description = card(you)
            return notification_embed(
                title=title, color=color,
                community_name=community, description=description, fields=fields,
            )

        staff_request_dms.mark_handled(
            SIGNUP_KIND[crew_type], crew_member.id, build, link, actor_id=actor_id,
        )
    except Exception:
        logger.exception('Crew signup DM edit failed for %s on match %s', crew_type, match.id)


async def restore_signup_dms(match, volunteer: User, crew_type: str, old_id: int, new_id: int) -> None:
    """A signup made again inside the notice window: the DMs become the ask again."""
    from application.services import staff_request_dms

    try:
        _, fields = await crew_dm_parts(match)
        title, description = handled_copy.crew_signup_pending(volunteer.preferred_name, crew_type)
        embed = notification_embed(
            title=title, color=COLOR_CREW,
            community_name=await TenantService.current_community_name(),
            description=description, fields=fields,
        )
        link = await notification_links.admin_match(match.id, label='Review the signup')
        staff_request_dms.carry_over(SIGNUP_KIND[crew_type], old_id, new_id, lambda _you: embed, link)
    except Exception:
        logger.exception('Crew signup DM restore failed for match %s', match.id)
