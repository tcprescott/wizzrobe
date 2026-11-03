"""Pick a volunteer for one shift: searchable, qualified first, availability shown.

Shared by Admin → Volunteer Shifts and Admin → Shift Board. The dialog only
picks; ``on_pick`` does the assigning (or the swapping) and returns whether it
worked, so the dialog stays open on a refusal.
"""

from typing import Awaitable, Callable, Optional

from nicegui import ui

from application.services.volunteer.volunteer_availability_service import VolunteerAvailabilityService
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.services.volunteer.volunteer_qualification_service import VolunteerQualificationService
from application.utils.timezone import format_local_time_range
from models import User, VolunteerAvailabilityStatus, VolunteerShift
from theme.dialog._helpers import form_dialog


class VolunteerAssignDialog:
    def __init__(
        self,
        shift: VolunteerShift,
        on_pick: Callable[[User], Awaitable[bool]],
        *,
        title: Optional[str] = None,
        action: str = 'Assign',
    ) -> None:
        self.shift = shift
        self.on_pick = on_pick
        self.title = title
        self.action = action

    async def open(self) -> None:
        shift = self.shift
        pool = await VolunteerPoolService().assignable_volunteers()
        avail_map = await VolunteerAvailabilityService().availability_map(
            [u.id for u in pool], shift.starts_at, shift.ends_at,
        )
        assigned_ids = {a.user_id for a in shift.assignments}  # type: ignore[attr-defined]
        qualified_ids = await VolunteerQualificationService() \
            .get_qualified_user_ids_for_position(shift.position_id)  # type: ignore[attr-defined]
        has_qualifications = bool(qualified_ids)
        picker_state = {'show_all': not has_qualifications}
        search_state = {'text': ''}

        position = shift.position.name if shift.position else 'Shift'
        title = self.title or \
            f'Assign to {position} ({format_local_time_range(shift.starts_at, shift.ends_at)})'
        with form_dialog(title) as dialog:
            if not pool:
                ui.label('No volunteers in pool. Users must have the Volunteer or Staff role assigned.') \
                    .classes('italic-note q-pa-md')

            if pool:
                def on_search_change(e) -> None:
                    search_state['text'] = (e.value or '').strip().lower()
                    picker_list.refresh()
                ui.input('Search by name', on_change=on_search_change) \
                    .props('dense clearable debounce=200 autofocus') \
                    .classes('full-width q-px-sm q-pt-xs')

            if has_qualifications:
                with ui.row().classes('items-center q-px-sm q-pt-xs'):
                    def on_show_all_change(e) -> None:
                        picker_state['show_all'] = e.value
                        picker_list.refresh()
                    ui.checkbox('Show unqualified volunteers', value=False,
                                on_change=on_show_all_change)

            @ui.refreshable
            def picker_list() -> None:
                visible = [v for v in pool if v.id not in assigned_ids]
                if not picker_state['show_all']:
                    visible = [v for v in visible if v.id in qualified_ids]
                if search_state['text']:
                    visible = [v for v in visible if search_state['text'] in v.preferred_name.lower()]
                with ui.column().classes('q-pa-sm gap-1').style('max-height: 50vh; overflow-y: auto;'):
                    if not visible:
                        empty = ('No one matches that search.' if search_state['text']
                                 else 'No qualified volunteers available.')
                        ui.label(empty).classes('italic-note')
                    for volunteer in visible:
                        status = VolunteerAvailabilityService.covers(
                            avail_map.get(volunteer.id, []), shift.starts_at, shift.ends_at,
                        )
                        self._row(
                            dialog, volunteer, status,
                            show_qualification=picker_state['show_all'] and has_qualifications,
                            is_qualified=volunteer.id in qualified_ids,
                        )

            picker_list()
        dialog.open()

    def _row(self, dialog, volunteer: User, status, *, show_qualification: bool, is_qualified: bool) -> None:
        with ui.row().classes('items-center justify-between full-width no-wrap'):
            with ui.row().classes('items-center gap-2'):
                ui.label(volunteer.preferred_name)
                if show_qualification and is_qualified:
                    ui.badge('Qualified', color='primary')
                if status == VolunteerAvailabilityStatus.PREFERRED:
                    ui.badge('Preferred', color='positive')
                elif status == VolunteerAvailabilityStatus.AVAILABLE:
                    ui.badge('Available', color='positive').props('outline')
                elif status == VolunteerAvailabilityStatus.UNAVAILABLE:
                    ui.badge('Unavailable', color='negative')
                else:
                    ui.badge('Not stated', color='grey').props('outline') \
                        .tooltip('No availability window covers this shift')

            async def pick(u=volunteer) -> None:
                if await self.on_pick(u):
                    dialog.close()
            ui.button(self.action, on_click=pick).props('dense flat color=primary')
