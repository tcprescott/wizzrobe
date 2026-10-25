"""The "matchup ready to schedule" DM for Challonge brackets, mixed into ``ChallongeService``.

Native brackets DM both entrants the moment a matchup becomes bookable. A
Challonge bracket used to send nothing, so a player only learned a match was
waiting by opening My Schedule. This sends the same DM, with the same card, and
its button opens that matchup's picker (``/home/player?book=<id>``).

"Bookable" is what the player tab lists with a working Schedule button: OPEN in
Challonge, not yet booked here, and both entrants resolved to a Wizzrobe user.
The DM is keyed on a match *becoming* bookable, so re-syncing an unchanged
bracket never repeats it. That happens on a sync that opens the match, and on a
staff hand-assignment that resolves its last unlinked entrant.
"""

import logging
from typing import Set

from application.repositories import ChallongeRepository
from application.services import notification_links
from application.services._bracket.notifications import BracketNotificationMixin
from application.services.discord import discord_queue
from application.utils.match_labels import challonge_round_label, entrant_name
from models import ChallongeMatch, ChallongeMatchState, Tournament

logger = logging.getLogger(__name__)


async def matchup_best_of(**ref: int) -> int:
    """The matchup's stage best-of from the round schedule, as the native DM reads its stage.

    Best-effort: a failed lookup sends the DM without the format line.
    """
    from application.services.round_schedule_service import RoundScheduleService

    try:
        return await RoundScheduleService().best_of_for(**ref)
    except Exception:
        logger.exception('Reading the best-of for a matchup-ready DM failed (%s)', ref)
        return 1


def is_bookable(cmatch: ChallongeMatch) -> bool:
    p1, p2 = cmatch.participant1, cmatch.participant2
    return (
        cmatch.state == ChallongeMatchState.OPEN
        and getattr(cmatch, 'match_id', None) is None
        and p1 is not None and p2 is not None
        and p1.user_id is not None and p2.user_id is not None
    )


class ChallongeNotificationMixin:
    repository: ChallongeRepository

    async def _bookable_match_ids(self, tournament: Tournament) -> Set[int]:
        return {m.id for m in await self.repository.list_matches(tournament) if is_bookable(m)}

    async def _announce_newly_bookable(self, tournament: Tournament, before: Set[int]) -> None:
        """DM both players of every match that is bookable now but wasn't in ``before``.

        Best-effort: a failed notification never fails the sync or the
        assignment that triggered it.
        """
        try:
            for cmatch in await self.repository.list_matches(tournament):
                if cmatch.id not in before and is_bookable(cmatch):
                    await self._notify_matchup_ready(cmatch)
        except Exception:
            logger.exception('Challonge matchup-ready notifications failed for tournament %s', tournament.id)

    @staticmethod
    async def _notify_matchup_ready(cmatch: ChallongeMatch) -> None:
        p1, p2 = cmatch.participant1, cmatch.participant2
        if p1 is None or p2 is None:
            return
        link = await notification_links.player_challonge_schedule(cmatch.id)
        round_name = challonge_round_label(cmatch.round) or 'Bracket match'
        best_of = await matchup_best_of(challonge_match_id=cmatch.id)
        for me, opponent in ((p1, p2), (p2, p1)):
            discord_queue.enqueue(BracketNotificationMixin._send_matchup_ready(
                user=me.user,
                opponent_name=entrant_name(opponent),
                opponent_seed=None,
                tournament_name=cmatch.tournament.name,
                round_name=round_name,
                best_of=best_of,
                schedule_link=link,
                rebook=False,
            ))
