"""The app's admin Match Schedule: making and changing matches.

Three things the board's first two reads leave out: the New/Edit match form
(its options and a save that takes one instant), the bracket matchups nobody
has booked yet (``bracket_sources.list_unbooked``, as the website's "Not booked
yet" view lists them) with booking and linking, and the "Check this time" read
both of those run before they save.

Times come in as UTC instants. The services below take a local date and time,
so each instant is split on the request's own clock and parsed back on the same
one, which makes the zone drop out.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from application.errors import NotFoundError
from application.event_context import get_current_event_id
from application.services import bracket_sources
from application.services.auth_service import AuthService
from application.services.bracket_service import BracketService
from application.services.event_service import EventService
from application.services.match.match_service import MatchService
from application.services.match.match_suggestion_service import MatchSuggestionService
from application.services.round_schedule_service import RoundScheduleService, RoundWindowError
from application.services.stage_service import StageService
from application.services.tournament_service import TournamentService
from application.services.user_service import UserService
from application.utils.timezone import to_local, to_utc_aware
from models import User

logger = logging.getLogger(__name__)

SOURCES = ('bracket', 'challonge', 'startgg')


def _local_parts(at: datetime) -> Tuple[str, str]:
    local = to_local(to_utc_aware(at))
    assert local is not None
    return local.strftime('%Y-%m-%d'), local.strftime('%H:%M')


def _ref(source: Optional[str], key: Optional[int]) -> Dict[str, int]:
    if source is None or key is None:
        return {}
    return {{'bracket': 'bracket_match_id', 'challonge': 'challonge_match_id'}.get(source, 'startgg_set_id'): key}


class AdminMatchFormService:
    """Stateless; every entry checks the caller the way the website's dialogs do."""

    def __init__(self) -> None:
        self.matches = MatchService()
        self.tournaments = TournamentService()

    @staticmethod
    async def _require_editor(actor: User) -> None:
        if await AuthService.is_staff(actor):
            return
        admin_ids, _ = await AuthService.tournament_scope(actor)
        if not admin_ids:
            raise PermissionError("Only staff and tournament admins can schedule matches.")

    async def form(self, actor: User, match_id: Optional[int] = None) -> Dict[str, Any]:
        """The New/Edit match form: its options and, for an edit, the match's values."""
        await self._require_editor(actor)
        match = None
        if match_id is not None:
            match = await self.matches.get_match_by_id(match_id)
            if match is None or not await AuthService.can_crud_match(actor, match):
                raise NotFoundError('Match not found')
        tournaments, _counts = await self.tournaments.list_schedulable(
            actor, keep_id=match.tournament_id if match else None,  # type: ignore[attr-defined]
        )
        options = []
        for t in tournaments:
            enrolled = await self.tournaments.get_enrolled_players_by_tournament_id(t.id)
            options.append({
                'id': t.id, 'name': t.name,
                'enrolled': sorted({tp.user_id for tp in enrolled}),
                'players_per_match': getattr(t, 'players_per_match', None),
            })
        values: Dict[str, Any] = {
            'tournament_id': None, 'stage_id': None, 'scheduled_at': None, 'player_ids': [],
            'commentator_ids': [], 'tracker_ids': [], 'broadcaster_ids': [], 'title': '',
            'description': '', 'comment': '', 'is_stream_candidate': False,
        }
        include: List[int] = []
        if match is not None:
            players = await self.matches.get_match_players(match)
            values.update({
                'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
                'stage_id': match.stage_id,  # type: ignore[attr-defined]
                'scheduled_at': match.scheduled_at,
                'player_ids': [p.user_id for p in players],
                'commentator_ids': [c.user_id for c in await match.commentators],  # type: ignore[attr-defined]
                'tracker_ids': [t.user_id for t in await match.trackers],  # type: ignore[attr-defined]
                'broadcaster_ids': [b.user_id for b in await match.broadcasters],  # type: ignore[attr-defined]
                'title': match.title or '',
                'description': match.description or '',
                'comment': match.comment or '',
                'is_stream_candidate': bool(match.is_stream_candidate),
            })
            include = [*values['player_ids'], *values['commentator_ids'], *values['tracker_ids'], *values['broadcaster_ids']]
            broadcasters = await EventService.broadcasters_on(match.event_id)  # type: ignore[attr-defined]
        else:
            broadcasters = await EventService.broadcasters_on(get_current_event_id())
        people = await UserService().get_community_people_summaries(include_user_ids=include, include_inactive=True)
        stages = await StageService().get_all_stages()
        return {
            'match_id': match.id if match else None,
            'synced_from_speedgaming': bool(match and getattr(match, 'speedgaming_episode_id', None)),
            'tournaments': options,
            'stages': [{'id': s.id, 'name': s.name} for s in stages],
            'people': [{'id': p.id, 'name': p.preferred_name} for p in people],
            'broadcasters': broadcasters or bool(values['broadcaster_ids']),
            'values': values,
        }

    async def save(self, actor: User, match_id: Optional[int], data: Dict[str, Any]) -> int:
        """Create the match, or change it, as the website's dialog does on Save."""
        at = data.get('scheduled_at')
        if at is None:
            raise ValueError('Choose a date and a time.')
        day, clock = _local_parts(at)
        stage_id = data.get('stage_id') or None
        if match_id is None:
            match = await self.matches.create_match(
                tournament_id=data['tournament_id'], scheduled_date=day, scheduled_time=clock,
                player_ids=data.get('player_ids') or [], comment=data.get('comment') or None,
                commentator_ids=data.get('commentator_ids') or [], tracker_ids=data.get('tracker_ids') or [],
                broadcaster_ids=data.get('broadcaster_ids') or [],
                is_stream_candidate=bool(data.get('is_stream_candidate')),
                title=data.get('title') or '', description=data.get('description') or '', actor=actor,
            )
            if stage_id:
                await self.matches.assign_stage(match.id, stage_id, actor=actor)
            return match.id
        match = await self.matches.get_match_by_id(match_id)
        if match is None:
            raise NotFoundError('Match not found')
        await self.matches.update_match(
            match_id, tournament_id=data['tournament_id'], scheduled_date=day, scheduled_time=clock,
            player_ids=data.get('player_ids') or [], commentator_ids=data.get('commentator_ids') or [],
            tracker_ids=data.get('tracker_ids') or [],
            broadcaster_ids=data.get('broadcaster_ids') if data.get('broadcaster_ids') is not None else None,
            comment=data.get('comment') or '', title=data.get('title') or '',
            description=data.get('description') or '', actor=actor,
        )
        if stage_id != match.stage_id:  # type: ignore[attr-defined]
            await self.matches.assign_stage(match_id, stage_id, actor=actor)
        if bool(data.get('is_stream_candidate')) != bool(match.is_stream_candidate):
            await self.matches.set_stream_candidate(match_id, bool(data.get('is_stream_candidate')), actor=actor)
        return match_id

    async def check_time(
        self, actor: User, *, tournament_id: int, player_ids: List[int], at: datetime,
        match_id: Optional[int] = None, source: Optional[str] = None, key: Optional[int] = None,
    ) -> Dict[str, Any]:
        """The booking window ``at`` falls outside of, if any, and the clashes the
        website's "Check this time" sheet would show. Nothing is saved."""
        at = to_utc_aware(at)
        window: Optional[Dict[str, Any]] = None
        ref = _ref(source, key)
        if ref:
            try:
                await RoundScheduleService().check_reference(at, actor, **ref)
            except RoundWindowError as e:
                window = {'message': str(e), 'can_override': e.can_override}
        clashes: List[str] = []
        ids = [p for p in player_ids if p]
        if ids:
            try:
                found = await MatchSuggestionService().find_clashes(
                    tournament_id, ids, at, actor=actor, exclude_match_id=match_id,
                )
                clashes = [c.message for c in found]
            except Exception:
                logger.warning('Clash check failed for tournament %s', tournament_id, exc_info=True)
        return {'window': window, 'clashes': clashes}

    async def suggest(
        self, actor: User, *, tournament_id: int, player_ids: List[int],
        match_id: Optional[int] = None, source: Optional[str] = None, key: Optional[int] = None,
    ) -> datetime:
        await self._require_editor(actor)
        return await MatchSuggestionService().suggest_match_time(
            tournament_id, [p for p in player_ids if p], actor=actor, exclude_match_id=match_id, **_ref(source, key),
        )

    async def unbooked(self, actor: User) -> List[Dict[str, Any]]:
        """Every open bracket matchup the viewer may book, players-TBD ones included."""
        await self._require_editor(actor)
        event_id = get_current_event_id()
        rows = await bracket_sources.list_unbooked(actor, include_players_tbd=True)
        out = []
        for item in rows:
            if event_id is not None and item.tournament.event_id != event_id:  # type: ignore[attr-defined]
                continue
            state = 'tbd' if item.players_tbd else 'ready' if item.ready else 'needs_account'
            out.append({
                'source': item.source,
                'source_label': item.source_label,
                'key': item.key,
                'tournament_id': item.tournament.id,
                'tournament': item.tournament.name,
                'round_label': item.round_label,
                'players': item.player_names,
                'player_ids': [p for p in item.player_ids if p is not None],
                'state': state,
                'unlinked': item.unlinked,
                'window_text': item.window_text,
                'best_of': item.best_of,
                'games_booked': item.games_booked,
                'link_candidates': [
                    {'id': m.id, 'scheduled_at': m.scheduled_at, 'stage': m.stage.name if m.stage else None}  # type: ignore[attr-defined]
                    for m in (item.lost_bookings if item.players_tbd else item.same_player_matches)
                ],
                'bracket_url': _bracket_url(item),
            })
        return out

    async def book(
        self, actor: User, source: str, key: int, at: datetime, *, allow_outside_round: bool = False,
    ) -> int:
        """Book one game of the matchup at ``at``: the whole match for a best-of-1,
        the next free game of a set, or a slot ahead of its players."""
        if source not in SOURCES:
            raise ValueError(f'Unknown bracket source: {source}')
        await self._require_editor(actor)
        day, clock = _local_parts(at)
        if source == 'bracket':
            match = await BracketService().schedule_bracket_match(
                actor, key, scheduled_date=day, scheduled_time=clock, allow_outside_round=allow_outside_round,
            )
            return match.id
        item = next(
            (i for i in await bracket_sources.list_unbooked(actor, include_players_tbd=True)
             if i.source == source and i.key == key),
            None,
        )
        if item is None:
            raise ValueError('That matchup is booked already or no longer open.')
        if item.players_tbd:
            match = await bracket_sources.book_ahead(source, key, day, clock, actor, allow_outside_round=allow_outside_round)
        elif max(item.best_of, await bracket_sources.best_of_for(source, key)) > 1:
            match = await bracket_sources.book_set_game(source, key, day, clock, actor, allow_outside_round=allow_outside_round)
        else:
            match = await bracket_sources.schedule(source, key, day, clock, actor, allow_outside_round=allow_outside_round)
        return match.id

    async def link(self, actor: User, source: str, key: int, match_id: int, *, ahead: bool = False) -> None:
        """Record a match scheduled by hand as this matchup's (or re-attach a TBD booking)."""
        if source not in SOURCES:
            raise ValueError(f'Unknown bracket source: {source}')
        if ahead:
            await bracket_sources.relink_ahead(source, key, match_id, actor)
        else:
            await bracket_sources.link_existing(source, key, match_id, actor)


def _bracket_url(item: Any) -> Optional[str]:
    if item.source == 'challonge':
        return item.tournament.challonge_tournament_url or item.tournament.bracket_url or None
    return None
