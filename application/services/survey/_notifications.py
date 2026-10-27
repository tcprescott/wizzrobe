"""The survey DMs: the invitation when staff send it, and the reminder.

Each names what the questions are about ("ALTTPR, OoTR and your Proctor
shifts") so the reader knows why they got it, and carries a link button
straight to the survey. Best-effort like every other notifier: a Discord
failure is logged and never undoes the send.
"""

import logging
from datetime import datetime
from typing import Iterable, List, Mapping, Optional, Tuple

from application.services.notification_catalog import MEMBERS, SURVEY_INVITE, SURVEY_REMINDER, NoticeTarget
from models import Survey, SurveyRecipient

logger = logging.getLogger(__name__)


def _literal(text: Optional[str]) -> str:
    from application.utils.discord_messages_community_events import literal

    return literal(text)


def _join(names: List[str]) -> str:
    if len(names) <= 1:
        return ''.join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def about_line(facts: Mapping) -> str:
    """"ALTTPR, OoTR and your Proctor shifts", from what the person did."""
    names = [_literal(t['name']) for t in facts.get('tournaments') or []]
    names += [f"your {_literal(p['name'])} shifts" for p in facts.get('positions') or []]
    if facts.get('crew'):
        names.append('crewing')
    return _join(names)


def invite_text(survey: Survey, facts: Mapping, *, reminder: bool) -> str:
    about = about_line(facts)
    picked = f' The questions are picked from what you did: **{about}**.' if about else ''
    anonymous = ' You can answer anonymously.' if survey.allow_anonymous else ''
    lead = (f"A reminder: **{_literal(survey.title)}** is still open, and we'd love your answers."
            if reminder else
            f"**{_literal(survey.title)}**: we'd love a few minutes of your thoughts on the event.")
    return f'{lead}{picked}{anonymous}'


def _embed(survey: Survey, community: Optional[str], text: str, closes_at: Optional[datetime]):
    from application.utils.discord_embeds import COLOR_SCHEDULED, notification_embed, time_field

    return notification_embed(
        title=f'📝 {_literal(survey.title)}', color=COLOR_SCHEDULED, community_name=community,
        description=text,
        fields=[('Open until', time_field(closes_at) if closes_at else '', False)],
    )


async def send_invites(survey: Survey, recipients: Iterable[Tuple[SurveyRecipient, Optional[str]]], *,
                       reminder: bool = False) -> List[int]:
    """DM each ``(recipient, discord id)`` pair; return the recipient ids a DM was queued for."""
    from application.services import notification_links
    from application.services.discord import DiscordService, discord_queue
    from application.services.tenant_service import TenantService

    queued: List[int] = []
    try:
        community = await TenantService.current_community_name()
        link = await notification_links.survey(survey.id)
        notice: NoticeTarget = (SURVEY_REMINDER if reminder else SURVEY_INVITE).to(MEMBERS)
        service = DiscordService()
        for recipient, discord_id in recipients:
            if not discord_id:
                continue
            text = invite_text(survey, recipient.facts or {}, reminder=reminder)
            embed = _embed(survey, community, text, survey.closes_at)
            discord_queue.enqueue(service.send_dm(int(discord_id), text, embed=embed, link=link, notice=notice))
            queued.append(recipient.id)
    except Exception:
        logger.exception('survey %s: queuing DMs failed', survey.id)
    return queued
