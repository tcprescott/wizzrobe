"""The survey DMs: the invitation when staff send it, and the reminder.

Each names what the questions are about ("ALTTPR, OoTR and your Proctor
shifts") so the reader knows why they got it, and carries a link button
straight to the survey, plus a Not interested button that stops the
reminders. A reminder to someone who saved answers but never sent them says
so. Best-effort like every other notifier: a Discord failure is logged and
never undoes the send.
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


def invite_text(survey: Survey, facts: Mapping, *, name: str = '', reminder: bool = False,
                partway: bool = False, last_call: bool = False) -> str:
    """The DM's text, greeting the reader by name with their own time to answer.

    ``partway``: they saved some answers and never sent them. ``last_call``: the
    second and final reminder.
    """
    title = _literal(survey.title)
    anonymous = ' You can answer anonymously.' if survey.allow_anonymous else ''
    if reminder and partway:
        body = (f"last call: you're partway through **{title}**, and your answers so far are saved. "
                'Send them before it closes.' if last_call else
                f"you're partway through **{title}**. Pick up where you left off: your answers so far are saved.")
        return _greet(name, body) + anonymous
    about = about_line(facts)
    takes = f' It takes {_minutes(survey, facts)}' + (
        f', and the questions are picked from what you did: **{about}**.' if about else '.')
    if reminder and last_call:
        return _greet(name, f'last call: **{title}** is still open, and this is the last reminder.') + takes + anonymous
    if reminder:
        return _greet(name, f"a reminder: **{title}** is still open, and we'd love your answers.") + takes + anonymous
    return (_greet(name, f"**{title}** is open, and we'd love your thoughts on the event.") + takes + anonymous
            + " We'll share what we change because of your answers.")


def _greet(name: str, sentence: str) -> str:
    """"Hi Sam, " before ``sentence``, or the sentence capitalised when there's no name."""
    return f'Hi {_literal(name)}, {sentence}' if name else sentence[:1].upper() + sentence[1:]


def _minutes(survey: Survey, facts: Mapping) -> str:
    from application.services.survey import plan

    seconds = plan.seconds(plan.parts_for(survey.sections, facts))
    minutes = max(1, round(seconds / 60))
    return f'about {minutes} minute' + ('' if minutes == 1 else 's')


def _embed(survey: Survey, community: Optional[str], text: str, closes_at: Optional[datetime]):
    from application.utils.discord_embeds import COLOR_SCHEDULED, notification_embed, time_field

    return notification_embed(
        title=f'📝 {_literal(survey.title)}', color=COLOR_SCHEDULED, community_name=community,
        description=text,
        fields=[('Open until', time_field(closes_at) if closes_at else '', False)],
    )


async def send_invites(survey: Survey, recipients: Iterable[Tuple[SurveyRecipient, Optional[int]]], *,
                       reminder: bool = False, last_call: bool = False) -> List[int]:
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
            partway = bool((recipient.progress or {}).get('done'))
            text = invite_text(survey, recipient.facts or {}, name=recipient.user.preferred_name,
                               reminder=reminder, partway=partway, last_call=last_call)
            embed = _embed(survey, community, text, survey.closes_at)
            discord_queue.enqueue(service.send_dm_with_survey_decline_button(
                int(discord_id), text, survey.id, embed=embed, link=link, notice=notice,
            ))
            queued.append(recipient.id)
    except Exception:
        logger.exception('survey %s: queuing DMs failed', survey.id)
    return queued
