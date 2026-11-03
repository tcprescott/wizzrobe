"""Booking a start.gg set before its players are known, mixed into ``StartggService``.

The start.gg twin of ``_challonge_placeholders``: :meth:`book_ahead` books a set
with whichever players are decided and a placeholder per other side, and each
sync fills the players in. A side reads "Andy or Benny" while the set feeding it
has both its entrants, else start.gg's own "Winner of B", else "TBD".

Only sets of a started phase can be booked this way: start.gg replaces the
``preview_…`` sets of an unstarted phase, which would orphan the booking.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import StartggRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.round_schedule_service import RoundScheduleService
from application.utils.match_labels import entrant_name
from models import FeatureFlag, Match, StartggSet, StartggSetState, Tournament, User

logger = logging.getLogger(__name__)


def placeholder_sides(
    row: StartggSet, by_set_id: Dict[str, StartggSet],
) -> Tuple[List[Optional[int]], List[str]]:
    """``(player ids, placeholders)``, as ``_challonge_placeholders.placeholder_sides``."""
    ids: List[Optional[int]] = []
    labels: List[str] = []
    for n, entrant in ((1, row.entrant1), (2, row.entrant2)):
        if entrant is not None:
            ids.append(entrant.user_id)
            if entrant.user_id is None:
                labels.append(entrant_name(entrant))
            continue
        ids.append(None)
        feeder = by_set_id.get(getattr(row, f'slot{n}_prereq') or '')
        if feeder is not None and feeder.entrant1 is not None and feeder.entrant2 is not None:
            labels.append(f'{entrant_name(feeder.entrant1)} or {entrant_name(feeder.entrant2)}')
        else:
            labels.append(getattr(row, f'slot{n}_label') or 'TBD')
    return ids, labels


class StartggPlaceholderMixin:
    repository: StartggRepository
    audit_service: AuditService

    async def placeholder_sides_for(self, rows: List[StartggSet]) -> Dict[int, Tuple[List[Optional[int]], List[str]]]:
        """Each row's :func:`placeholder_sides`, reading each tournament's sets once."""
        out: Dict[int, Tuple[List[Optional[int]], List[str]]] = {}
        events: Dict[int, Dict[str, StartggSet]] = {}
        for row in rows:
            tid: int = row.tournament_id  # type: ignore[attr-defined]
            if tid not in events:
                events[tid] = {s.set_id: s for s in await self.repository.list_sets(row.tournament)}
            out[row.id] = placeholder_sides(row, events[tid])
        return out

    @requires_feature(FeatureFlag.STARTGG)
    async def list_unbooked_pending(self, tournament_ids: Optional[List[int]] = None) -> List[StartggSet]:
        """Unbooked sets of a started phase still waiting on a player. Callers authorize."""
        rows = await self.repository.pending_unbooked_sets(tournament_ids)
        return [r for r in rows if not r.set_id.startswith('preview')]

    @requires_feature(FeatureFlag.STARTGG)
    async def book_ahead(
        self, set_pk: int, scheduled_date: str, scheduled_time: str, actor: User,
        comment: Optional[str] = None, *, allow_outside_round: bool = False,
    ) -> Match:
        """Book a set whose players aren't all known yet. Staff and the tournament's admins only."""
        row = require_found(await self.repository.get_set(set_pk), 'start.gg match')
        tournament_id: int = row.tournament_id  # type: ignore[attr-defined]
        if not (await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id)):
            raise PermissionError('Only staff or a tournament admin can book a match before its players are known.')
        if row.match_id is not None:  # type: ignore[attr-defined]
            raise ValueError('This match has already been scheduled.')
        if row.state == StartggSetState.COMPLETE:
            raise ValueError('This match has already been played.')
        if row.set_id.startswith('preview'):
            raise ValueError("This phase hasn't started on start.gg yet, so its matches can't be booked.")
        ids, labels = (await self.placeholder_sides_for([row]))[row.id]
        rounds = RoundScheduleService()
        await rounds.check_booking(
            await rounds.window_for_startgg_set(row), row.tournament,
            scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )
        from application.services.match.match_service import MatchService

        match = await MatchService().create_match(
            tournament_id=tournament_id, scheduled_date=scheduled_date, scheduled_time=scheduled_time,
            player_ids=[uid for uid in ids if uid is not None], comment=comment, actor=actor,
            from_bracket=True, placeholders=labels,
        )
        await self.repository.link_match(row, match)
        return match

    @requires_feature(FeatureFlag.STARTGG)
    async def relink_ahead(self, set_pk: int, match_id: int, actor: User) -> Match:
        """Attach a placeholder booking a bracket reset cut loose to this set."""
        row = require_found(await self.repository.get_set(set_pk), 'start.gg match')
        tournament_id: int = row.tournament_id  # type: ignore[attr-defined]
        if not (await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id)):
            raise PermissionError('Only staff or a tournament admin can link matches to a bracket.')
        if row.match_id is not None or row.state == StartggSetState.COMPLETE:  # type: ignore[attr-defined]
            raise ValueError('This matchup is already booked or played.')
        ids, labels = (await self.placeholder_sides_for([row]))[row.id]
        from application.services.match.match_service import MatchService

        service = MatchService()
        match = await service.require_placeholder_relinkable(match_id, tournament_id, ids)
        await self.repository.link_match(row, match)
        await self.audit_service.write_log(actor, AuditActions.STARTGG_SET_LINKED, {
            'tournament_id': tournament_id, 'set_id': row.set_id, 'match_id': match.id,
        })
        await service.fill_bracket_players(match, ids, labels, actor)
        return match

    async def _refresh_placeholder_bookings(self, tournament: Tournament, actor: User) -> None:
        """Fill in the players start.gg has decided since each placeholder booking. Never raises."""
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

    async def _flag_lost_placeholders(self, tournament: Tournament, keep_set_ids: Set[str]) -> None:
        """Flag placeholder bookings whose set this sync is about to drop. Never raises."""
        try:
            lost = [
                row.match.id for row in await self.repository.list_placeholder_bookings(tournament)  # type: ignore[union-attr]
                if row.set_id not in keep_set_ids
            ]
            if lost:
                from application.services.match.match_service import MatchService

                await MatchService().mark_bracket_link_lost(lost)
        except Exception:
            logger.exception('Flagging lost placeholder bookings failed for tournament %s', tournament.id)
