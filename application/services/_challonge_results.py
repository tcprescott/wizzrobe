"""Sending confirmed results to Challonge, mixed into ``ChallongeService``.

A match can be reported on Challonge's own site before Wizzrobe confirms its
copy, so every push reads the match first and leaves a result already there
alone.
"""

import asyncio
import logging
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.bracket_set_service import BracketSetService
from application.services.feature_flag_service import FeatureFlagService
from application.utils.clients.challonge_client import ChallongeAPIError, ChallongeClient
from models import ChallongeMatch, FeatureFlag, Match, User

logger = logging.getLogger(__name__)

_RESYNC_RETRY_DELAY = 2.0
_PUSH_RETRY_DELAY = 2.0
# Two attempts plus this wait must fit inside the confirm flow's 90s budget
# (_BRACKET_PUSH_SECONDS), so a longer Retry-After is cut down to it.
_PUSH_RETRY_MAX_DELAY = 10.0
_TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
# A Cloudflare error page is several KB of HTML.
_AUDIT_ERROR_CHARS = 300


def _push_retry_delay(error: ChallongeAPIError) -> float:
    if error.retry_after is None:
        return _PUSH_RETRY_DELAY
    return min(error.retry_after, _PUSH_RETRY_MAX_DELAY)


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
                winner.user_id if winner else None, (winner.name if winner else None) or '',
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
        standing = await BracketSetService().standing(match)
        if standing is not None:
            # A set's game: Challonge hears only the set's result, once it's won.
            if not standing.decided:
                return False
            match = standing.first
        cmatch = await self.repository.get_challonge_match_for_match(match)
        if cmatch is None:
            return False
        for attempt in range(2):
            try:
                return await self.push_match_result(match, actor)
            except ChallongeAPIError as e:
                # Sentry SGLMAN-2M/2N: a timeout, dropped connection (no status),
                # gateway error or rate limit usually clears on the next request.
                # Retrying is safe because the push re-reads the Challonge match
                # and leaves a complete one alone, which covers a PUT that did land.
                transient = e.status is None or e.status in _TRANSIENT_STATUSES
                if attempt == 0 and transient:
                    await asyncio.sleep(_push_retry_delay(e))
                    continue
                error = str(e)[:_AUDIT_ERROR_CHARS]
                await self.audit_service.write_log(
                    actor, AuditActions.CHALLONGE_RESULT_PUSH_FAILED,
                    {'match_id': match.id, 'challonge_match_id': cmatch.challonge_match_id,
                     'error': error, 'status': e.status},
                )
                if not transient:
                    raise
                logger.warning("Challonge result push failed for match %s: %s", match.id, error)
                return False
        return False

    @requires_feature(FeatureFlag.CHALLONGE)
    async def push_match_result(self, match: Match, actor: User) -> bool:
        """Report the confirmed winner to Challonge. Returns False when Challonge already has a result."""
        cmatch = await self.repository.get_challonge_match_for_match(match)
        if cmatch is None:
            raise ValueError("This match is not linked to a Challonge match.")

        await match.fetch_related('players__user', 'tournament')
        scores = ('1', '0')
        standing = await BracketSetService().standing(match)
        if standing is not None:
            winner_id, loser_id = standing.winner_id, standing.loser_id()
            if winner_id is None:
                raise ValueError(f"This set isn't decided yet ({standing.summary()}).")
            if loser_id is None:
                raise ValueError("Cannot push a result without an opponent.")
            scores = (str(standing.score(winner_id)), str(standing.score(loser_id)))
        else:
            ranked = [p for p in match.players if p.finish_rank is not None]  # type: ignore[attr-defined]
            winner = next((p for p in ranked if p.finish_rank == 1), None)
            if winner is None:
                raise ValueError("No winner has been recorded for this match yet.")
            losers = [p for p in match.players if p.id != winner.id]  # type: ignore[attr-defined]
            if not losers:
                raise ValueError("Cannot push a result without an opponent.")
            winner_id, loser_id = winner.user_id, losers[0].user_id

        winner_pid = self._participant_id_for_user(cmatch, winner_id)
        loser_pid = self._participant_id_for_user(cmatch, loser_id)
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
            winner_score=scores[0],
            loser_score=scores[1],
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
        for attempt in range(2):
            try:
                await self._sync_tournament(cmatch.tournament, actor, force=True)
                return
            except ChallongeAPIError as e:
                # Sentry SGLMAN-2J: a timeout or dropped connection (no status)
                # usually clears on the next request, so retry it once.
                if attempt == 0 and e.status is None:
                    await asyncio.sleep(_RESYNC_RETRY_DELAY)
                    continue
                error: Exception = e
            except ValueError as e:
                error = e
            # The result is already on Challonge; auto-sync or a manual Sync
            # picks up the newly-open matches.
            logger.warning(
                "Challonge post-push re-sync failed for tournament %s: %s",
                cmatch.tournament_id, error,  # type: ignore[attr-defined]
            )
            return

    @staticmethod
    def _participant_id_for_user(cmatch: ChallongeMatch, user_id: int) -> Optional[str]:
        for participant in (cmatch.participant1, cmatch.participant2):
            if participant is not None and participant.user_id == user_id:
                return participant.challonge_participant_id
        return None
