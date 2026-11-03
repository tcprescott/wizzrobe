"""Dialog for creating/editing a VolunteerPosition."""

from nicegui import app, ui

from application.services import get_user_from_session
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from models import VolunteerPosition
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet, submit_on_enter
from theme.notify import notify_error


class VolunteerPositionDialog:
    def __init__(self, position: VolunteerPosition = None, on_submit=None):
        self.position = position
        self.on_submit = on_submit
        self.dialog = None
        self.service = VolunteerPositionService()

    async def open(self):
        editing = self.position is not None
        default_name = self.position.name if editing else ''
        default_desc = (self.position.description or '') if editing else ''
        default_order = self.position.display_order if editing else 0
        default_active = self.position.is_active if editing else True
        default_length = self.position.shift_length_minutes if editing else None
        default_stagger = self.position.stagger_minutes if editing else None
        default_alerts = self.position.staff_alerts if editing else False
        title = 'Edit Position' if editing else 'Add Position'

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(title, dialog)
            with ui.column().classes('q-pa-md gap-2'):
                name_input = ui.input('Position Name', value=default_name).classes('input-full-width')
                desc_input = ui.input('Description', value=default_desc).classes('input-full-width')
                order_input = ui.number('Display Order', value=default_order, format='%d').props('inputmode=numeric').classes('input-full-width')
                active_checkbox = ui.checkbox('Active', value=default_active)
                with ui.row().classes('items-center no-wrap gap-3 full-width'):
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label('Staff alerts while on shift').classes('text-weight-bold')
                        ui.label(
                            "Whoever's on this shift gets player comments and reschedule "
                            'requests. With nobody on shift, all staff get them.'
                        ).classes('text-muted text-caption')
                    alerts_switch = ui.switch(value=default_alerts) \
                        .props('aria-label="Staff alerts while on shift"')
                length_input = ui.number(
                    'Shift length (min)', value=default_length, format='%d',
                ).props('inputmode=numeric').classes('input-full-width')
                stagger_input = ui.number(
                    'Stagger interval (min)', value=default_stagger, format='%d',
                ).props('inputmode=numeric').classes('input-full-width')
                ui.label(
                    'Leave both blank for fixed shared blocks; set both to stagger handoffs.'
                ).classes('text-caption text-grey')

            def _opt_int(value):
                if value is None or value == '':
                    return None
                return int(value)

            async def submit():
                actor = await get_user_from_session(app.storage.user)
                try:
                    order_value = int(order_input.value or 0)
                    length_value = _opt_int(length_input.value)
                    stagger_value = _opt_int(stagger_input.value)
                    if editing:
                        result = await self.service.update(
                            actor, self.position,
                            name=name_input.value.strip(),
                            description=(desc_input.value or '').strip() or None,
                            display_order=order_value,
                            is_active=active_checkbox.value,
                            shift_length_minutes=length_value,
                            stagger_minutes=stagger_value,
                            staff_alerts=alerts_switch.value,
                        )
                    else:
                        result = await self.service.create(
                            actor,
                            name=name_input.value.strip(),
                            description=(desc_input.value or '').strip() or None,
                            display_order=order_value,
                            is_active=active_checkbox.value,
                            shift_length_minutes=length_value,
                            stagger_minutes=stagger_value,
                            staff_alerts=alerts_switch.value,
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
