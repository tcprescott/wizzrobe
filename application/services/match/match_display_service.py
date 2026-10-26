"""
Match Display Service - Read/Formatting Layer

View-model assembly for the match tables: reads matches (and their
acknowledgments) via repositories and formats them into the plain dicts the
NiceGUI table slots consume. Extracted from ``MatchService`` so the latter
keeps only lifecycle logic.

Read-only: no writes, no audit, no Discord notifications.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from application.content import parse_user_blocks, plain_text
from application.repositories import (
    MatchAcknowledgmentRepository,
    MatchRepository,
    MatchStreamVolunteerRepository,
    ProviderTaskRepository,
    StageRepository,
    TournamentRepository,
)
from application.services.bracket_set_service import BracketSetService
from application.services.match.match_status import (
    closes_without_winner,
    has_recorded_result,
    legacy_label,
    resolve,
)
from application.services.match.player_late_service import late_tag, late_window_open
from application.services.reporting_shared import crew_requirement
from application.utils.discord_messages import seed_code_line
from application.utils.timezone import (
    format_local_datetime,
    format_local_display,
    format_local_time,
    to_utc_aware,
)
from models import Match, MatchAcknowledgment, ProviderTask


def rolling_elapsed_label(started_at: datetime) -> str:
    """"Rolling… 1:24" — how long an in-flight seed roll has been going.

    Elapsed rather than a percentage or an ETA because the provider gives
    nothing to base one on: its status endpoint reports queued/started/finished
    and no position in the queue. A counting clock is the only honest thing to
    show, and it is what tells someone the page is working rather than stuck.
    """
    seconds = max(0, int((datetime.now(timezone.utc) - to_utc_aware(started_at)).total_seconds()))
    return f'Rolling… {seconds // 60}:{seconds % 60:02d}'


#: The states in which a rolled seed can still be needed at a room PC. Started
#: is in on purpose: a seed rerolled mid-race has to be downloadable there, so a
#: match leaves the room view when it finishes, not when it starts.
ROOM_SEED_STATES = ('Scheduled', 'Checked In', 'Started')

# The Stage filter's "No stage" choice. Stage ids start at 1, so 0 never names
# a real stage and can ride in the same multi-select list as the real ones.
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
        )
        await self._attach_site_results([row], [match])
        await self._attach_set_labels([row], [match])
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
        # One query for the whole page, not one per row: the seed cell asks "is
        # this match rolling?" of every row it draws.
        rolling_map, failed_map = await self.provider_task_repository.board_state_for_matches(
            match_ids_loaded,
        )
        rows = [
            self._format_match_for_display(
                m, ack_map.get(m.id, []), volunteer_map.get(m.id, []),
                rolling_map.get(m.id), failed_map.get(m.id),
            )
            for m in matches
        ]
        await self._attach_site_results(rows, matches)
        await self._attach_set_labels(rows, matches)
        return rows

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

        # Only a tournament linked to a bracket site can have one, and the
        # tournament is already loaded: a board with none costs no query.
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
            Dict mapping tournament ID to name
        """
        return await self.tournament_repository.get_all_as_dict()

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
            # Only meaningful in a best-of-N series; game 1 of 1 is just "the match".
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
    ) -> Dict[str, Any]:
        """Format a match object for UI display."""
        state = self._get_match_state(match)

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
            # Not rendered as a column — it is how the surfaces *name* the match
            # in their copy, so a confirmation dialog can say what it is about
            # instead of quoting a primary key (``match_labels.match_row_label``).
            'title': match.title or '',
            # Staff markdown, rendered on the match sheet; the flattened text is
            # the run sheet's one-line summary of it.
            'description': getattr(match, 'description', None) or '',
            'description_text': plain_text(parse_user_blocks(desc)) if (desc := getattr(match, 'description', None)) else '',
            # Per-row board capabilities are scoped by tournament (MatchBoardAccess).
            'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
            'tournament': match.tournament.name if match.tournament else '',
            # The run sheets' label; the full name rides along as its tooltip.
            'tournament_short': match.tournament.short_label if match.tournament else '',
            # None unless a bracket scheduled this match; drives the schedule's
            # link into the (publicly readable) bracket view.
            'bracket': self._bracket_ref(match),
            # "Game 2 of 3" comes from the set's other games: _attach_set_labels.
            # A crew hold's round ("Grand final") rides here, beside the tournament.
            'set_label': match.title if getattr(match, 'crew_hold_source', None) else None,
            'scheduled_at': format_local_datetime(match.scheduled_at) if match.scheduled_at else '',
            # Sort key and urgency flag for the proctor board. The formatted
            # ``scheduled_at`` string is display-only and does not sort.
            'scheduled_ts': to_utc_aware(match.scheduled_at).timestamp() if match.scheduled_at else None,
            # Once a match starts, the run sheets show these instead of the
            # booked time: the actual start, then the finish, or while it runs
            # an estimate of start + the tournament's match length.
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
            # The proctor's dispute flag: "an admin should look at this before
            # confirming". Cleared by confirming.
            'needs_review': match.needs_review,
            # Whether a result is on the board. A match can be Finished with
            # nothing recorded, and ``confirm_match`` refuses that — so the
            # surfaces gate the Confirm control on this rather than on the state
            # alone, instead of offering a button whose only outcome is a refusal.
            # The *same* predicate the service enforces, not a second spelling of
            # it: see ``has_recorded_result``. An open race confirms with none
            # (``closes_without_winner``).
            'has_result': has_recorded_result(match.players) or closes_without_winner(match.players),
            # "No-show" etc. when staff decided it by forfeit; the player left
            # without a finish rank is the one who forfeited.
            'forfeit': reason.label if (reason := getattr(match, 'forfeit_reason', None)) else '',
            'state_timestamp': state_timestamp,
            'state_time': format_local_time(state_changed_at),
            'players': [
                {
                    'name': p.user.preferred_name,
                    'user_id': p.user_id,
                    'finish_rank': p.finish_rank,
                    'station': p.assigned_station,
                    'discord_id': str(p.user.discord_id) if p.user.discord_id else None,
                }
                for p in match.players
            ],
            # A booking made before the bracket decided its players: what stands
            # in for each empty side ("Andy or Benny"), and whether a reset cut
            # it loose from its matchup.
            'placeholders': list(getattr(match, 'bracket_placeholders', None) or []),
            'bracket_link_lost': bool(getattr(match, 'bracket_link_lost', False)),
            # Crew signed up for a bracket matchup nobody has booked yet: this
            # match only holds their signups (``CrewHoldService``).
            'crew_hold': getattr(match, 'crew_hold_source', None) is not None,
            # Players who said they're running late, while it still matters:
            # check-in makes the heads-up moot, so it drops off the row then.
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
            'acknowledgments': acknowledgments_summary,
            'stage': match.stage.name if match.stage else '',
            # The id, not just the name: the board's Stage cell is a select whose
            # value is the assigned stage, so it needs what the service takes back.
            'stage_id': match.stage_id,  # type: ignore[attr-defined]
            'stage_url': (
                match.stage.stream_url
                if match.stage and match.stage.stream_url
                and match.stage.stream_url.lower().startswith(('http://', 'https://'))
                else ''
            ),
            'is_stream_candidate': match.is_stream_candidate,
            # Players who put this match forward for stream. Advisory: it does
            # not set is_stream_candidate and does not book anything — staff read
            # it while they build the stream schedule. Names rather than a count,
            # because "one of the two asked" is the interesting case.
            'stream_volunteers': list(stream_volunteers or []),
            # Online (racetime.gg) tournaments run remotely, so the table hides
            # on-site-only controls (check-in, station assignment) for their rows.
            'is_racetime': match.tournament.is_racetime_enabled if match.tournament else False,
            'seed': match.generated_seed.seed_url if match.generated_seed else '',
            'generated_seed': match.generated_seed.seed_url if match.generated_seed else '',
            # The file-select code racers compare before starting, as one line.
            'seed_code': seed_code_line(match.generated_seed.seed_code if match.generated_seed else None),
            'tournament_seed_generator': match.tournament.seed_generator if match.tournament else None,
            # When an in-flight task-queue roll started, so the cell can show a
            # live elapsed clock. Server-side and therefore shared: it survives a
            # refresh and every viewer sees the same roll in progress, unlike the
            # per-browser flag it replaces. ``None`` whenever nothing is rolling.
            'seed_rolling_since': (
                to_utc_aware(rolling_task.created_at).isoformat()
                if rolling_task is not None else None
            ),
            # Rendered server-side rather than computed in the Vue slot: a slot
            # template has no ticking clock of its own, so the board refreshes
            # this label on a timer while any row is rolling.
            'seed_rolling_label': (
                rolling_elapsed_label(rolling_task.created_at)
                if rolling_task is not None else ''
            ),
            # Why the last roll produced nothing. Without this the row reverts to
            # a plain Generate button, which is exactly what an unrolled match
            # looks like — so the person who clicked and walked away is never
            # told it broke. Only meaningful while the match has no seed; a
            # later successful roll fills that in and the cell stops asking.
            'seed_roll_error': (
                failed_task.error or 'The seed roll did not complete.'
                if failed_task is not None else ''
            ),
            # Players the seed DM cannot reach — no linked Discord account, or
            # DMs opted out. This is deliverability, not delivery: it mirrors the
            # skip condition in ``_send_seed_dms`` and says nothing about whether
            # any DM was actually sent or read. ``players__user`` is already
            # prefetched, so this costs no extra query.
            'seed_dm_blocked': [
                p.user.preferred_name for p in match.players
                if not (p.user.discord_id and p.user.dm_notifications)
            ],
            # Whether crew signup is still open on this match — the *same*
            # predicate ``CrewService.signup_crew`` enforces, not a second
            # spelling of it, so the table stops offering a control whose only
            # outcome is a refusal. Withdrawing is deliberately not gated on it.
            'crew_signup_open': match.started_at is None and match.finished_at is None,
            # Which crew roles this tournament actually uses. A tournament that
            # requires none of a role has no use for volunteers in it, so the
            # table stops offering Sign up (existing signups still render, and
            # CrewService refuses a signup that gets here another way).
            'crew_wanted': {
                'commentators': (
                    match.tournament.required_commentators > 0 if match.tournament else True
                ),
                'trackers': (
                    match.tournament.required_trackers > 0 if match.tournament else True
                ),
            },
            # How much approved crew the match needs, per role, so the home
            # board's Needs crew switch can tell a covered match from an open
            # one. Same numbers (and same no-tournament default) as the
            # coverage report's ``crew_requirement``. Crew only works a match
            # on a stage, so a match without one needs none: every board,
            # filter and checklist reads "needed" from this.
            'crew_required': dict(zip(
                ('commentators', 'trackers'),
                crew_requirement(match.tournament) if match.stage_id else (0, 0),  # type: ignore[attr-defined]
                strict=True,
            )),
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
                }
                for c in match.commentators
            ],
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
            # Keep these for backward compatibility with existing code that may reference them
            'seated': format_local_datetime(match.seated_at) if match.seated_at else '',
            'finished': format_local_datetime(match.finished_at) if match.finished_at else '',
        }
