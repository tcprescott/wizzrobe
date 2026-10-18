"""
Volunteer Schedule Service - Business Logic Layer

Shifts, assignments, acknowledgement, and coverage for the onsite volunteer
schedule. Coordinator-driven; mirrors the crew signup/approve/acknowledge flow
for Discord notifications.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from tortoise.transactions import in_transaction

from application.errors import require_found
from application.events import Event, EventType, event_bus
from application.feature_flags import requires_feature
from application.repositories import (
    VolunteerAssignmentRepository,
    VolunteerPositionRepository,
    VolunteerShiftRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.timezone_service import TimezoneService
from application.services.volunteer.volunteer_role_mapping_service import (
    VolunteerRoleMappingService,
)
from application.services.volunteer.volunteer_schedule_grid import (
    DayGrid,
    build_day_grid,
    day_window,
)
from application.tenant_context import require_tenant_id
from application.utils.timezone import (
    combine_local,
    format_local_display,
    format_local_time,
    parse_local_datetime,
)
from models import FeatureFlag, User, VolunteerAssignment, VolunteerPosition, VolunteerShift

logger = logging.getLogger(__name__)

RELEASE_REASON_MAX_CHARS = 500


async def _ensure_can_view_schedule(viewer: Optional[User]) -> None:
    await AuthService.ensure(await AuthService.can_view_volunteer_schedule(viewer),
                             "You don't have access to the volunteer schedule.")


class VolunteerScheduleService:
    """Core volunteer-scheduling operations."""

    def __init__(self) -> None:
        self.shift_repository = VolunteerShiftRepository()
        self.assignment_repository = VolunteerAssignmentRepository()
        self.position_repository = VolunteerPositionRepository()
        self.audit_service = AuditService()
        self.discord_service = DiscordService()
        self.role_mapping_service = VolunteerRoleMappingService()

    # --- Shifts -----------------------------------------------------------

    async def list_shifts_for_window(self, start: datetime, end: datetime) -> List[VolunteerShift]:
        return await self.shift_repository.list_for_window(start, end)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def shifts_for_viewer(self, viewer: Optional[User], start: datetime, end: datetime) -> List[VolunteerShift]:
        await _ensure_can_view_schedule(viewer)
        return await self.shift_repository.list_for_window(start, end)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def shift_for_viewer(self, viewer: Optional[User], shift_id: int) -> Optional[VolunteerShift]:
        await _ensure_can_view_schedule(viewer)
        return await self.shift_repository.get_by_id(shift_id)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def grid_roles(self, viewer: Optional[User]) -> List[Tuple[int, str]]:
        """The roles a Full Schedule viewer can show, hide and reorder."""
        await _ensure_can_view_schedule(viewer)
        return [(p.id, p.name) for p in await self.position_repository.list_active()]

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def day_grid(self, viewer: Optional[User], day: date) -> DayGrid:
        """One event day's published schedule, for anyone the volunteer hub admits."""
        await _ensure_can_view_schedule(viewer)
        midnight = combine_local(day, time(0))
        start, end = day_window(midnight)
        shifts = await self.shift_repository.list_for_window(start, end)
        return build_day_grid(shifts, midnight, viewer.id)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def board_day_grid(self, actor: Optional[User], day: date) -> DayGrid:
        """One event day for the Shift Board: drafts included, every active
        position a column, and who has or hasn't turned up on started shifts."""
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can edit the shift board.",
        )
        midnight = combine_local(day, time(0))
        start, end = day_window(midnight)
        shifts = await self.shift_repository.list_for_window(start, end)
        positions = [(p.id, p.name) for p in await self.position_repository.list_active()]
        return build_day_grid(
            shifts, midnight, actor.id if actor else 0, for_staff=True, positions=positions,
            now=datetime.now(timezone.utc),
        )

    async def get_shift(self, shift_id: int) -> Optional[VolunteerShift]:
        return await self.shift_repository.get_by_id(shift_id)

    async def create_shift(
        self,
        actor: User,
        position_id: int,
        starts_at: datetime,
        ends_at: datetime,
        label: Optional[str] = None,
        slots_needed: int = 1,
        notes: Optional[str] = None,
    ) -> VolunteerShift:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage shifts.",
        )
        if ends_at <= starts_at:
            raise ValueError("A shift must end after it starts.")
        if slots_needed < 1:
            raise ValueError("A shift needs at least one slot.")
        await self._require_position(position_id)
        shift = await self.shift_repository.create(
            position_id=position_id, starts_at=starts_at, ends_at=ends_at,
            label=label, slots_needed=slots_needed, notes=notes,
        )
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_SHIFT_CREATED,
            {'shift_id': shift.id, 'position_id': position_id},
        )
        return shift

    async def generate_day_shifts(
        self,
        actor: User,
        date_str: str,
        position_ids: Sequence[int],
        blocks: Sequence[Tuple[str, str, str]],
    ) -> List[VolunteerShift]:
        """Create shifts for one event day.

        ``blocks`` is a sequence of ``(label, start_hhmm, end_hhmm)`` in the
        community's own timezone.
        A block whose end is at or before its start (e.g. 20:00–00:00) is treated
        as crossing midnight into the next day.

        Positions configured with ``shift_length_minutes``/``stagger_minutes``
        instead get staggered rolling shifts spanning the same overall day window
        (first block start → last block end), so their crew hands off one at a
        time rather than all together.
        """
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage shifts.",
        )
        created: List[VolunteerShift] = []
        positions = [await self._require_position(pid) for pid in position_ids]
        async with in_transaction():
            for position_id, position in zip(position_ids, positions, strict=True):
                if position.is_staggered:
                    created.extend(
                        await self._generate_staggered(position, date_str, blocks)
                    )
                else:
                    created.extend(
                        await self._generate_blocks(position_id, date_str, blocks)
                    )
            await self.audit_service.write_log(
                actor, AuditActions.VOLUNTEER_SHIFT_CREATED,
                {'generated': len(created), 'date': date_str,
                 'position_ids': list(position_ids)},
            )
        return created

    async def _generate_blocks(
        self, position_id: int, date_str: str,
        blocks: Sequence[Tuple[str, str, str]],
    ) -> List[VolunteerShift]:
        """Create one fixed shift per block for a position.

        The block times are the *community's* wall clock — "Shift 1 is 08:00" is
        a fact about the event, not about whoever is generating the schedule. A
        coordinator working from another country must not lay down a whole day
        of shifts shifted by their own UTC offset.
        """
        tz = await TimezoneService.tenant_timezone_name()
        out: List[VolunteerShift] = []
        for label, start_hhmm, end_hhmm in blocks:
            starts_at = parse_local_datetime(date_str, start_hhmm, tz)
            ends_at = parse_local_datetime(date_str, end_hhmm, tz)
            if ends_at <= starts_at:
                ends_at = ends_at + timedelta(days=1)
            out.append(
                await self.shift_repository.create(
                    position_id=position_id, starts_at=starts_at,
                    ends_at=ends_at, label=label or None,
                )
            )
        return out

    async def _generate_staggered(
        self, position: VolunteerPosition, date_str: str,
        blocks: Sequence[Tuple[str, str, str]],
    ) -> List[VolunteerShift]:
        """Create staggered rolling shifts spanning the day's coverage window.

        On the community's clock, for the same reason as :meth:`_fixed_shifts`.
        """
        tz = await TimezoneService.tenant_timezone_name()
        coverage_start = parse_local_datetime(date_str, blocks[0][1], tz)
        coverage_end = parse_local_datetime(date_str, blocks[-1][2], tz)
        if coverage_end <= coverage_start:
            coverage_end = coverage_end + timedelta(days=1)
        shift_length = timedelta(minutes=position.shift_length_minutes)
        stagger = timedelta(minutes=position.stagger_minutes)
        out: List[VolunteerShift] = []
        cursor = coverage_start
        while cursor < coverage_end:
            ends_at = min(cursor + shift_length, coverage_end)
            out.append(
                await self.shift_repository.create(
                    position_id=position.id, starts_at=cursor, ends_at=ends_at,
                )
            )
            cursor += stagger
        return out

    async def update_shift(self, actor: User, shift: VolunteerShift, **fields) -> VolunteerShift:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage shifts.",
        )
        starts = fields.get('starts_at', shift.starts_at)
        ends = fields.get('ends_at', shift.ends_at)
        if ends <= starts:
            raise ValueError("A shift must end after it starts.")
        if fields.get('slots_needed', shift.slots_needed) < 1:
            raise ValueError("A shift needs at least one slot.")
        if 'position_id' in fields:
            await self._require_position(fields['position_id'])
        old_starts, old_ends = shift.starts_at, shift.ends_at
        moved = starts != old_starts or ends != old_ends
        old_position_id = shift.position_id  # type: ignore[attr-defined]
        repositioned = fields.get('position_id', old_position_id) != old_position_id
        shift = await self.shift_repository.update(shift, **fields)
        if repositioned:
            await self.role_mapping_service.reconcile_users(
                actor, await self.assignment_repository.published_user_ids(shift.id),
            )
        details = {'shift_id': shift.id}
        # Someone who agreed to 08:00–12:00 has not agreed to 16:00–20:00, so a
        # time change withdraws the question and asks it again. A slots-only or
        # label-only edit notifies nobody; check-ins are never cleared.
        reacknowledge = await self._reask_moved_shift(shift, old_starts, old_ends) if moved else 0
        if reacknowledge:
            details['reacknowledge_cleared'] = reacknowledge
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_SHIFT_UPDATED, details,
        )
        return shift

    async def _reask_moved_shift(
        self, shift: VolunteerShift, old_starts: datetime, old_ends: datetime,
    ) -> int:
        """Clear acknowledgments on a moved shift and re-ask. Returns how many."""
        cleared = 0
        for assignment in await self.assignment_repository.list_for_shift(shift.id):
            if assignment.auto_generated:
                continue
            was_acknowledged = assignment.acknowledged_at is not None
            # The reminder was for the old time; the new one needs its own.
            if was_acknowledged or assignment.reminder_sent_at is not None:
                assignment.acknowledged_at = None  # type: ignore[assignment]
                assignment.reminder_sent_at = None  # type: ignore[assignment]
                await self.assignment_repository.save(
                    assignment, update_fields=['acknowledged_at', 'reminder_sent_at'],
                )
            if was_acknowledged:
                cleared += 1
            await self._notify_shift_moved(
                assignment, shift, assignment.user, old_starts, old_ends,
            )
        return cleared

    async def delete_shift(self, actor: User, shift: VolunteerShift) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage shifts.",
        )
        shift_id = shift.id
        assigned = await self.assignment_repository.published_user_ids(shift_id)
        to_tell = await self.assignment_repository.published_unfinished(
            datetime.now(timezone.utc), shift_id=shift_id,
        )
        await self.shift_repository.delete(shift)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_SHIFT_DELETED, {'shift_id': shift_id},
        )
        await self.role_mapping_service.reconcile_users(actor, assigned)
        for assignment in to_tell:
            await self._notify_removed(assignment.user, assignment.shift)

    async def reset_all_shifts(self, actor: User) -> int:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage shifts.",
        )
        # Only shifts still to come: a DM about last year's finished shift is noise.
        to_tell = await self.assignment_repository.published_unfinished(datetime.now(timezone.utc))
        deleted = await self.shift_repository.delete_all()
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_SHIFTS_RESET, {'deleted_count': deleted},
        )
        await self.role_mapping_service.reconcile_all(actor)
        for assignment in to_tell:
            await self._notify_removed(assignment.user, assignment.shift)
        return deleted

    # --- Assignments ------------------------------------------------------

    async def assign(
        self,
        actor: User,
        shift: VolunteerShift,
        user: User,
        *,
        auto_generated: bool = False,
        notify: bool = True,
    ) -> Tuple[VolunteerAssignment, List[str]]:
        """Place ``user`` into ``shift``. Returns (assignment, soft warnings).

        Hard failures (already assigned, overlapping shift) raise ValueError.
        Soft conditions (overfilled, outside stated availability) are returned as
        warnings for the UI to surface but do not block.
        """
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can assign volunteers.",
        )
        await TenantMembershipService.require_community_users([user.id], actor)
        if await self.assignment_repository.exists(shift.id, user.id):
            raise ValueError(f"{user.preferred_name} is already on this shift.")

        overlapping = await self.assignment_repository.overlapping_for_user(
            user.id, shift.starts_at, shift.ends_at, exclude_shift_id=shift.id,
        )
        if overlapping:
            raise ValueError(
                f"{user.preferred_name} is already assigned to an overlapping shift."
            )

        warnings: List[str] = []
        existing = await VolunteerAssignment.filter(shift_id=shift.id, tenant_id=require_tenant_id()).count()
        if existing >= shift.slots_needed:
            warnings.append(
                f"This shift already has {existing}/{shift.slots_needed} slots filled."
            )

        availability_warning = await self._availability_warning(user, shift)
        if availability_warning:
            warnings.append(availability_warning)

        assignment = await self.assignment_repository.create(
            shift_id=shift.id, user_id=user.id,
            assigned_by_id=actor.id, auto_generated=auto_generated,
        )
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_ASSIGNED,
            {'assignment_id': assignment.id, 'shift_id': shift.id,
             'user_id': user.id, 'auto_generated': auto_generated},
        )
        if not auto_generated:
            await self.role_mapping_service.reconcile_users(actor, [user.id])
        if notify and not auto_generated:
            await self.request_acknowledgment(assignment, shift, user)
        if not auto_generated:
            event_bus.publish(Event.create(EventType.VOLUNTEER_ASSIGNED, {
                'assignment_id': assignment.id, 'shift_id': shift.id, 'user_id': user.id,
            }, actor))
        return assignment, warnings

    async def confirm_assignment(
        self, actor: User, assignment: VolunteerAssignment, *, notify: bool = True,
    ) -> VolunteerAssignment:
        """Turn one draft assignment into a commitment: flip the flag, DM the
        volunteer, emit the same event a manual assign emits. Idempotent."""
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can publish assignments.",
        )
        if not assignment.auto_generated:
            return assignment
        await self._refuse_published_clash(assignment)
        await self.assignment_repository.mark_published(assignment)
        await self.audit_service.write_and_publish(
            actor, AuditActions.VOLUNTEER_ASSIGNED,
            {'assignment_id': assignment.id, 'shift_id': assignment.shift_id,
             'user_id': assignment.user_id, 'published_from_draft': True},
            EventType.VOLUNTEER_ASSIGNED,
        )
        await self.role_mapping_service.reconcile_users(actor, [assignment.user_id])  # type: ignore[attr-defined]
        if notify:
            await self.request_acknowledgment(
                assignment, assignment.shift, assignment.user,
            )
        return assignment

    async def count_drafts(self, start: datetime, end: datetime) -> int:
        """How many unpublished draft assignments sit in the window."""
        return await self.assignment_repository.count_auto_for_window(start, end)

    async def get_assignment(self, assignment_id: int) -> Optional[VolunteerAssignment]:
        """Read-only load-or-None lookup for entry surfaces (api/, discordbot/)."""
        return await self.assignment_repository.get_by_id(assignment_id)

    async def find_assignment(
        self, shift_id: int, user_id: int,
    ) -> Optional[VolunteerAssignment]:
        """This volunteer's assignment on this shift, if they hold one."""
        return await self.assignment_repository.get_for_shift_and_user(shift_id, user_id)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def find_own_assignment(
        self, assignment_id: int, user: User,
    ) -> Optional[VolunteerAssignment]:
        """``user``'s published assignment with this id, or None (gone, a draft, or not theirs)."""
        assignment = await self.assignment_repository.get_by_id(assignment_id)
        if assignment is None or assignment.user_id != user.id or assignment.auto_generated:  # type: ignore[attr-defined]
            return None
        return assignment

    async def unassign(self, actor: User, assignment: VolunteerAssignment) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can remove assignments.",
        )
        details = {
            'assignment_id': assignment.id,
            'shift_id': assignment.shift_id,
            'user_id': assignment.user_id,
        }
        was_draft = assignment.auto_generated
        shift = assignment.shift
        user = assignment.user
        await self.assignment_repository.delete(assignment)
        await self.audit_service.write_and_publish(
            actor, AuditActions.VOLUNTEER_UNASSIGNED, details, EventType.VOLUNTEER_UNASSIGNED,
        )
        # An unpublished draft's removal is silent because its creation was.
        if not was_draft:
            await self.role_mapping_service.reconcile_users(actor, [details['user_id']])
            await self._notify_removed(user, shift)

    async def _own_published(self, assignment_id: int, user: User, verb: str) -> VolunteerAssignment:
        assignment = require_found(
            await self.assignment_repository.get_by_id(assignment_id), "Volunteer assignment"
        )
        if assignment.user_id != user.id:
            raise ValueError(f"You can only {verb} your own assignments.")
        if assignment.auto_generated:
            raise ValueError("That shift is still a draft — your coordinator has not confirmed it yet.")
        return assignment

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def acknowledge(self, assignment_id: int, user: User) -> VolunteerAssignment:
        """Self-acknowledge an assignment. Idempotent."""
        assignment = await self._own_published(assignment_id, user, "acknowledge")
        if assignment.acknowledged_at is not None:
            return assignment
        assignment.acknowledged_at = datetime.now(timezone.utc)
        await self.assignment_repository.save(assignment)
        await self.audit_service.write_and_publish(
            user, AuditActions.VOLUNTEER_ACKNOWLEDGED,
            {'assignment_id': assignment.id, 'shift_id': assignment.shift_id},
            EventType.VOLUNTEER_ACKNOWLEDGED, event_extra={'user_id': user.id},
        )
        return assignment

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def release(
        self, assignment_id: int, user: User, reason: Optional[str] = None,
    ) -> None:
        """Give a shift back. The volunteer's own decision, not the coordinator's.

        Frees the slot immediately and DMs the coordinators — a schedule showing
        someone who has already said no is worse than an open one.
        """
        assignment = await self._own_published(assignment_id, user, "release")
        shift = assignment.shift
        if assignment.checked_in_at is not None:
            raise ValueError(
                "You are already checked in for this shift — talk to your coordinator."
            )
        now = datetime.now(timezone.utc)
        if shift.ends_at <= now:
            raise ValueError("That shift has already finished.")
        details = {
            'assignment_id': assignment.id, 'shift_id': shift.id, 'user_id': user.id,
            'reason': (reason or '').strip()[:RELEASE_REASON_MAX_CHARS] or None,
            'hours_notice': round((shift.starts_at - now).total_seconds() / 3600.0, 1),
        }
        await self.assignment_repository.delete(assignment)
        await self.audit_service.write_and_publish(
            user, AuditActions.VOLUNTEER_RELEASED, details, EventType.VOLUNTEER_RELEASED,
        )
        await self.role_mapping_service.reconcile_users(user, [user.id])
        await self._notify_coordinators_of_release(user, shift, details)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def check_in(self, assignment_id: int, actor: User) -> VolunteerAssignment:
        """Record that a volunteer appeared for their shift."""
        if not await AuthService.can_manage_volunteers(actor):
            raise PermissionError('Only coordinators and staff can record check-ins.')
        assignment = require_found(
            await self.assignment_repository.get_by_id(assignment_id), "Assignment"
        )
        if assignment.checked_in_at is None:
            assignment.checked_in_at = datetime.now(timezone.utc)
            assignment.checked_in_by_id = actor.id
            assignment.late_minutes = None
            await self.assignment_repository.save(
                assignment, update_fields=['checked_in_at', 'checked_in_by_id', 'late_minutes'],
            )
            await self.audit_service.write_and_publish(
                actor, AuditActions.VOLUNTEER_CHECKED_IN,
                {'assignment_id': assignment.id, 'shift_id': assignment.shift_id,
                 'self_reported': False},
                EventType.VOLUNTEER_CHECKED_IN, event_extra={'user_id': assignment.user_id},
            )
        return assignment

    async def assignments_for_user(
        self,
        user: User,
        upcoming_after: Optional[datetime] = None,
        *,
        include_drafts: bool = False,
        with_shiftmates: bool = False,
    ) -> List[VolunteerAssignment]:
        return await self.assignment_repository.list_for_user(
            user, upcoming_after=upcoming_after,
            include_drafts=include_drafts, with_shiftmates=with_shiftmates,
        )

    # --- Coverage ---------------------------------------------------------

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def coverage_for_viewer(self, viewer: Optional[User], start: datetime, end: datetime) -> List[Dict]:
        await _ensure_can_view_schedule(viewer)
        return await self.coverage(start, end)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def coverage(self, start: datetime, end: datetime) -> List[Dict]:
        """Per-shift filled/needed counts across [start, end].

        ``filled`` counts drafts: this is the coordinator's working schedule, and
        a draft is real work-in-progress to them. ``drafts`` and ``acknowledged``
        break that total down so a caller can tell how much of it is committed.
        """
        shifts = await self.shift_repository.list_for_window(start, end)
        rows: List[Dict] = []
        for shift in shifts:
            assignments = list(shift.assignments)
            filled = len(assignments)
            drafts = sum(1 for a in assignments if a.auto_generated)
            acknowledged = sum(
                1 for a in assignments
                if not a.auto_generated and a.acknowledged_at is not None
            )
            rows.append({
                'shift_id': shift.id,
                'position': shift.position.name if shift.position else '',
                'label': shift.label or '',
                'starts_at': shift.starts_at,
                'ends_at': shift.ends_at,
                'filled': filled,
                'needed': shift.slots_needed,
                'understaffed': filled < shift.slots_needed,
                'drafts': drafts,
                'acknowledged': acknowledged,
            })
        return rows

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def day_summary(self, start: datetime, end: datetime) -> Dict:
        """Aggregate coverage for a day: the numbers the coordinator's strip shows.

        Declares the gate rather than inheriting ``coverage``'s: this is a
        top-level read returning the feature's data, and the refusal should not
        depend on which internal call it happens to make.
        """
        rows = await self.coverage(start, end)
        return {
            'shifts': len(rows),
            'needed': sum(r['needed'] for r in rows),
            'filled': sum(r['filled'] for r in rows),
            'open': sum(max(0, r['needed'] - r['filled']) for r in rows),
            'drafts': sum(r['drafts'] for r in rows),
            'unacknowledged': sum(
                r['filled'] - r['drafts'] - r['acknowledged'] for r in rows
            ),
            'understaffed_shifts': sum(1 for r in rows if r['understaffed']),
        }

    # --- Helpers ----------------------------------------------------------

    async def _require_position(self, position_id: int) -> VolunteerPosition:
        """This community's position, or a ValueError. Never another tenant's."""
        position = await self.position_repository.get_by_id(position_id)
        if position is None:
            raise ValueError("That position doesn't exist in this community.")
        return position

    async def _refuse_published_clash(self, assignment: VolunteerAssignment) -> None:
        """Raise if publishing this draft would double-book its volunteer."""
        if getattr(assignment.shift, "starts_at", None) is None:
            # A freshly created row carries unfetched relations.
            assignment = require_found(
                await self.assignment_repository.get_by_id(assignment.id), "Volunteer assignment",
            )
        shift = assignment.shift
        clashes = await self.assignment_repository.overlapping_for_user(
            assignment.user_id, shift.starts_at, shift.ends_at,  # type: ignore[attr-defined]
            exclude_shift_id=shift.id, published_only=True,
        )
        if not clashes:
            return
        other = clashes[0].shift
        other_name = other.position.name if other.position else 'another shift'
        if other.label:
            other_name = f'{other_name} — {other.label}'
        name = assignment.user.preferred_name if assignment.user else 'This volunteer'
        raise ValueError(
            f"{name} is already on {other_name} "
            f"({format_local_display(other.starts_at)}–{format_local_time(other.ends_at)}), "
            f"which overlaps this shift."
        )

    async def _availability_warning(self, user: User, shift: VolunteerShift) -> Optional[str]:
        from application.services.volunteer.volunteer_availability_service import (
            VolunteerAvailabilityService,
        )
        from models import VolunteerAvailabilityStatus

        svc = VolunteerAvailabilityService()
        windows = await svc.availability_for(user)
        status = VolunteerAvailabilityService.covers(windows, shift.starts_at, shift.ends_at)
        if status == VolunteerAvailabilityStatus.UNAVAILABLE:
            return f"{user.preferred_name} marked this time as unavailable."
        if status is None and windows:
            return f"{user.preferred_name} has not marked this time as available."
        return None

    async def request_acknowledgment(
        self, assignment: VolunteerAssignment, shift: VolunteerShift, user: User,
    ) -> None:
        """Best-effort Discord DM asking the volunteer to confirm. Never raises."""
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import volunteer_assignment_dm

        discord_id = getattr(user, 'discord_id', None)
        if not discord_id:
            return
        position_name = shift.position.name if shift.position else ''
        message = volunteer_assignment_dm(
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
        )
        description = 'Tap **Acknowledge** below to confirm you can cover this shift.'
        if shift.label:
            description = f'**{shift.label}**\n{description}'
        embed = volunteer_embed(
            title='🙋 Volunteer shift assigned',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description=description,
        )
        discord_queue.enqueue(
            self.discord_service.send_dm_with_volunteer_acknowledgment_button(
                int(discord_id), message, assignment.id, embed=embed,
            )
        )

    async def _notify_removed(self, user: User, shift: VolunteerShift) -> None:
        """Best-effort Discord DM telling a volunteer they are off a shift. Never raises."""
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import volunteer_unassigned_dm

        discord_id = getattr(user, 'discord_id', None)
        if not discord_id:
            return
        position_name = shift.position.name if shift.position else ''
        message = volunteer_unassigned_dm(
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
        )
        embed = volunteer_embed(
            title='🙋 Volunteer shift removed',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description='No action needed.',
        )
        discord_queue.enqueue(
            self.discord_service.send_dm(int(discord_id), message, embed=embed)
        )

    async def _notify_shift_moved(
        self, assignment: VolunteerAssignment, shift: VolunteerShift, user: User,
        old_starts: datetime, old_ends: datetime,
    ) -> None:
        """Best-effort DM re-asking a volunteer to confirm a moved shift. Never raises."""
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import volunteer_shift_changed_dm

        discord_id = getattr(user, 'discord_id', None)
        if not discord_id:
            return
        position_name = shift.position.name if shift.position else ''
        message = volunteer_shift_changed_dm(
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
            old_starts_display=time_field(old_starts),
            old_ends_display=time_field(old_ends),
        )
        embed = volunteer_embed(
            title='🙋 Volunteer shift moved',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description='Tap **Acknowledge** to confirm you can still cover it.',
        )
        discord_queue.enqueue(
            self.discord_service.send_dm_with_volunteer_acknowledgment_button(
                int(discord_id), message, assignment.id, embed=embed,
            )
        )

    async def _notify_coordinators_of_release(
        self, volunteer: User, shift: VolunteerShift, details: Dict,
    ) -> None:
        """Best-effort DM to whoever has to find cover. Never raises."""
        from application.services.tenant_service import TenantService
        from application.services.volunteer.volunteer_coordinator_dm import notify_coordinators
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import (
            released_description,
            volunteer_released_dm,
        )

        position_name = shift.position.name if shift.position else ''
        message = volunteer_released_dm(
            volunteer_name=volunteer.preferred_name,
            position_name=position_name,
            label=shift.label,
            starts_display=time_field(shift.starts_at),
            ends_display=time_field(shift.ends_at),
            hours_notice=details.get('hours_notice'),
            reason=details.get('reason'),
        )
        embed = volunteer_embed(
            title='🙋 Volunteer dropped a shift',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at,
            ends=shift.ends_at,
            description=released_description(
                volunteer.preferred_name, details.get('hours_notice'), details.get('reason'),
            ),
        )
        # The ask is "find cover", so the button opens the roster on the day of
        # the shift rather than on whatever day the schedule defaults to.
        await notify_coordinators(shift, message, embed, link_label='Find cover')
