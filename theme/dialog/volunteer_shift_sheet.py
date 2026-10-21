"""Everything staff can do to one volunteer shift, in one sheet.

Admin → Shift Board opens this when a shift is tapped: a side sheet on a
desktop, a bottom sheet on a phone, like the match sheet on the run sheet. It
shows who is on the shift and whether they've turned up, and holds the shift's
edits: assign, check in, swap, move, remove, edit and delete.

The sheet reads the board's latest grid through ``lookup`` rather than holding
a copy, so after any change (here, or by someone else and picked up by the
board's timer) ``sync`` repaints it from the same data the grid shows.
"""

from typing import Awaitable, Callable, List, Optional, Tuple

from nicegui import app, ui

from application.services import VolunteerArrivalService, get_user_from_session
from application.services.volunteer import (
    GridPerson,
    GridShift,
    VolunteerScheduleService,
    VolunteerShiftMoveService,
)
from application.utils.timezone import timezone_label, to_local
from models import User
from theme.dialog._helpers import dialog_actions, dialog_header, dialog_more_menu, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.volunteer_assign_dialog import VolunteerAssignDialog
from theme.dialog.volunteer_shift_dialog import VolunteerShiftDialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.volunteer_grid import day_label, filled_label, person_status, shift_range

# (position name, shift) pairs for every shift on the board's day.
DayShifts = List[Tuple[str, GridShift]]

_STALE = 'That shift has changed since the board loaded. It has been refreshed.'


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
        # mobile_sheet's own breakpoint, but a side sheet rather than a centred
        # card above it: the grid stays readable beside the open shift.
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

    # --- Rendering -----------------------------------------------------

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
            with more_menu(person.name):
                if not person.draft and person.checked_in_at is None:
                    menu_action('how_to_reg', 'Check in', lambda: self._check_in(person))
                if person.checked_in_at is not None:
                    menu_action('undo', 'Undo check-in…', lambda: self._confirm_undo_check_in(person))
                menu_action('swap_horiz', 'Swap with someone…',
                            lambda: self._swap(person, position, shift))
                menu_action('arrow_forward', 'Move to another shift…',
                            lambda: self._move(person, shift))
                ui.separator()
                menu_action('person_remove', 'Remove from shift…',
                            lambda: self._confirm_remove(person, position, shift), negative=True)

    # --- Actions ---------------------------------------------------------

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
