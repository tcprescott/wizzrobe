"""Linking a match made outside the bracket to its Challonge matchup, mixed into ``ChallongeService``.

A match staff scheduled by hand (New match, or before the tournament was
linked) leaves its Challonge matchup on Not booked yet, and its result never
reaches Challonge. Linking records which match backs the matchup, as booking
it would have. A sync does the same on its own when the pairing leaves only
one match it could be, which is how bookings survive a bracket reset.
"""

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository
from application.services._challonge_notifications import is_bookable
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import ChallongeMatchState, FeatureFlag, Match, Tournament, User


class ChallongeLinkMixin:
    repository: ChallongeRepository
    audit_service: AuditService

    @requires_feature(FeatureFlag.CHALLONGE)
    async def link_existing_match(self, challonge_match_pk: int, match_id: int, actor: User) -> Match:
        cmatch = require_found(await self.repository.get_match(challonge_match_pk), 'Challonge match')
        tournament_id: int = cmatch.tournament_id  # type: ignore[attr-defined]
        if not (await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id)):
            raise PermissionError('Only staff or a tournament admin can link matches to a bracket.')
        if cmatch.match_id is not None:  # type: ignore[attr-defined]
            raise ValueError('This matchup is already booked.')
        if cmatch.state != ChallongeMatchState.OPEN:
            raise ValueError("This matchup isn't ready yet.")
        from application.services.match.match_service import MatchService

        players = [p.user_id if p else None for p in (cmatch.participant1, cmatch.participant2)]
        match = await MatchService().require_bracket_linkable(match_id, tournament_id, players)
        await self.repository.link_match(cmatch, match)
        await self.audit_service.write_log(actor, AuditActions.CHALLONGE_MATCH_LINKED, {
            'tournament_id': tournament_id, 'challonge_match_id': cmatch.id, 'match_id': match.id,
        })
        return match

    async def _relink_bookings(self, tournament: Tournament, actor: User) -> int:
        """Re-attach each open, unbooked matchup to its players' one unlinked match.

        A reset on Challonge renumbers every match, so the sync that follows
        drops the mirrored rows a booking hung off and the restarted bracket
        offers the same pairing as new. Linking here keeps the booking's result
        flowing back and skips a second "ready to book" DM.
        """
        from application.services.match.match_service import MatchService

        orphans = await MatchService().unambiguous_link_candidates(tournament.id)
        if not orphans:
            return 0
        linked = 0
        for cmatch in await self.repository.list_matches(tournament):
            p1, p2 = cmatch.participant1, cmatch.participant2
            if not is_bookable(cmatch) or p1 is None or p2 is None:
                continue
            match = orphans.pop(frozenset((p1.user_id, p2.user_id)), None)
            if match is None:
                continue
            await self.repository.link_match(cmatch, match)
            await self.audit_service.write_log(actor, AuditActions.CHALLONGE_MATCH_LINKED, {
                'tournament_id': tournament.id, 'challonge_match_id': cmatch.id, 'match_id': match.id,
                'automatic': True,
            })
            linked += 1
        return linked
