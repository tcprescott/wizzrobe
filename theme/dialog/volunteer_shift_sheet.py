"""Everything staff can do to one volunteer shift, in one sheet.

Admin → Shift Board opens this when a shift is tapped: a side sheet on a
desktop, a bottom sheet on a phone, like the match sheet on the run sheet. It
shows who is on the shift and whether they've turned up, and holds the shift's
edits: assign, check in, call off, swap, move, remove, edit and delete.

The sheet reads the board's latest grid through ``lookup`` rather than holding
a copy, so after any change (here, or by someone else and picked up by the
board's timer) ``sync`` repaints it from the same data the grid shows.
"""

from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional, Tuple

from nicegui import app, ui

from application.services import (
    VolunteerArrivalService,
    VolunteerCallOffService,
    get_user_from_session,
)
from application.services.volunteer import (
    GridPerson,
    GridShift,
    VolunteerScheduleService,
    VolunteerShiftMoveService,
)
from application.services.volunteer.volunteer_schedule_grid import CALLED_OFF
from application.utils.discord_messages_volunteer import REASON_MAX_CHARS
from application.utils.timezone import timezone_label, to_local
from models import User
from theme.dialog._helpers import dialog_actions, dialog_header, dialog_more_menu, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.volunteer_assign_dialog import VolunteerAssignDialog
from theme.dialog.volunteer_shift_dialog import VolunteerShiftDialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.volunteer_grid import day_label, filled_label, person_status, shift_range

DayShifts = List[Tuple[str, GridShift]]

_STALE = 'That shift has changed since the board loaded. It has been refreshed.'


def _ended(shift: GridShift) -> bool:
    return shift.ends_at <= datetime.now(timezone.utc)


def _hours(shift: GridShift) -> str:
    hours = round((shift.ends_at - shift.starts_at).total_seconds() / 3600, 1)
    return f'{hours:g} hour' + ('' if hours == 1 else 's')


def _show_warnings(warnings: List[str]) -> None:
    """The soft warnings an assign returns (overfilled, outside availability)."""
    for warning in warnings:
        ui.notify(warning, color='warning')


class VolunteerShiftSheet:
    def __init__(
        self,
        lookup: Callable[[int], Optional[Tuple[str, GridShift]]],
        day_shifts: Callable[[], DayShifts],
        on_changed: Callable[[], Awaitable[None]],
        on_closed: Callable[[], None],
    ) -> None:
        self.lookup = lookup
        self.day_shifts = day_shifts
        self.on_changed = on_changed
        self.on_closed = on_closed
        self.service = VolunteerScheduleService()
        self.moves = VolunteerShiftMoveService()
        self.shift_id = 0
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet shift-sheet'):
            dialog_header('Shift details', self.dialog)
            self._content = ui.refreshable(self._render)
            self._content()
        self.dialog.on('hide', self._hidden)

    def _hidden(self) -> None:
        self.shift_id = 0
        self.on_closed()

    def open(self, shift_id: int) -> None:
        self.shift_id = shift_id
        with self.dialog:
            self._content.refresh()
            self.dialog.open()

    def sync(self) -> None:
        """Repaint from the board's latest grid; close if the shift is gone."""
        if not self.shift_id:
            return
        if self.lookup(self.shift_id) is None:
            self.dialog.close()
            return
        self._content.refresh()

    async def _actor(self) -> User:
        user = await get_user_from_session(app.storage.user)
        if user is None:
            raise PermissionError('Your session has ended. Sign in again.')
        return user

    async def _changed(self) -> None:
        await self.on_changed()
        self.sync()

    async def _stale(self) -> None:
        ui.notify(_STALE, color='info')
        await self._changed()

    def _render(self) -> None:
        found = self.lookup(self.shift_id) if self.shift_id else None
        if found is None:
            return
        position, shift = found
        with ui.column().classes('match-sheet__body full-width'):
            ui.label(position).classes('match-sheet__title')
            ui.label(
                f'{day_label(to_local(shift.starts_at).date())} · '  # type: ignore[union-attr]
                f'{shift_range(shift)} {timezone_label()}'
            ).classes('text-caption text-muted')
            if shift.label:
                ui.label(shift.label).classes('text-caption')
            ui.label(filled_label(shift)).classes(
                'wiz-chip self-start ' + ('wiz-chip--pending' if shift.open_slots else 'wiz-chip--ok')
            )

            ui.label('People').classes('match-sheet__section')
            if not shift.people:
                ui.label('Nobody on this shift yet.').classes('italic-note')
            for person in shift.people:
                self._person_row(person, position, shift)
            if shift.open_slots:
                slots = 'slot' if shift.open_slots == 1 else 'slots'
                ui.label(f'{shift.open_slots} open {slots}').classes('shift-sheet__open')

            if shift.notes:
                ui.label('Shift notes').classes('match-sheet__section')
                ui.label(shift.notes).classes('wiz-free-text')

        with dialog_actions():
            with dialog_more_menu():
                menu_action('edit', 'Edit shift…', lambda: self._edit_shift(shift.shift_id))
                if shift.can_call_off and not _ended(shift):
                    menu_action('event_available', 'Call off whole shift…',
                                lambda: self._confirm_call_off_shift(position, shift))
                ui.separator()
                menu_action('delete', 'Delete shift…', lambda: self._confirm_delete(shift), negative=True)
            ui.space()
            ui.button('Assign volunteer', icon='person_add',
                      on_click=lambda: self._assign(shift.shift_id)) \
                .props('no-caps color=primary')

    def _person_row(self, person: GridPerson, position: str, shift: GridShift) -> None:
        status, tone = person_status(person)
        with ui.row().classes('shift-sheet__person items-center no-wrap full-width'):
            with ui.column().classes('gap-0 col'):
                ui.label(person.name).classes(
                    'text-weight-medium' + (' wiz-vgrid__name--draft' if person.draft else '')
                )
                ui.label(status).classes(f'text-caption {tone}')
            called_off = person.arrival == CALLED_OFF
            open_ = not _ended(shift)
            with more_menu(person.name):
                if not person.draft and person.checked_in_at is None and not called_off:
                    menu_action('how_to_reg', 'Check in', lambda: self._check_in(person))
                if not person.draft and not called_off and open_:
                    menu_action('event_available', 'Call off…',
                                lambda: self._confirm_call_off(person, position, shift))
                if called_off and open_:
                    menu_action('undo', 'Undo call-off…',
                                lambda: self._confirm_undo_call_off(person, position, shift))
                if person.checked_in_at is not None and not called_off:
                    menu_action('undo', 'Undo check-in…', lambda: self._confirm_undo_check_in(person))
                menu_action('swap_horiz', 'Swap with someone…',
                            lambda: self._swap(person, position, shift))
                menu_action('arrow_forward', 'Move to another shift…',
                            lambda: self._move(person, shift))
                ui.separator()
                menu_action('person_remove', 'Remove from shift…',
                            lambda: self._confirm_remove(person, position, shift), negative=True)

    async def _check_in(self, person: GridPerson) -> None:
        try:
            await self.service.check_in(person.assignment_id, await self._actor())
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(f'Checked in {person.name}.', color='positive')
        await self._changed()

    def _confirm_undo_check_in(self, person: GridPerson) -> None:
        async def undo() -> None:
            try:
                await VolunteerArrivalService().undo_check_in_as_coordinator(
                    person.assignment_id, await self._actor(),
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'{person.name} is no longer checked in.', color='info')
            await self._changed()

        ConfirmationDialog(
            message=f'Mark {person.name} as not here yet?',
            on_confirm=undo, confirm_text='Undo check-in', title='Undo check-in?',
        ).open()

    def _call_off_dialog(
        self, title: str, message: str, note_label: str,
        do_call_off: Callable[[Optional[str]], Awaitable[bool]],
    ) -> None:
        """The confirm both call-offs share: what happens, plus a note for the DM."""
        with form_dialog(title, card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md gap-2 full-width'):
                ui.label(message).classes('text-body2')
                note = ui.textarea(note_label).classes('full-width').props(
                    f'maxlength={REASON_MAX_CHARS} counter '
                    'hint="Goes in the DM."'
                )

            async def go() -> None:
                if await do_call_off(note.value):
                    dialog.close()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat no-caps')
                ui.button('Call off', on_click=go).props('color=primary no-caps')
        dialog.open()

    def _confirm_call_off(self, person: GridPerson, position: str, shift: GridShift) -> None:
        started = shift.starts_at <= datetime.now(timezone.utc)
        hours = _hours(shift)
        if started:
            message = (f'{person.name} can head out from {position} ({shift_range(shift)}). '
                       f"They'll get a DM now, and the whole shift still counts as worked: "
                       f'all {hours} toward their total and comps.')
        else:
            message = (f"{person.name} doesn't need to come in for {position} "
                       f"({shift_range(shift)}). They'll get a DM now, and the shift still "
                       f'counts as worked: all {hours} toward their total and comps.')

        async def call_off(note: Optional[str]) -> bool:
            try:
                await VolunteerCallOffService().call_off(
                    person.assignment_id, await self._actor(), note,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return False
            ui.notify(f'Called off {person.name}. They still get credit.', color='positive')
            await self._changed()
            return True

        self._call_off_dialog(
            f"Call off {person.name}'s shift?", message, f'Note to {person.name} (optional)', call_off,
        )

    def _confirm_call_off_shift(self, position: str, shift: GridShift) -> None:
        count = shift.can_call_off
        people = 'person' if count == 1 else 'people'
        message = (f"All {count} {people} on {position} ({shift_range(shift)}) will get a DM "
                   f"saying they're not needed, and the shift still counts as worked for "
                   f'each of them: {_hours(shift)} toward their total and comps.')
        if any(p.draft for p in shift.people):
            message += ' Drafts are left as they are.'

        async def call_off(note: Optional[str]) -> bool:
            try:
                done = await VolunteerCallOffService().call_off_shift(
                    shift.shift_id, await self._actor(), note,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return False
            called = 'person' if len(done) == 1 else 'people'
            ui.notify(f'Called off {len(done)} {called}. They still get credit.', color='positive')
            await self._changed()
            return True

        self._call_off_dialog(
            f'Call off all {count} {people} on {position}?', message, 'Note to everyone (optional)',
            call_off,
        )

    def _confirm_undo_call_off(self, person: GridPerson, position: str, shift: GridShift) -> None:
        async def undo() -> None:
            try:
                await VolunteerCallOffService().undo_call_off(person.assignment_id, await self._actor())
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'{person.name} is back on the shift.', color='info')
            await self._changed()

        ConfirmationDialog(
            message=(f"Put {person.name} back on {position} ({shift_range(shift)})? "
                     "They'll get a DM saying they're needed after all."),
            on_confirm=undo, confirm_text='Undo call-off', title='Undo call-off?', tone='primary',
        ).open()

    async def _assign(self, shift_id: int) -> None:
        model = await self.service.get_shift(shift_id)
        if model is None:
            await self._stale()
            return

        async def do_assign(user: User) -> bool:
            try:
                _, warnings = await self.service.assign(await self._actor(), model, user)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return False
            _show_warnings(warnings)
            ui.notify(f'Assigned {user.preferred_name}.', color='positive')
            await self._changed()
            return True
        await VolunteerAssignDialog(model, do_assign).open()

    async def _swap(self, person: GridPerson, position: str, shift: GridShift) -> None:
        model = await self.service.get_shift(shift.shift_id)
        assignment = await self.service.get_assignment(person.assignment_id)
        if model is None or assignment is None:
            await self._stale()
            return

        async def do_swap(user: User) -> bool:
            try:
                _, warnings = await self.moves.replace_assignment(await self._actor(), assignment, user)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return False
            _show_warnings(warnings)
            ui.notify(f'Swapped {user.preferred_name} in for {person.name}.', color='positive')
            await self._changed()
            return True
        await VolunteerAssignDialog(
            model, do_swap, action='Swap in',
            title=f'Swap {person.name} on {position} ({shift_range(shift)})',
        ).open()

    def _move(self, person: GridPerson, shift: GridShift) -> None:
        targets = [(p, s) for p, s in self.day_shifts() if s.shift_id != shift.shift_id]
        with form_dialog(f'Move {person.name}') as dialog:
            with ui.column().classes('q-pa-sm gap-1 full-width').style('max-height: 60vh; overflow-y: auto;'):
                if not targets:
                    ui.label('There are no other shifts on this day.').classes('italic-note')
                current = None
                for position, target in targets:
                    if position != current:
                        current = position
                        ui.label(position).classes('match-sheet__section')
                    with ui.row().classes('items-center no-wrap full-width gap-2'):
                        with ui.column().classes('gap-0 col'):
                            ui.label(shift_range(target))
                            ui.label(filled_label(target)).classes(
                                'text-caption ' + ('st-pending' if target.open_slots else 'text-muted')
                            )

                        async def go(t=target) -> None:
                            if await self._do_move(person, t):
                                dialog.close()
                        ui.button('Move here', on_click=go).props('dense flat no-caps color=primary')
        dialog.open()

    async def _do_move(self, person: GridPerson, target: GridShift) -> bool:
        assignment = await self.service.get_assignment(person.assignment_id)
        model = await self.service.get_shift(target.shift_id)
        if assignment is None or model is None:
            await self._stale()
            return False
        try:
            _, warnings = await self.moves.move_assignment(await self._actor(), assignment, model)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        _show_warnings(warnings)
        ui.notify(f'Moved {person.name}.', color='positive')
        await self._changed()
        return True

    def _confirm_remove(self, person: GridPerson, position: str, shift: GridShift) -> None:
        told = '' if person.draft else (
            '\n\nThey’ve already been told about this shift, so they’ll get a DM saying it’s off.'
        )

        async def remove() -> None:
            assignment = await self.service.get_assignment(person.assignment_id)
            if assignment is None:
                await self._stale()
                return
            try:
                await self.service.unassign(await self._actor(), assignment)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'Removed {person.name}.', color='info')
            await self._changed()

        ConfirmationDialog(
            message=f'Take {person.name} off {position} ({shift_range(shift)})?{told}',
            on_confirm=remove, confirm_text='Remove', title='Remove from shift?',
        ).open()

    async def _edit_shift(self, shift_id: int) -> None:
        model = await self.service.get_shift(shift_id)
        if model is None:
            await self._stale()
            return

        async def after(_) -> None:
            await self._changed()
        await VolunteerShiftDialog(shift=model, on_submit=after).open()

    def _confirm_delete(self, shift: GridShift) -> None:
        assigned = len(shift.people)
        message = 'Delete this shift?'
        if assigned:
            message = (f'This shift has {assigned} volunteer(s) on it. Deleting it removes them, '
                       'and anyone already told gets a DM saying it’s off.')

        async def delete() -> None:
            model = await self.service.get_shift(shift.shift_id)
            if model is not None:
                try:
                    await self.service.delete_shift(await self._actor(), model)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Shift deleted.', color='info')
            self.dialog.close()
            await self.on_changed()

        ConfirmationDialog(message=message, on_confirm=delete, confirm_text='Delete',
                           title='Delete shift?').open()
