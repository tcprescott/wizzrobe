"""
Reports Service - Business Logic Layer

One method per report. Day boundaries resolve on the viewer's display clock;
inputs and outputs are timezone-aware datetimes (UTC on the wire).
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from application.event_context import event_scoped
from application.services.reporting_shared import (
    DEFAULT_MATCH_DURATION_MIN,
    ON_TIME_THRESHOLD_MIN,
    crew_requirement,
    ensure_report_reader,
    is_crew_covered,
    is_outlier_duration,
    to_display,
    window_hours,
)
from application.services.system_config_service import SystemConfigService
from application.utils.timezone import combine_local
from models import (
    Match,
    Stage,
    User,
)

# How long an unchecked-in late match keeps sliding forward on the capacity
# forecast before it's treated as a no-show and stops growing.
LATE_PUSH_LIMIT = timedelta(hours=2)

# Players are asked to check in at the scheduled time; the race itself starts
# about this long after check-in (seat_delay/setup in the match operations
# report measure it). The capacity forecast plans unstarted matches with it.
CHECK_IN_TO_START = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# A start or check-in this long before its scheduled time is a data-entry
# artefact (a result recorded ahead of the slot), not a team arriving early.
IMPLAUSIBLE_EARLY_MIN = 6 * 60

_SEAT_PREFIXES = ('', 'staged_', 'unstaged_')


def _minutes_between(earlier: Optional[datetime], later: Optional[datetime]) -> Optional[int]:
    if earlier is None or later is None:
        return None
    return int((later - earlier).total_seconds() / 60)


def _implausible_times(match: Match, start_delay: Optional[int], seat_delay: Optional[int]) -> bool:
    """Whether this match's timestamps describe a click rather than a race.

    Closing a match as played on its bracket site stamps check-in, start and
    finish with the same instant, whenever staff get to it.
    """
    if any(d is not None and d < -IMPLAUSIBLE_EARLY_MIN for d in (start_delay, seat_delay)):
        return True
    return (
        match.seated_at is not None
        and match.seated_at == match.started_at == match.finished_at
    )


def _avg(values: List[int]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def _on_time_pct(delays: List[int]) -> Optional[float]:
    if not delays:
        return None
    on_time = sum(1 for d in delays if abs(d) <= ON_TIME_THRESHOLD_MIN)
    return round(on_time / len(delays) * 100, 1)


class _SeatTally:
    """Check-in delays and check-in-to-start times for one slice of a tournament."""

    def __init__(self) -> None:
        self.seat_delays: List[int] = []
        self.setups: List[int] = []

    def add(self, seat_delay: Optional[int], setup: Optional[int]) -> None:
        if seat_delay is not None:
            self.seat_delays.append(seat_delay)
        if setup is not None:
            self.setups.append(setup)

    def summary(self, prefix: str) -> Dict[str, Optional[float]]:
        return {
            f'{prefix}avg_seat_delay_min': _avg(self.seat_delays),
            f'{prefix}seated_on_time_pct': _on_time_pct(self.seat_delays),
            f'{prefix}avg_setup_min': _avg(self.setups),
        }


class ReportsService:
    """Generates aggregated views over matches, crew, stages, and audit logs."""

    # --- Capacity Forecast -------------------------------------------------

    @staticmethod
    def _local(dt: Optional[datetime]) -> Optional[datetime]:
        return to_display(dt)

    @staticmethod
    def _match_window(
        match: Match,
        now: Optional[datetime] = None,
        setup: timedelta = timedelta(0),
    ) -> Optional[Tuple[datetime, datetime]]:
        """The span a match holds its players at a setup.

        A late match nobody has checked in keeps sliding forward with ``now``
        (up to ``LATE_PUSH_LIMIT``, after which it reads as a no-show); a match
        checked in late runs from its check-in. ``now=None`` takes the schedule
        at face value. An unstarted match is expected to start ``setup`` after
        its check-in (actual or expected), so its estimated end moves with it.
        """
        scheduled = ReportsService._local(match.scheduled_at)
        if scheduled is None:
            return None
        seated = ReportsService._local(match.seated_at)
        now_local = ReportsService._local(now)
        if seated:
            start = seated
        else:
            start = scheduled - timedelta(hours=1)
        if match.finished_at:
            end = ReportsService._local(match.finished_at)
        else:
            duration = DEFAULT_MATCH_DURATION_MIN
            if match.tournament_id and match.tournament and match.tournament.average_match_duration:
                duration = match.tournament.average_match_duration
            if match.started_at:
                anchor = ReportsService._local(match.started_at)
            else:
                if seated:
                    check_in = max(scheduled, seated)
                elif now_local is not None and now_local > scheduled:
                    check_in = min(now_local, scheduled + LATE_PUSH_LIMIT)
                else:
                    check_in = scheduled
                anchor = check_in + setup
            end = anchor + timedelta(minutes=duration)
        if end < start:
            end = start
        return start, end

    @staticmethod
    def _auto_interval_minutes(start: datetime, end: datetime) -> int:
        span = end - start
        if span <= timedelta(hours=24):
            return 15
        if span <= timedelta(hours=72):
            return 30
        return 60

    async def generate_capacity_forecast(
        self,
        start: datetime,
        end: datetime,
        tournament_id: Optional[int] = None,
        *, actor: Optional[User],
    ) -> Dict:
        """Compute concurrent-player counts at intervals across [start, end]."""
        await ensure_report_reader(actor)
        start = self._local(start)
        end = self._local(end)
        if end < start:
            end = start
        interval_min = self._auto_interval_minutes(start, end)

        query = event_scoped(Match.filter(
            scheduled_at__gte=start - timedelta(hours=24),
            scheduled_at__lte=end + timedelta(hours=24),
        ))
        if tournament_id:
            query = query.filter(tournament_id=tournament_id)
        matches = await query.prefetch_related('tournament', 'players', 'stage')
        now = _now()

        windows: List[Tuple[datetime, datetime, int, Match]] = []
        for match in matches:
            window = self._match_window(match, now=now, setup=CHECK_IN_TO_START)
            if not window:
                continue
            ws, we = window
            if we < start or ws > end:
                continue
            if not match.stage_id and match.tournament and not match.tournament.uses_tournament_room:
                continue
            player_count = len(match.players)
            if player_count == 0 and match.tournament_id and match.tournament:
                player_count = match.tournament.players_per_match or 0
            windows.append((ws, we, player_count, match))

        intervals: List[datetime] = []
        player_counts: List[int] = []
        on_stream_counts: List[int] = []
        match_ids_per_interval: List[List[int]] = []

        current = start
        while current <= end:
            active = 0
            on_stream = 0
            ids: List[int] = []
            for ws, we, count, match in windows:
                if ws <= current <= we:
                    active += count
                    if match.stage_id:
                        on_stream += count
                    ids.append(match.id)
            intervals.append(current)
            player_counts.append(active)
            on_stream_counts.append(on_stream)
            match_ids_per_interval.append(ids)
            current += timedelta(minutes=interval_min)

        max_capacity = await SystemConfigService.get_max_concurrent_players()

        return {
            'intervals': intervals,
            'player_counts': player_counts,
            'on_stream_player_counts': on_stream_counts,
            'off_stream_player_counts': [a - o for a, o in zip(player_counts, on_stream_counts, strict=True)],
            'match_ids_per_interval': match_ids_per_interval,
            'interval_minutes': interval_min,
            'start': start,
            'end': end,
            'max_capacity': max_capacity,
        }

    @staticmethod
    def peak_times(
        intervals: List[datetime],
        counts: List[int],
        top_n: int = 5,
    ) -> List[Tuple[datetime, int]]:
        return sorted(zip(intervals, counts, strict=False), key=lambda x: x[1], reverse=True)[:top_n]

    async def matches_active_at(
        self,
        instant: datetime,
        tournament_id: Optional[int] = None,
        *, actor: Optional[User],
    ) -> List[Match]:
        await ensure_report_reader(actor)
        instant = self._local(instant)
        query = event_scoped(Match.filter(
            scheduled_at__gte=instant - timedelta(hours=24),
            scheduled_at__lte=instant + timedelta(hours=24),
        ))
        if tournament_id:
            query = query.filter(tournament_id=tournament_id)
        matches = await query.prefetch_related('tournament', 'players', 'players__user', 'stage')
        now = _now()
        out: List[Match] = []
        for match in matches:
            window = self._match_window(match, now=now, setup=CHECK_IN_TO_START)
            if not window:
                continue
            ws, we = window
            if ws <= instant <= we:
                out.append(match)
        return out

    # --- Match Operations --------------------------------------------------

    async def match_operations(
        self,
        start: datetime,
        end: datetime,
        tournament_id: Optional[int] = None,
        *, actor: Optional[User],
    ) -> Dict:
        await ensure_report_reader(actor)
        start = self._local(start)
        end = self._local(end)
        query = event_scoped(Match.filter(
            scheduled_at__gte=start,
            scheduled_at__lte=end,
        ))
        if tournament_id:
            query = query.filter(tournament_id=tournament_id)
        matches = await query.prefetch_related('tournament', 'stage', 'players')

        rows: List[Dict] = []
        per_tournament: Dict[int, Dict] = {}
        flagged_ids: List[int] = []
        duration_outlier_ids: List[int] = []
        for match in matches:
            scheduled = self._local(match.scheduled_at)
            seated = self._local(match.seated_at)
            started = self._local(match.started_at)
            finished = self._local(match.finished_at)
            confirmed = self._local(match.confirmed_at)

            start_delay = _minutes_between(scheduled, started)
            seat_delay = _minutes_between(scheduled, seated)
            setup = _minutes_between(seated, started)
            duration = _minutes_between(started, finished)
            confirmation_lag = _minutes_between(finished, confirmed)
            staged = match.stage_id is not None

            flagged = _implausible_times(match, start_delay, seat_delay)
            if flagged:
                flagged_ids.append(match.id)
            duration_outlier = not flagged and is_outlier_duration(duration, match.tournament)
            if duration_outlier:
                duration_outlier_ids.append(match.id)

            rows.append({
                'match_id': match.id,
                'tournament_id': match.tournament_id,
                'tournament_name': match.tournament.name if match.tournament else '',
                'scheduled_at': scheduled,
                'state': match.current_state,
                'stage': match.stage.name if match.stage else '',
                'staged': staged,
                'start_delay_min': start_delay,
                'seat_delay_min': seat_delay,
                'setup_min': setup,
                'duration_min': duration,
                'confirmation_lag_min': confirmation_lag,
                'player_count': len(match.players),
            })

            t_id = match.tournament_id
            stats = per_tournament.setdefault(t_id, {
                'tournament_id': t_id,
                'tournament_name': match.tournament.name if match.tournament else '',
                'expected_avg_min': match.tournament.average_match_duration if match.tournament else None,
                'matches_total': 0,
                'matches_started': 0,
                'matches_finished': 0,
                'start_delays': [],
                'durations': [],
                'seat': {prefix: _SeatTally() for prefix in _SEAT_PREFIXES},
            })
            stats['matches_total'] += 1
            # Counted on the timestamp alone, as Insights counts them; the
            # averages below still need both ends of their interval.
            if started:
                stats['matches_started'] += 1
            if finished:
                stats['matches_finished'] += 1
            if flagged:
                continue
            if start_delay is not None:
                stats['start_delays'].append(start_delay)
            if duration is not None and not duration_outlier:
                stats['durations'].append(duration)
            for prefix in ('', 'staged_' if staged else 'unstaged_'):
                stats['seat'][prefix].add(seat_delay, setup)

        aggregates: List[Dict] = []
        for stats in per_tournament.values():
            aggregate = {
                'tournament_id': stats['tournament_id'],
                'tournament_name': stats['tournament_name'],
                'matches_total': stats['matches_total'],
                'matches_started': stats['matches_started'],
                'matches_finished': stats['matches_finished'],
                'avg_start_delay_min': _avg(stats['start_delays']),
                'avg_duration_min': _avg(stats['durations']),
                'expected_avg_min': stats['expected_avg_min'],
                'on_time_pct': _on_time_pct(stats['start_delays']),
            }
            for prefix, tally in stats['seat'].items():
                aggregate.update(tally.summary(prefix))
            aggregates.append(aggregate)

        return {
            'rows': rows,
            'aggregates': aggregates,
            'flagged_match_ids': flagged_ids,
            'duration_outlier_ids': duration_outlier_ids,
        }

    # --- Staff / Crew Activity ---------------------------------------------

    async def crew_coverage(
        self,
        start: datetime,
        end: datetime,
        tournament_id: Optional[int] = None,
        user_id: Optional[int] = None,
        approved_only: bool = False,
        *, actor: Optional[User],
    ) -> Dict:
        await ensure_report_reader(actor)
        start = self._local(start)
        end = self._local(end)

        match_query = event_scoped(Match.filter(
            scheduled_at__gte=start,
            scheduled_at__lte=end,
        ))
        if tournament_id:
            match_query = match_query.filter(tournament_id=tournament_id)
        from application.services.auth_service import AuthService

        scope = await AuthService.report_tournament_scope(actor)
        if scope is not None:
            match_query = match_query.filter(tournament_id__in=list(scope))
        matches = await match_query.prefetch_related(
            'tournament', 'stage',
            'commentators', 'commentators__user',
            'trackers', 'trackers__user',
        )

        coverage_rows: List[Dict] = []
        per_user: Dict[int, Dict] = {}
        per_tournament: Dict[int, Dict] = {}
        for match in matches:
            if match.stage_id is not None and match.finished_at is not None:  # type: ignore[attr-defined]
                _tally_crewed(per_tournament, match, user_id)

            comm_total = len(match.commentators)
            comm_approved = sum(1 for c in match.commentators if c.approved)
            trk_total = len(match.trackers)
            trk_approved = sum(1 for t in match.trackers if t.approved)
            need_commentators, need_trackers = crew_requirement(match.tournament)

            include_match = True
            if user_id is not None:
                touched_by_user = any(c.user_id == user_id for c in match.commentators) or \
                                  any(t.user_id == user_id for t in match.trackers)
                include_match = touched_by_user

            if include_match:
                coverage_rows.append({
                    'match_id': match.id,
                    'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
                    'tournament_name': match.tournament.name if match.tournament else '',
                    'scheduled_at': self._local(match.scheduled_at),
                    'stage': match.stage.name if match.stage else '',
                    'is_stream_candidate': match.is_stream_candidate,
                    'commentators_approved': comm_approved,
                    'commentators_total': comm_total,
                    'trackers_approved': trk_approved,
                    'trackers_total': trk_total,
                    'commentators_required': need_commentators,
                    'trackers_required': need_trackers,
                    'coverage_gap': match.is_stream_candidate and not is_crew_covered(
                        match.tournament, comm_approved, trk_approved
                    ),
                })

            duration_hours = 0.0
            if match.scheduled_at:
                window = self._match_window(match)
                if window:
                    ws, we = window
                    duration_hours = window_hours(ws, we)

            def bump(entry, slot_total, slot_approved, hours) -> None:
                entry[f'{slot_total}_total'] += 1
                if not approved_only:
                    entry['hours_total'] += hours
                if slot_approved:
                    entry[f'{slot_total}_approved'] += 1
                    entry['hours_covered'] += hours

            for c in match.commentators:
                if user_id is not None and c.user_id != user_id:
                    continue
                entry = per_user.setdefault(c.user_id, _new_crew_entry(c.user))
                bump(entry, 'commentator', c.approved, duration_hours)

            for t in match.trackers:
                if user_id is not None and t.user_id != user_id:
                    continue
                entry = per_user.setdefault(t.user_id, _new_crew_entry(t.user))
                bump(entry, 'tracker', t.approved, duration_hours)

        contribution_rows = []
        for entry in per_user.values():
            contribution_rows.append({
                'user_id': entry['user_id'],
                'name': entry['name'],
                'commentator_approved': entry['commentator_approved'],
                'commentator_total': entry['commentator_total'],
                'tracker_approved': entry['tracker_approved'],
                'tracker_total': entry['tracker_total'],
                'hours_covered': round(entry['hours_covered'], 1),
                'hours_total': round(entry['hours_total'], 1),
            })
        contribution_rows.sort(key=lambda r: (-r['hours_covered'], r['name']))

        return {
            'coverage_rows': coverage_rows,
            'contribution_rows': contribution_rows,
            'tournament_rows': _tournament_rows(per_tournament),
        }

    # --- Stage Utilization ------------------------------------------

    async def stage_utilization(
        self,
        start: datetime,
        end: datetime,
        tournament_id: Optional[int] = None,
        stage_id: Optional[int] = None,
        *, actor: Optional[User],
    ) -> Dict:
        await ensure_report_reader(actor)
        start = self._local(start)
        end = self._local(end)

        stages = await event_scoped(Stage.filter(is_active=True)).order_by('name')
        if stage_id:
            stages = [r for r in stages if r.id == stage_id]

        match_query = event_scoped(Match.filter(
            scheduled_at__gte=start,
            scheduled_at__lte=end,
        ))
        if tournament_id:
            match_query = match_query.filter(tournament_id=tournament_id)
        matches = await match_query.prefetch_related('tournament', 'stage')

        unplaced_candidates = [
            m for m in matches
            if m.is_stream_candidate and m.stage_id is None
        ]

        per_stage: Dict[int, Dict] = {}
        for stage in stages:
            per_stage[stage.id] = {
                'stage_id': stage.id,
                'stage_name': stage.name,
                'scheduled_hours': 0.0,
                'back_to_back_count': 0,
                'gap_hours': 0.0,
                'matches': [],
            }

        for match in matches:
            if match.stage_id is None or match.stage_id not in per_stage:
                continue
            window = self._match_window(match)
            if not window:
                continue
            ws, we = window
            ws = max(ws, start)
            we = min(we, end)
            if we <= ws:
                continue
            block = per_stage[match.stage_id]
            block['scheduled_hours'] += (we - ws).total_seconds() / 3600.0
            block['matches'].append({
                'match_id': match.id,
                'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
                'tournament_name': match.tournament.name if match.tournament else '',
                'start': ws,
                'end': we,
                'scheduled_at': self._local(match.scheduled_at),
            })

        for block in per_stage.values():
            block['matches'].sort(key=lambda m: m['start'])
            prev_end = None
            gap_total = 0.0
            for m in block['matches']:
                if prev_end is not None:
                    gap = (m['start'] - prev_end).total_seconds() / 60.0
                    if gap < 15 and gap >= -1:
                        block['back_to_back_count'] += 1
                    if gap > 0:
                        gap_total += gap / 60.0
                prev_end = m['end']
            block['gap_hours'] = round(gap_total, 1)
            block['scheduled_hours'] = round(block['scheduled_hours'], 1)

        return {
            'stages': list(per_stage.values()),
            'unplaced_candidate_count': len(unplaced_candidates),
            'unplaced_candidates': unplaced_candidates,
        }


def _new_crew_entry(user: Optional[User]) -> Dict:
    return {
        'user_id': user.id if user else None,
        'name': (user.preferred_name if user else 'Unknown'),
        'commentator_approved': 0,
        'commentator_total': 0,
        'tracker_approved': 0,
        'tracker_total': 0,
        'hours_covered': 0.0,
        'hours_total': 0.0,
    }


def _tally_crewed(per_tournament: Dict[int, Dict], match: Match, user_id: Optional[int]) -> None:
    """Count a match's approved crew toward its tournament.

    Only a finished match that went to a stage counts: without a stage there was
    no broadcast, so approved crew on it never actually commentated or tracked.
    """
    group = per_tournament.setdefault(match.tournament_id, {  # type: ignore[attr-defined]
        'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
        'tournament_name': match.tournament.name if match.tournament else '',
        'people': {},
    })
    for slot, rows in (('commentary', match.commentators), ('tracker', match.trackers)):
        for row in rows:
            if not row.approved or (user_id is not None and row.user_id != user_id):
                continue
            person = group['people'].setdefault(row.user_id, {
                'user_id': row.user_id,
                'name': row.user.preferred_name if row.user else 'Unknown',
                'commentary': 0,
                'tracker': 0,
            })
            person[slot] += 1


def _tournament_rows(per_tournament: Dict[int, Dict]) -> List[Dict]:
    rows = []
    for group in per_tournament.values():
        people = [
            {**p, 'total': p['commentary'] + p['tracker']}
            for p in group['people'].values()
        ]
        if not people:
            continue
        people.sort(key=lambda p: (-p['total'], -p['commentary'], p['name'].lower()))
        rows.append({
            'tournament_id': group['tournament_id'],
            'tournament_name': group['tournament_name'],
            'commentary': sum(p['commentary'] for p in people),
            'tracker': sum(p['tracker'] for p in people),
            'people': people,
        })
    rows.sort(key=lambda r: r['tournament_name'].lower())
    return rows


def event_day_bounds(d: date) -> Tuple[datetime, datetime]:
    """Midnight-to-next-midnight UTC bounds for a date on the display clock."""
    start = combine_local(d, time(0, 0))
    end = start + timedelta(days=1)
    return start, end
