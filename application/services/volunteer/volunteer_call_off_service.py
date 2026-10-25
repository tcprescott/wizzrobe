"""Staff calling off a volunteer's shift: you're not needed, and it still counts.

Some shifts turn out to be overstaffed, or a stage goes dark early. Removing the
volunteer would cost them the hours they set aside, so instead staff "call off"
the assignment: the volunteer is told by DM they needn't come (or can head out,
if the shift has started), and the shift is credited as fully worked. A
called-off assignment skips check-in entirely: no "I'm here" banner, no
shift-start DM, no reminder. Hours, comps and attendance read it through
``VolunteerAssignment.counts_as_worked``.
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from application.errors import require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories import UserRepository, VolunteerAssignmentRepository
from application.services import notification_catalog as notices
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.utils.discord_messages_volunteer import REASON_MAX_CHARS
from models import FeatureFlag, User, VolunteerAssignment


def can_call_off(assignment: VolunteerAssignment, now: datetime) -> bool:
    """Whether the Shift Board offers "Call off…" for this person."""
    return (
        assignment.called_off_at is None
        and not assignment.auto_generated
        and assignment.shift.ends_at > now
    )


async def call_off_names(shifts: Iterable) -> Dict[int, str]:
    """Names of whoever called someone off on these (assignment-prefetched) shifts."""
    ids = {a.called_off_by_id for s in shifts for a in s.assignments if a.called_off_by_id}
    if not ids:
        return {}
    return {uid: u.preferred_name for uid, u in (await UserRepository.get_by_ids(list(ids))).items()}


def _clean_note(note: Optional[str]) -> Optional[str]:
    text = (note or '').strip()
    return text[:REASON_MAX_CHARS] or None


class VolunteerCallOffService:
    """Call off (and take back) volunteer assignments, with a DM each way."""

    def __init__(self) -> None:
        self.assignment_repository = VolunteerAssignmentRepository()
        self.audit_service = AuditService()
        self.discord_service = DiscordService()

    async def _ensure_coordinator(self, actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            'Only coordinators and staff can call off shifts.',
        )

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def call_off(
        self, assignment_id: int, actor: User, note: Optional[str] = None,
    ) -> VolunteerAssignment:
        """Tell one volunteer they're not needed. Repeating it is a no-op."""
        await self._ensure_coordinator(actor)
        assignment = require_found(
            await self.assignment_repository.get_by_id(assignment_id), 'Volunteer assignment',
        )
        if assignment.called_off_at is not None:
            return assignment
        if assignment.auto_generated:
            raise ValueError(
                "That's still a draft, so they haven't been told about it. Remove them instead."
            )
        now = datetime.now(timezone.utc)
        if assignment.shift.ends_at <= now:
            raise ValueError('That shift has already finished.')
        await self._call_off(assignment, actor, _clean_note(note), now)
        return assignment

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def call_off_shift(
        self, shift_id: int, actor: User, note: Optional[str] = None,
    ) -> List[VolunteerAssignment]:
        """Call off everyone told about this shift. Returns the ones newly called off.

        Drafts are left alone (nobody told them, so there is nothing to call
        off) and so is anyone already called off.
        """
        await self._ensure_coordinator(actor)
        assignments = await self.assignment_repository.list_for_shift(shift_id)
        now = datetime.now(timezone.utc)
        if assignments and assignments[0].shift.ends_at <= now:
            raise ValueError('That shift has already finished.')
        cleaned = _clean_note(note)
        done: List[VolunteerAssignment] = []
        for assignment in assignments:
            if assignment.auto_generated or assignment.called_off_at is not None:
                continue
            await self._call_off(assignment, actor, cleaned, now)
            done.append(assignment)
        if not done:
            raise ValueError('Nobody on this shift is left to call off.')
        return done

    async def _call_off(
        self, assignment: VolunteerAssignment, actor: User, note: Optional[str], now: datetime,
    ) -> None:
        shift = assignment.shift
        started = shift.starts_at <= now
        assignment.called_off_at = now
        assignment.called_off_by_id = actor.id
        assignment.called_off_note = note  # type: ignore[assignment]
        assignment.late_minutes = None  # type: ignore[assignment]
        await self.assignment_repository.save(
            assignment,
            update_fields=['called_off_at', 'called_off_by_id', 'called_off_note', 'late_minutes'],
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.VOLUNTEER_CALLED_OFF,
            {'assignment_id': assignment.id, 'shift_id': shift.id,
             'user_id': assignment.user_id,  # type: ignore[attr-defined]
             'started': started, 'note': note},
            EventType.VOLUNTEER_CALLED_OFF,
            event_details={'assignment_id': assignment.id, 'shift_id': shift.id,
                           'user_id': assignment.user_id,  # type: ignore[attr-defined]
                           'started': started},
        )
        await self._notify_called_off(assignment, actor, started, note)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def undo_call_off(self, assignment_id: int, actor: User) -> VolunteerAssignment:
        """Put a called-off volunteer back on the shift and DM them that they're needed."""
        await self._ensure_coordinator(actor)
        assignment = require_found(
            await self.assignment_repository.get_by_id(assignment_id), 'Volunteer assignment',
        )
        if assignment.called_off_at is None:
            return assignment
        if assignment.shift.ends_at <= datetime.now(timezone.utc):
            raise ValueError("That shift has already finished, so the call-off stands.")
        assignment.called_off_at = None  # type: ignore[assignment]
        assignment.called_off_by_id = None
        assignment.called_off_note = None  # type: ignore[assignment]
        await self.assignment_repository.save(
            assignment, update_fields=['called_off_at', 'called_off_by_id', 'called_off_note'],
        )
        details = {'assignment_id': assignment.id, 'shift_id': assignment.shift_id,  # type: ignore[attr-defined]
                   'user_id': assignment.user_id}  # type: ignore[attr-defined]
        await self.audit_service.write_and_publish(
            actor, AuditActions.VOLUNTEER_CALL_OFF_UNDONE, details,
            EventType.VOLUNTEER_CALL_OFF_UNDONE,
        )
        await self._notify_undone(assignment, actor)
        return assignment

    # --- DMs ---------------------------------------------------------------

    async def _notify_called_off(
        self, assignment: VolunteerAssignment, actor: User, started: bool, note: Optional[str],
    ) -> None:
        """Best-effort DM to the volunteer. Never raises."""
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import (
            call_off_description,
            call_off_title,
            volunteer_called_off_dm,
        )

        user, shift = assignment.user, assignment.shift
        discord_id = getattr(user, 'discord_id', None)
        if not discord_id:
            return
        position_name = shift.position.name if shift.position else ''
        hours = round((shift.ends_at - shift.starts_at).total_seconds() / 3600, 1)
        message = volunteer_called_off_dm(
            position_name=position_name, label=shift.label,
            starts_display=time_field(shift.starts_at), ends_display=time_field(shift.ends_at),
            hours=hours, started=started, note=note, by_name=actor.preferred_name,
        )
        embed = volunteer_embed(
            title=f'🙌 {call_off_title(position_name, started)}',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at, ends=shift.ends_at,
            description=call_off_description(hours, started, note, actor.preferred_name),
        )
        discord_queue.enqueue(self.discord_service.send_dm(
            int(discord_id), message, embed=embed,
            link=await notification_links.volunteer_my_shifts(label='View my shifts'),
            notice=notices.SHIFT_CALLED_OFF.to(notices.VOLUNTEERS),
        ))

    async def _notify_undone(self, assignment: VolunteerAssignment, actor: User) -> None:
        """Best-effort DM: the shift is on again. Never raises."""
        from application.services import notification_links
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import time_field, volunteer_embed
        from application.utils.discord_messages_volunteer import (
            call_off_undone_description,
            call_off_undone_title,
            volunteer_call_off_undone_dm,
        )

        user, shift = assignment.user, assignment.shift
        discord_id = getattr(user, 'discord_id', None)
        if not discord_id:
            return
        position_name = shift.position.name if shift.position else ''
        message = volunteer_call_off_undone_dm(
            position_name=position_name, label=shift.label,
            starts_display=time_field(shift.starts_at), ends_display=time_field(shift.ends_at),
            by_name=actor.preferred_name,
        )
        embed = volunteer_embed(
            title=f'🙋 {call_off_undone_title(position_name)}',
            position=position_name or 'Volunteer shift',
            community_name=await TenantService.current_community_name(),
            starts=shift.starts_at, ends=shift.ends_at,
            description=call_off_undone_description(actor.preferred_name),
        )
        discord_queue.enqueue(self.discord_service.send_dm(
            int(discord_id), message, embed=embed,
            link=await notification_links.volunteer_my_shifts(label='View my shifts'),
            notice=notices.SHIFT_CALL_OFF_UNDONE.to(notices.VOLUNTEERS),
        ))
