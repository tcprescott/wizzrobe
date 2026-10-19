"""Volunteers saying they've arrived, are running late, or want to undo a check-in.

Coordinators have always been able to check a volunteer in from the schedule
(``VolunteerScheduleService.check_in``). This is the volunteer's own side of the
same record: the "I'm here" banner, the card on My Shifts and the shift-start DM
all land here, and write the same ``checked_in_at`` / ``checked_in_by`` a
coordinator does, so the schedule and the export need nothing new to show it.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from application.errors import require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories import VolunteerAssignmentRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.notification_catalog import VOLUNTEER_LATE
from application.utils.discord_messages_volunteer import LATE_CHOICES, late_label
from models import FeatureFlag, User, VolunteerAssignment

# How long before a shift starts its "I'm here" opens.
CHECK_IN_LEAD = timedelta(minutes=30)


def check_in_open(assignment: VolunteerAssignment, now: datetime) -> bool:
    """Whether a volunteer may say they've arrived for this shift right now."""
    shift = assignment.shift
    return shift.starts_at - CHECK_IN_LEAD <= now < shift.ends_at


def can_self_check_in(assignment: VolunteerAssignment, now: datetime) -> bool:
    """The "I'm here" button's own rule, mirroring what the service accepts."""
    return (
        assignment.checked_in_at is None
        and not assignment.auto_generated
        and check_in_open(assignment, now)
    )


def can_undo_check_in(assignment: VolunteerAssignment, user: User, now: datetime) -> bool:
    """A volunteer may take back their own check-in, not a coordinator's, until the shift ends."""
    return (
        assignment.checked_in_at is not None
        and assignment.checked_in_by_id == user.id  # type: ignore[attr-defined]
        and assignment.shift.ends_at > now
    )


class VolunteerArrivalService:
    """Self-reported arrival for volunteer shifts."""

    def __init__(self) -> None:
        self.assignment_repository = VolunteerAssignmentRepository()
        self.audit_service = AuditService()

    async def _own(self, assignment_id: int, user: User) -> VolunteerAssignment:
        assignment = require_found(
            await self.assignment_repository.get_by_id(assignment_id), 'Volunteer assignment',
        )
        if assignment.user_id != user.id:  # type: ignore[attr-defined]
            raise ValueError('You can only check in for your own shifts.')
        if assignment.auto_generated:
            raise ValueError(
                'That shift is still a draft. Your coordinator has not confirmed it yet.'
            )
        return assignment

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def awaiting_arrival(self, user: User) -> List[VolunteerAssignment]:
        """The viewer's shifts whose "I'm here" is open and not yet tapped."""
        now = datetime.now(timezone.utc)
        return await self.assignment_repository.awaiting_arrival(
            user.id, now + CHECK_IN_LEAD, now,
        )

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def check_in_self(self, assignment_id: int, user: User) -> VolunteerAssignment:
        """Record that the volunteer says they've arrived. Repeating it is a no-op."""
        assignment = await self._own(assignment_id, user)
        if assignment.checked_in_at is not None:
            return assignment
        now = datetime.now(timezone.utc)
        if now >= assignment.shift.ends_at:
            raise ValueError('That shift has already finished.')
        if not check_in_open(assignment, now):
            minutes = int(CHECK_IN_LEAD.total_seconds() // 60)
            raise ValueError(
                f'Check-in opens {minutes} minutes before your shift starts.'
            )
        assignment.checked_in_at = now
        assignment.checked_in_by_id = user.id
        assignment.late_minutes = None
        await self.assignment_repository.save(
            assignment, update_fields=['checked_in_at', 'checked_in_by_id', 'late_minutes'],
        )
        await self.audit_service.write_and_publish(
            user, AuditActions.VOLUNTEER_CHECKED_IN,
            {'assignment_id': assignment.id, 'shift_id': assignment.shift_id,
             'self_reported': True},
            EventType.VOLUNTEER_CHECKED_IN, event_extra={'user_id': user.id},
        )
        return assignment

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def undo_check_in(self, assignment_id: int, user: User) -> VolunteerAssignment:
        """Take back a mistaken "I'm here". Only the volunteer's own, before the shift ends."""
        assignment = await self._own(assignment_id, user)
        if assignment.checked_in_at is None:
            return assignment
        if assignment.checked_in_by_id != user.id:  # type: ignore[attr-defined]
            raise ValueError('Your coordinator checked you in. Ask them to change it.')
        if assignment.shift.ends_at <= datetime.now(timezone.utc):
            raise ValueError('That shift has already finished.')
        assignment.checked_in_at = None  # type: ignore[assignment]
        assignment.checked_in_by_id = None
        await self.assignment_repository.save(
            assignment, update_fields=['checked_in_at', 'checked_in_by_id'],
        )
        await self.audit_service.write_and_publish(
            user, AuditActions.VOLUNTEER_CHECK_IN_UNDONE,
            {'assignment_id': assignment.id, 'shift_id': assignment.shift_id},  # type: ignore[attr-defined]
            EventType.VOLUNTEER_CHECK_IN_UNDONE, event_extra={'user_id': user.id},
        )
        return assignment

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def report_late(
        self, assignment_id: int, user: User, minutes: int,
    ) -> VolunteerAssignment:
        """The volunteer is on their way. Keeps the shift and tells the coordinators."""
        if minutes not in LATE_CHOICES:
            raise ValueError('Pick how late you expect to be.')
        assignment = await self._own(assignment_id, user)
        if assignment.checked_in_at is not None:
            raise ValueError("You're already checked in for this shift.")
        if assignment.shift.ends_at <= datetime.now(timezone.utc):
            raise ValueError('That shift has already finished.')
        if assignment.late_minutes == minutes:
            return assignment
        assignment.late_minutes = minutes
        await self.assignment_repository.save(assignment, update_fields=['late_minutes'])
        await self.audit_service.write_and_publish(
            user, AuditActions.VOLUNTEER_RUNNING_LATE,
            {'assignment_id': assignment.id, 'shift_id': assignment.shift_id,
             'minutes': minutes},
            EventType.VOLUNTEER_RUNNING_LATE, event_extra={'user_id': user.id},
        )
        await self._notify_coordinators_of_late(user, assignment, minutes)
        return assignment

    async def _notify_coordinators_of_late(
        self, volunteer: User, assignment: VolunteerAssignment, minutes: int,
    ) -> None:
        from application.services.tenant_service import TenantService
        from application.services.volunteer.volunteer_coordinator_dm import notify_coordinators
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import (
            running_late_description,
            volunteer_running_late_dm,
        )

        shift = assignment.shift
        position_name = shift.position.name if shift.position else ''
        message = volunteer_running_late_dm(
            volunteer_name=volunteer.preferred_name,
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
            minutes=minutes,
        )
        embed = volunteer_embed(
            title='🕒 Volunteer running late',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description=running_late_description(volunteer.preferred_name, minutes),
        )
        await notify_coordinators(
            shift, message, embed, link_label='Open the schedule',
            notice=VOLUNTEER_LATE,
        )


def describe_late(assignment: VolunteerAssignment) -> Optional[str]:
    """'Running late (about 10 minutes)' for a volunteer who said so and hasn't arrived."""
    if assignment.late_minutes is None or assignment.checked_in_at is not None:
        return None
    return f'Running late ({late_label(assignment.late_minutes).lower()})'
