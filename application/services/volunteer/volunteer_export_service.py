"""
Volunteer Export Service - Business Logic Layer

Flattens the coordinator's volunteer data — the roster and its stated
preferences, positions, shifts, assignments, and a slot-by-slot schedule grid —
into plain tables, so an initial schedule can be drafted in a spreadsheet and
brought back by hand.

Read-only: nothing here mutates volunteer data. The export is audited because it
copies every volunteer's declared availability and free-text notes out of the
app.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from application.feature_flags import requires_feature
from application.repositories import (
    UserRepository,
    VolunteerAssignmentRepository,
    VolunteerAvailabilityRepository,
    VolunteerPositionRepository,
    VolunteerQualificationRepository,
    VolunteerShiftRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.volunteer.volunteer_availability_service import (
    VolunteerAvailabilityService,
)
from application.services.volunteer.volunteer_hours import merged_hours
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.timezone_context import current_timezone_name
from application.utils.csv_export import files_to_zip_bytes, rows_to_csv_bytes
from application.utils.timezone import (
    format_local_date,
    format_local_display,
    format_local_time,
    timezone_label,
    to_local,
    to_utc_aware,
)
from models import FeatureFlag, User, VolunteerAvailability, VolunteerAvailabilityStatus

_STATUS_LABEL = {
    VolunteerAvailabilityStatus.PREFERRED: 'Preferred',
    VolunteerAvailabilityStatus.AVAILABLE: 'Available',
    VolunteerAvailabilityStatus.UNAVAILABLE: 'Unavailable',
}


def _tz_label() -> str:
    """Short label for the zone this export is being rendered in.

    Column headers used to read "(ET)" because there was only one zone. Now that
    the clock follows the reader, a fixed label would caption the wrong zone —
    worse than no label, since a forwarded CSV has no other context.
    """
    return timezone_label()

def _columns(*pairs: tuple) -> List[Dict[str, str]]:
    """``('name', 'Label')`` pairs → the column descriptors CSV export expects."""
    return [{'name': name, 'label': label, 'field': name} for name, label in pairs]


def _hours(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 3600, 2)


def _status_label(status: Optional[VolunteerAvailabilityStatus]) -> str:
    if status is None:
        return ''
    return _STATUS_LABEL.get(status, str(status))


@dataclass(frozen=True)
class ExportSheet:
    """One flat table, destined for a single CSV file / spreadsheet tab."""

    name: str
    title: str
    description: str
    columns: List[Dict[str, str]]
    rows: List[Dict]


@dataclass(frozen=True)
class VolunteerExportBundle:
    """Everything one export produces: the tables plus their covering note."""

    start: datetime
    end: datetime
    sheets: List[ExportSheet]
    community_name: str = ''

    def sheet(self, name: str) -> ExportSheet:
        """The sheet with this ``name`` (raises ``KeyError`` when absent)."""
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        raise KeyError(name)

    def readme(self) -> str:
        """Plain-text guide packaged alongside the CSVs."""
        who = f'{self.community_name} volunteer data' if self.community_name else 'Volunteer data'
        lines = [
            f'{who} exported from Wizzrobe',
            '',
            f'Window: {format_local_display(self.start)} → {format_local_display(self.end)}',
            '',
            f'Times in columns labelled "({_tz_label()})" are in '
            f'{current_timezone_name()}, the timezone this export was generated in.',
            'Every table also carries the underlying UTC timestamps in ISO 8601,',
            'which is what to sort or compute on.',
            '',
            'Files',
            '-----',
        ]
        for sheet in self.sheets:
            lines.append(f'{sheet.name}.csv — {sheet.description} ({len(sheet.rows)} rows)')
        lines += [
            '',
            'Editing these files does not change anything in Wizzrobe. Bring a',
            'schedule built here back by creating the shifts and assignments in',
            'Admin → Vol. Schedule.',
        ]
        return '\n'.join(lines) + '\n'

    def to_zip_bytes(self) -> bytes:
        """``README.txt`` plus one CSV per sheet, zipped."""
        files = {'README.txt': self.readme().encode('utf-8')}
        for sheet in self.sheets:
            files[f'{sheet.name}.csv'] = rows_to_csv_bytes(sheet.columns, sheet.rows)
        return files_to_zip_bytes(files)


class VolunteerExportService:
    """Builds the coordinator's volunteer data export."""

    def __init__(self) -> None:
        self.assignment_repository = VolunteerAssignmentRepository()
        self.availability_repository = VolunteerAvailabilityRepository()
        self.position_repository = VolunteerPositionRepository()
        self.qualification_repository = VolunteerQualificationRepository()
        self.shift_repository = VolunteerShiftRepository()
        self.user_repository = UserRepository()
        self.pool_service = VolunteerPoolService()
        self.audit_service = AuditService()

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def build(self, actor: User, start: datetime, end: datetime) -> VolunteerExportBundle:
        """Gather every volunteer table covering ``[start, end]``.

        The window bounds the schedule (shifts, assignments) and the availability
        windows that overlap it. The roster itself is never windowed — a
        volunteer with no declared availability still has to appear, because
        filling the gaps is exactly what the coordinator is doing.
        """
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can export volunteer data.",
        )
        if end <= start:
            raise ValueError("The export window must end after it starts.")
        start = to_utc_aware(start)
        end = to_utc_aware(end)

        positions = await self.position_repository.list_all()
        shifts = await self.shift_repository.list_for_window(start, end)
        pool = await self.pool_service.assignable_volunteers()
        qualifications = await self.qualification_repository.list_all()

        users: Dict[int, User] = {u.id: u for u in pool}
        for shift in shifts:
            for assignment in shift.assignments:
                if assignment.user is not None:
                    users.setdefault(assignment.user_id, assignment.user)

        availability = await self.availability_repository.for_users_overlapping(
            list(users), start, end,
        )
        windows_by_user: Dict[int, List[VolunteerAvailability]] = {}
        for window in availability:
            windows_by_user.setdefault(window.user_id, []).append(window)
        for user_windows in windows_by_user.values():
            user_windows.sort(key=lambda w: w.starts_at)

        staff_ids = {
            actor_id
            for shift in shifts
            for assignment in shift.assignments
            for actor_id in (
                assignment.assigned_by_id, assignment.checked_in_by_id, assignment.called_off_by_id,
            )
            if actor_id and actor_id not in users
        }
        names = {uid: user.preferred_name for uid, user in users.items()}
        if staff_ids:
            for uid, user in (await self.user_repository.get_by_ids(list(staff_ids))).items():
                names[uid] = user.preferred_name

        pool_ids = {u.id for u in pool}
        qualifications_by_user: Dict[int, List[str]] = {}
        for qualification in qualifications:
            if qualification.position is not None:
                qualifications_by_user.setdefault(qualification.user_id, []).append(
                    qualification.position.name
                )

        sheets = [
            self._volunteers_sheet(users, pool_ids, qualifications_by_user,
                                   windows_by_user, shifts),
            self._availability_sheet(users, windows_by_user),
            self._positions_sheet(positions, shifts),
            self._shifts_sheet(shifts),
            self._assignments_sheet(shifts, windows_by_user, names),
            self._schedule_sheet(shifts),
        ]

        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_DATA_EXPORTED,
            {
                'start': start.isoformat(),
                'end': end.isoformat(),
                'rows': {sheet.name: len(sheet.rows) for sheet in sheets},
            },
        )

        from application.services.tenant_service import TenantService
        return VolunteerExportBundle(
            start=start, end=end, sheets=sheets,
            community_name=await TenantService.current_community_name() or '',
        )

    def _volunteers_sheet(
        self,
        users: Mapping[int, User],
        pool_ids: set,
        qualifications_by_user: Mapping[int, Sequence[str]],
        windows_by_user: Mapping[int, Sequence[VolunteerAvailability]],
        shifts: Sequence,
    ) -> ExportSheet:
        assigned_windows: Dict[int, List[Tuple[datetime, datetime]]] = {}
        for shift in shifts:
            for assignment in shift.assignments:
                assigned_windows.setdefault(assignment.user_id, []).append(
                    (shift.starts_at, shift.ends_at)
                )
        assigned_count = {uid: len(w) for uid, w in assigned_windows.items()}
        assigned_hours = {uid: round(merged_hours(w), 2) for uid, w in assigned_windows.items()}

        rows = []
        for user in sorted(users.values(), key=lambda u: u.preferred_name.lower()):
            windows = windows_by_user.get(user.id, [])
            rows.append({
                'user_id': user.id,
                'name': user.preferred_name,
                'username': user.username,
                'pronouns': user.pronouns or '',
                'in_volunteer_pool': user.id in pool_ids,
                'qualifications': ', '.join(sorted(qualifications_by_user.get(user.id, []))),
                'availability_windows': len(windows),
                'available_hours': round(
                    sum(_hours(w.starts_at, w.ends_at) for w in windows
                        if w.status != VolunteerAvailabilityStatus.UNAVAILABLE), 2,
                ),
                'assigned_shifts': assigned_count.get(user.id, 0),
                'assigned_hours': assigned_hours.get(user.id, 0.0),
            })
        return ExportSheet(
            name='volunteers',
            title='Volunteers',
            description='the roster — who is in the pool, their qualifications, '
                        'and how much they are already carrying',
            columns=_columns(
                ('user_id', 'User ID'),
                ('name', 'Name'),
                ('username', 'Discord Username'),
                ('pronouns', 'Pronouns'),
                ('in_volunteer_pool', 'In Pool'),
                ('qualifications', 'Qualified Positions'),
                ('availability_windows', 'Availability Windows'),
                ('available_hours', 'Available Hours'),
                ('assigned_shifts', 'Assigned Shifts'),
                ('assigned_hours', 'Assigned Hours'),
            ),
            rows=rows,
        )

    def _availability_sheet(
        self,
        users: Mapping[int, User],
        windows_by_user: Mapping[int, Sequence[VolunteerAvailability]],
    ) -> ExportSheet:
        rows = []
        for user_id, windows in windows_by_user.items():
            user = users.get(user_id)
            for window in windows:
                rows.append({
                    'user_id': user_id,
                    'name': user.preferred_name if user else '',
                    'status': _status_label(window.status),
                    'date_local': format_local_date(window.starts_at),
                    'start_local': format_local_time(window.starts_at),
                    'end_local': format_local_time(window.ends_at),
                    'hours': _hours(window.starts_at, window.ends_at),
                    'note': window.note or '',
                    'starts_at_utc': to_utc_aware(window.starts_at).isoformat(),
                    'ends_at_utc': to_utc_aware(window.ends_at).isoformat(),
                })
        rows.sort(key=lambda r: (r['name'].lower(), r['starts_at_utc']))
        return ExportSheet(
            name='availability',
            title='Availability',
            description='every declared availability window overlapping the export range, '
                        'including the times volunteers marked unavailable',
            columns=_columns(
                ('user_id', 'User ID'),
                ('name', 'Name'),
                ('status', 'Status'),
                ('date_local', f'Date ({_tz_label()})'),
                ('start_local', f'Start ({_tz_label()})'),
                ('end_local', f'End ({_tz_label()})'),
                ('hours', 'Hours'),
                ('note', 'Note'),
                ('starts_at_utc', 'Starts At (UTC)'),
                ('ends_at_utc', 'Ends At (UTC)'),
            ),
            rows=rows,
        )

    def _positions_sheet(self, positions: Sequence, shifts: Sequence) -> ExportSheet:
        needed: Dict[int, int] = {}
        filled: Dict[int, int] = {}
        counts: Dict[int, int] = {}
        for shift in shifts:
            counts[shift.position_id] = counts.get(shift.position_id, 0) + 1
            needed[shift.position_id] = needed.get(shift.position_id, 0) + shift.slots_needed
            filled[shift.position_id] = filled.get(shift.position_id, 0) + len(shift.assignments)

        rows = [
            {
                'position_id': position.id,
                'name': position.name,
                'description': position.description or '',
                'is_active': position.is_active,
                'display_order': position.display_order,
                'shift_length_minutes': position.shift_length_minutes or '',
                'stagger_minutes': position.stagger_minutes or '',
                'shifts': counts.get(position.id, 0),
                'slots_needed': needed.get(position.id, 0),
                'slots_filled': filled.get(position.id, 0),
            }
            for position in positions
        ]
        return ExportSheet(
            name='positions',
            title='Positions',
            description='the jobs volunteers can be scheduled into, with their staffing totals '
                        'for this range',
            columns=_columns(
                ('position_id', 'Position ID'),
                ('name', 'Position'),
                ('description', 'Description'),
                ('is_active', 'Active'),
                ('display_order', 'Display Order'),
                ('shift_length_minutes', 'Rolling Shift Length (min)'),
                ('stagger_minutes', 'Stagger (min)'),
                ('shifts', 'Shifts'),
                ('slots_needed', 'Slots Needed'),
                ('slots_filled', 'Slots Filled'),
            ),
            rows=rows,
        )

    def _shifts_sheet(self, shifts: Sequence) -> ExportSheet:
        rows = []
        for shift in shifts:
            assigned = [a.user.preferred_name for a in shift.assignments if a.user]
            rows.append({
                'shift_id': shift.id,
                'position': shift.position.name if shift.position else '',
                'label': shift.label or '',
                'date_local': format_local_date(shift.starts_at),
                'day': to_local(shift.starts_at).strftime('%A'),
                'start_local': format_local_time(shift.starts_at),
                'end_local': format_local_time(shift.ends_at),
                'hours': _hours(shift.starts_at, shift.ends_at),
                'slots_needed': shift.slots_needed,
                'filled': len(shift.assignments),
                'open': max(0, shift.slots_needed - len(shift.assignments)),
                'assigned': ', '.join(sorted(assigned)),
                'notes': shift.notes or '',
                'starts_at_utc': to_utc_aware(shift.starts_at).isoformat(),
                'ends_at_utc': to_utc_aware(shift.ends_at).isoformat(),
            })
        return ExportSheet(
            name='shifts',
            title='Shifts',
            description='every shift in the range with its staffing level',
            columns=_columns(
                ('shift_id', 'Shift ID'),
                ('position', 'Position'),
                ('label', 'Shift'),
                ('date_local', f'Date ({_tz_label()})'),
                ('day', 'Day'),
                ('start_local', f'Start ({_tz_label()})'),
                ('end_local', f'End ({_tz_label()})'),
                ('hours', 'Hours'),
                ('slots_needed', 'Slots Needed'),
                ('filled', 'Filled'),
                ('open', 'Open'),
                ('assigned', 'Assigned'),
                ('notes', 'Notes'),
                ('starts_at_utc', 'Starts At (UTC)'),
                ('ends_at_utc', 'Ends At (UTC)'),
            ),
            rows=rows,
        )

    def _assignments_sheet(
        self,
        shifts: Sequence,
        windows_by_user: Mapping[int, Sequence[VolunteerAvailability]],
        names: Mapping[int, str],
    ) -> ExportSheet:
        rows = []
        for shift in shifts:
            for assignment in shift.assignments:
                status = VolunteerAvailabilityService.covers(
                    windows_by_user.get(assignment.user_id, []), shift.starts_at, shift.ends_at,
                )
                rows.append({
                    'assignment_id': assignment.id,
                    'shift_id': shift.id,
                    'position': shift.position.name if shift.position else '',
                    'label': shift.label or '',
                    'date_local': format_local_date(shift.starts_at),
                    'start_local': format_local_time(shift.starts_at),
                    'end_local': format_local_time(shift.ends_at),
                    'hours': _hours(shift.starts_at, shift.ends_at),
                    'user_id': assignment.user_id,
                    'name': assignment.user.preferred_name if assignment.user else '',
                    'source': 'Auto-draft' if assignment.auto_generated else 'Coordinator',
                    'stated_availability': _status_label(status) or 'Not stated',
                    'assigned_by': names.get(assignment.assigned_by_id, ''),
                    'acknowledged_at_local': format_local_display(assignment.acknowledged_at),
                    'checked_in_at_local': format_local_display(assignment.checked_in_at),
                    'checked_in_by': names.get(assignment.checked_in_by_id, ''),
                    'called_off_at_local': format_local_display(assignment.called_off_at),
                    'called_off_by': names.get(assignment.called_off_by_id, ''),
                    'counts_as_worked': 'Yes' if assignment.counts_as_worked else 'No',
                })
        return ExportSheet(
            name='assignments',
            title='Assignments',
            description='who is currently on which shift, whether the placement was a draft, '
                        'and whether it matches what they said they were available for',
            columns=_columns(
                ('assignment_id', 'Assignment ID'),
                ('shift_id', 'Shift ID'),
                ('position', 'Position'),
                ('label', 'Shift'),
                ('date_local', f'Date ({_tz_label()})'),
                ('start_local', f'Start ({_tz_label()})'),
                ('end_local', f'End ({_tz_label()})'),
                ('hours', 'Hours'),
                ('user_id', 'User ID'),
                ('name', 'Volunteer'),
                ('source', 'Source'),
                ('stated_availability', 'Stated Availability'),
                ('assigned_by', 'Assigned By'),
                ('acknowledged_at_local', f'Acknowledged At ({_tz_label()})'),
                ('checked_in_at_local', f'Checked In At ({_tz_label()})'),
                ('checked_in_by', 'Checked In By'),
                ('called_off_at_local', f'Called Off At ({_tz_label()})'),
                ('called_off_by', 'Called Off By'),
                ('counts_as_worked', 'Counts As Worked'),
            ),
            rows=rows,
        )

    def _schedule_sheet(self, shifts: Sequence) -> ExportSheet:
        """One row per slot — including the empty ones.

        This is the sheet to actually build a schedule on: the open slots are
        rows waiting for a name, which the normalized ``assignments`` table
        cannot show because an unfilled slot has no record.
        """
        rows = []
        for shift in shifts:
            assignments = sorted(
                shift.assignments,
                key=lambda a: (a.user.preferred_name.lower() if a.user else ''),
            )
            slots = max(shift.slots_needed, len(assignments))
            for index in range(slots):
                assignment = assignments[index] if index < len(assignments) else None
                rows.append({
                    'date_local': format_local_date(shift.starts_at),
                    'day': to_local(shift.starts_at).strftime('%A'),
                    'start_local': format_local_time(shift.starts_at),
                    'end_local': format_local_time(shift.ends_at),
                    'position': shift.position.name if shift.position else '',
                    'label': shift.label or '',
                    'slot': index + 1,
                    'volunteer': assignment.user.preferred_name if assignment and assignment.user else '',
                    'state': self._slot_state(shift, assignment, index),
                    'shift_id': shift.id,
                })
        return ExportSheet(
            name='schedule',
            title='Schedule grid',
            description='one row per slot, open slots included — the sheet to draft a '
                        'schedule on',
            columns=_columns(
                ('date_local', f'Date ({_tz_label()})'),
                ('day', 'Day'),
                ('start_local', f'Start ({_tz_label()})'),
                ('end_local', f'End ({_tz_label()})'),
                ('position', 'Position'),
                ('label', 'Shift'),
                ('slot', 'Slot'),
                ('volunteer', 'Volunteer'),
                ('state', 'State'),
                ('shift_id', 'Shift ID'),
            ),
            rows=rows,
        )

    @staticmethod
    def _slot_state(shift, assignment, index: int) -> str:
        if assignment is None:
            return 'Open'
        if index >= shift.slots_needed:
            return 'Extra'
        if assignment.auto_generated:
            return 'Draft'
        if assignment.acknowledged_at:
            return 'Confirmed'
        return 'Assigned'
