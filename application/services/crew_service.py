"""
Crew Service - Business Logic Layer

Handles crew (commentator and tracker) related operations.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from tortoise.transactions import in_transaction

from application.errors import require_found
from application.events import Event, EventType, event_bus, match_live
from application.repositories import CommentatorRepository, MatchRepository, TrackerRepository
from application.repositories._save import save_fields
from application.services import notification_links
from application.services._crew_signup_dms import (
    SIGNUP_KIND,
    crew_dm_parts,
    mark_signup_dms,
    restore_signup_dms,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import (
    ADMINS,
    COMMENTATOR_WITHDREW,
    CREW,
    CREW_ASSIGNED,
    CREW_UNASSIGNED,
    TRACKER_WITHDREW,
)
from application.services.tenant_service import TenantService
from application.utils import discord_messages_staff_requests as handled_copy
from application.utils.discord_embeds import (
    COLOR_CANCELLED,
    COLOR_CONFIRMED,
    COLOR_CREW,
    COLOR_FINISHED,
    notification_embed,
)
from application.utils.discord_messages_crew import (
    crew_approval_withdrawn_dm,
    crew_assignment_dm,
    crew_signup_pending_dm,
    crew_withdrawn_dm,
)
from application.utils.match_labels import players_label
from application.utils.timezone import format_local_display, to_utc_aware
from models import Commentator, Match, Tracker, User

logger = logging.getLogger(__name__)

# Signing up and undoing it are a tap each, and every signup DMs each of the
# match's crew owners, so a loop would flood them (and the DM queue every
# community shares). One notice per person, match and slot per window is plenty.
_SIGNUP_NOTICE_WINDOW = timedelta(minutes=10)
_SIGNUP_NOTICE_MAX = 2048
_signup_notices: dict[tuple[int, int, str], datetime] = {}
# The crew row each recent notice was about, so a signup made again inside the
# window turns the owners' "withdrawn" DMs back into the ask.
_signup_notice_rows: dict[tuple[int, int, str], int] = {}

_WITHDREW_NOTICE = {'commentator': COMMENTATOR_WITHDREW, 'tracker': TRACKER_WITHDREW}


def _signup_notice_due(match_id: int, user_id: int, crew_type: str) -> bool:
    """Whether this signup should DM the owners, recording it if so."""
    now = datetime.now(timezone.utc)
    key = (match_id, user_id, crew_type)
    last = _signup_notices.get(key)
    if last is not None and now - last < _SIGNUP_NOTICE_WINDOW:
        return False
    if len(_signup_notices) >= _SIGNUP_NOTICE_MAX:
        oldest = next(iter(_signup_notices))
        _signup_notices.pop(oldest, None)
        _signup_notice_rows.pop(oldest, None)
    _signup_notices.pop(key, None)
    _signup_notices[key] = now
    return True


class CrewService:
    """Service for crew-related business operations."""

    def __init__(self) -> None:
        self.commentator_repository = CommentatorRepository()
        self.tracker_repository = TrackerRepository()
        self.match_repository = MatchRepository()
        self.audit_service = AuditService()
        self.discord_service = DiscordService()

    async def signup_crew(
        self,
        match_id: int,
        user: User,
        role: str
    ) -> None:
        """
        Sign up a user as crew (commentator or tracker) for a match.

        Args:
            match_id: Match ID
            user: User signing up
            role: 'commentator' or 'tracker'

        Raises:
            ValueError: If role is invalid or user already signed up
        """
        if role not in ['commentator', 'tracker']:
            raise ValueError(f"Invalid role: {role}. Must be 'commentator' or 'tracker'")

        # The audit's measured symptom was not that a stranger *saw* a new
        # community's schedule — it was that they were offered Sign Up on it. The
        # page gate stops them reaching the surface, but UI-only gating is not
        # gating: this is reached from the REST API and a Discord button too.
        # ``admit`` rather than a bare membership check: the Discord signup
        # button is often someone's first contact with the community, and a
        # community whose rule would let them in should do so here too, not
        # only once they've opened a web page.
        from application.services.tenant_membership_service import TenantMembershipService
        if not await TenantMembershipService().admit(user):
            raise ValueError(
                'You are not a member of this community. Ask to join it first.'
            )

        match = require_found(
            await self.match_repository.get_by_id(match_id, prefetch_relations=True),
            f"Match {match_id}",
        )

        # Crew signup closes once the match is under way. Enforced here (not in a
        # single caller) so the web UI, REST API, and Discord button all honor it.
        # The audit measured signups accepted on matches already in progress, on
        # which "awaiting approval" describes a match that will be over before
        # anyone reads it. Seated-but-not-started stays open on purpose: that is
        # exactly when a stream discovers it still needs a commentator.
        # Withdrawing stays open in either state.
        if match.finished_at is not None:
            raise ValueError("This match has already finished. Crew signup is closed.")
        if match.started_at is not None:
            raise ValueError("This match has already started. Crew signup is closed.")

        # A tournament that requires none of a role does not staff it, and the
        # schedule hides its Sign up control — but UI-only hiding is not a rule,
        # so refuse here too, for the REST route and the Discord button.
        # Withdrawing stays open: a signup from before the setting changed must
        # still be removable.
        required = (
            match.tournament.required_commentators if role == 'commentator'
            else match.tournament.required_trackers
        ) if match.tournament else 1
        if required == 0:
            raise ValueError(f"This tournament does not use {role}s.")

        crew_list = match.commentators if role == 'commentator' else match.trackers
        if any(c.user_id == user.id for c in crew_list):
            raise ValueError(f"You're already signed up as {role} for this match.")

        players = await self.match_repository.get_players(match)
        if any(p.user_id == user.id for p in players):
            raise ValueError("You can't crew a match you're playing in.")

        repository = self.commentator_repository if role == 'commentator' else self.tracker_repository
        crew_member = await repository.create(match=match, user=user, approved=False)

        await self.audit_service.write_and_publish(
            user,
            AuditActions.CREW_SIGNUP_CREATED,
            {'match_id': match_id, 'role': role},
            EventType.CREW_SIGNUP_CREATED,
            event_extra={'user_id': user.id},
        )
        match_live.publish(match_id)
        await self._notify_crew_owners_of_signup(match, user, role, crew_member.id)

    async def undo_crew_signup(
        self,
        match_id: int,
        user: User,
        role: str
    ) -> None:
        """
        Remove a user's crew signup (commentator or tracker) from a match.

        Args:
            match_id: Match ID
            user: User to remove
            role: 'commentator' or 'tracker'

        Raises:
            ValueError: If role is invalid or user not signed up
        """
        if role not in ['commentator', 'tracker']:
            raise ValueError(f"Invalid role: {role}. Must be 'commentator' or 'tracker'")

        # The audit's measured symptom was not that a stranger *saw* a new
        # community's schedule — it was that they were offered Sign Up on it. The
        # page gate stops them reaching the surface, but UI-only gating is not
        # gating: this is reached from the REST API and a Discord button too.
        from application.services.tenant_membership_service import TenantMembershipService
        if not await TenantMembershipService().is_member(user):
            raise ValueError(
                'You are not a member of this community. Ask to join it first.'
            )

        match = require_found(
            await self.match_repository.get_by_id(match_id, prefetch_relations=True),
            f"Match {match_id}",
        )

        crew_list = match.commentators if role == 'commentator' else match.trackers
        crew_member = next((c for c in crew_list if c.user_id == user.id), None)

        if not crew_member:
            raise ValueError(f"You're not signed up as {role} for this match.")

        # Read before the delete: the row is the only place the approver is
        # recorded, and the notification below needs it.
        was_approved = bool(crew_member.approved)
        approved_by_id = crew_member.approved_by_id

        await crew_member.delete()

        await self.audit_service.write_and_publish(
            user,
            AuditActions.CREW_SIGNUP_REMOVED,
            {'match_id': match_id, 'role': role, 'was_approved': was_approved},
            EventType.CREW_SIGNUP_REMOVED,
            event_extra={'user_id': user.id},
        )
        match_live.publish(match_id)

        if was_approved:
            await self._notify_crew_withdrawal(match, user, role, approved_by_id)
        else:
            now = datetime.now(timezone.utc)
            await mark_signup_dms(
                match, crew_member, role, COLOR_FINISHED,
                lambda _you: handled_copy.crew_signup_withdrawn(user.preferred_name, role, now),
            )

    async def get_match_crew(self, match_id: int, actor: Optional[User]) -> Match:
        """A match with every crew signup on it, pending ones included.

        Pending crew are the approver's working view, so this is gated like
        approving them (``can_approve_crew``): staff, or a TA or CC of *this*
        match's tournament, not any tournament's.
        """
        match = require_found(await self.match_repository.get_by_id(match_id), 'Match')
        await AuthService.ensure(
            await AuthService.can_approve_crew(actor, match),
            "You can't see crew signups for this match.",
        )
        return match

    async def get_crew_member_by_id(
        self,
        crew_id: int,
        crew_type: str,
    ) -> Optional[Union[Commentator, Tracker]]:
        if crew_type == 'commentator':
            return await self.commentator_repository.get_by_id(crew_id)
        elif crew_type == 'tracker':
            return await self.tracker_repository.get_by_id(crew_id)
        else:
            raise ValueError(f"Invalid crew_type: {crew_type}. Must be 'commentator' or 'tracker'")

    async def list_my_commitments(
        self, user: User, upcoming_only: bool = True,
    ) -> list[dict]:
        """Everything ``user`` has signed up to crew, both roles, soonest first.

        The volunteer's side of the loop had no surface at all: the Player tab
        is players-only, there was no My Commitments anywhere, and after signing
        up the only handle on the commitment was a Withdraw button in one row of
        a 17-row shared board. Volunteers who work *shifts* have had exactly this
        page (My Shifts) the whole time; commentators and trackers are the same
        people doing the same kind of work through a different door.

        Returns plain dicts — the presentation layer needs a merged, sorted view
        across two models, and merging ORM rows of different types in the page
        would put the join in the wrong layer.
        """
        since = datetime.now(timezone.utc) if upcoming_only else None
        rows = []
        for role, repository in (
            ('commentator', self.commentator_repository),
            ('tracker', self.tracker_repository),
        ):
            for entry in await repository.get_for_user(user, since=since):
                match = entry.match
                rows.append({
                    'id': entry.id,
                    'role': role,
                    'match_id': match.id,
                    'scheduled_at': match.scheduled_at,
                    'tournament': match.tournament.name if match.tournament else '',
                    'stage': match.stage.name if match.stage else '',
                    'players': [p.user.preferred_name for p in match.players],
                    'approved': bool(entry.approved),
                    'acknowledged': entry.acknowledged_at is not None,
                    # The state of the *match*, so a commitment to something
                    # already played reads as history rather than as a duty.
                    'started': match.started_at is not None,
                    'finished': match.finished_at is not None,
                })
        # A match with no time sorts last: it is real, but it is not "next".
        rows.sort(key=lambda r: (r['scheduled_at'] is None, r['scheduled_at']))
        return rows

    #: How long a match occupies its crew when the tournament says nothing.
    #: Same default ``MatchSuggestionService`` uses for player availability.
    DEFAULT_MATCH_MINUTES = 90

    async def find_scheduling_conflicts(
        self, user: User, match, role: str = 'commentator',
    ) -> list[str]:
        """What else ``user`` is committed to while ``match`` is running.

        No conflict check existed anywhere in the crew path — the only
        overlap-aware code in the codebase was player availability — so a
        coordinator staffing a stream day approved N people from a dialog that
        told them nothing they needed in order to decide. Being a *player* in
        the overlapping match counts: someone racing at 19:00 cannot commentate
        at 19:00 either.

        Returns one human sentence per clash, ready to print; an empty list is
        the common answer and costs one query.
        """
        if match is None or not getattr(match, 'scheduled_at', None):
            return []
        start = to_utc_aware(match.scheduled_at)
        await match.fetch_related('tournament')
        minutes = (
            getattr(match.tournament, 'average_match_duration', None)
            or self.DEFAULT_MATCH_MINUTES
        )
        window = timedelta(minutes=minutes)
        # Widened both ways so a match starting shortly *before* this one, and
        # still running, is caught — a window anchored only forward would miss
        # the overlap that matters most.
        others = await self.match_repository.commitments_for_user(
            user.id, start - window, start + window, exclude_match_id=match.id,
        )

        clashes = []
        for other in others:
            other_start = to_utc_aware(other.scheduled_at)
            other_minutes = (
                getattr(other.tournament, 'average_match_duration', None)
                or self.DEFAULT_MATCH_MINUTES
            )
            if other_start >= start + window or other_start + timedelta(
                    minutes=other_minutes) <= start:
                continue
            names = players_label(
                [p.user.preferred_name for p in other.players],  # type: ignore[attr-defined]
            )
            when = format_local_display(other_start)
            clashes.append(f'{names or "a match"} at {when}' if names else f'a match at {when}')
        return clashes

    async def update_crew_approval(
        self,
        crew_member: Union[Commentator, Tracker],
        crew_type: str,
        approved: bool,
        actor: Optional[User] = None,
    ) -> Union[Commentator, Tracker]:
        if crew_type not in ('commentator', 'tracker'):
            raise ValueError(f"Invalid crew_type: {crew_type}. Must be 'commentator' or 'tracker'")

        await crew_member.fetch_related('match', 'user', 'match__stage')
        await AuthService.ensure(
            await AuthService.can_approve_crew(actor, crew_member.match),
            f"User cannot approve {crew_type} signups for match {crew_member.match.id}",
        )

        # Refresh from DB to narrow the race window where two admins approve concurrently.
        await crew_member.refresh_from_db()
        was_approved = bool(crew_member.approved)
        was_acknowledged = crew_member.acknowledged_at is not None

        # No-op if state already matches — don't audit or DM for a click that didn't change anything.
        if was_approved == approved:
            return crew_member

        crew_member.approved = approved
        crew_member.approved_by = actor if approved else None
        if not approved:
            crew_member.acknowledged_at = None

        async with in_transaction():
            await save_fields(crew_member, 'approved', 'approved_by', 'acknowledged_at')
            details = {
                'crew_type': crew_type,
                'crew_id': crew_member.id,
                'match_id': crew_member.match.id,
                'approved': approved,
            }
            if not approved and was_acknowledged:
                details['previously_acknowledged'] = True
            await self.audit_service.write_log(
                actor,
                AuditActions.CREW_APPROVAL_CHANGED,
                details,
            )

        now = datetime.now(timezone.utc)
        if approved:
            await self._request_crew_acknowledgment(crew_member, crew_type)
            await mark_signup_dms(
                crew_member.match, crew_member, crew_type, COLOR_CONFIRMED,
                lambda you: handled_copy.crew_signup_approved(
                    you, actor.preferred_name if actor else 'staff', crew_member.user.preferred_name,
                    crew_type, now),
                actor_id=actor.id if actor else None,
            )
        else:
            await self._notify_crew_approval_withdrawn(crew_member, crew_type)
            await mark_signup_dms(
                crew_member.match, crew_member, crew_type, COLOR_CREW,
                lambda _you: handled_copy.crew_signup_pending(crew_member.user.preferred_name, crew_type),
                link_label='Review the signup',
            )

        match_live.publish(crew_member.match.id)
        event_bus.publish(Event.create(EventType.CREW_APPROVAL_CHANGED, {
            'match_id': crew_member.match.id, 'crew_type': crew_type,
            'user_id': crew_member.user_id, 'approved': approved,
        }, actor))

        return crew_member

    async def announce_admin_assignment(
        self,
        crew_member: Union[Commentator, Tracker],
        crew_type: str,
        actor: User,
    ) -> None:
        """Treat a crew row an admin created already-approved as an approval.

        Assigning crew from the match dialog writes the row with
        ``approved=True`` and never passes through :meth:`update_crew_approval`,
        so the ``crew.approval_changed`` event and the acknowledgment DM that
        approving a signup sends were both skipped. The assignee was left with
        no way to learn they were on the crew short of reading the schedule.
        """
        await crew_member.fetch_related('match', 'user', 'match__stage')
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.CREW_APPROVAL_CHANGED,
            {
                'crew_type': crew_type,
                'crew_id': crew_member.id,
                'match_id': crew_member.match.id,
                'approved': True,
                'assigned': True,
            },
            EventType.CREW_APPROVAL_CHANGED,
            event_details={
                'match_id': crew_member.match.id, 'crew_type': crew_type,
                'user_id': crew_member.user.id, 'approved': True,
            },
        )
        await self._request_crew_acknowledgment(crew_member, crew_type)

    async def announce_admin_removal(
        self,
        crew_member: Union[Commentator, Tracker],
        crew_type: str,
        actor: User,
    ) -> None:
        """The removal counterpart of :meth:`announce_admin_assignment`.

        ``crew_member`` is already deleted; its loaded fields are all that's
        left. An approved crew member dropped from the match dialog hears the
        same "you're off the crew" DM as one whose approval was withdrawn.
        """
        await crew_member.fetch_related('match', 'user', 'match__stage')
        was_approved = bool(crew_member.approved)
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.CREW_SIGNUP_REMOVED,
            {
                'match_id': crew_member.match.id,
                'role': crew_type,
                'was_approved': was_approved,
                'removed_by_admin': True,
            },
            EventType.CREW_SIGNUP_REMOVED,
            event_extra={'user_id': crew_member.user.id},
        )
        if was_approved:
            await self._notify_crew_approval_withdrawn(crew_member, crew_type)
        else:
            now = datetime.now(timezone.utc)
            await mark_signup_dms(
                crew_member.match, crew_member, crew_type, COLOR_CANCELLED,
                lambda you: handled_copy.crew_signup_removed(
                    you, actor.preferred_name, crew_member.user.preferred_name, crew_type, now),
                actor_id=actor.id,
            )

    async def approve_crew_member(
        self,
        crew_member: Union[Commentator, Tracker],
        crew_type: str,
        actor: Optional[User] = None,
    ) -> Union[Commentator, Tracker]:
        return await self.update_crew_approval(crew_member, crew_type, approved=True, actor=actor)

    async def acknowledge_crew_assignment(
        self,
        crew_id: int,
        crew_type: str,
        user: User,
    ) -> Union[Commentator, Tracker]:
        """Mark a crew assignment as acknowledged by the crew member themselves.

        Only the assigned crew member may acknowledge their own assignment, and
        only after an admin has approved it. Already-acknowledged assignments
        are treated as a no-op so double-clicks don't surface confusing errors.
        """
        crew_member = require_found(
            await self.get_crew_member_by_id(crew_id, crew_type),
            f"{crew_type.capitalize()} assignment",
        )

        await crew_member.fetch_related('user', 'match')

        if crew_member.user_id != user.id:
            raise ValueError("You can only acknowledge your own crew assignments.")

        if not crew_member.approved:
            raise ValueError(
                f"This {crew_type} assignment has not been approved yet."
            )

        if crew_member.acknowledged_at is not None:
            return crew_member

        async with in_transaction():
            if crew_type == 'commentator':
                crew_member = await self.commentator_repository.acknowledge(crew_member)
            else:
                crew_member = await self.tracker_repository.acknowledge(crew_member)

            await self.audit_service.write_log(
                user,
                AuditActions.CREW_ACKNOWLEDGED,
                {
                    'crew_type': crew_type,
                    'crew_id': crew_member.id,
                    'match_id': crew_member.match_id,
                },
            )

        match_live.publish(crew_member.match_id)
        event_bus.publish(Event.create(EventType.CREW_ACKNOWLEDGED, {
            'match_id': crew_member.match_id, 'crew_type': crew_type,
            'user_id': user.id,
        }, user))

        return crew_member

    async def _crew_dm_parts(self, match):
        return await crew_dm_parts(match)

    async def _request_crew_acknowledgment(
        self,
        crew_member: Union[Commentator, Tracker],
        crew_type: str,
    ) -> None:
        """Send a Discord DM asking the crew member to confirm their assignment.

        Best-effort: failures (DMs disabled, user not on Discord, bot offline,
        Discord library errors) are logged but never raise. The web
        acknowledgment button remains available regardless of DM delivery.
        """
        discord_id = getattr(crew_member.user, 'discord_id', None)
        if not discord_id or crew_member.match.stage_id is None:
            return

        message_kwargs, fields = await self._crew_dm_parts(crew_member.match)
        message = crew_assignment_dm(crew_type=crew_type, **message_kwargs)
        embed = notification_embed(
            title=f'🎙️ {crew_type.capitalize()} assignment',
            color=COLOR_CREW,
            community_name=await TenantService.current_community_name(),
            description='Tap **Acknowledge** below to confirm you can cover this.',
            fields=fields,
        )

        discord_queue.enqueue(
            self.discord_service.send_dm_with_crew_acknowledgment_button(
                int(discord_id), message, crew_type, crew_member.id, embed=embed,
                # Acknowledging is one button; the other opens the crew member's
                # own card for this match, where Confirm and Withdraw both live.
                # The whole community board left them hunting for their row.
                link=await notification_links.crew_match(crew_member.match.id), notice=CREW_ASSIGNED.to(CREW),
            )
        )

    async def _notify_crew_approval_withdrawn(
        self,
        crew_member: Union[Commentator, Tracker],
        crew_type: str,
    ) -> None:
        """Tell a crew member their approval was withdrawn.

        The counterpart to :meth:`_request_crew_acknowledgment`: withdrawing also
        clears any acknowledgment, so without this the person who confirmed they
        would cover a match is silently dropped from it. Best-effort on the same
        terms — a failed DM never blocks the withdrawal.
        """
        discord_id = getattr(crew_member.user, 'discord_id', None)
        if not discord_id or crew_member.match.stage_id is None:
            return

        message_kwargs, fields = await self._crew_dm_parts(crew_member.match)
        message = crew_approval_withdrawn_dm(crew_type=crew_type, **message_kwargs)
        embed = notification_embed(
            title=f'🎙️ {crew_type.capitalize()} assignment withdrawn',
            color=COLOR_CANCELLED,
            community_name=await TenantService.current_community_name(),
            description="You're no longer on the crew for this match.",
            fields=fields,
        )

        discord_queue.enqueue(
            self.discord_service.send_dm(int(discord_id), message, embed=embed, notice=CREW_UNASSIGNED.to(CREW))
        )

    async def _notify_crew_owners_of_signup(
        self, match, volunteer: User, crew_type: str, crew_id: int,
    ) -> None:
        """Tell whoever approves this match's crew that someone is waiting on them.

        Approval is the one wait in the volunteer's journey that nothing used
        to announce: a signup sat unseen until somebody happened to open the
        board, while the volunteer's own reply said an admin would confirm it
        shortly. The button opens the board filtered to this match, where the
        approval toggle is.

        Same recipients as :meth:`_notify_crew_withdrawal` (the tournament's
        admins and crew coordinators), minus the volunteer. Best-effort: a
        failed DM never blocks the signup.
        """
        from application.repositories import TournamentRepository
        from application.services import staff_request_dms

        tournament_id = getattr(match, 'tournament_id', None)
        if not tournament_id:
            return
        key = (match.id, volunteer.id, crew_type)
        if not _signup_notice_due(*key):
            previous = _signup_notice_rows.get(key)
            if previous is not None and previous != crew_id:
                _signup_notice_rows[key] = crew_id
                await restore_signup_dms(match, volunteer, crew_type, previous, crew_id)
            return
        _signup_notice_rows[key] = crew_id
        try:
            owners = [
                owner for owner in await TournamentRepository.get_crew_owners(tournament_id)
                if owner.id != volunteer.id
                and getattr(owner, 'discord_id', None)
            ]
            if not owners:
                return

            message_kwargs, fields = await self._crew_dm_parts(match)
            message = crew_signup_pending_dm(
                crew_type=crew_type,
                volunteer_name=volunteer.preferred_name,
                **message_kwargs,
            )
            title, description = handled_copy.crew_signup_pending(volunteer.preferred_name, crew_type)
            embed = notification_embed(
                title=title, color=COLOR_CREW,
                community_name=await TenantService.current_community_name(),
                description=description, fields=fields,
            )
            link = await notification_links.admin_match(match.id, label='Review the signup')
            await staff_request_dms.send(
                SIGNUP_KIND[crew_type], crew_id, [(owner, ADMINS) for owner in owners],
                message, embed=embed, link=link,
            )
        except Exception:
            logger.exception('Crew signup notice failed for match %s', match.id)

    async def _notify_crew_withdrawal(
        self,
        match,
        volunteer: User,
        crew_type: str,
        approved_by_id: Optional[int],
    ) -> None:
        """Tell whoever owns this match's crew that an approved member dropped it.

        The mirror of :meth:`_notify_crew_approval_withdrawn`, and the shape
        ``VolunteerScheduleService._notify_coordinators_of_release`` established:
        a schedule still showing someone who has already gone is worse than one
        showing an open slot, so the people who can fill it are told.

        Only for an *approved* signup. An unapproved one was never announced to
        anybody, so its removal has nothing to correct — the same reason a
        cleared volunteer draft stays silent.

        Best-effort: a failed DM never blocks the withdrawal.
        """
        from application.repositories import TournamentRepository

        recipients: dict[int, User] = {}
        if match.tournament_id:
            for owner in await TournamentRepository.get_crew_owners(match.tournament_id):
                recipients[owner.id] = owner
        if approved_by_id and approved_by_id not in recipients:
            # The approver is the one person who acted on this signup, and may be
            # community STAFF rather than one of the tournament's own people.
            approver = await User.get_or_none(id=approved_by_id)
            if approver:
                recipients[approver.id] = approver
        recipients.pop(volunteer.id, None)
        if not recipients:
            return

        message_kwargs, fields = await self._crew_dm_parts(match)
        hours_notice = None
        if match.scheduled_at:
            hours_notice = round(
                (to_utc_aware(match.scheduled_at) - datetime.now(timezone.utc)).total_seconds()
                / 3600.0,
                1,
            )
        message = crew_withdrawn_dm(
            crew_type=crew_type,
            volunteer_name=volunteer.preferred_name,
            hours_notice=hours_notice,
            **message_kwargs,
        )
        embed = notification_embed(
            title=f'🎙️ {crew_type.capitalize()} withdrew',
            color=COLOR_CANCELLED,
            community_name=await TenantService.current_community_name(),
            description=f'**{volunteer.preferred_name}** is no longer covering this '
                        f'as {crew_type}. The slot is open again.',
            fields=fields,
        )
        # The ask is "find someone else", so the button opens the board already
        # filtered to this match rather than the whole schedule.
        link = await notification_links.admin_match(
            match.id, label='Fill the slot',
        )

        for recipient in recipients.values():
            discord_id = getattr(recipient, 'discord_id', None)
            if not discord_id:
                continue
            discord_queue.enqueue(
                self.discord_service.send_dm(
                    int(discord_id), message, embed=embed, link=link, notice=_WITHDREW_NOTICE[crew_type].to(ADMINS),
                )
            )
