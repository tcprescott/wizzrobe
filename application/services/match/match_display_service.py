"""
Match Display Service - Read/Formatting Layer

View-model assembly for the match tables: reads matches (and their
acknowledgments) via repositories and formats them into the plain dicts the
NiceGUI table slots consume. Extracted from ``MatchService`` so the latter
keeps only lifecycle logic.

Read-only: no writes, no audit, no Discord notifications.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from application.content import parse_user_blocks, plain_text
from application.repositories import (
    CommentaryInviteRepository,
    MatchAcknowledgmentRepository,
    MatchRepository,
    MatchStreamVolunteerRepository,
    ProviderTaskRepository,
    StageRepository,
    TournamentRepository,
)
from application.services.bracket_set_service import BracketSetService
from application.services.event_service import EventService
from application.services.match.match_status import (
    closes_without_winner,
    has_recorded_result,
    legacy_label,
    resolve,
)
from application.services.match.player_late_service import late_tag, late_window_open
from application.services.match.seed_ready import ready_count, seed_ready_open
from application.services.reporting_shared import crew_requirement
from application.utils.discord_messages import seed_code_line
from application.utils.timezone import (
    format_local_datetime,
    format_local_display,
    format_local_time,
    to_utc_aware,
)
from models import Match, MatchAcknowledgment, ProviderTask, Tournament


def rolling_elapsed_label(started_at: datetime) -> str:
    """"Rolling… 1:24" — how long an in-flight seed roll has been going.

    Elapsed rather than a percentage or an ETA because the provider gives
    nothing to base one on: its status endpoint reports queued/started/finished
    and no position in the queue. A counting clock is the only honest thing to
    show, and it is what tells someone the page is working rather than stuck.
    """
    seconds = max(0, int((datetime.now(timezone.utc) - to_utc_aware(started_at)).total_seconds()))
    return f'Rolling… {seconds // 60}:{seconds % 60:02d}'


# 'Started' is deliberate: a seed rerolled mid-race must stay downloadable at the room PC.
ROOM_SEED_STATES = ('Scheduled', 'Checked In', 'Started')

# 0 is safe as the sentinel only because stage ids start at 1.
NO_STAGE = 0


def is_room_seed_row(row: Dict[str, Any]) -> bool:
    """Whether a display row is a rolled seed for a match not yet finished.

    Pure, over an already-formatted row, so the tournament-room board can apply
    it both to a full refresh and to a single row arriving from a live update —
    a match that finishes has to leave the room screen, not merely stop updating.
    """
    return bool(row.get('generated_seed')) and row.get('state') in ROOM_SEED_STATES


class MatchDisplayService:
    """Service for reading and formatting matches for table display."""

    def __init__(self) -> None:
        self.repository = MatchRepository()
        self.ack_repository = MatchAcknowledgmentRepository()
        self.volunteer_repository = MatchStreamVolunteerRepository()
        self.tournament_repository = TournamentRepository()
        self.stage_repository = StageRepository()
        self.provider_task_repository = ProviderTaskRepository()

    async def get_match_for_display(
        self,
        match_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Get a match with all related data formatted for display.

        Args:
            match_id: The match ID

        Returns:
            Dictionary with match data or None
        """
        match = await self.repository.get_by_id(match_id, prefetch_relations=True)
        if not match:
            return None

        acks = await self.ack_repository.list_for_match(match)
        volunteers = await self.volunteer_repository.names_by_match([match.id])
        rolling = await self.provider_task_repository.active_for_match(match.id)
        failed = await self.provider_task_repository.latest_failure_for_match(match.id)
        row = self._format_match_for_display(
            match, acks, volunteers.get(match.id, []), rolling, failed,
            await EventService.broadcaster_events([match.event_id]),
        )
        await self._attach_site_results([row], [match])
        await self._attach_racetime_bot([row], [match])
        await self._attach_set_labels([row], [match])
        await self._attach_partner_invites([row])
        return row

    async def get_matches_for_display(
        self,
        *,
        tournament_ids: Optional[List[int]] = None,
        stage_ids: Optional[List[int]] = None,
        only_upcoming: bool = False,
        user_discord_id: Optional[str] = None,
        crew_discord_id: Optional[str] = None,
        exclude_racetime: bool = False,
        match_ids: Optional[List[int]] = None,
        scheduled_window: Optional[Tuple[datetime, datetime]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get matches formatted for table display.

        Args:
            tournament_ids: Filter by tournament IDs
            stage_ids: Filter by stage IDs; ``NO_STAGE`` in the list also keeps
                matches with no stage
            only_upcoming: Only return unfinished matches
            user_discord_id: Filter by player discord ID
            crew_discord_id: Filter to matches this discord ID crews
            exclude_racetime: Drop matches run by a racetime.gg tournament — the
                proctor board's rows are the ones a proctor can actually act on
            match_ids: Restrict to these match IDs (a deep-linked board)
            scheduled_window: Half-open ``(start, end)`` on the scheduled time,
                applied in the database so a one-day board does not load the
                whole event

        Returns:
            List of formatted match dictionaries
        """
        include_unstaged = False
        if stage_ids and NO_STAGE in stage_ids:
            include_unstaged = True
            stage_ids = [sid for sid in stage_ids if sid != NO_STAGE]
        matches = await self.repository.get_all(
            tournament_ids=tournament_ids,
            stage_ids=stage_ids,
            include_unstaged=include_unstaged,
            only_upcoming=only_upcoming,
            user_discord_id=user_discord_id,
            crew_discord_id=crew_discord_id,
            exclude_racetime=exclude_racetime,
            match_ids=match_ids,
            scheduled_window=scheduled_window,
            prefetch_relations=True
        )

        match_ids_loaded = [m.id for m in matches]
        ack_map = await self.ack_repository.list_for_matches(match_ids_loaded)
        volunteer_map = await self.volunteer_repository.names_by_match(match_ids_loaded)
        rolling_map, failed_map = await self.provider_task_repository.board_state_for_matches(
            match_ids_loaded,
        )
        broadcaster_events = await EventService.broadcaster_events(m.event_id for m in matches)
        rows = [
            self._format_match_for_display(
                m, ack_map.get(m.id, []), volunteer_map.get(m.id, []),
                rolling_map.get(m.id), failed_map.get(m.id), broadcaster_events,
            )
            for m in matches
        ]
        await self._attach_site_results(rows, matches)
        await self._attach_racetime_bot(rows, matches)
        await self._attach_set_labels(rows, matches)
        await self._attach_partner_invites(rows)
        if user_discord_id or crew_discord_id:
            await self._attach_event_names(rows, matches)
        return rows

    @staticmethod
    async def _attach_event_names(rows: List[Dict[str, Any]], matches: List[Match]) -> None:
        """A person's own list spans live events: lead the tournament with its event once two share it."""
        from application.services.event_service import with_event_name

        names = await EventService.names_if_mixed(m.event_id for m in matches)
        if not names:
            return
        for row, match in zip(rows, matches, strict=True):
            event_id = match.event_id
            row['tournament'] = with_event_name(row['tournament'], event_id, names)
            row['tournament_short'] = with_event_name(row['tournament_short'], event_id, names)

    @staticmethod
    async def _attach_partner_invites(rows: List[Dict[str, Any]]) -> None:
        """Each row's commentary partner invites still waiting on an answer, one query."""
        invites = await CommentaryInviteRepository.pending_by_match(
            [r['id'] for r in rows if (r.get('commentary_seats') or 0) >= 2])
        for row in rows:
            row['partner_invites'] = [{
                'id': invite.id,
                'inviter_id': invite.inviter_id,
                'inviter_discord_id': str(invite.inviter.discord_id) if invite.inviter.discord_id else None,
                'invitee': invite.invitee.preferred_name,
            } for invite in invites.get(row['id'], [])]

    @staticmethod
    async def _attach_set_labels(rows: List[Dict[str, Any]], matches: List[Match]) -> None:
        """Fill each set game's "Game 2 of 3", which depends on when the set's other games are."""
        labels = await BracketSetService().labels_for(matches)
        for row in rows:
            row['set_label'] = labels.get(row['id'], row.get('set_label'))

    @staticmethod
    async def _attach_site_results(rows: List[Dict[str, Any]], matches: List[Match]) -> None:
        """Mark unconfirmed rows whose bracket site already has the result (``played_on_site``).

        Someone reported it on Challonge or start.gg directly, so the match on
        the board would otherwise be run again or sit there forever.
        """
        from application.services import bracket_sources

        linked = {
            m.id for m in matches
            if m.tournament and (m.tournament.challonge_tournament_id or m.tournament.startgg_event_slug)
        }
        open_ids = [
            r['id'] for r in rows
            if r['id'] in linked and (
                r['state'] in ('Scheduled', 'Checked In', 'Started')
                or (r['state'] == 'Finished' and not r.get('has_result'))
            )
        ]
        results = await bracket_sources.results_on_site(open_ids) if open_ids else {}
        for row in rows:
            result = results.get(row['id'])
            row['played_on_site'] = (
                {'site': result.site, 'winner': result.winner_name, 'winner_user_id': result.winner_user_id,
                 'no_winner': result.no_winner}
                if result else None
            )

    @staticmethod
    async def _attach_racetime_bot(rows: List[Dict[str, Any]], matches: List[Match]) -> None:
        """What the racetime room bot needs staff to see on a row.

        ``racetime_held``: the race finished but its result waits for a check
        (``RaceRoomService.record_finish``). ``racetime_unlinked``: players the bot
        can't invite or score until they link racetime. Only tournaments the bot
        runs rooms for, so a board with none costs no query.
        """
        from application.repositories import RacetimeRoomRepository
        from application.services.race_room_holds import hold_label

        bot_run = {m.id: m for m in matches if m.tournament and m.tournament.racetime_bot_runs_room}
        held_ids = [
            r['id'] for r in rows if r['id'] in bot_run and r['state'] == 'Finished' and not r.get('has_result')
        ]
        rooms = await RacetimeRoomRepository().for_matches(held_ids) if held_ids else {}
        for row in rows:
            row['racetime_held'] = None
            row['racetime_unlinked'] = []
            match = bot_run.get(row['id'])
            if match is None:
                continue
            room = rooms.get(match.id)
            if room is not None and room.held_reason:
                reason = getattr(room.held_reason, 'value', room.held_reason)
                row['racetime_held'] = {
                    'reason': reason, 'label': hold_label(reason),
                    'detail': room.held_detail or '', 'url': room.url,
                }
            if row['state'] in ('Scheduled', 'Checked In', 'Started'):
                row['racetime_unlinked'] = [
                    p.user.preferred_name for p in match.players if not p.user.racetime_user_id  # type: ignore[attr-defined]
                ]

    async def get_room_seed_rows(self) -> List[Dict[str, Any]]:
        """Rolled-but-unfinished matches, for the tournament room's kiosk board.

        ``only_upcoming`` drops the finished history at the DB layer; the
        predicate then drops the ones with no seed, which is the whole selection
        rule — a room PC shows the seeds someone is about to need, or is racing
        on, and nothing else.
        """
        rows = await self.get_matches_for_display(only_upcoming=True)
        return [row for row in rows if is_room_seed_row(row)]

    async def get_tournaments_for_filter(self) -> Dict[int, str]:
        """
        Get all tournaments formatted for filter dropdown.

        Returns:
            Dict mapping tournament ID to its short label, ordered by that label
        """
        return Tournament.short_label_options(await self.tournament_repository.get_all())

    async def get_stages_for_filter(self) -> Dict[int, str]:
        """
        Get all stages formatted for filter dropdown.

        Returns:
            Dict mapping stage ID to name
        """
        return await self.stage_repository.get_all_as_dict()

    def _get_match_state(self, match: Match) -> str:
        """The schedule table's state string for a match.

        A thin adapter over :func:`match_status.resolve` rather than its own
        ladder, so this table's labels cannot drift from the bracket's — that
        drift is exactly what made the two surfaces read as separate products.
        No ``room_status`` is passed: the schedule shows the recorded lifecycle,
        and letting a racetime room advance a row here would disagree with the
        timestamp the row's own action buttons act on.

        Returns one of the five historical strings — 'Confirmed', 'Finished',
        'Started', 'Checked In', 'Scheduled' — which the filter chips and the
        Vue table slots compare against by value.
        """
        return legacy_label(resolve(match=match))

    @staticmethod
    def _bracket_ref(match: Match) -> Optional[Dict[str, Any]]:
        """The bracket matchup this match is a game of, for the schedule's link out.

        ``bracket_match_game`` is a reverse OneToOne, so it is ``None`` for the
        vast majority of matches (nothing scheduled it through a bracket) and
        raises when the relation was not prefetched — a caller that skipped
        ``prefetch_relations`` gets no link rather than an exception.

        Beyond the stage name and game number this now carries the **series
        context** (U4): the resolved best-of and the standing so far, so a
        schedule row reads "Game 2 of 3 · 1-0" instead of a bare "Game 2". Both
        come off the games the repository already prefetched — no extra query
        per row. The round *name* is deliberately absent: naming a round is
        structural and needs the stage's whole graph, which is a query the
        schedule table must not pay per row; the Discord DMs, which fan out one
        at a time, do resolve it.
        """
        try:
            game = match.bracket_match_game
        except Exception:
            return None
        if game is None:
            return None
        bracket_match = getattr(game, 'bracket_match', None)
        bracket = getattr(bracket_match, 'bracket', None) if bracket_match else None
        if bracket is None:
            return None
        best_of, standing = MatchDisplayService._series_context(bracket, bracket_match)
        number = MatchDisplayService._game_number(game, bracket_match)
        return {
            'id': bracket.id,
            'name': bracket.name,
            'game': number if number and (number > 1 or best_of > 1) else None,
            'best_of': best_of,
            'standing': standing,
        }

    @staticmethod
    def _game_number(game, bracket_match) -> Optional[int]:
        """The game's number by the clock among its series' games, off the prefetched rows."""
        from application.services.bracket_service import BracketService

        try:
            return BracketService.numbers_by_time(list(bracket_match.games)).get(game.id, game.game_number)
        except Exception:
            return game.game_number

    @staticmethod
    def _series_context(bracket, bracket_match) -> tuple:
        """``(best_of, standing)`` for a matchup, or ``(1, '')`` when unavailable.

        Both computations are the bracket service's pure ones (``resolve_best_of``
        / ``_standing_from``) reused rather than re-derived, so the schedule
        cannot quote a different series length or score than the bracket does.
        ``standing`` is '' before the first game settles — "0-0" reads as a
        result that has been played.
        """
        from application.services.bracket_service import BracketService

        try:
            best_of = BracketService.resolve_best_of(bracket, bracket_match)
            if best_of <= 1:
                return best_of, ''
            games = list(bracket_match.games)
            e1, e2 = BracketService._standing_from(games, bracket_match)
            standing = f'{max(e1, e2)}-{min(e1, e2)}' if (e1 or e2) else ''
            return best_of, standing
        except Exception:
            return 1, ''

    def _format_match_for_display(
        self,
        match: Match,
        acknowledgments: Optional[List[MatchAcknowledgment]] = None,
        stream_volunteers: Optional[List[str]] = None,
        rolling_task: Optional['ProviderTask'] = None,
        failed_task: Optional['ProviderTask'] = None,
        broadcaster_events: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        """Format a match object for UI display.

        ``broadcaster_events`` are the events with broadcasters switched on
        (``EventService.broadcaster_events``); a match in any other event has no
        Broadcaster role.
        """
        state = self._get_match_state(match)
        broadcasters_on = match.event_id in (broadcaster_events or set())
        need_broadcasters = (
            (match.tournament.required_broadcasters if match.tournament else 1) if broadcasters_on else 0
        )

        if match.confirmed_at:
            state_changed_at = match.confirmed_at
        elif match.finished_at:
            state_changed_at = match.finished_at
        elif match.started_at:
            state_changed_at = match.started_at
        elif match.seated_at:
            state_changed_at = match.seated_at
        else:
            state_changed_at = match.created_at
        state_timestamp = format_local_datetime(state_changed_at)

        ack_by_user: Dict[int, MatchAcknowledgment] = {
            a.user_id: a for a in (acknowledgments or [])
        }
        acknowledgments_summary = []
        for p in match.players:
            user_id = getattr(p, 'user_id', None) or getattr(p.user, 'id', None)
            ack = ack_by_user.get(user_id) if user_id is not None else None
            acknowledged = ack is not None and ack.acknowledged_at is not None
            ts_display = (
                format_local_display(ack.acknowledged_at)
                if acknowledged and ack and ack.acknowledged_at else ''
            )
            discord_id = getattr(p.user, 'discord_id', None)
            acknowledgments_summary.append({
                'name': p.user.preferred_name,
                'acknowledged': acknowledged,
                'auto': bool(ack and ack.auto_acknowledged),
                'ts': ts_display,
                'discord_id': str(discord_id) if discord_id else None,
            })

        return {
            'id': match.id,
            'title': match.title or '',
            'description': getattr(match, 'description', None) or '',
            'description_text': plain_text(parse_user_blocks(desc)) if (desc := getattr(match, 'description', None)) else '',
            'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
            'tournament': match.tournament.name if match.tournament else '',
            'tournament_short': match.tournament.short_label if match.tournament else '',
            'bracket': self._bracket_ref(match),
            'set_label': match.title if getattr(match, 'crew_hold_source', None) else None,
            'scheduled_at': format_local_datetime(match.scheduled_at) if match.scheduled_at else '',
            'scheduled_ts': to_utc_aware(match.scheduled_at).timestamp() if match.scheduled_at else None,
            'started_ts': to_utc_aware(match.started_at).timestamp() if match.started_at else None,
            'finished_ts': to_utc_aware(match.finished_at).timestamp() if match.finished_at else None,
            'match_length': getattr(match.tournament, 'average_match_duration', None),
            'is_overdue': bool(
                match.scheduled_at
                and match.seated_at is None
                and match.finished_at is None
                and to_utc_aware(match.scheduled_at) < datetime.now(timezone.utc)
            ),
            'state': state,
            'needs_review': match.needs_review,
            # Must stay the predicate confirm_match enforces: a Finished match can have no result.
            'has_result': has_recorded_result(match.players) or closes_without_winner(match.players),
            'forfeit': reason.label if (reason := getattr(match, 'forfeit_reason', None)) else '',
            'state_timestamp': state_timestamp,
            'state_time': format_local_time(state_changed_at),
            'players': [
                {
                    'id': p.id,
                    'name': p.user.preferred_name,
                    'user_id': p.user_id,
                    'finish_rank': p.finish_rank,
                    'station': p.assigned_station,
                    'discord_id': str(p.user.discord_id) if p.user.discord_id else None,
                    'seed_ready': getattr(p, 'seed_ready_at', None) is not None,
                }
                for p in match.players
            ],
            'placeholders': list(getattr(match, 'bracket_placeholders', None) or []),
            'bracket_link_lost': bool(getattr(match, 'bracket_link_lost', False)),
            'crew_hold': getattr(match, 'crew_hold_source', None) is not None,
            'late': [] if match.seated_at or match.finished_at else [
                {
                    'name': p.user.preferred_name,
                    'user_id': p.user_id,
                    'tag': late_tag(p),
                    'said': format_local_time(p.late_at) if p.late_at else '',
                    'note': p.late_note or '',
                }
                for p in match.players if getattr(p, 'late_minutes', None) is not None  # type: ignore[attr-defined]
            ],
            'late_window': late_window_open(match),
            'seed_ready_open': seed_ready_open(match) and rolling_task is None,
            'seed_ready_label': (
                '{} of {} ready'.format(*ready_count(match))
                if seed_ready_open(match) and rolling_task is None else ''
            ),
            'acknowledgments': acknowledgments_summary,
            'stage': match.stage.name if match.stage else '',
            # The id, not the name: the board's Stage select needs the value the service takes back.
            'stage_id': match.stage_id,  # type: ignore[attr-defined]
            'stage_url': (
                match.stage.stream_url
                if match.stage and match.stage.stream_url
                and match.stage.stream_url.lower().startswith(('http://', 'https://'))
                else ''
            ),
            'is_stream_candidate': match.is_stream_candidate,
            'stream_volunteers': list(stream_volunteers or []),
            'is_racetime': match.tournament.is_racetime_enabled if match.tournament else False,
            'seed': match.generated_seed.seed_url if match.generated_seed else '',
            'generated_seed': match.generated_seed.seed_url if match.generated_seed else '',
            'seed_code': seed_code_line(match.generated_seed.seed_code if match.generated_seed else None),
            'tournament_seed_generator': match.tournament.seed_generator if match.tournament else None,
            'seed_rolling_since': (
                to_utc_aware(rolling_task.created_at).isoformat()
                if rolling_task is not None else None
            ),
            # Server-rendered on purpose: a slot template has no ticking clock to recompute it.
            'seed_rolling_label': (
                rolling_elapsed_label(rolling_task.created_at)
                if rolling_task is not None else ''
            ),
            'seed_roll_error': (
                failed_task.error or 'The seed roll did not complete.'
                if failed_task is not None else ''
            ),
            # Must match the skip condition in _send_seed_dms.
            'seed_dm_blocked': [
                p.user.preferred_name for p in match.players
                if not (p.user.discord_id and p.user.dm_notifications)
            ],
            # Must match the open check CrewService.signup_crew enforces; withdrawing is deliberately ungated.
            'crew_signup_open': match.started_at is None and match.finished_at is None,
            'crew_wanted': {
                'commentators': (
                    match.tournament.required_commentators > 0 if match.tournament else True
                ),
                'trackers': (
                    match.tournament.required_trackers > 0 if match.tournament else True
                ),
                'broadcasters': need_broadcasters > 0,
            },
            'broadcasters_on': broadcasters_on,
            'crew_required': {
                **dict(zip(
                    ('commentators', 'trackers'),
                    crew_requirement(match.tournament) if match.stage_id else (0, 0),  # type: ignore[attr-defined]
                    strict=True,
                )),
                'broadcasters': need_broadcasters if match.stage_id else 0,  # type: ignore[attr-defined]
            },
            'commentators': [
                {
                    'name': c.user.preferred_name,
                    'approved': c.approved,
                    # str: raw snowflake ints lose precision as JS numbers, breaking == checks
                    'discord_id': str(c.user.discord_id) if c.user.discord_id else None,
                    'user_id': c.user_id,
                    'acknowledged': c.acknowledged_at is not None,
                    'ack_ts': format_local_display(c.acknowledged_at) if c.acknowledged_at else '',
                    'id': c.id,
                    'invited_by_id': getattr(c, 'invited_by_id', None),
                    'with': next((o.user.preferred_name for o in match.commentators  # type: ignore[attr-defined]
                                  if getattr(c, 'invited_by_id', None) and o.user_id == c.invited_by_id), ''),
                }
                for c in match.commentators
            ],
            'commentary_seats': (match.tournament.required_commentators or 0) if match.tournament else 0,
            'trackers': [
                {
                    'name': t.user.preferred_name,
                    'approved': t.approved,
                    'discord_id': str(t.user.discord_id) if t.user.discord_id else None,
                    'user_id': t.user_id,
                    'acknowledged': t.acknowledged_at is not None,
                    'ack_ts': format_local_display(t.acknowledged_at) if t.acknowledged_at else '',
                    'id': t.id,
                }
                for t in match.trackers
            ],
            'broadcasters': [
                {
                    'name': b.user.preferred_name,
                    'approved': b.approved,
                    'discord_id': str(b.user.discord_id) if b.user.discord_id else None,
                    'user_id': b.user_id,
                    'acknowledged': b.acknowledged_at is not None,
                    'ack_ts': format_local_display(b.acknowledged_at) if b.acknowledged_at else '',
                    'id': b.id,
                }
                for b in match.broadcasters  # type: ignore[attr-defined]
            ],
            # Keep these for backward compatibility with existing code that may reference them
            'seated': format_local_datetime(match.seated_at) if match.seated_at else '',
            'finished': format_local_datetime(match.finished_at) if match.finished_at else '',
        }
