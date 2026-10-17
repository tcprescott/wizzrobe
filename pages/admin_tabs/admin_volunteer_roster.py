"""Admin Volunteer Roster page — view availability and manage qualifications."""

from datetime import datetime, timedelta, timezone

from nicegui import app, ui

from application.services import AuthService, get_user_from_session
from application.services.volunteer.volunteer_availability_service import VolunteerAvailabilityService
from application.services.volunteer.volunteer_hours import VolunteerHoursService
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.services.volunteer.volunteer_qualification_service import VolunteerQualificationService
from application.utils.timezone import format_local_date, format_local_time_range
from models import VolunteerAvailabilityStatus
from theme.dialog.volunteer_export_dialog import VolunteerExportDialog
from theme.dialog.volunteer_profile_dialog import VolunteerProfileDialog
from theme.help import help_icon
from theme.page_header import page_header
from theme.tables.preferences import TableKeys, customize_table, preferences_button

_STATUS_ABBR = {
    VolunteerAvailabilityStatus.PREFERRED: 'Preferred',
    VolunteerAvailabilityStatus.AVAILABLE: 'Available',
    VolunteerAvailabilityStatus.UNAVAILABLE: 'Unavailable',
}

_COLUMNS: list[dict] = [
    {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True, 'align': 'left'},
    # Sorts on the raw number, never the rendered string — "10" must not fall
    # between "1" and "2".
    {'name': 'hours', 'label': 'Hours', 'field': 'hours', 'sortable': True, 'align': 'right'},
    {'name': 'qualifications', 'label': 'Qualifications', 'field': 'qualifications', 'align': 'left'},
    {'name': 'availability', 'label': 'Availability', 'field': 'availability', 'align': 'left'},
    {'name': 'actions', 'label': '', 'field': 'id', 'align': 'right'},
]


def _tier_label(summary) -> str:
    """The comp tier this volunteer has cleared, for the chip beside their hours."""
    if summary is None or summary.tier_cleared is None:
        return ''
    tier = summary.tier_cleared
    return f'{int(tier) if float(tier).is_integer() else tier}h'


async def admin_volunteer_roster_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    if not await AuthService.can_manage_volunteers(actor):
        ui.label('You do not have permission to manage volunteers.').classes('text-error')
        return

    pool_service = VolunteerPoolService()
    position_service = VolunteerPositionService()
    availability_service = VolunteerAvailabilityService()
    hours_service = VolunteerHoursService()
    qualification_service = VolunteerQualificationService()

    volunteers_by_id: dict = {}

    with ui.column().classes('page-container-wide'):
        with page_header(
            'Volunteers',
            'Everyone with the Volunteer or Staff role, their qualifications, and '
            'declared availability. Manage a volunteer to edit which positions they can fill.',
        ):
            await help_icon('vol-roster', 'vol-hours', user=actor)
            ui.space()
            # Filled once the table exists; the header is drawn first.
            gear_slot = ui.row().classes('items-center')
            ui.button('Export data', icon='download',
                      on_click=lambda: VolunteerExportDialog().open()) \
                .props('flat color=primary') \
                .tooltip('Download the roster, availability and schedule as CSVs')
            ui.button(icon='refresh', on_click=lambda: load_roster()) \
                .props('flat color=primary').tooltip('Refresh')


        table = ui.table(
            columns=_COLUMNS,
            rows=[],
            row_key='id',
        ).classes('full-width wiz-table').props(':grid="Quasar.Screen.lt.md"')

        # --- Desktop cell slots ---

        table.add_slot('body-cell-hours', '''
            <q-td :props="props">
                <span class="text-weight-medium">{{ props.value }}</span>
                <span v-if="props.row.tier" class="wiz-chip wiz-chip--ok q-ml-xs">{{ props.row.tier }}</span>
            </q-td>
        ''')

        table.add_slot('body-cell-qualifications', '''
            <q-td :props="props">
                <template v-if="props.value">
                    <q-chip v-for="q in props.value.split(',')" :key="q"
                            dense outline color="primary" class="q-ma-xs">{{ q.trim() }}</q-chip>
                </template>
                <span v-else class="text-grey-7">—</span>
            </q-td>
        ''')

        table.add_slot('body-cell-availability', '''
            <q-td :props="props">
                <div v-if="props.value">
                    <div v-for="line in props.value.split('\\n')" :key="line"
                         class="text-caption">{{ line }}</div>
                </div>
                <span v-else class="text-grey-7">—</span>
            </q-td>
        ''')

        table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat dense icon="manage_accounts" color="primary"
                       @click="$parent.$emit('manage_volunteer', props.row)"
                       title="View availability &amp; qualifications" />
            </q-td>
        ''')

        # --- Mobile card slot ---

        table.add_slot('item', '''
            <div class="q-pa-xs col-xs-12 col-sm-6 col-md-4">
                <q-card bordered flat class="q-pa-sm">
                    <div class="row items-center q-mb-xs">
                        <div class="col-5 text-grey-7 text-caption">Name</div>
                        <div class="col-7 text-weight-medium">{{ props.row.name }}</div>
                    </div>
                    <div class="row items-center q-mb-xs">
                        <div class="col-5 text-grey-7 text-caption">Hours</div>
                        <div class="col-7">
                            <span class="text-weight-medium">{{ props.row.hours }}</span>
                            <span v-if="props.row.tier" class="wiz-chip wiz-chip--ok q-ml-xs">{{ props.row.tier }}</span>
                        </div>
                    </div>
                    <div class="row items-start q-mb-xs">
                        <div class="col-5 text-grey-7 text-caption">Qualifications</div>
                        <div class="col-7">
                            <template v-if="props.row.qualifications">
                                <q-chip v-for="q in props.row.qualifications.split(',')" :key="q"
                                        dense outline color="primary" class="q-ma-none">{{ q.trim() }}</q-chip>
                            </template>
                            <span v-else class="text-grey-7 text-caption">None</span>
                        </div>
                    </div>
                    <div class="row items-start q-mb-xs">
                        <div class="col-5 text-grey-7 text-caption">Availability</div>
                        <div class="col-7">
                            <template v-if="props.row.availability">
                                <div v-for="line in props.row.availability.split('\\n')" :key="line"
                                     class="text-caption">{{ line }}</div>
                            </template>
                            <span v-else class="text-grey-7 text-caption">None declared</span>
                        </div>
                    </div>
                    <div class="row justify-end q-mt-xs">
                        <q-btn flat dense icon="manage_accounts" color="primary"
                               @click="$parent.$emit('manage_volunteer', props.row)"
                               title="View availability &amp; qualifications" />
                    </div>
                </q-card>
            </div>
        ''')

        customize_table(table, _COLUMNS, key=TableKeys.ADMIN_VOLUNTEER_ROSTER)
        with gear_slot:
            preferences_button(table)

        # --- Data loading ---

        async def load_roster() -> None:
            nonlocal volunteers_by_id

            now = datetime.now(timezone.utc)
            win_start = now - timedelta(days=365)
            win_end = now + timedelta(days=365)

            volunteers = await pool_service.assignable_volunteers()
            volunteers_by_id = {u.id: u for u in volunteers}

            positions = await position_service.list_all()
            position_by_id = {p.id: p for p in positions}

            hours_by_user = {s.user_id: s for s in await hours_service.roster(actor=actor)}

            all_quals = await qualification_service.list_all_qualifications()
            qual_map: dict[int, list[str]] = {}
            for q in all_quals:
                pos = position_by_id.get(q.position_id)
                if pos:
                    qual_map.setdefault(q.user_id, []).append(pos.name)

            avail_map = await availability_service.availability_map(
                [u.id for u in volunteers], win_start, win_end,
            )

            rows = []
            for volunteer in volunteers:
                windows = sorted(avail_map.get(volunteer.id, []), key=lambda w: w.starts_at)
                avail_lines = [
                    f'{_STATUS_ABBR.get(w.status, w.status)}: '
                    f'{format_local_date(w.starts_at)} '
                    f'{format_local_time_range(w.starts_at, w.ends_at)}'
                    for w in windows
                ]
                summary = hours_by_user.get(volunteer.id)
                rows.append({
                    'id': volunteer.id,
                    'name': volunteer.preferred_name,
                    'hours': summary.hours if summary else 0,
                    'tier': _tier_label(summary),
                    'qualifications': ', '.join(sorted(qual_map.get(volunteer.id, []))),
                    'availability': '\n'.join(avail_lines),
                })

            table.rows = rows
            table.update()

        # --- Event handler ---

        async def handle_manage(event) -> None:
            row = event.args if isinstance(event.args, dict) else {}
            user_id = row.get('id')
            user = volunteers_by_id.get(user_id)
            if not user:
                ui.notify("Couldn't find that volunteer. They may have been removed.", color='warning')
                return
            active_positions = await position_service.list_active()
            await VolunteerProfileDialog(
                user=user,
                positions=active_positions,
                on_submit=load_roster,
            ).open()

        table.on('manage_volunteer', handle_manage)

        await load_roster()
