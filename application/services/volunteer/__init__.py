"""Volunteer services — profiles, positions, shifts, and scheduling.

``VolunteerScheduleService`` builds and publishes the shift roster,
``VolunteerAutoscheduleService`` proposes a draft fill from availability +
qualifications, and ``volunteer_reminder`` is the background sweep that DMs
upcoming shifts (stamping ``reminder_sent_at`` before the send so a restart
resumes rather than double-notifies).
"""

from application.services.volunteer import volunteer_coordinator_dm, volunteer_reminder
from application.services.volunteer.volunteer_arrival_service import (
    CHECK_IN_LEAD,
    VolunteerArrivalService,
    can_self_check_in,
    can_undo_check_in,
    describe_late,
)
from application.services.volunteer.volunteer_autoschedule_service import (
    DEFAULT_MAX_HOURS,
    DraftPolicy,
    VolunteerAutoscheduleService,
)
from application.services.volunteer.volunteer_availability_service import (
    VolunteerAvailabilityService,
)
from application.services.volunteer.volunteer_call_off_service import (
    VolunteerCallOffService,
    can_call_off,
)
from application.services.volunteer.volunteer_export_service import VolunteerExportService
from application.services.volunteer.volunteer_hours import (
    HoursSummary,
    VolunteerHoursService,
    merged_hours,
)
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.services.volunteer.volunteer_qualification_service import (
    VolunteerQualificationService,
)
from application.services.volunteer.volunteer_role_mapping_service import (
    VolunteerRoleMappingService,
)
from application.services.volunteer.volunteer_schedule_grid import (
    DayGrid,
    GridColumn,
    GridPerson,
    GridRole,
    GridShift,
    apply_roles,
    arrange_roles,
    build_day_grid,
    roles_config,
)
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.services.volunteer.volunteer_shift_move_service import VolunteerShiftMoveService

__all__ = [
    'CHECK_IN_LEAD',
    'DEFAULT_MAX_HOURS',
    'DayGrid',
    'DraftPolicy',
    'GridColumn',
    'GridPerson',
    'GridRole',
    'GridShift',
    'HoursSummary',
    'VolunteerArrivalService',
    'VolunteerAutoscheduleService',
    'VolunteerAvailabilityService',
    'VolunteerCallOffService',
    'VolunteerExportService',
    'VolunteerHoursService',
    'VolunteerPoolService',
    'VolunteerPositionService',
    'VolunteerQualificationService',
    'VolunteerRoleMappingService',
    'VolunteerScheduleService',
    'VolunteerShiftMoveService',
    'apply_roles',
    'arrange_roles',
    'build_day_grid',
    'can_call_off',
    'can_self_check_in',
    'can_undo_check_in',
    'describe_late',
    'merged_hours',
    'roles_config',
    'volunteer_coordinator_dm',
    'volunteer_reminder',
]
