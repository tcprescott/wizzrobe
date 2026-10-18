"""Hand-mapping Challonge bracket entrants to Wizzrobe users, mixed into ``ChallongeService``.

An entrant added to a bracket by name has no Challonge account, so sync can
never resolve them to anyone. A tournament editor assigns them by hand here, and
``ChallongeService._mirror_bracket`` keeps that assignment on every re-sync.
"""

from typing import Any, Dict, List

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import (
    ChallongeRepository,
    TenantMembershipRepository,
    TournamentRepository,
    UserRepository,
)
from application.services._challonge_notifications import ChallongeNotificationMixin
from application.services._tournament_signup import record_enrolment_change
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id
from models import ChallongeParticipant, FeatureFlag, Tournament, User


class ChallongeParticipantAssignmentMixin(ChallongeNotificationMixin):
    """The three collaborators are set up by ``ChallongeService.__init__``.

    Resolving an entrant can make their matchup
    bookable, which is the moment both players get the ready-to-schedule DM.
    """

    repository: ChallongeRepository
    tournament_repository: TournamentRepository
    audit_service: AuditService

    @requires_feature(FeatureFlag.CHALLONGE)
    async def list_bracket_participants(
        self, tournament_id: int, actor: User,
    ) -> List[Dict[str, Any]]:
        """A linked bracket's entrants with how each maps to a Wizzrobe user.

        ``status`` is ``manual`` (a tournament editor assigned it), ``verified``
        (resolved from the entrant's linked Challonge account) or ``unlinked``.
        ``verified_user`` is set on a manual row whose Challonge account belongs
        to someone else, so the disagreement is visible.
        """
        tournament = await self._editable_tournament(tournament_id, actor)
        participants = await self.repository.list_participants(tournament)
        verified = await self.repository.resolve_users_by_challonge_ids(
            [p.challonge_user_id for p in participants]
        )
        rows = []
        for p in sorted(participants, key=lambda p: (p.name or '').lower()):
            account_user = verified.get(p.challonge_user_id) if p.challonge_user_id else None
            if p.user_assigned_manually:
                status = 'manual'
            elif p.user is not None:
                status = 'verified'
            else:
                status = 'unlinked'
            conflict = (
                status == 'manual' and account_user is not None
                and (p.user is None or account_user.id != p.user.id)
            )
            rows.append({
                'id': p.id,
                'name': p.name,
                'challonge_user_id': p.challonge_user_id,
                'user': p.user,
                'status': status,
                'verified_user': account_user if conflict else None,
            })
        return rows

    @requires_feature(FeatureFlag.CHALLONGE)
    async def count_unmatched_participants(self, tournament_ids: List[int]) -> Dict[int, int]:
        """How many entrants on each linked bracket still need a Wizzrobe user."""
        return await self.repository.count_unmatched_participants(tournament_ids)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def assign_participant_user(
        self, participant_id: int, user_id: int, actor: User,
    ) -> ChallongeParticipant:
        """Map a bracket entrant to a Wizzrobe user by hand; sync keeps it."""
        participant = await self._editable_participant(participant_id, actor)
        user = require_found(await UserRepository.get_by_id(user_id), "User")
        if not await TenantMembershipRepository.is_member(user.id, require_tenant_id()):
            raise ValueError(f"{user.display_name or user.username} isn't a member of this community.")
        taken = next(
            (p for p in await self.repository.list_participants(participant.tournament)
             if p.user is not None and p.user.id == user.id and p.id != participant.id),
            None,
        )
        if taken is not None:
            raise ValueError(
                f"{user.display_name or user.username} is already mapped to "
                f"{taken.name or 'another entrant'} in this bracket."
            )
        bookable_before = await self._bookable_match_ids(participant.tournament)
        await self.repository.set_participant_user(participant, user, manual=True)
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_PARTICIPANT_ASSIGNED,
            {'tournament_id': participant.tournament.id, 'participant_id': participant.id,
             'challonge_participant_id': participant.challonge_participant_id,
             'user_id': user.id},
        )
        await self._mirror_players(participant.tournament, actor)
        await self._announce_newly_bookable(participant.tournament, bookable_before)
        return participant

    @requires_feature(FeatureFlag.CHALLONGE)
    async def clear_participant_user(
        self, participant_id: int, actor: User,
    ) -> ChallongeParticipant:
        """Drop a hand assignment; the entrant falls back to its Challonge account."""
        participant = await self._editable_participant(participant_id, actor)
        if not participant.user_assigned_manually:
            raise ValueError("That entrant isn't assigned by hand.")
        verified = await self.repository.resolve_users_by_challonge_ids(
            [participant.challonge_user_id] if participant.challonge_user_id else []
        )
        await self.repository.set_participant_user(
            participant, verified.get(participant.challonge_user_id), manual=False,
        )
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_PARTICIPANT_UNASSIGNED,
            {'tournament_id': participant.tournament.id, 'participant_id': participant.id,
             'challonge_participant_id': participant.challonge_participant_id},
        )
        await self._mirror_players(participant.tournament, actor)
        return participant

    async def _mirror_players(self, tournament: Tournament, actor: User) -> None:
        """Make the tournament's Players list the bracket's mapped entrants.

        Challonge owns a linked tournament's roster, so a player dropped there
        (or a whole reset) leaves the list too. Their scheduled matches stay.
        """
        wanted = {
            p.user.id: p.user for p in await self.repository.list_participants(tournament)
            if p.user is not None
        }
        enrolled = {tp.user.id: tp.user for tp in await self.tournament_repository.get_enrolled_players(tournament)}
        for user_id in wanted.keys() - enrolled.keys():
            await self.tournament_repository.enroll_player(tournament, wanted[user_id])
            await record_enrolment_change(
                self.audit_service, actor, wanted[user_id], tournament.id, added=True,
            )
        for user_id in enrolled.keys() - wanted.keys():
            await self.tournament_repository.unenroll_player(tournament, enrolled[user_id])
            await record_enrolment_change(
                self.audit_service, actor, enrolled[user_id], tournament.id, added=False,
            )

    async def _editable_tournament(self, tournament_id: int, actor: User) -> Tournament:
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), f"Tournament {tournament_id}"
        )
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            "You do not have permission to manage this tournament's bracket",
        )
        return tournament

    async def _editable_participant(self, participant_id: int, actor: User) -> ChallongeParticipant:
        participant = require_found(
            await self.repository.get_participant_by_id(participant_id), "Bracket entrant"
        )
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, participant.tournament),
            "You do not have permission to manage this tournament's bracket",
        )
        return participant
