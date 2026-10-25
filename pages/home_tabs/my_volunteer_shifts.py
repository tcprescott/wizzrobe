"""Your volunteer shifts, as a My Schedule section.

My Schedule answers "what am I doing at this event?", and for anyone who
volunteers, the shifts were the one commitment it left out: they lived only on
Volunteer → My Shifts, which this replaced. This is the agenda of what is still
ahead, grouped by day, one row per shift with the one thing to do about it (I'm
here, or Acknowledge) and the rest behind ⋮, then the finished shifts.
"""

from datetime import datetime, timezone
from typing import Optional

from nicegui import ui

from application.services import (
    SystemConfigService,
    VolunteerArrivalService,
)
from application.services.volunteer import can_self_check_in, can_undo_check_in, describe_late
from application.services.volunteer.volunteer_hours import VolunteerHoursService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.timezone import format_local_display, format_local_time, to_local
from models import User
from pages.volunteer_tabs.my_shifts import (
    hours_message,
    open_release_dialog,
    render_shift_brief,
    shift_actions,
    shift_title,
)
from theme.anchors import copy_link_item, shift_anchor
from theme.dialog._helpers import form_dialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.section import section_panel
from theme.shift_arrival_banner import arrival_changed, on_arrival_change
from theme.steady import steady_refreshable

#: The section's DOM id, so a link can scroll to it.
SHIFTS_SECTION_ID = 'my-shifts'


def shift_status(assignment, now: datetime) -> tuple[str, str, str]:
    """(chip kind, icon, text) for one row.

    Module-level and pure so the precedence is testable: being called off says
    the most (staff told them not to come), then arriving says more than
    running late, which says more than the shift being under way, which
    says more than having acknowledged it.
    """
    shift = assignment.shift
    if assignment.called_off_at is not None:
        return 'info', 'event_available', 'Called off'
    if assignment.checked_in_at is not None:
        return 'ok', 'how_to_reg', f'Arrived {format_local_time(assignment.checked_in_at)}'
    late = describe_late(assignment)
    if late:
        return 'pending', 'schedule', late
    if shift.starts_at <= now < shift.ends_at:
        return 'live', 'radio_button_checked', 'On now'
    if assignment.acknowledged_at is not None:
        return 'ok', 'check', 'Acknowledged'
    return 'pending', 'error_outline', 'Needs your OK'


def called_off_note(assignment, now: datetime) -> Optional[str]:
    """The line under a called-off row's chip: what it means for them now."""
    if assignment.called_off_at is None:
        return None
    shift = assignment.shift
    if shift.ends_at <= now:
        return 'Counted as worked'
    if shift.starts_at <= now:
        return 'You can head out. It still counts as worked.'
    return 'No need to come. It still counts as worked.'


def primary_action(assignment, now: datetime) -> Optional[str]:
    """The row's one button: ``'arrive'``, ``'ack'`` or None.

    "I'm here" wins once check-in opens, because by then acknowledging is moot.
    """
    if can_self_check_in(assignment, now):
        return 'arrive'
    if shift_actions(assignment, now)[0]:
        return 'ack'
    return None


def open_shift_details(assignment) -> None:
    """When, what the shift involves, and who else is on it."""
    shift = assignment.shift
    with form_dialog(shift_title(shift), card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-0 full-width'):
            ui.label(
                f'{format_local_display(shift.starts_at)} → '
                f'{format_local_display(shift.ends_at)}'
            ).classes('text-caption')
            render_shift_brief(assignment)
    dialog.open()


def day_heading(starts_at: datetime) -> str:
    local = to_local(starts_at) or starts_at
    return f'{local:%A, %B} {local.day}'


def finished_status(assignment) -> tuple[str, str, str]:
    """(chip kind, icon, text) for a shift that has ended."""
    if assignment.called_off_at is not None:
        return 'info', 'event_available', 'Called off'
    if assignment.checked_in_at is not None:
        return 'ok', 'how_to_reg', f'Arrived {format_local_time(assignment.checked_in_at)}'
    return 'neutral', 'history', 'Finished'


async def my_volunteer_shifts_section(
    user: User, *, release: Optional[int] = None, focus: bool = False,
) -> bool:
    """Build the panel; the caller has already checked ``FeatureFlag.VOLUNTEERS``.

    Nothing renders for someone who has never had a shift: most players never
    volunteer, and an empty panel would sit on every one of their schedules.
    ``release`` is an assignment id from the reminder DM's "Can't make it"
    button and opens that shift's give-back dialog; ``focus`` scrolls here.
    Returns whether the panel was drawn.
    """
    service = VolunteerScheduleService()
    arrival_service = VolunteerArrivalService()
    every = await service.assignments_for_user(user, with_shiftmates=True)
    if not every and release is None:
        return False

    summary = await VolunteerHoursService().for_user(user)
    lead_minutes = await SystemConfigService.get_volunteer_reminder_lead_minutes()
    state = {'finished': False}

    panel = await section_panel(
        'Your volunteer shifts',
        icon='volunteer_activism',
        subtitle=hours_message(summary),
        help_topics=[('my-shifts', 'Your shifts'), 'shift-release'],
        user=user,
    )
    panel.body.props(f'id={SHIFTS_SECTION_ID}')

    async def arrive(assignment) -> None:
        try:
            await arrival_service.check_in_self(assignment.id, user)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify("You're checked in. Thanks!", color='positive')
        await arrival_changed()

    async def undo(assignment) -> None:
        try:
            await arrival_service.undo_check_in(assignment.id, user)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Check-in taken back.', color='info')
        await arrival_changed()

    async def acknowledge(assignment) -> None:
        try:
            await service.acknowledge(assignment.id, user)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Shift acknowledged. Thanks!', color='positive')
        await rows.refresh()

    async def release_shift(assignment) -> None:
        async def released() -> None:
            await rows.refresh()
        await open_release_dialog(assignment, user, lead_minutes, released)

    def render_row(assignment, now: datetime) -> None:
        shift = assignment.shift
        title = shift_title(shift)
        ended = shift.ends_at <= now
        kind, icon, text = finished_status(assignment) if ended else shift_status(assignment, now)
        _, can_release = shift_actions(assignment, now)
        called_off = assignment.called_off_at is not None
        row = ui.element('div').classes(
            'wiz-shift-row' + (' wiz-shift-row--done' if ended or called_off else '')
        )
        row.props['data-wiz-shift'] = shift.id
        with row:
            with ui.column().classes('wiz-shift-row__time gap-0'):
                ui.label(format_local_time(shift.starts_at)).classes('text-weight-medium')
                ui.label(f'to {format_local_time(shift.ends_at)}').classes('text-caption text-muted')
            with ui.column().classes('wiz-shift-row__main gap-1'):
                ui.label(title).classes('text-weight-medium')
                with ui.element('span').classes(f'wiz-chip wiz-chip--{kind}'):
                    ui.icon(icon, size='14px')
                    ui.label(text)
                note = called_off_note(assignment, now)
                if note:
                    ui.label(note).classes('text-caption text-muted')
            with ui.row().classes('wiz-shift-row__actions items-center no-wrap gap-1'):
                action = None if ended else primary_action(assignment, now)
                if action == 'arrive':
                    ui.button("I'm here", icon='place',
                              on_click=lambda a=assignment: arrive(a)).props('color=primary no-caps')
                elif action == 'ack':
                    ui.button('Acknowledge', icon='check',
                              on_click=lambda a=assignment: acknowledge(a)).props('color=primary no-caps')
                with more_menu(title):
                    menu_action('info', 'Shift details', lambda a=assignment: open_shift_details(a))
                    if can_undo_check_in(assignment, user, now):
                        menu_action('undo', 'Undo check-in', lambda a=assignment: undo(a))
                    copy_link_item(shift_anchor(shift.id))
                    if can_release:
                        ui.separator()
                        menu_action('event_busy', "Can't make it…",
                                    lambda a=assignment: release_shift(a), negative=True)

    def render_days(assignments, now: datetime) -> None:
        day = None
        for assignment in assignments:
            heading = day_heading(assignment.shift.starts_at)
            if heading != day:
                day = heading
                ui.label(heading).classes('wiz-shift-day')
            render_row(assignment, now)

    @steady_refreshable
    async def rows() -> None:
        now = datetime.now(timezone.utc)
        current = await service.assignments_for_user(user, with_shiftmates=True)
        upcoming = [a for a in current if a.shift.ends_at > now]
        finished = [a for a in current if a.shift.ends_at <= now]
        if not upcoming:
            ui.label('No upcoming shifts.').classes('text-muted')
        render_days(upcoming, now)
        if finished:
            # Newest first, folded: they matter for the hours, not for what's next.
            with ui.expansion(f'Finished shifts ({len(finished)})', icon='history',
                              value=state['finished'],
                              on_value_change=lambda e: state.update(finished=e.value)) \
                    .classes('wiz-shift-finished full-width q-mt-sm').props('dense'):
                render_days(list(reversed(finished)), now)

    with panel.body:
        with ui.column().classes('gap-0 full-width'):
            await rows()
    on_arrival_change(rows.refresh)

    if focus:
        ui.run_javascript(
            f"document.getElementById('{SHIFTS_SECTION_ID}')"
            "?.closest('.wiz-panel')?.scrollIntoView({block: 'start', behavior: 'smooth'})"
        )
    if release is not None:
        target = await service.find_own_assignment(release, user)
        if target is None:
            ui.notify("That shift isn't on your list any more.", color='warning')
        elif not shift_actions(target, datetime.now(timezone.utc))[1]:
            ui.notify(
                "That shift can't be given back here: it's finished or you're "
                "checked in. Talk to your coordinator.", color='warning',
            )
        else:
            await release_shift(target)
    return True
