"""
Match Service - Business Logic Layer

Coordinates match-related operations, enforces business rules,
and orchestrates between repositories.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.events import EventType, match_live
from application.repositories import (
    CommentatorRepository,
    MatchAcknowledgmentRepository,
    MatchRepository,
    StageRepository,
    StationRepository,
    TournamentRepository,
    TrackerRepository,
    UserRepository,
)
from application.services._tournament_signup import record_enrolment_change
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.match.match_acknowledgment import MatchAcknowledgmentMixin
from application.services.match.match_cancellation import CancellationMixin
from application.services.match.match_forfeit import ForfeitMixin
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.match.match_participants import MatchParticipants
from application.services.match.match_placeholders import PlaceholderBookingMixin
from application.services.match.match_reads import MatchReadsMixin
from application.services.match.match_request import MatchRequestMixin
from application.services.match.match_result import RecordResultMixin
from application.services.match.match_review import MatchReviewMixin
from application.services.match.match_schedule_service import MatchScheduleService
from application.services.match.match_source_guard import assert_sg_fields_unchanged
from application.services.match.match_station_draw import StationDrawMixin
from application.services.match.match_stations import StationAssignmentMixin
from application.services.match.match_text import new_match_text, text_updates
from application.services.stage_service import StageService
from application.services.system_config_service import SystemConfigService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.timezone_service import TimezoneService
from application.tenant_context import require_tenant_id
from application.utils.timezone import (
    parse_local_datetime,
    timezone_label,
    to_local,
)
from models import (
    Match,
    Tournament,
    User,
)


class MatchService(
    CancellationMixin, ForfeitMixin, MatchAcknowledgmentMixin, MatchReadsMixin, MatchRequestMixin,
    MatchReviewMixin, PlaceholderBookingMixin, RecordResultMixin, StationAssignmentMixin, StationDrawMixin,
):
    """Service for match-related business operations."""

    def __init__(self) -> None:
        self.repository = MatchRepository()
        self.station_repository = StationRepository()
        self.stage_repository = StageRepository()
        self.tournament_repository = TournamentRepository()
        self.user_repository = UserRepository()
        self.commentator_repository = CommentatorRepository()
        self.tracker_repository = TrackerRepository()
        self.ack_repository = MatchAcknowledgmentRepository()
        self.audit_service = AuditService()
        self.match_schedule_service = MatchScheduleService()
        self.hard_preset_service = MatchHardPresetService()

    @property
    def participants(self) -> MatchParticipants:
        """Roster/acknowledgment row orchestration bound to this service's repos.

        A property (rebuilt per access) rather than an ``__init__`` attribute so it
        reads the service's current repositories — this keeps it working when a
        caller assembles the service via ``object.__new__`` with hand-set repos.
        """
        return MatchParticipants(
            match_repository=self.repository,
            user_repository=self.user_repository,
            tournament_repository=self.tournament_repository,
            ack_repository=self.ack_repository,
        )

    async def _require_match(
        self, match_id: int, *, prefetch_relations: bool = True
    ) -> Match:
        """Load a match or raise :class:`NotFoundError` (API maps to 404)."""
        match = await self.repository.get_by_id(
            match_id, prefetch_relations=prefetch_relations
        )
        return require_found(match, f"Match {match_id}")

    async def create_match(
        self,
        tournament_id: int,
        scheduled_date: str,
        scheduled_time: str,
        player_ids: List[int],
        comment: Optional[str] = None,
        stage_id: Optional[int] = None,
        commentator_ids: Optional[List[int]] = None,
        tracker_ids: Optional[List[int]] = None,
        is_stream_candidate: bool = False,
        title: Optional[str] = None,
        description: Optional[str] = None,
        actor: Optional[User] = None,
        from_bracket: bool = False,
        placeholders: Optional[List[str]] = None,
    ) -> Match:
        """
        Create a new match with validation and business rules.

        ``from_bracket`` is set only by ``BracketService.schedule_bracket_match``,
        whose players are the matchup's linked entrants rather than ids the
        caller typed. Never accept it from a request body. ``placeholders``
        stand in for a bracket matchup's undecided sides (``PlaceholderBookingMixin``).

        Args:
            tournament_id: Tournament ID
            scheduled_date: Date string (YYYY-MM-DD)
            scheduled_time: Time string (HH:MM)
            player_ids: List of user IDs to add as players
            comment: Optional comment
            stage_id: Optional stage ID
            commentator_ids: Optional list of commentator user IDs
            tracker_ids: Optional list of tracker user IDs
            title: Optional display label. It **replaces** the Discord scheduled
                event's whole title and names the racetime room, so a caller must
                pass something self-describing (see
                ``SchedulingMixin._game_title``); leave it None for the default
                "<tournament>: <players>" rendering.
            admin_user: User creating the match (for audit log)

        Returns:
            Created Match object

        Raises:
            ValueError: If validation fails
        """
        # Scoped read, so a foreign tournament id raises here rather than
        # producing a match that points across the tenant boundary: is_staff
        # short-circuits the check below, and assert_within_tournament_hours
        # falls through for a tournament it cannot see. submit_match_request
        # already does this.
        require_found(
            await self.tournament_repository.get_by_id(tournament_id),
            f"Tournament {tournament_id}",
        )

        if await AuthService.is_staff(actor):
            pass
        else:
            await AuthService.ensure(
                await AuthService.is_tournament_admin(actor, tournament_id),
                f"User cannot create matches in tournament {tournament_id}",
            )

        title, description = new_match_text(
            player_ids, title, description, from_bracket=from_bracket, placeholders=placeholders,
        )
        # A bracket game's players come from the matchup's entrants, which staff
        # linked (and that link is checked); only caller-supplied ids need it.
        await TenantMembershipService.require_community_users(
            [*([] if from_bracket else player_ids), *(commentator_ids or []), *(tracker_ids or [])],
            actor,
        )

        await StageService().require_in_tenant(stage_id)

        # Parse datetime - input is on the caller's display clock, stored UTC
        try:
            scheduled_at = parse_local_datetime(scheduled_date, scheduled_time)
        except ValueError as e:
            raise ValueError("That date or time doesn't look right. Check the format and try again.") from e

        await self.assert_within_tournament_hours(scheduled_at, tournament_id)

        # Resolve every referenced user up-front (one query per role list) so a
        # missing ID doesn't leave an orphan Match row behind.
        players = await self.participants.resolve_users(player_ids)
        commentators = await self.participants.resolve_users(commentator_ids or [])
        trackers = await self.participants.resolve_users(tracker_ids or [])

        player_id_set = {u.id for u in players}
        if player_id_set & {u.id for u in commentators}:
            raise ValueError("A player in this match can't also crew it as a commentator.")
        if player_id_set & {u.id for u in trackers}:
            raise ValueError("A player in this match can't also crew it as a tracker.")

        match = await self.repository.create(
            tournament_id=tournament_id,
            scheduled_at=scheduled_at,
            comment=comment,
            stage_id=stage_id,
            is_stream_candidate=is_stream_candidate,
            title=title,
            description=description,
            bracket_placeholders=list(placeholders) if from_bracket and placeholders else None,
        )

        await self._record_auto_enrolments(
            await self.participants.ensure_enrolled(tournament_id, players),
            tournament_id, actor,
        )
        for user in players:
            await self.repository.add_player(match, user)

        assigned_crew = [
            (await self.commentator_repository.create(
                match=match, user=user, approved=True, approved_by=actor,
            ), 'commentator')
            for user in commentators
        ] + [
            (await self.tracker_repository.create(
                match=match, user=user, approved=True, approved_by=actor,
            ), 'tracker')
            for user in trackers
        ]

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_CREATED,
            {
                'match_id': match.id,
                'tournament_id': tournament_id,
                'player_ids': player_ids,
                'commentator_ids': commentator_ids or [],
                'tracker_ids': tracker_ids or [],
                'is_stream_candidate': is_stream_candidate,
            },
            EventType.MATCH_CREATED,
            event_details={
                'match_id': match.id,
                'tournament_id': tournament_id,
                'player_ids': player_ids,
                'is_stream_candidate': is_stream_candidate,
            },
        )

        # Seed per-player acknowledgments (actor auto-acks themselves)
        await self._seed_acknowledgments(match, player_ids, actor)

        await self.match_schedule_service.notify_match_scheduled(
            match, rescheduled=False, is_stream_candidate=is_stream_candidate,
        )

        await self._announce_crew_changes(assigned_crew, actor)

        match_live.publish(match.id, match_live.CREATED)

        return match

    async def update_match(
        self,
        match_id: int,
        *,
        tournament_id: Optional[int] = None,
        scheduled_date: Optional[str] = None,
        scheduled_time: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        player_ids: Optional[List[int]] = None,
        commentator_ids: Optional[List[int]] = None,
        tracker_ids: Optional[List[int]] = None,
        comment: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        clear_seated: bool = False,
        clear_started: bool = False,
        clear_finished: bool = False,
        clear_confirmed: bool = False,
        clear_seed: bool = False,
        actor: Optional[User] = None,
        allow_outside_round: bool = False,
    ) -> Match:
        """
        Update a match with validation.

        Args:
            match_id: Match to update
            tournament_id: New tournament ID
            scheduled_date: New date
            scheduled_time: New time
            scheduled_at: New time as a resolved UTC instant, for a service-to-
                service caller that already holds one (an approved reschedule
                request). Takes precedence over the date/time strings, which
                exist for form input. Passing an instant avoids a
                UTC → wall-clock → UTC round trip that would resolve an
                ambiguous DST hour on whichever clock the *decider* happens to
                be reading, not the clock the request was made on.
            player_ids: New player list
            commentator_ids: New commentator list
            tracker_ids: New tracker list
            comment: New comment
            title, description: New text; ``''`` clears it, None leaves it alone
            stage_id: New stage ID
            clear_seated: Clear seated_at timestamp
            clear_started: Clear started_at timestamp
            clear_finished: Clear finished_at timestamp
            clear_confirmed: Clear confirmed_at timestamp
            clear_seed: Clear generated seed
            allow_outside_round: Staff confirmed a time outside the bracket
                stage's booking window (``RoundScheduleService``).
            clear_stage: Clear stage assignment
            admin_user: User making the update

        Returns:
            Updated Match object
        """
        match = await self._require_match(match_id)

        await AuthService.ensure(
            await AuthService.can_crud_match(actor, match),
            f"User cannot edit match {match_id}",
        )

        # can_crud_match only authorizes the actor against the match's CURRENT
        # tournament. A reassignment must also be authorized against the TARGET
        # tournament, otherwise a Tournament Admin could move a match into (and
        # enroll its players in) a tournament they do not administer.
        if tournament_id is not None and tournament_id != match.tournament_id:
            await AuthService.ensure(
                await AuthService.is_staff(actor)
                or await AuthService.is_tournament_admin(actor, tournament_id),
                f"User cannot move match into tournament {tournament_id}",
            )

        old_scheduled_at = match.scheduled_at

        # Snapshot the existing player list so we can detect adds/removals.
        old_player_ids = {p.user_id for p in await self.repository.get_players(match)}
        if player_ids is not None:
            new_player_ids = set(player_ids)
        else:
            new_player_ids = old_player_ids
        players_changed = old_player_ids != new_player_ids

        # SpeedGaming read-only contract (PR 7): reject edits to ETL-owned fields
        # on a sourced match (schedule / players / tournament). See helper module.
        assert_sg_fields_unchanged(
            match,
            tournament_id=tournament_id,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            scheduled_at=scheduled_at,
            players_changed=players_changed,
        )

        current_commentator_ids = {c.user_id for c in match.commentators}
        current_tracker_ids = {t.user_id for t in match.trackers}
        effective_commentator_ids = set(commentator_ids) if commentator_ids is not None else current_commentator_ids
        effective_tracker_ids = set(tracker_ids) if tracker_ids is not None else current_tracker_ids
        # Only the people this edit adds: someone already on the match stays
        # editable even if they have since left the community.
        await TenantMembershipService.require_community_users(
            (new_player_ids - old_player_ids)
            | (effective_commentator_ids - current_commentator_ids)
            | (effective_tracker_ids - current_tracker_ids),
            actor,
        )
        if new_player_ids & effective_commentator_ids:
            raise ValueError("A player in this match can't also crew it as a commentator.")
        if new_player_ids & effective_tracker_ids:
            raise ValueError("A player in this match can't also crew it as a tracker.")

        update_fields = {}

        if tournament_id is not None:
            # Same scoped resolve as create_match: reassignment must not be able
            # to move a match into another community's tournament.
            require_found(
                await self.tournament_repository.get_by_id(tournament_id),
                f"Tournament {tournament_id}",
            )
            update_fields['tournament_id'] = tournament_id

        if scheduled_at is None and scheduled_date and scheduled_time:
            # Parse datetime - input is on the caller's display clock, stored UTC
            scheduled_at = parse_local_datetime(scheduled_date, scheduled_time)
        if scheduled_at is not None:
            # Validate against the target tournament (new one on reassignment).
            await self.assert_within_tournament_hours(
                scheduled_at, tournament_id if tournament_id is not None else match.tournament_id,
            )
            if scheduled_at != old_scheduled_at:
                await self._check_round_window(match, scheduled_at, actor, allow_outside_round)
            update_fields['scheduled_at'] = scheduled_at

        if comment is not None:
            update_fields['comment'] = comment
        update_fields.update(text_updates(title, description))

        if clear_seated:
            update_fields['seated_at'] = None

        if clear_started:
            update_fields['started_at'] = None

        if clear_finished:
            update_fields['finished_at'] = None

        if clear_confirmed:
            update_fields['confirmed_at'] = None

        if clear_seed:
            update_fields['generated_seed'] = None

        # Before any write: both a roster rewrite and a reassignment destroy the
        # information the opt-in agreement has to be re-answered from.
        hard_preset_before = await self.hard_preset_service.snapshot(match)

        if update_fields:
            # A moved match re-arms its stage reminder, as a stage change does
            # (``assign_stage``); kept out of ``update_fields`` so audit and event
            # payloads still list only what the caller changed.
            rearm = (
                {'stage_reminder_sent_at': None}
                if 'scheduled_at' in update_fields and update_fields['scheduled_at'] != old_scheduled_at
                else {}
            )
            # Rewinding past the finish undoes a forfeit along with the result it set.
            if clear_finished and match.forfeit_reason is not None:
                rearm = {**rearm, 'forfeit_reason': None, 'forfeit_note': None}
            await self.repository.update(match, **update_fields, **rearm)

        removed_players: List[User] = []
        if player_ids is not None:
            removed_players = await self.participants.sync_players(
                match, player_ids, tournament_id or match.tournament_id,
            )

        # After both: a swap can break an agreement the remaining players were
        # told about, dropping the one holdout can complete one, and moving the
        # match discards every opt-in, since the players agreed to a named
        # preset rather than to whichever one the match lands on next.
        await self.hard_preset_service.reconcile_edit(match, hard_preset_before, actor)

        assigned_crew: list = []
        removed_crew: list = []
        for crew_type, ids, crew_repository in (
            ('commentator', commentator_ids, self.commentator_repository),
            ('tracker', tracker_ids, self.tracker_repository),
        ):
            if ids is None:
                continue
            created, removed = await self.participants.sync_crew(match, ids, crew_repository, actor)
            assigned_crew += [(row, crew_type) for row in created]
            removed_crew += [(row, crew_type) for row in removed]

        audit_details: Dict[str, Any] = {
            'match_id': match.id,
            'changed_fields': list(update_fields.keys()),
        }
        if player_ids is not None:
            audit_details['player_ids'] = player_ids
        if commentator_ids is not None:
            audit_details['commentator_ids'] = commentator_ids
        if tracker_ids is not None:
            audit_details['tracker_ids'] = tracker_ids
        new_scheduled_at = update_fields.get('scheduled_at')
        scheduled_at_changed = bool(
            new_scheduled_at and old_scheduled_at and new_scheduled_at != old_scheduled_at
        )
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_RESCHEDULED if scheduled_at_changed else AuditActions.MATCH_UPDATED,
            audit_details,
            EventType.MATCH_RESCHEDULED if scheduled_at_changed else EventType.MATCH_UPDATED,
            event_details={
                'match_id': match.id, 'tournament_id': match.tournament_id,
                'changed_fields': list(update_fields.keys()),
            },
        )

        if scheduled_at_changed or players_changed:
            await self._seed_acknowledgments(match, list(new_player_ids), actor)
            if scheduled_at_changed:
                await self.match_schedule_service.notify_match_scheduled(match, rescheduled=True)
            else:
                # Resolve the embed-footer community here (request context); the
                # enqueued coroutine runs later in the scope-less queue worker.
                from application.services.tenant_service import TenantService
                community = await TenantService.current_community_name()
                discord_queue.enqueue(self.match_schedule_service.notify_acknowledgment_request(
                    match, rescheduled=False, community=community,
                ))

        await self._announce_crew_changes(assigned_crew, actor, removed_crew)
        await self._announce_removed_players(match, removed_players)

        match_live.publish(match.id)

        return match

    async def rewind_match(self, match_id: int, to_step: str, *, actor: Optional[User]) -> Match:
        """Take a match back to ``to_step``, clearing every later step together."""
        from application.services.match.match_rewind import clear_flags_for, rewind_targets

        match = await self._require_match(match_id)
        if to_step not in {step for step, _ in rewind_targets(match)}:
            raise ValueError("This match hasn't gone past that step, so there's nothing to rewind.")
        return await self.update_match(match_id, actor=actor, **clear_flags_for(to_step))

    async def clear_match_seed(self, match_id: int, *, actor: Optional[User]) -> Match:
        """Drop the match's rolled seed so a new one can be rolled."""
        match = await self._require_match(match_id)
        if match.generated_seed_id is None:
            raise ValueError("This match doesn't have a seed to clear.")
        return await self.update_match(match_id, clear_seed=True, actor=actor)

    async def _announce_removed_players(self, match: Match, removed: List[User]) -> None:
        """Tell players dropped from the line-up, the way removed crew are told."""
        discord_ids = [int(u.discord_id) for u in removed if u.discord_id]
        if not discord_ids:
            return
        from application.services.match.match_schedule_service import (
            _community_name,
            _match_embed_kwargs,
            bracket_dm_context,
        )
        from application.utils.discord_embeds import COLOR_CANCELLED, match_embed, time_field
        from application.utils.discord_messages import removed_from_match_dm

        await match.fetch_related('tournament', 'players__user', 'stage')
        bracket_line, _ = await bracket_dm_context(match.id)
        message = removed_from_match_dm(
            match.tournament.name,
            scheduled_at_display=time_field(match.scheduled_at),
            stage_name=match.stage.name if match.stage else '',
            bracket_line=bracket_line,
        )
        embed = match_embed(
            title='Removed from match',
            color=COLOR_CANCELLED,
            **_match_embed_kwargs(match, await _community_name()),
        )
        discord_queue.enqueue(
            self.match_schedule_service.notify_players_removed(discord_ids, message, embed)
        )

    async def _announce_crew_changes(
        self, assigned_crew: list, actor: Optional[User], removed_crew: Optional[list] = None,
    ) -> None:
        # actor is None only for callers the auth gates above already rejected.
        if not (assigned_crew or removed_crew) or actor is None:
            return
        from application.services.crew_service import CrewService
        crew_service = CrewService()
        for crew_member, crew_type in assigned_crew:
            await crew_service.announce_admin_assignment(crew_member, crew_type, actor)
        for crew_member, crew_type in removed_crew or []:
            await crew_service.announce_admin_removal(crew_member, crew_type, actor)

    async def set_stream_candidate(
        self,
        match_id: int,
        flag: bool,
        actor: Optional[User] = None,
    ) -> Match:
        """Toggle Match.is_stream_candidate. Broadcast Techs globally; TAs within their tournaments."""
        match = await self._require_match(match_id)

        await AuthService.ensure(
            await AuthService.can_assign_match_stream(actor, match),
            "User cannot toggle stream candidate for this match",
        )

        was_candidate = match.is_stream_candidate
        await self.repository.update(match, is_stream_candidate=flag)

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_STREAM_CANDIDATE_SET if flag else AuditActions.MATCH_STREAM_CANDIDATE_CLEARED,
            {'match_id': match.id},
            EventType.MATCH_STREAM_CANDIDATE_SET if flag else EventType.MATCH_STREAM_CANDIDATE_CLEARED,
            event_extra={'tournament_id': match.tournament_id},
        )

        if flag and not was_candidate:
            await self.match_schedule_service.notify_stream_candidate(match)

        match_live.publish(match.id)

        return match

    async def assign_stage(
        self,
        match_id: int,
        stage_id: Optional[int],
        actor: Optional[User] = None,
    ) -> Match:
        """Assign or clear the Stage for a match. Broadcast Techs globally; TAs within their tournaments.

        Both branches DM the players and approved crew. Until this fanned out,
        the only way to learn you were on stage was to be looking at the
        schedule board at the right moment, or for somebody to walk over and
        say so.

        Clearing ``stage_reminder_sent_at`` on every change is what re-arms the
        pre-match reminder: moving a match from one stage to another has to
        produce a second reminder naming the new one, and a stamp that survived
        the change would suppress it.
        """
        match = await self._require_match(match_id)

        await AuthService.ensure(
            await AuthService.can_assign_match_stream(actor, match),
            "User cannot assign stages for this match",
        )
        await StageService().require_in_tenant(stage_id)

        await self.repository.update(match, stage_id=stage_id, stage_reminder_sent_at=None)

        # Enqueued, never awaited: this runs from a table-cell handler on the
        # schedule board, where a slow Discord call blocks the shared loop.
        discord_queue.enqueue(
            self.match_schedule_service.notify_stage_changed(match)
        )

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_STAGE_ASSIGNED if stage_id is not None else AuditActions.MATCH_STAGE_CLEARED,
            {'match_id': match.id, 'stage_id': stage_id},
            EventType.MATCH_STAGE_ASSIGNED if stage_id is not None else EventType.MATCH_STAGE_CLEARED,
            event_extra={'tournament_id': match.tournament_id},
        )

        match_live.publish(match.id)

        return match


    async def ensure_players_enrolled(
        self,
        tournament_id: int,
        player_ids: List[int],
        actor: User,
    ) -> List[User]:
        """
        Ensure all players are enrolled in the tournament.

        Args:
            tournament_id: Tournament ID
            player_ids: List of user IDs to enroll
            actor: Who is causing the enrolment. Required, not optional: each
                row this creates is audited and announced, and an enrolment
                nobody is named for is not answerable.

        Returns:
            The users this call enrolled (empty when all were already entrants),
            so the caller can report the side effect rather than hiding it.

        Raises:
            ValueError: If any user is not found
        """
        await TenantMembershipService.require_community_users(player_ids, actor)
        users = await self.participants.resolve_users(player_ids)
        newly = await self.participants.ensure_enrolled(tournament_id, users)
        await self._record_auto_enrolments(newly, tournament_id, actor)
        return newly

    async def _record_auto_enrolments(
        self, newly: List[User], tournament_id: int, actor: Optional[User],
    ) -> None:
        """Audit and announce the entrants scheduling just added.

        ``MatchParticipants`` deliberately writes no audit and publishes no
        events, so the roster rows it creates as a side effect of scheduling used
        to appear from nowhere: no audit line naming who caused them, and nothing
        on the event bus, so a subscriber mirroring the roster silently drifted.
        The users it enrolled are exactly what it returns.
        """
        for user in newly:
            await record_enrolment_change(
                self.audit_service, actor, user, tournament_id, added=True,
            )

    # Match lifecycle transitions (seat / start / finish / confirm) live solely
    # in MatchScheduleService._transition, which enforces the ordering rules and
    # emits state-change notifications. Earlier permissive seat_players /
    # finish_match duplicates were removed to keep one lifecycle path.

    async def _check_round_window(
        self, match: Match, scheduled_at: datetime, actor: Optional[User], allow_outside_round: bool,
    ) -> None:
        from application.services.round_schedule_service import RoundScheduleService

        tournament = await self.tournament_repository.get_by_id(match.tournament_id)  # type: ignore[attr-defined]
        if tournament is not None:
            await RoundScheduleService().check_match_time(
                match, tournament, scheduled_at, actor, allow_outside_round=allow_outside_round,
            )

    async def assert_within_tournament_hours(
        self, scheduled_at: datetime, tournament_id: Optional[int],
    ) -> None:
        """Reject scheduled_at (UTC) outside the window for its date.

        Resolved for ``tournament_id``: the tournament's own per-day hours win
        when set, otherwise the tenant-wide setting applies.
        """
        tournament = await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id()) if tournament_id is not None else None
        # Operating hours belong to the community, not the reader: "matches run
        # 09:00–22:00" means the community's clock. Resolving the day and the
        # start time in the viewer's zone instead would let two people in
        # different countries get opposite verdicts on the same instant.
        tz = await TimezoneService.tenant_timezone_name()
        local_dt = to_local(scheduled_at, tz)
        d = local_dt.date()
        window = await SystemConfigService.get_tournament_window_for_date(d, tournament=tournament)
        if window is None:
            return
        open_t, close_t = window
        start_time = local_dt.time().replace(second=0, microsecond=0)
        if start_time < open_t or start_time >= close_t:
            raise ValueError(
                f"Matches on {d} can only start between "
                f"{open_t.strftime('%H:%M')} and {close_t.strftime('%H:%M')} "
                f"({timezone_label(tz)})."
            )
