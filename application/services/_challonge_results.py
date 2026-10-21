"""Sending confirmed results to Challonge, mixed into ``ChallongeService``.

A match can be reported on Challonge's own site before Wizzrobe confirms its
copy, so every push reads the match first and leaves a result already there
alone.
"""

import logging
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.feature_flag_service import FeatureFlagService
from application.utils.clients.challonge_client import ChallongeAPIError, ChallongeClient
from models import ChallongeMatch, FeatureFlag, Match, User

logger = logging.getLogger(__name__)


class ChallongeResultsMixin:
    """Collaborators come from ``ChallongeService``; see its ``__init__``."""

    repository: ChallongeRepository
    audit_service: AuditService
    _api_client: Callable[[], ChallongeClient]
    _sync_tournament: Callable[..., Awaitable[Dict[str, int]]]

    @requires_feature(FeatureFlag.CHALLONGE)
    async def results_on_site(self, match_ids: List[int]) -> Dict[int, Tuple[Optional[int], str]]:
        """``match id -> (winner user id, winner name)`` for booked matches Challonge has a result for."""
        out: Dict[int, Tuple[Optional[int], str]] = {}
        for cmatch in await self.repository.completed_for_matches(match_ids):
            winner = cmatch.winner_participant
            out[cmatch.match_id] = (  # type: ignore[attr-defined]
                winner.user_id if winner else None, (winner.name if winner else None) or '',  # type: ignore[attr-defined]
            )
        return out

    # feature-gate: exempt — the soft integration point; see the docstring below.
    async def push_result_if_linked(self, match: Match, actor: Optional[User]) -> bool:
        """Push the result only when the match mirrors a Challonge match.

        Returns True if a result was sent. Used by the confirm flow so
        non-Challonge matches are silently skipped. Requires an actor for the
        audit entry; with none, the push is skipped.

        Soft-gated: the match confirm/finish flow calls this for *every* match, so
        a community without the Challonge feature must simply confirm its matches
        — raising here would break match completion for everyone who does not use
        Challonge. The flag is still enforced (no result leaves the tenant where
        the feature is off) by answering "nothing pushed"; the administrative
        entry points above raise instead.
        """
        if actor is None:
            return False
        if not await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE):
            return False
        cmatch = await self.repository.get_challonge_match_for_match(match)
        if cmatch is None:
            return False
        return await self.push_match_result(match, actor)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def push_match_result(self, match: Match, actor: User) -> bool:
        """Report the confirmed winner to Challonge. Returns False when Challonge already has a result."""
        cmatch = await self.repository.get_challonge_match_for_match(match)
        if cmatch is None:
            raise ValueError("This match is not linked to a Challonge match.")

        await match.fetch_related('players__user', 'tournament')
        ranked = [p for p in match.players if p.finish_rank is not None]
        winner = next((p for p in ranked if p.finish_rank == 1), None)
        if winner is None:
            raise ValueError("No winner has been recorded for this match yet.")
        losers = [p for p in match.players if p.id != winner.id]
        if not losers:
            raise ValueError("Cannot push a result without an opponent.")
        loser = losers[0]

        winner_pid = self._participant_id_for_user(cmatch, winner.user_id)
        loser_pid = self._participant_id_for_user(cmatch, loser.user_id)
        if winner_pid is None or loser_pid is None:
            raise ValueError("Could not map both players to Challonge participants; re-sync the bracket.")

        if not cmatch.tournament.challonge_tournament_id:
            raise ValueError("The tournament is no longer linked to Challonge.")

        client = self._api_client()
        current = await client.get_match(
            cmatch.tournament.challonge_tournament_id, cmatch.challonge_match_id,
            community=cmatch.tournament.challonge_community,
        )
        if current.get('state') == 'complete':
            # An admin reported it on Challonge already: theirs stands. Re-sync
            # so the mirror catches up with the bracket.
            await self.audit_service.write_log(
                actor, AuditActions.CHALLONGE_RESULT_SKIPPED,
                {'match_id': match.id, 'challonge_match_id': cmatch.challonge_match_id,
                 'challonge_winner_participant_id': current.get('winner_participant_id')},
            )
            await self._resync_after_push(cmatch, actor)
            return False

        await client.update_match(
            tournament_id=cmatch.tournament.challonge_tournament_id,
            match_id=cmatch.challonge_match_id,
            winner_participant_id=winner_pid,
            loser_participant_id=loser_pid,
            community=cmatch.tournament.challonge_community,
        )
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_RESULT_PUSHED,
            {'match_id': match.id, 'challonge_match_id': cmatch.challonge_match_id,
             'winner_participant_id': winner_pid},
        )

        await self._resync_after_push(cmatch, actor)
        return True

    async def _resync_after_push(self, cmatch: ChallongeMatch, actor: User) -> None:
        # The push advanced the bracket (next-round matches now open). Re-sync
        # once so those newly-open matches surface locally without a manual
        # Sync. Failures here must not undo the successful push.
        try:
            await self._sync_tournament(cmatch.tournament, actor, force=True)
        except (ValueError, ChallongeAPIError):
            # Logged rather than printed so a silently-stale bracket leaves a
            # diagnostic trail in structured logs and Sentry.
            logger.error(
                "Challonge post-push re-sync failed for tournament %s",
                cmatch.tournament_id, exc_info=True,
            )

    @staticmethod
    def _participant_id_for_user(cmatch: ChallongeMatch, user_id: int) -> Optional[str]:
        for participant in (cmatch.participant1, cmatch.participant2):
            if participant is not None and participant.user_id == user_id:
                return participant.challonge_participant_id
        return None
