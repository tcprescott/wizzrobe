"""Shared pieces of a volunteer's own shifts: titles, actions, give-back, hours."""

from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from nicegui import ui

from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.timezone import format_local_display
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.help import resolve_help
from theme.notify import notify_error


def release_warning(starts_at: datetime, lead_minutes: int) -> Optional[str]:
    """Warn when giving this shift back leaves the coordinator little time.

    Module-level and pure so the copy decision is testable without a slot
    context. The window is the tenant's reminder lead or 24 hours, whichever is
    longer — inside either, "your coordinator has been told" is not the same as
    "someone will cover it".
    """
    hours_out = (starts_at - datetime.now(timezone.utc)).total_seconds() / 3600.0
    if hours_out <= 0:
        return ('This shift has already started. Your coordinator will be told '
                'right away, but may not find cover.')
    window_hours = max(24.0, (lead_minutes or 0) / 60.0)
    if hours_out > window_hours:
        return None
    return (f'This shift starts in about {round(hours_out)} hour(s). Your coordinator '
            'will be told right away, but may not find cover.')


def _hours(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f'{value:g}'


def hours_message(summary) -> str:
    """One line telling a volunteer where their hours stand.

    Module-level and pure so the copy is testable without a slot context. The
    total covers every published shift in the event window, past and upcoming,
    counting overlapping shifts once.
    """
    if summary.hours <= 0:
        return 'No shifts on the books yet.'
    line = f"You're down for {_hours(summary.hours)} hours this event."
    if summary.tier_cleared is not None:
        line += f" You've cleared {_hours(summary.tier_cleared)}."
    if summary.next_tier is not None:
        line += f' {_hours(summary.hours_to_next)} more reaches {_hours(summary.next_tier)}.'
    return line


def shift_actions(assignment, now: datetime) -> tuple[bool, bool]:
    """(show Acknowledge, show "Can't make this") for one assignment card.

    Module-level and pure so the rule is testable. Both mirror what the service
    will accept: nothing on a finished or called-off shift, and no giving back
    a shift you are already checked in for.
    """
    finished = assignment.shift.ends_at <= now or assignment.called_off_at is not None
    can_ack = assignment.acknowledged_at is None and not finished
    can_release = assignment.checked_in_at is None and not finished
    return can_ack, can_release


def shift_title(shift) -> str:
    position = shift.position.name if shift.position else 'Volunteer'
    return position + (f' — {shift.label}' if shift.label else '')


def render_shift_brief(assignment) -> None:
    """What the shift actually involves — the two fields the coordinator
    writes — and who else is on it."""
    shift = assignment.shift
    description = getattr(shift.position, 'description', None) if shift.position else None
    if description:
        ui.label(description).classes('text-body2 q-mt-sm wiz-free-text')
    if shift.notes:
        with ui.column().classes('q-mt-sm q-pa-sm gap-0') \
                .style('border: 1px solid var(--q-primary); border-radius: 4px;'):
            ui.label('For this shift').classes('text-caption text-weight-medium')
            ui.label(shift.notes).classes('text-body2 wiz-free-text')
    # Drafts excluded: naming someone who hasn't been told would leak the draft.
    others = sorted(
        a.user.preferred_name
        for a in shift.assignments
        if a.user_id != assignment.user_id and not a.auto_generated and a.user
    )
    ui.label(
        f"With: {', '.join(others)}" if others
        else 'You are the only one scheduled for this slot.'
    ).classes('text-caption text-grey q-mt-sm')


async def open_release_dialog(
    assignment, user, lead_minutes: int, on_released: Callable[[], Awaitable[None]],
) -> None:
    """The "Give this shift back?" confirm, shared by My Shifts and My Schedule."""
    shift = assignment.shift
    help = await resolve_help(['shift-release'])
    with form_dialog('Give this shift back?', card_classes='dialog-card dialog-card--sm', help=help) as dialog:
        with ui.column().classes('q-pa-md gap-2 full-width'):
            ui.label(shift_title(shift)).classes('text-body2 text-weight-medium')
            ui.label(
                f'{format_local_display(shift.starts_at)} → '
                f'{format_local_display(shift.ends_at)}'
            ).classes('text-caption')
            warning = release_warning(shift.starts_at, lead_minutes)
            if warning:
                ui.label(warning).classes('text-caption text-warning')
            reason_input = ui.textarea('Reason (optional)').classes('full-width')
            reason_input.props('hint="Your coordinator sees this."')

        async def do_release() -> None:
            dialog.close()
            try:
                await VolunteerScheduleService().release(assignment.id, user, reason_input.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Shift released. Your coordinator has been told.', color='info')
            await on_released()

        with dialog_actions().classes('justify-end'):
            ui.button('Keep it', on_click=dialog.close).props('flat')
            ui.button('Give it back', on_click=do_release).props('color=negative')
    dialog.open()
