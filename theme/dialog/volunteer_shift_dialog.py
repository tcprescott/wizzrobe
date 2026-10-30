"""Dialog for creating/editing a VolunteerShift."""

from datetime import datetime, timedelta

from nicegui import app, ui

from application.services import get_user_from_session
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.timezone import (
    format_local_date,
    input_time,
    parse_local_datetime,
)
from models import VolunteerPosition, VolunteerShift
from theme.dialog._helpers import (
    dialog_actions,
    dialog_header,
    mobile_sheet,
    native_date_input,
    native_time_input,
    submit_on_enter,
)
from theme.notify import notify_error


class VolunteerShiftDialog:
    def __init__(
        self,
        shift: VolunteerShift | None = None,
        position: VolunteerPosition | None = None,
        default_day: str | None = None,
        on_submit=None,
        default_start: datetime | None = None,
        default_hours: int = 2,
    ):
        self.shift = shift
        self.position = position
        self.default_day = default_day
        self.default_start = default_start
        self.default_hours = default_hours
        self.on_submit = on_submit
        self.dialog = None
        self.position_service = VolunteerPositionService()
        self.schedule_service = VolunteerScheduleService()

    async def open(self):
        editing = self.shift is not None
        positions = await self.position_service.list_all()
        position_options = {p.id: p.name for p in positions}

        if editing:
            default_position = self.shift.position_id
            default_start_date = format_local_date(self.shift.starts_at)
            default_start_time = input_time(self.shift.starts_at)
            default_end_date = format_local_date(self.shift.ends_at)
            default_end_time = input_time(self.shift.ends_at)
            default_label = self.shift.label or ''
            default_slots = self.shift.slots_needed
            default_notes = self.shift.notes or ''
        elif self.default_start is not None:
            default_position = self.position.id if self.position else None
            ends = self.default_start + timedelta(hours=self.default_hours)
            default_start_date = format_local_date(self.default_start)
            default_start_time = input_time(self.default_start)
            default_end_date = format_local_date(ends)
            default_end_time = input_time(ends)
            default_label = ''
            default_slots = 1
            default_notes = ''
        else:
            default_position = self.position.id if self.position else None
            default_start_date = self.default_day or ''
            default_start_time = ''
            default_end_date = self.default_day or ''
            default_end_time = ''
            default_label = ''
            default_slots = 1
            default_notes = ''

        title = 'Edit Shift' if editing else 'Add Shift'

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(title, dialog)
            with ui.column().classes('q-pa-md gap-2'):
                position_select = ui.select(
                    position_options, value=default_position, label='Position',
                ).classes('input-full-width')
                with ui.row().classes('form-pair gap-2'):
                    start_date_input = native_date_input('Start date', default_start_date)
                    start_time_input = native_time_input('Start time', default_start_time)
                end_date_edited = {'value': editing}

                with ui.row().classes('form-pair gap-2'):
                    end_date_input = native_date_input('End date', default_end_date)
                    end_time_input = native_time_input('End time', default_end_time)
                end_date_input.on('change', lambda: end_date_edited.update(value=True))

                def _sync_end_date(e):
                    if not end_date_edited['value']:
                        end_date_input.value = e.value
                start_date_input.on_value_change(_sync_end_date)

                label_input = ui.input('Label (optional)', value=default_label).classes('input-full-width')
                slots_input = ui.number(
                    'Slots needed', value=default_slots, format='%d', min=1,
                ).props('inputmode=numeric').classes('input-full-width')
                notes_input = ui.textarea('Notes (optional)', value=default_notes).classes('input-full-width')

            async def submit():
                actor = await get_user_from_session(app.storage.user)
                try:
                    if not position_select.value:
                        raise ValueError('Select a position.')
                    if not (start_date_input.value and start_time_input.value
                            and end_date_input.value and end_time_input.value):
                        raise ValueError('Set a start and end date/time.')
                    starts_at = parse_local_datetime(start_date_input.value, start_time_input.value)
                    ends_at = parse_local_datetime(end_date_input.value, end_time_input.value)
                    label = (label_input.value or '').strip() or None
                    notes = (notes_input.value or '').strip() or None
                    slots = int(slots_input.value or 1)

                    if editing:
                        result = await self.schedule_service.update_shift(
                            actor, self.shift,
                            position_id=position_select.value,
                            starts_at=starts_at, ends_at=ends_at,
                            label=label, slots_needed=slots, notes=notes,
                        )
                    else:
                        result = await self.schedule_service.create_shift(
                            actor,
                            position_id=position_select.value,
                            starts_at=starts_at, ends_at=ends_at,
                            label=label, slots_needed=slots, notes=notes,
                        )
                    dialog.close()
                    if self.on_submit:
                        await self.on_submit(result)
                except (ValueError, PermissionError) as e:
                    with self.dialog:
                        notify_error(e)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save' if editing else 'Create', on_click=submit).props('color=primary')

            submit_on_enter(dialog, submit)
            dialog.open()
