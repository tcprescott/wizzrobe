"""
The Not interested button on survey invite and reminder DMs.

Pressing it stops the reminders for that survey; the survey link beside it
keeps working for anyone who changes their mind.
"""

import logging
from typing import Any

import discord

from application.utils.discord_messages import MSG_UNEXPECTED_ERROR
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = 'survey'
DECLINED_MESSAGE = "Got it, we won't remind you about this one. The link still works if you change your mind."


def make_survey_decline_view(survey_id: int, *, declined: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Won't remind you" if declined else 'Not interested',
        style=discord.ButtonStyle.secondary,
        custom_id=f'{CUSTOM_ID_PREFIX}:decline:{survey_id}',
        disabled=declined,
    ))
    return view


def _parse(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 3 or parts[1] != 'decline':
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid survey ID.') from None


async def handle_survey_decline_interaction(interaction: discord.Interaction) -> None:
    """custom_id ``survey:decline:<survey_id>``; replies ephemerally and greys the button out."""
    from application.services import SurveyResponseService, notification_links
    from application.services.discord.discord_service import _with_link_button
    from discordbot._tenant import survey_scope

    async def handle(inter: discord.Interaction, survey_id: int, user: Any, send: SendFn) -> None:
        declined = await SurveyResponseService().decline(user, survey_id)
        view = _with_link_button(make_survey_decline_view(survey_id, declined=True),
                                 await notification_links.survey(survey_id))
        try:
            await inter.message.edit(view=view)  # type: ignore[union-attr]
        except Exception:
            logger.warning('Could not update survey decline button (survey_id=%s)', survey_id)
        await send(DECLINED_MESSAGE if declined else "You've already answered or turned this one down.")

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=survey_scope,
        not_found_message='Survey not found.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )
