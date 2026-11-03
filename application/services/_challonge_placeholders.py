"""Booking a Challonge matchup before its players are known, mixed into ``ChallongeService``.

Staff stage late elimination rounds while the matches feeding them are still
being played. :meth:`book_ahead` books such a matchup with whichever players
are decided and a placeholder for each other side; every sync afterwards calls
:meth:`_refresh_placeholder_bookings`, which fills the players in as Challonge
decides them (``MatchService.fill_bracket_players``).

A side reads "Andy or Benny" while the match feeding it has both its players,
else "Winner of C" (Challonge's letter for that match), else "TBD". A decided
side whose entrant has no Wizzrobe account reads as the entrant's name until
staff match it.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.round_schedule_service import RoundScheduleService
from application.utils.match_labels import entrant_name
from models import ChallongeMatch, ChallongeMatchState, FeatureFlag, Match, Tournament, User

logger = logging.getLogger(__name__)


def slot_fields(remote: Dict, letters: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """The mirror columns naming a remote match's letter and what feeds each slot."""
    out: Dict[str, Optional[str]] = {'identifier': remote.get('identifier')}
    for n in (1, 2):
        prereq = remote.get(f'player{n}_prereq_match_id')
        letter = letters.get(prereq) if prereq else None
        out[f'slot{n}_prereq'] = prereq
        out[f'slot{n}_label'] = (
            f"{'Loser' if remote.get(f'player{n}_is_prereq_loser') else 'Winner'} of {letter}"
            if letter else None
        )
    return out


def placeholder_sides(
    cmatch: ChallongeMatch, by_remote_id: Dict[str, ChallongeMatch],
) -> Tuple[List[Optional[int]], List[str]]:
    """``(player ids, placeholders)``: each side's account (None if undecided) and
    the label standing in for every side without one."""
    ids: List[Optional[int]] = []
    labels: List[str] = []
    for n, participant in ((1, cmatch.participant1), (2, cmatch.participant2)):
        if participant is not None:
            ids.append(participant.user_id)
            if participant.user_id is None:
                labels.append(entrant_name(participant))
            continue
        ids.append(None)
        feeder = by_remote_id.get(getattr(cmatch, f'slot{n}_prereq') or '')
        if feeder is not None and feeder.participant1 is not None and feeder.participant2 is not None:
            labels.append(f'{entrant_name(feeder.participant1)} or {entrant_name(feeder.participant2)}')
        else:
            labels.append(getattr(cmatch, f'slot{n}_label') or 'TBD')
    return ids, labels


class ChallongePlaceholderMixin:
    repository: ChallongeRepository
    audit_service: AuditService

    async def placeholder_sides_for(self, rows: List[ChallongeMatch]) -> Dict[int, Tuple[List[Optional[int]], List[str]]]:
        """Each row's :func:`placeholder_sides`, reading each tournament's bracket once."""
        out: Dict[int, Tuple[List[Optional[int]], List[str]]] = {}
        brackets: Dict[int, Dict[str, ChallongeMatch]] = {}
        for row in rows:
            tid: int = row.tournament_id  # type: ignore[attr-defined]
            if tid not in brackets:
                brackets[tid] = {
                    m.challonge_match_id: m for m in await self.repository.list_matches(row.tournament)
                }
            out[row.id] = placeholder_sides(row, brackets[tid])
        return out

    @requires_feature(FeatureFlag.CHALLONGE)
    async def list_unbooked_pending(self, tournament_ids: Optional[List[int]] = None) -> List[ChallongeMatch]:
        """Unbooked elimination matchups still waiting on a player. Callers authorize."""
        return await self.repository.unscheduled_pending_matches(tournament_ids)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def book_ahead(
        self, challonge_match_pk: int, scheduled_date: str, scheduled_time: str, actor: User,
        comment: Optional[str] = None, *, allow_outside_round: bool = False,
    ) -> Match:
        """Book a matchup whose players aren't all known yet. Staff and the tournament's admins only."""
        cmatch = require_found(await self.repository.get_match(challonge_match_pk), 'Challonge match')
        tournament_id: int = cmatch.tournament_id  # type: ignore[attr-defined]
        if not (await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id)):
            raise PermissionError('Only staff or a tournament admin can book a match before its players are known.')
        if cmatch.match_id is not None:  # type: ignore[attr-defined]
            raise ValueError('This match has already been scheduled.')
        if cmatch.state == ChallongeMatchState.COMPLETE:
            raise ValueError('This match has already been played.')
        ids, labels = (await self.placeholder_sides_for([cmatch]))[cmatch.id]
        await RoundScheduleService().check_challonge_booking(
            cmatch, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )
        from application.services.match.match_service import MatchService

        match = await MatchService().create_match(
            tournament_id=tournament_id, scheduled_date=scheduled_date, scheduled_time=scheduled_time,
            player_ids=[uid for uid in ids if uid is not None], comment=comment, actor=actor,
            from_bracket=True, placeholders=labels,
        )
        await self.repository.link_match(cmatch, match)
        return match

    @requires_feature(FeatureFlag.CHALLONGE)
    async def relink_ahead(self, challonge_match_pk: int, match_id: int, actor: User) -> Match:
        """Attach a placeholder booking a bracket reset cut loose to this matchup."""
        cmatch = require_found(await self.repository.get_match(challonge_match_pk), 'Challonge match')
        tournament_id: int = cmatch.tournament_id  # type: ignore[attr-defined]
        if not (await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id)):
            raise PermissionError('Only staff or a tournament admin can link matches to a bracket.')
        if cmatch.match_id is not None or cmatch.state == ChallongeMatchState.COMPLETE:  # type: ignore[attr-defined]
            raise ValueError('This matchup is already booked or played.')
        ids, labels = (await self.placeholder_sides_for([cmatch]))[cmatch.id]
        from application.services.match.match_service import MatchService

        service = MatchService()
        match = await service.require_placeholder_relinkable(match_id, tournament_id, ids)
        await self.repository.link_match(cmatch, match)
        await self.audit_service.write_log(actor, AuditActions.CHALLONGE_MATCH_LINKED, {
            'tournament_id': tournament_id, 'challonge_match_id': cmatch.id, 'match_id': match.id,
        })
        await service.fill_bracket_players(match, ids, labels, actor)
        return match

    async def _refresh_placeholder_bookings(self, tournament: Tournament, actor: User) -> None:
        """Fill in the players Challonge has decided since each placeholder booking.

        Best-effort: a failure here never fails the sync that called it.
        """
        try:
            rows = await self.repository.list_placeholder_bookings(tournament)
            if not rows:
                return
            from application.services.match.match_service import MatchService

            service = MatchService()
            sides = await self.placeholder_sides_for(rows)
            for row in rows:
                ids, labels = sides[row.id]
                await service.fill_bracket_players(row.match, ids, labels, actor)  # type: ignore[arg-type]
        except Exception:
            logger.exception('Filling placeholder bookings failed for tournament %s', tournament.id)

    async def _flag_lost_placeholders(self, tournament: Tournament, keep_remote_ids: Set[str]) -> None:
        """Flag placeholder bookings whose matchup this sync is about to drop."""
        try:
            lost = [
                row.match.id for row in await self.repository.list_placeholder_bookings(tournament)  # type: ignore[union-attr]
                if row.challonge_match_id not in keep_remote_ids
            ]
            if lost:
                from application.services.match.match_service import MatchService

                await MatchService().mark_bracket_link_lost(lost)
        except Exception:
            logger.exception('Flagging lost placeholder bookings failed for tournament %s', tournament.id)
