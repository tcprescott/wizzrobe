"""
The shift-start DM's buttons: I'm here, Tell staff I'm late, I can't make it.

custom_id formats:
  'vol_here:<assignment_id>'                  check in
  'vol_late:<assignment_id>'                  ask how late (ephemeral choice)
  'vol_late_min:<assignment_id>:<minutes>'    record it and tell the coordinators
  'vol_cant:<assignment_id>'                  open the reason form (a modal)
  'vol_cant_reason:<assignment_id>'           the form's submit: give the shift back
"""

import logging
from typing import Any

import discord

from application.utils.discord_messages_volunteer import (
    LATE_CHOICES,
    late_label,
    volunteer_here_confirmation,
    volunteer_late_confirmation,
    volunteer_released_confirmation,
)
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction
from discordbot._tenant import assignment_tenant_id

logger = logging.getLogger(__name__)

HERE_PREFIX = 'vol_here'
LATE_PREFIX = 'vol_late'
LATE_MINUTES_PREFIX = 'vol_late_min'
CANT_PREFIX = 'vol_cant'
CANT_REASON_PREFIX = 'vol_cant_reason'
REASON_INPUT_ID = 'reason'
REASON_MAX_CHARS = 500

MSG_UNEXPECTED_ERROR = (
    "Something went wrong with your shift. Please try again, or use My Shifts "
    "on the website."
)


def make_volunteer_arrival_view(assignment_id: int) -> discord.ui.View:
    """The three buttons on the shift-start DM."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="I'm here", style=discord.ButtonStyle.success,
        custom_id=f'{HERE_PREFIX}:{assignment_id}',
    ))
    view.add_item(discord.ui.Button(
        label="Tell staff I'm late", style=discord.ButtonStyle.secondary,
        custom_id=f'{LATE_PREFIX}:{assignment_id}',
    ))
    view.add_item(discord.ui.Button(
        label="I can't make it", style=discord.ButtonStyle.danger,
        custom_id=f'{CANT_PREFIX}:{assignment_id}',
    ))
    return view


def _done_view(label: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label=label, style=discord.ButtonStyle.secondary,
        custom_id=f'{HERE_PREFIX}:done', disabled=True,
    ))
    return view


def _parse_id(custom_id: str) -> int:
    parts = custom_id.split(':')
    if len(parts) != 2:
        raise DMInteractionError('Invalid interaction.')
    try:
        return int(parts[1])
    except ValueError:
        raise DMInteractionError('Invalid assignment ID.') from None


def _parse_late(custom_id: str) -> tuple[int, int]:
    parts = custom_id.split(':')
    if len(parts) != 3:
        raise DMInteractionError('Invalid interaction.')
    try:
        assignment_id, minutes = int(parts[1]), int(parts[2])
    except ValueError:
        raise DMInteractionError('Invalid interaction.') from None
    if minutes not in LATE_CHOICES:
        raise DMInteractionError('Invalid interaction.')
    return assignment_id, minutes


async def _replace_buttons(inter: discord.Interaction, label: str) -> None:
    """Grey out the DM's buttons once the volunteer has answered."""
    message = getattr(inter, 'message', None)
    if message is None:
        return
    try:
        await message.edit(view=_done_view(label))
    except Exception:
        logger.warning('Could not update the shift-start DM buttons', exc_info=True)


async def _run(interaction: discord.Interaction, *, log_label: str, parse, handle) -> None:
    await run_dm_interaction(
        interaction,
        log_label=log_label,
        parse=parse,
        resolve_tenant=lambda parsed: assignment_tenant_id(
            parsed[0] if isinstance(parsed, tuple) else parsed
        ),
        not_found_message='That shift no longer exists.',
        handle=handle,
        unexpected_error_message=MSG_UNEXPECTED_ERROR,
    )


async def handle_here_interaction(interaction: discord.Interaction) -> None:
    from application.services import VolunteerArrivalService

    async def handle(inter: discord.Interaction, assignment_id: int, user: Any, send: SendFn) -> None:
        assignment = await VolunteerArrivalService().check_in_self(assignment_id, user)
        position = assignment.shift.position.name if assignment.shift.position else 'your'
        await _replace_buttons(inter, 'Checked in')
        await send(volunteer_here_confirmation(position))

    await _run(interaction, log_label=HERE_PREFIX, parse=_parse_id, handle=handle)


async def handle_late_interaction(interaction: discord.Interaction) -> None:
    """Offer the how-late choice as an ephemeral row of buttons."""
    async def handle(inter: discord.Interaction, assignment_id: int, user: Any, send: SendFn) -> None:
        view = discord.ui.View(timeout=None)
        for minutes in LATE_CHOICES:
            view.add_item(discord.ui.Button(
                label=late_label(minutes), style=discord.ButtonStyle.primary,
                custom_id=f'{LATE_MINUTES_PREFIX}:{assignment_id}:{minutes}',
            ))
        await inter.followup.send('About how late will you be?', view=view, ephemeral=True)

    await _run(interaction, log_label=LATE_PREFIX, parse=_parse_id, handle=handle)


async def handle_late_minutes_interaction(interaction: discord.Interaction) -> None:
    from application.services import VolunteerArrivalService

    async def handle(inter: discord.Interaction, parsed: tuple[int, int], user: Any, send: SendFn) -> None:
        assignment_id, minutes = parsed
        await VolunteerArrivalService().report_late(assignment_id, user, minutes)
        await send(volunteer_late_confirmation(minutes))

    await _run(interaction, log_label=LATE_MINUTES_PREFIX, parse=_parse_late, handle=handle)


async def handle_cant_interaction(interaction: discord.Interaction) -> None:
    """Open the reason form. A modal must be the first response, so no defer here."""
    custom_id = str((interaction.data or {}).get('custom_id', ''))  # type: ignore[call-overload]
    try:
        assignment_id = _parse_id(custom_id)
    except DMInteractionError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    modal = discord.ui.Modal(
        title="Can't make your shift?", custom_id=f'{CANT_REASON_PREFIX}:{assignment_id}',
        timeout=None,
    )
    modal.add_item(discord.ui.TextInput(
        label='Reason (optional)', custom_id=REASON_INPUT_ID,
        style=discord.TextStyle.paragraph, required=False, max_length=REASON_MAX_CHARS,
        placeholder='Your coordinator sees this.',
    ))
    try:
        await interaction.response.send_modal(modal)
    except Exception:
        logger.exception('Failed to open the give-back form')


def modal_value(data: Any, input_id: str) -> str:
    """Pull one text input's value out of a raw modal-submit payload."""
    for row in (data or {}).get('components', []) or []:
        for component in row.get('components', []) or []:
            if component.get('custom_id') == input_id:
                return str(component.get('value') or '')
    return ''


async def handle_cant_reason_interaction(interaction: discord.Interaction) -> None:
    from application.services import VolunteerScheduleService

    reason = modal_value(interaction.data, REASON_INPUT_ID)[:REASON_MAX_CHARS]

    async def handle(inter: discord.Interaction, assignment_id: int, user: Any, send: SendFn) -> None:
        await VolunteerScheduleService().release(assignment_id, user, reason or None)
        await _replace_buttons(inter, 'Shift given back')
        await send(volunteer_released_confirmation())

    await _run(interaction, log_label=CANT_REASON_PREFIX, parse=_parse_id, handle=handle)
