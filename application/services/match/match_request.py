"""Player-initiated match creation, split out of ``match_service``.

Pure code motion to keep ``match_service`` under the file-length guideline —
``MatchRequestMixin`` is composed into :class:`MatchService` and reaches its
repositories, ``participants``, and ``_seed_acknowledgments`` through that
composed class, the same way :class:`CancellationMixin` does.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional, cast

from application.errors import require_found
from application.events import EventType, match_live
from application.repositories import (
    MatchRepository,
    TournamentRepository,
)
from application.services._tournament_signup import record_enrolment_change
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.match.match_request_guard import assert_player_requests_allowed
from application.services.tenant_membership_service import TenantMembershipService
from application.utils.timezone import parse_local_datetime
from models import Match, User


class MatchRequestMixin:
    """The player-initiated match request, mixed into :class:`MatchService`.

    The collaborators below are set up by the host's ``__init__``; they are
    annotated here so this module type-checks on its own rather than deferring
    to whoever mixes it in. ``participants`` is deliberately absent: the host
    exposes it as a read-only property, which a writable annotation here would
    clash with.
    """

    audit_service: AuditService
    repository: MatchRepository
    tournament_repository: TournamentRepository
    match_schedule_service: Any

    async def book_from_bracket(
        self,
        tournament_id: int,
        scheduled_date: str,
        scheduled_time: str,
        player_ids: List[int],
        actor: User,
        comment: Optional[str] = None,
    ) -> Match:
        """Book a linked bracket site's matchup, whoever is booking it.

        Staff and the tournament's admins book on the players' behalf through
        :meth:`create_match`, as they do for a native bracket; anyone else goes
        through :meth:`submit_match_request`, which only lets a player of the
        matchup in. Both skip the player-request toggle and the roster
        membership check, because the bracket supplied the players.
        """
        if await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id):
            # create_match lives on the host MatchService.
            return await cast(Any, self).create_match(
                tournament_id=tournament_id, scheduled_date=scheduled_date,
                scheduled_time=scheduled_time, player_ids=player_ids,
                comment=comment, actor=actor, from_bracket=True,
            )
        return await self.submit_match_request(
            tournament_id=tournament_id, scheduled_date=scheduled_date,
            scheduled_time=scheduled_time, player_ids=player_ids,
            actor=actor, comment=comment, from_bracket=True,
        )

    async def submit_match_request(
        self,
        tournament_id: int,
        scheduled_date: str,
        scheduled_time: str,
        player_ids: List[int],
        actor: User,
        comment: Optional[str] = None,
        *,
        title: Optional[str] = None,
        from_bracket: bool = False,
    ) -> Match:
        """Player-initiated match creation.

        Allowed when the actor is a player in the new match (typically self-vs-opponent
        in a tournament they're enrolled in). Bypasses the TA/Staff CRUD gate but
        does not grant Tournament Admin powers.

        ``from_bracket`` is set **only** by ``BracketService.schedule_bracket_match``
        and ``ChallongeService.schedule_challonge_match``, which have already
        authorized the actor as one of the matchup's entrants. It skips the
        tournament's ``allow_player_match_requests`` toggle because scheduling
        through the bracket *is* the sanctioned path that toggle exists to force.
        Never accept it from a request body.

        ``title`` is likewise caller-supplied rather than user-supplied — see
        ``create_match`` for what it replaces.
        """
        if actor is None:
            raise PermissionError("Login required to submit a match request")
        if actor.id not in player_ids:
            raise PermissionError("You may only submit match requests where you are a player")

        if not player_ids:
            raise ValueError("Match must have at least one player")

        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id),
            f"Tournament {tournament_id}",
        )
        # Archived is closed to everyone, the bracket path included: a matchup
        # left open in a finished season is not a reason to book a new game.
        if not tournament.is_active:
            raise ValueError(f"{tournament.name} has finished — it isn't taking new matches.")
        if not from_bracket:
            assert_player_requests_allowed(tournament)
            # The bracket path names its players from the matchup's entrants,
            # which staff linked; only a caller-supplied roster needs checking.
            await TenantMembershipService.require_community_users(player_ids, actor)
            # Booking a game names opponents; it isn't a way to sign other people
            # up. Enrolling someone else is Staff's (``enroll_player``), and the
            # signup window and a Challonge-owned roster would both be skipped.
            enrolled = await self.tournament_repository.get_enrolled_user_ids(tournament_id)
            if any(pid != actor.id and pid not in enrolled for pid in player_ids):
                raise ValueError(
                    f"Everyone you're playing has to be signed up for {tournament.name} already."
                )
            if tournament.players_per_match and len(set(player_ids)) > tournament.players_per_match:
                raise ValueError(
                    f"{tournament.name} matches have {tournament.players_per_match} players."
                )

        try:
            scheduled_at = parse_local_datetime(scheduled_date, scheduled_time)
        except ValueError as e:
            raise ValueError(f"Invalid date/time format: {e}") from e

        # A player books forward. Staff go through create_match, which stays
        # free to record a match that already happened.
        if scheduled_at <= datetime.now(timezone.utc):
            raise ValueError("That time has already passed. Pick a time later than now.")

        await self.assert_within_tournament_hours(scheduled_at, tournament_id)

        # Resolve every player before touching the match row so a missing user
        # doesn't leave an orphan Match behind.
        players = await self.participants.resolve_users(player_ids)

        match = await self.repository.create(
            tournament_id=tournament_id,
            scheduled_at=scheduled_at,
            comment=comment,
            title=title,
        )
        for enrolled_user in await self.participants.ensure_enrolled(tournament_id, players):
            await record_enrolment_change(
                self.audit_service, actor, enrolled_user, tournament_id, added=True,
            )
        for user in players:
            await self.repository.add_player(match, user)

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_REQUESTED,
            {
                'match_id': match.id,
                'tournament_id': tournament_id,
                'player_ids': player_ids,
            },
            EventType.MATCH_CREATED,
        )

        await self._seed_acknowledgments(match, player_ids, actor)

        await self.match_schedule_service.notify_match_scheduled(match, rescheduled=False)

        match_live.publish(match.id, match_live.CREATED)

        return match
