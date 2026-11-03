"""
Volunteer Auto-schedule Service - Business Logic Layer

Greedy/heuristic draft generator. Fills open shift slots from the opted-in
volunteer pool using qualifications, availability, no-overlap, and hour
load-balancing. Produces ``auto_generated`` assignments the coordinator reviews.
"""

import itertools
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from tortoise.transactions import in_transaction

from application.repositories import (
    VolunteerAssignmentRepository,
    VolunteerQualificationRepository,
    VolunteerShiftRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.volunteer.volunteer_availability_service import VolunteerAvailabilityService
from application.services.volunteer.volunteer_pool_service import VolunteerPoolService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from models import (
    User,
    VolunteerAvailabilityStatus,
)

logger = logging.getLogger(__name__)

_AVAIL_RANK = {
    VolunteerAvailabilityStatus.PREFERRED: 0,
    VolunteerAvailabilityStatus.AVAILABLE: 1,
    None: 2,
}

DEFAULT_MAX_HOURS = 8.0

_CONSECUTIVE_GAP = timedelta(minutes=15)

_REASON_ORDER = ('not_stated', 'at_hour_cap', 'unavailable', 'overlapping')


@dataclass(frozen=True)
class DraftPolicy:
    """What the coordinator is willing to let the autoscheduler do."""

    max_hours: float = DEFAULT_MAX_HOURS
    fill_outside_availability: bool = False


class VolunteerAutoscheduleService:
    """Builds a draft volunteer schedule from availability + qualifications."""

    def __init__(self) -> None:
        self.shift_repository = VolunteerShiftRepository()
        self.assignment_repository = VolunteerAssignmentRepository()
        self.qualification_repository = VolunteerQualificationRepository()
        self.pool_service = VolunteerPoolService()
        self.availability_service = VolunteerAvailabilityService()
        self.schedule_service = VolunteerScheduleService()
        self.audit_service = AuditService()

    async def generate_draft(
        self,
        actor: User,
        start: datetime,
        end: datetime,
        *,
        position_ids: Optional[Sequence[int]] = None,
        clear_existing_drafts: bool = True,
        policy: Optional[DraftPolicy] = None,
    ) -> Dict:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can auto-fill the schedule.",
        )
        policy = policy or DraftPolicy()

        if clear_existing_drafts:
            # Only the re-drafted positions: other positions' reviewed drafts must survive.
            await self.assignment_repository.delete_auto_for_window(
                start, end, position_ids=position_ids or None,
            )

        shifts = await self.shift_repository.list_for_window(start, end)
        if position_ids:
            allowed = set(position_ids)
            shifts = [s for s in shifts if s.position_id in allowed]
        shifts.sort(key=lambda s: (s.starts_at, s.id))

        pool = await self.pool_service.assignable_volunteers()
        if not pool:
            empty = {s.id: len(s.assignments) for s in shifts}
            return {
                'created': 0,
                'pool_size': 0,
                'policy': policy,
                'unfilled': self._unfilled_summary(shifts, empty, {}, policy),
                'outside_availability': [],
                'heavy_loads': [],
            }
        pool_ids = [u.id for u in pool]

        quals = await self.qualification_repository.position_ids_by_user(pool_ids)
        avail_map = await self.availability_service.availability_map(pool_ids, start, end)

        # Seed across the shifts' full span, not the window: an overnight shift can clash outside it.
        intervals: Dict[int, List[Tuple[datetime, datetime]]] = {uid: [] for uid in pool_ids}
        hours: Dict[int, float] = {uid: 0.0 for uid in pool_ids}
        on_shift: Dict[int, Set[int]] = {uid: set() for uid in pool_ids}
        span_start = min([start, *(s.starts_at for s in shifts)])
        span_end = max([end, *(s.ends_at for s in shifts)])
        for a in await self.assignment_repository.list_for_window(span_start, span_end):
            if a.user_id in intervals:
                intervals[a.user_id].append((a.shift.starts_at, a.shift.ends_at))
                if a.shift.starts_at < end and a.shift.ends_at > start:
                    hours[a.user_id] += self._hours(a.shift.starts_at, a.shift.ends_at)
                on_shift[a.user_id].add(a.shift_id)

        created = 0
        filled_counts: Dict[int, int] = {}
        reasons_by_shift: Dict[int, Counter] = {}
        outside: List[Dict] = []
        async with in_transaction():
            for shift in shifts:
                filled = len(shift.assignments)
                shift_hours = self._hours(shift.starts_at, shift.ends_at)
                reasons = reasons_by_shift.setdefault(shift.id, Counter())
                for _ in range(shift.slots_needed - filled):
                    candidate = self._pick(
                        shift, pool, pool_ids, quals, avail_map, intervals, hours, on_shift,
                        policy=policy, shift_hours=shift_hours, reasons=reasons,
                    )
                    if candidate is None:
                        break
                    await self.assignment_repository.create(
                        shift_id=shift.id, user_id=candidate.id,
                        assigned_by_id=actor.id, auto_generated=True,
                    )
                    status = VolunteerAvailabilityService.covers(
                        avail_map.get(candidate.id, []), shift.starts_at, shift.ends_at,
                    )
                    if status is None:
                        outside.append({
                            'user_id': candidate.id,
                            'name': candidate.preferred_name,
                            'shift_id': shift.id,
                            'position': shift.position.name if shift.position else '',
                            'label': shift.label or '',
                            'starts_at': shift.starts_at,
                        })
                    intervals[candidate.id].append((shift.starts_at, shift.ends_at))
                    hours[candidate.id] += shift_hours
                    on_shift[candidate.id].add(shift.id)
                    filled += 1
                    created += 1
                filled_counts[shift.id] = filled

            open_slots = sum(
                max(0, s.slots_needed - filled_counts.get(s.id, len(s.assignments)))
                for s in shifts
            )
            await self.audit_service.write_log(
                actor, AuditActions.VOLUNTEER_DRAFT_GENERATED,
                {'created': created, 'start': start, 'end': end,
                 'open': open_slots, 'pool_size': len(pool),
                 'max_hours': policy.max_hours,
                 'fill_outside_availability': policy.fill_outside_availability},
            )

        return {
            'created': created,
            'pool_size': len(pool),
            'policy': policy,
            'unfilled': self._unfilled_summary(shifts, filled_counts, reasons_by_shift, policy),
            'outside_availability': outside,
            'heavy_loads': self._heavy_loads(pool, hours, intervals, policy),
        }

    async def publish_draft(self, actor: User, start: datetime, end: datetime) -> Dict:
        """Commit every draft assignment in the window and tell each volunteer.

        The inverse of ``clear_draft``: same window, same authorization, opposite
        decision. Returns counts for the coordinator's confirmation toast.
        """
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can publish drafts.",
        )
        drafts = await self.assignment_repository.list_auto_for_window(start, end)
        # Not in a transaction: a rollback can't un-send the DMs, and confirm_assignment is idempotent.
        published = []
        clashes: List[str] = []
        for assignment in drafts:
            try:
                await self.schedule_service.confirm_assignment(actor, assignment)
            except ValueError as e:
                clashes.append(str(e))
                continue
            published.append(assignment)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_DRAFT_PUBLISHED,
            {'published': len(published), 'skipped': len(clashes), 'start': start, 'end': end},
        )
        return {'published': len(published),
                'volunteers': len({a.user_id for a in published}),
                'clashes': clashes}

    async def clear_draft(self, actor: User, start: datetime, end: datetime) -> int:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can clear drafts.",
        )
        removed = await self.assignment_repository.delete_auto_for_window(start, end)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_DRAFT_CLEARED,
            {'removed': removed, 'start': start, 'end': end},
        )
        return removed

    def _pick(
        self, shift, pool, pool_ids, quals, avail_map, intervals, hours, on_shift,
        *,
        policy: Optional[DraftPolicy] = None,
        shift_hours: float = 0.0,
        reasons: Optional[Counter] = None,
    ) -> Optional[User]:
        policy = policy or DraftPolicy()
        if reasons is None:
            reasons = Counter()
        best: Optional[User] = None
        best_key = None
        for user in pool:
            uid = user.id
            if shift.id in on_shift[uid]:
                continue
            qualified_set = quals.get(uid, set())
            if qualified_set and shift.position_id not in qualified_set:
                continue
            windows = avail_map.get(uid, [])
            status = VolunteerAvailabilityService.covers(windows, shift.starts_at, shift.ends_at)
            if status == VolunteerAvailabilityStatus.UNAVAILABLE:
                reasons['unavailable'] += 1
                continue
            if status is None and not policy.fill_outside_availability:
                reasons['not_stated'] += 1
                continue
            if self._overlaps(intervals[uid], shift.starts_at, shift.ends_at):
                reasons['overlapping'] += 1
                continue
            if policy.max_hours and hours[uid] + shift_hours > policy.max_hours:
                reasons['at_hour_cap'] += 1
                continue
            qual_priority = 0 if shift.position_id in qualified_set else 1
            key = (qual_priority, _AVAIL_RANK.get(status, 2), hours[uid], user.preferred_name.lower())
            if best_key is None or key < best_key:
                best_key = key
                best = user
        return best

    @staticmethod
    def _overlaps(existing: List[Tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
        return any(s < end and e > start for s, e in existing)

    @staticmethod
    def _hours(start: datetime, end: datetime) -> float:
        return max(0.0, (end - start).total_seconds() / 3600.0)

    @staticmethod
    def _reason_sentence(reasons: Optional[Counter], max_hours: Optional[float]) -> str:
        """Say why a slot stayed open, in the coordinator's terms."""
        if reasons:
            dominant = max(
                _REASON_ORDER,
                key=lambda name: (reasons.get(name, 0), -_REASON_ORDER.index(name)),
            )
            if reasons.get(dominant):
                if dominant == 'not_stated':
                    return 'Nobody qualified has marked this time as available.'
                if dominant == 'at_hour_cap':
                    limit = f'{max_hours:g}' if max_hours else 'hour'
                    return f'Everyone eligible is at the {limit}-hour limit.'
                if dominant == 'unavailable':
                    return 'Everyone qualified marked this time unavailable.'
                return 'Everyone qualified is already on another shift.'
        return 'No qualified volunteer in the pool.'

    @classmethod
    def _unfilled_summary(
        cls,
        shifts,
        filled_counts: Dict[int, int],
        reasons_by_shift: Optional[Dict[int, Counter]] = None,
        policy: Optional[DraftPolicy] = None,
    ) -> List[Dict]:
        reasons_by_shift = reasons_by_shift or {}
        max_hours = policy.max_hours if policy else None
        out: List[Dict] = []
        for shift in shifts:
            filled = filled_counts.get(shift.id, len(shift.assignments))
            if filled < shift.slots_needed:
                out.append({
                    'shift_id': shift.id,
                    'position': shift.position.name if shift.position else '',
                    'label': shift.label or '',
                    'starts_at': shift.starts_at,
                    'open': shift.slots_needed - filled,
                    'reason': cls._reason_sentence(
                        reasons_by_shift.get(shift.id), max_hours,
                    ),
                })
        return out

    @classmethod
    def _heavy_loads(
        cls, pool, hours: Dict[int, float],
        intervals: Dict[int, List[Tuple[datetime, datetime]]],
        policy: DraftPolicy,
    ) -> List[Dict]:
        """Volunteers this run leaves working a long day, so the coordinator can look."""
        threshold = (policy.max_hours or 0) * 0.75
        out: List[Dict] = []
        for user in pool:
            blocks = intervals.get(user.id) or []
            if not blocks:
                continue
            worked = hours.get(user.id, 0.0)
            if not (threshold and worked >= threshold) and cls._longest_run(blocks) < 3:
                continue
            out.append({
                'user_id': user.id,
                'name': user.preferred_name,
                'hours': round(worked, 1),
                'shifts': len(blocks),
            })
        return out

    @staticmethod
    def _longest_run(blocks: List[Tuple[datetime, datetime]]) -> int:
        """How many of these blocks are worked back-to-back, at most."""
        ordered = sorted(blocks)
        longest = run = 1
        for (_, prev_end), (next_start, _) in itertools.pairwise(ordered):
            run = run + 1 if next_start - prev_end <= _CONSECUTIVE_GAP else 1
            longest = max(longest, run)
        return longest
