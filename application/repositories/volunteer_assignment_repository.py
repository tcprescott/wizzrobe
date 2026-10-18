"""
VolunteerAssignment Repository - Data Access Layer

Volunteers placed into shifts.
"""

from datetime import datetime
from typing import List, Optional, Sequence, Set, cast

from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import User, VolunteerAssignment, VolunteerShift

_PREFETCH = ('user', 'shift', 'shift__position', 'assigned_by')

# Only for the volunteer's own card, which names who else is on the slot. Kept
# off _PREFETCH so every other caller does not pay for the extra two joins.
_PREFETCH_WITH_SHIFTMATES = (*_PREFETCH, 'shift__assignments', 'shift__assignments__user')


class VolunteerAssignmentRepository:
    """Repository for volunteer shift assignments."""

    @staticmethod
    async def get_by_id(assignment_id: int) -> Optional[VolunteerAssignment]:
        return await VolunteerAssignment.get_or_none(
            id=assignment_id, tenant_id=current_tenant_id(),
        ).prefetch_related(*_PREFETCH)

    @staticmethod
    async def get_for_shift_and_user(
        shift_id: int, user_id: int,
    ) -> Optional[VolunteerAssignment]:
        return await scoped(
            VolunteerAssignment.filter(shift_id=shift_id, user_id=user_id)
        ).prefetch_related(*_PREFETCH).first()

    @staticmethod
    async def exists(shift_id: int, user_id: int) -> bool:
        return await scoped(VolunteerAssignment.filter(shift_id=shift_id, user_id=user_id)).exists()

    @staticmethod
    async def create(
        shift_id: int,
        user_id: int,
        assigned_by_id: Optional[int] = None,
        auto_generated: bool = False,
    ) -> VolunteerAssignment:
        return await VolunteerAssignment.create(
            tenant_id=current_tenant_id(),
            shift_id=shift_id,
            user_id=user_id,
            assigned_by_id=assigned_by_id,
            auto_generated=auto_generated,
        )

    @staticmethod
    async def delete(assignment: VolunteerAssignment) -> None:
        await assignment.delete()

    @staticmethod
    async def save(
        assignment: VolunteerAssignment, update_fields: Sequence[str],
    ) -> VolunteerAssignment:
        await save_fields(assignment, *update_fields)
        return assignment

    @staticmethod
    async def overlapping_for_user(
        user_id: int, start: datetime, end: datetime, exclude_shift_id: Optional[int] = None,
        *, published_only: bool = False,
    ) -> List[VolunteerAssignment]:
        """Assignments whose shift overlaps [start, end] for this user."""
        query = scoped(VolunteerAssignment.filter(
            user_id=user_id, shift__starts_at__lt=end, shift__ends_at__gt=start,
        ))
        if published_only:
            query = query.filter(auto_generated=False)
        if exclude_shift_id is not None:
            query = query.exclude(shift_id=exclude_shift_id)
        return await query.prefetch_related(*_PREFETCH)

    @staticmethod
    async def list_for_user(
        user: User,
        upcoming_after: Optional[datetime] = None,
        *,
        include_drafts: bool = False,
        with_shiftmates: bool = False,
    ) -> List[VolunteerAssignment]:
        """This user's assignments.

        Drafts are excluded by default: an ``auto_generated`` row is the
        coordinator's sketch and the volunteer has not been told about it (see
        ``VolunteerAutoscheduleService.publish_draft``).
        """
        query = scoped(VolunteerAssignment.filter(user=user))
        if not include_drafts:
            query = query.filter(auto_generated=False)
        if upcoming_after is not None:
            query = query.filter(shift__ends_at__gte=upcoming_after)
        prefetch = _PREFETCH_WITH_SHIFTMATES if with_shiftmates else _PREFETCH
        return await query.order_by('shift__starts_at').prefetch_related(*prefetch)

    @staticmethod
    async def published_position_ids_for_user(user_id: int) -> Set[int]:
        """Positions this user holds a published assignment on, any time."""
        ids = await scoped(VolunteerAssignment.filter(
            user_id=user_id, auto_generated=False,
        )).values_list('shift__position_id', flat=True)
        return set(cast(List[int], ids))

    @staticmethod
    async def published_user_ids(shift_id: Optional[int] = None) -> Set[int]:
        """Everyone holding a published assignment, optionally on one shift."""
        query = scoped(VolunteerAssignment.filter(auto_generated=False))
        if shift_id is not None:
            query = query.filter(shift_id=shift_id)
        return set(cast(List[int], await query.values_list('user_id', flat=True)))

    @staticmethod
    async def list_for_window(start: datetime, end: datetime) -> List[VolunteerAssignment]:
        return await (
            scoped(VolunteerAssignment.filter(shift__starts_at__lt=end, shift__ends_at__gt=start))
            .prefetch_related(*_PREFETCH)
        )

    @staticmethod
    async def on_duty_users(at: datetime) -> List[User]:
        """Who holds a published shift at ``at`` on a position that takes staff alerts."""
        assignments = await scoped(VolunteerAssignment.filter(
            auto_generated=False,
            shift__position__staff_alerts=True,
            shift__starts_at__lte=at,
            shift__ends_at__gt=at,
        )).prefetch_related('user')
        users: dict = {}
        for a in assignments:
            users.setdefault(a.user_id, a.user)
        return list(users.values())

    @staticmethod
    async def alert_shifts_for_user(user_id: int, now: datetime) -> List[VolunteerAssignment]:
        """The user's published shifts on alert-receiving positions not yet over, soonest first."""
        return await scoped(VolunteerAssignment.filter(
            user_id=user_id,
            auto_generated=False,
            shift__position__staff_alerts=True,
            shift__ends_at__gt=now,
        )).order_by('shift__starts_at').prefetch_related('shift__position')

    @staticmethod
    async def published_for_window(
        start: datetime, end: datetime, user_id: Optional[int] = None,
    ) -> List[VolunteerAssignment]:
        """Published assignments whose shift overlaps [start, end].

        ``auto_generated=False`` is the published half of the draft flip; a
        released shift is deleted outright, so nothing else needs excluding.
        Tenant-scoped: this runs from a page, not a worker.
        """
        query = scoped(VolunteerAssignment.filter(
            auto_generated=False, shift__starts_at__lt=end, shift__ends_at__gt=start,
        ))
        if user_id is not None:
            query = query.filter(user_id=user_id)
        return await query.order_by('shift__starts_at').prefetch_related('shift', 'user')

    @staticmethod
    async def delete_auto_for_window(
        start: datetime, end: datetime, position_ids: Optional[Sequence[int]] = None,
    ) -> int:
        """Delete draft (auto-generated) assignments whose shift overlaps the window.

        ``position_ids`` narrows it to those positions' shifts; ``None`` means all.

        Done in two steps (resolve shift ids, then delete by id) so it works on
        databases that don't support join-based DELETE (e.g. SQLite in tests).
        """
        shifts = scoped(VolunteerShift.filter(starts_at__lt=end, ends_at__gt=start))
        if position_ids is not None:
            shifts = shifts.filter(position_id__in=list(position_ids))
        shift_ids = await shifts.values_list('id', flat=True)
        if not shift_ids:
            return 0
        return await scoped(VolunteerAssignment.filter(
            auto_generated=True, shift_id__in=list(shift_ids),
        )).delete()

    @staticmethod
    async def count_auto_for_window(start: datetime, end: datetime) -> int:
        """How many draft assignments sit in the window."""
        shift_ids = await scoped(VolunteerShift.filter(
            starts_at__lt=end, ends_at__gt=start,
        )).values_list('id', flat=True)
        if not shift_ids:
            return 0
        return await scoped(VolunteerAssignment.filter(
            auto_generated=True, shift_id__in=list(shift_ids),
        )).count()

    @staticmethod
    async def list_auto_for_window(start: datetime, end: datetime) -> List[VolunteerAssignment]:
        """Draft assignments whose shift overlaps the window, prefetched for notification."""
        shift_ids = await scoped(VolunteerShift.filter(
            starts_at__lt=end, ends_at__gt=start,
        )).values_list('id', flat=True)
        if not shift_ids:
            return []
        return await (
            scoped(VolunteerAssignment.filter(
                auto_generated=True, shift_id__in=list(shift_ids),
            ))
            .order_by('shift__starts_at')
            .prefetch_related(*_PREFETCH)
        )

    @staticmethod
    async def mark_published(assignment: VolunteerAssignment) -> VolunteerAssignment:
        assignment.auto_generated = False
        await assignment.save(update_fields=['auto_generated', 'updated_at'])
        return assignment

    @staticmethod
    async def published_unfinished(
        now: datetime, shift_id: Optional[int] = None,
    ) -> List[VolunteerAssignment]:
        """Published assignments on shifts that have not ended, optionally one shift."""
        query = scoped(VolunteerAssignment.filter(auto_generated=False, shift__ends_at__gt=now))
        if shift_id is not None:
            query = query.filter(shift_id=shift_id)
        return await query.prefetch_related(*_PREFETCH)

    @staticmethod
    async def list_for_shift(shift_id: int) -> List[VolunteerAssignment]:
        return await (
            scoped(VolunteerAssignment.filter(shift_id=shift_id))
            .prefetch_related(*_PREFETCH)
        )

    @staticmethod
    async def awaiting_arrival(
        user_id: int, opens_before: datetime, now: datetime,
    ) -> List[VolunteerAssignment]:
        """The user's published, un-checked-in assignments whose check-in window is open.

        A shift's window runs from ``opens_before`` (now plus the lead) back to its
        start, and ends when the shift does.
        """
        return await (
            scoped(VolunteerAssignment.filter(
                user_id=user_id,
                auto_generated=False,
                checked_in_at__isnull=True,
                shift__starts_at__lte=opens_before,
                shift__ends_at__gt=now,
            ))
            .prefetch_related(*_PREFETCH)
            .order_by('shift__starts_at')
        )

    @staticmethod
    async def due_for_start_dm(
        started_after: datetime, now: datetime,
    ) -> List[VolunteerAssignment]:
        """Published assignments whose shift started in (started_after, now] and
        that have had no shift-start DM and no check-in yet."""
        # Deliberately cross-tenant, like due_for_reminder: the worker wraps each
        # assignment in its own tenant_scope. Do NOT scope this.
        return await (
            VolunteerAssignment.filter(
                auto_generated=False,
                start_dm_sent_at__isnull=True,
                checked_in_at__isnull=True,
                shift__starts_at__gt=started_after,
                shift__starts_at__lte=now,
            )
            .prefetch_related(*_PREFETCH)
        )

    @staticmethod
    async def due_for_reminder(
        window_start: datetime, window_end: datetime,
    ) -> List[VolunteerAssignment]:
        """Un-reminded assignments whose shift starts within [window_start, window_end].

        Drafts are skipped — the reminder must not be the message that first tells
        someone they have a shift.
        """
        # Deliberately cross-tenant: the reminder loop scans every tenant and wraps
        # each assignment in its own tenant_scope before acting. Do NOT scope this.
        return await (
            VolunteerAssignment.filter(
                auto_generated=False,
                reminder_sent_at__isnull=True,
                shift__starts_at__gte=window_start,
                shift__starts_at__lte=window_end,
            )
            .prefetch_related(*_PREFETCH)
        )
