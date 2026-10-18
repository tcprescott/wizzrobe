"""
Match Suggestion Service - Business Logic Layer

Suggests a match start time: the earliest slot in the next 4 hours where both
players are free and the room has space, falling back to the rest of the event
when nothing in that range fits.

The room is filled early rather than spread thin: a slot that keeps concurrent
players within ``_HEADROOM`` of ``max_concurrent_players`` beats any later one,
and the cap itself is never crossed. Spreading evenly across the event pushed
group matches to its last night and left no room for later rounds. A slot that
overlaps any of the players' other matches is never offered.

Capacity information is never surfaced to callers — the suggestion appears as a
neutral "best time".

Player availability is read opt-out: a slot is eligible unless a player has
blocked it, and a slot inside a player's PREFERRED window scores higher. A
player who has entered nothing constrains nothing.

A bracket matchup narrows that search to its round's window
(``Bracket.config['rounds'][str(round)]``): pass ``bracket_match_id`` and the
suggestion is a **hard bound** — only slots the match fits inside wholly are
offered, and "nothing fits" raises rather than silently drifting outside the
round. Either half of the window may stand alone (a start alone is a floor, an
end alone a deadline), so rounds configured before ``scheduled_end`` existed keep
working.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

from application.repositories.bracket_repository import BracketRepository
from application.repositories.player_availability_repository import PlayerAvailabilityRepository
from application.services.auth_service import AuthService
from application.services.player_availability_service import PlayerAvailabilityService
from application.services.round_schedule_service import RoundScheduleService
from application.services.round_windows import RoundWindow
from application.services.system_config_service import SystemConfigService
from application.services.timezone_service import TimezoneService
from application.tenant_context import require_tenant_id
from application.timezone_context import get_zone
from application.utils.timezone import now_local, parse_local_datetime, to_local
from models import Match, PlayerAvailability, Tournament, User, VolunteerAvailabilityStatus

_SLOT_INTERVAL_MIN = 30
_PRIMARY_WINDOW_HOURS = 4
# Fill the room to this share of its cap before going any fuller, so walk-ups
# and overruns still have somewhere to go.
_HEADROOM = 0.75
_DEFAULT_CAPACITY = 60


class _Booking(NamedTuple):
    start: datetime
    end: datetime
    player_count: int
    user_ids: FrozenSet[int]


class MatchSuggestionService:
    """Suggests match start times that fit the players and the room."""

    def __init__(self) -> None:
        self.availability_repository = PlayerAvailabilityRepository()
        self.bracket_repository = BracketRepository()

    @staticmethod
    async def _ensure_may_suggest(actor: User, tournament_id: int, player_ids: List[int]) -> None:
        from application.services.tenant_membership_service import TenantMembershipService

        if actor.id not in player_ids and not (
            await AuthService.is_staff(actor)
            or await AuthService.is_tournament_admin(actor, tournament_id)
        ):
            raise PermissionError('You can only ask for a time for a match you play in.')
        await TenantMembershipService.require_community_users(player_ids, actor)

    async def suggest_match_time(
        self,
        tournament_id: int,
        player_ids: List[int],
        bracket_match_id: Optional[int] = None,
        *,
        exclude_match_id: Optional[int] = None,
        actor: Optional[User] = None,
        challonge_match_id: Optional[int] = None,
        startgg_set_id: Optional[int] = None,
    ) -> datetime:
        """Return a UTC datetime representing the best slot for the match.

        Primary search: next 4 hours within today's tournament-hours window.
        Fallback search: full remaining event window if primary yields no eligible slot.

        ``bracket_match_id`` clamps both searches to the matchup's round window,
        as a hard bound: the match must fit inside it end to end.
        ``challonge_match_id`` and ``startgg_set_id`` do the same from the
        tournament's round schedule (``RoundScheduleService``), daily hours
        included; with none of the three, a rescheduled bracket match
        (``exclude_match_id``) is held to its own stage's window.

        ``exclude_match_id`` is the match being rescheduled, whose current slot
        must not count against its own players or the room.

        Raises ValueError if no eligible slot is found at all.

        ``actor`` is required from REST and MCP: a member may ask about a match
        they're in, Staff and the tournament's admins about anyone. Otherwise
        the answer, or its "no slot" refusal, reads out other players' private
        availability one probe at a time.
        """
        if actor is not None:
            await self._ensure_may_suggest(actor, tournament_id, player_ids)
        tournament = await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id())
        duration_min = (tournament.average_match_duration or 90) if tournament else 90
        duration = timedelta(minutes=duration_min)

        hours_map = await SystemConfigService.get_tournament_hours(tournament)
        event_start, event_end = await SystemConfigService.get_event_window(tournament)

        # The whole search is laid out on the community's clock, because the
        # operating hours and event window that bound it are written in that
        # clock. Only the returned instant is timezone-free.
        tz_name = await TimezoneService.tenant_timezone_name()
        tz = get_zone(tz_name)
        now = now_local(tz)

        round_start, round_end = await self._round_window(bracket_match_id, tz)
        stage = await RoundScheduleService().window_for(
            bracket_match_id=bracket_match_id, challonge_match_id=challonge_match_id,
            startgg_set_id=startgg_set_id,
            match_id=exclude_match_id if bracket_match_id is None else None,
        )
        stage_window = stage.window if stage is not None else None
        if stage_window is not None and bracket_match_id is None:
            round_start = stage_window.opens.astimezone(tz) if stage_window.opens else None
            round_end = stage_window.closes.astimezone(tz) if stage_window.closes else None
        in_round = round_start is not None or round_end is not None or stage_window is not None
        # A round that opens later moves the whole search forward; one that has
        # already opened leaves the "no earlier than now" floor alone.
        if round_start is not None:
            now = max(now, round_start)
        if round_end is not None and now + duration > round_end:
            raise ValueError(
                "This round's scheduled window has no room left for a match."
            )

        existing_matches = await Match.filter(
            scheduled_at__isnull=False, confirmed_at__isnull=True,
            tenant_id=require_tenant_id(),
        ).prefetch_related('tournament', 'players')
        existing_matches = [m for m in existing_matches if m.id != exclude_match_id]
        capacity = await SystemConfigService.get_max_concurrent_players(_DEFAULT_CAPACITY)

        event_start_dt = parse_local_datetime(event_start.isoformat(), '00:00', tz)
        event_end_dt = parse_local_datetime(event_end.isoformat(), '23:59', tz)
        avail_map = await self._build_availability_map(player_ids, event_start_dt, event_end_dt)

        # --- Primary search: next 4 hours ---
        primary_end = now + timedelta(hours=_PRIMARY_WINDOW_HOURS)
        primary_candidates = self._in_stage_hours(self._generate_candidates(
            now, primary_end, hours_map, duration, event_start, event_end, tz, round_end,
        ), stage_window, duration, tz)
        result = self._best_candidate(
            primary_candidates, player_ids, avail_map, existing_matches, duration, capacity,
        )
        if result is not None:
            return result

        # --- Fallback search: full remaining event window ---
        fallback_candidates = self._in_stage_hours(self._generate_candidates(
            now, None, hours_map, duration, event_start, event_end, tz, round_end,
        ), stage_window, duration, tz)
        primary_set = {s for s, _ in primary_candidates}
        fallback_candidates = [(s, e) for s, e in fallback_candidates if s not in primary_set]
        result = self._best_candidate(
            fallback_candidates, player_ids, avail_map, existing_matches, duration, capacity,
        )
        if result is not None:
            return result

        if in_round:
            raise ValueError(
                "No available slot found for these players inside this round's "
                "scheduled window."
            )
        raise ValueError("No available slot found for these players within the event schedule.")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _in_stage_hours(
        candidates: List[Tuple[datetime, datetime]], window: Optional[RoundWindow],
        duration: timedelta, tz,
    ) -> List[Tuple[datetime, datetime]]:
        """Drop slots outside a stage's daily hours (its open/close already bound the grid)."""
        if window is None or not window.has_daily_hours:
            return candidates
        return [(s, e) for s, e in candidates if window.slot_fits(s, duration, tz)]

    async def _round_window(
        self, bracket_match_id: Optional[int], tz,
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """The matchup's round window as ``(start, end)`` on ``tz``, or ``(None, None)``.

        Absent config — no bracket match, no ``rounds`` blob, no entry for this
        round, or a half left blank — is an absent bound, not an error: a round
        nobody has scheduled must not stop its matchups being scheduled.
        """
        if bracket_match_id is None:
            return None, None
        bracket_match = await self.bracket_repository.get_match_with_bracket(bracket_match_id)
        if bracket_match is None:
            return None, None
        rounds = (bracket_match.bracket.config or {}).get('rounds') or {}
        cfg = rounds.get(str(bracket_match.round)) or {}
        return (
            _parse_config_dt(cfg.get('scheduled_at'), tz),
            _parse_config_dt(cfg.get('scheduled_end'), tz),
        )

    def _generate_candidates(
        self,
        from_dt: datetime,
        to_dt: Optional[datetime],
        hours_map,
        duration: timedelta,
        event_start,
        event_end,
        tz,
        hard_end: Optional[datetime] = None,
    ) -> List[Tuple[datetime, datetime]]:
        """Generate (slot_start, slot_end) pairs at 30-min intervals, on ``tz``.

        ``tz`` is the community's zone, not the viewer's: the slots are bounded by
        the community's operating hours, so they must be laid out on the clock
        those hours are written in.

        ``hard_end`` is the round window's close, when the caller is scheduling a
        bracket matchup: a slot only survives if the whole match fits before it.
        """

        candidates: List[Tuple[datetime, datetime]] = []
        current_date = from_dt.date()
        end_date = to_dt.date() if to_dt else event_end
        if hard_end is not None:
            end_date = min(end_date, hard_end.date())

        while current_date <= end_date:
            if current_date < event_start or current_date > event_end:
                current_date += timedelta(days=1)
                continue

            window = hours_map.get(current_date)
            if window is None:
                if hours_map:
                    # Some days are configured — skip unconfigured days entirely
                    current_date += timedelta(days=1)
                    continue
                # No hours configured at all → treat every day as open all day
                from datetime import time as time_type
                open_t, close_t = time_type(0, 0), time_type(23, 30)
            else:
                open_t, close_t = window
            day_open = datetime(
                current_date.year, current_date.month, current_date.day,
                open_t.hour, open_t.minute, tzinfo=tz,
            )
            day_close = datetime(
                current_date.year, current_date.month, current_date.day,
                close_t.hour, close_t.minute, tzinfo=tz,
            )

            slot = max(day_open, from_dt)
            slot = self._round_up_to_interval(slot)

            while slot + duration <= day_close:
                # Stay within the to_dt ceiling on the last day
                if to_dt and slot >= to_dt:
                    break
                if hard_end is not None and slot + duration > hard_end:
                    break
                candidates.append((slot, slot + duration))
                slot += timedelta(minutes=_SLOT_INTERVAL_MIN)

            current_date += timedelta(days=1)

        return candidates

    def _best_candidate(
        self,
        candidates: List[Tuple[datetime, datetime]],
        player_ids: List[int],
        avail_map,
        existing_matches: List[Match],
        duration: timedelta,
        capacity: int = _DEFAULT_CAPACITY,
    ) -> Optional[datetime]:
        """Score candidates and return the best UTC start time, or None.

        A slot is out when a player blocked it, when a player is already in a
        match that overlaps it, or when adding this match would take the room
        past ``capacity`` concurrent players at any moment of the slot.

        Of the rest, the earliest slot that keeps the room within its headroom
        wins; only when none does is the room filled up to the cap. A player's
        PREFERRED window can win over an earlier slot on the same day, never
        over an earlier day.
        """
        bookings = self._bookings(existing_matches, duration)
        wanted = frozenset(player_ids)
        scored: List[Tuple] = []

        for slot_start, slot_end in candidates:
            preferred_count = 0
            eligible = True
            for pid in player_ids:
                windows: List[PlayerAvailability] = avail_map.get(pid, [])
                status = _covers(windows, slot_start, slot_end)
                if status == VolunteerAvailabilityStatus.UNAVAILABLE:
                    eligible = False
                    break
                if status == VolunteerAvailabilityStatus.PREFERRED:
                    preferred_count += 1

            if not eligible:
                continue

            overlapping = [b for b in bookings if b.start < slot_end and b.end > slot_start]
            if any(b.user_ids & wanted for b in overlapping):
                continue
            load = self._peak_load(overlapping, slot_start) + len(player_ids)
            if load > capacity:
                continue
            band = 0 if load <= capacity * _HEADROOM else 1
            scored.append((band, slot_start.date(), -preferred_count, slot_start))

        if not scored:
            return None

        scored.sort()
        return scored[0][-1].astimezone(dt_timezone.utc)

    @staticmethod
    def _bookings(matches: List[Match], duration: timedelta) -> List[_Booking]:
        """Each scheduled match as its occupied window and who is in it."""
        out: List[_Booking] = []
        for m in matches:
            if not m.scheduled_at:
                continue
            start = to_local(m.scheduled_at)
            dur = duration
            if m.tournament and m.tournament.average_match_duration:
                dur = timedelta(minutes=m.tournament.average_match_duration)
            players = list(m.players)
            out.append(_Booking(
                start, start + dur, len(players),
                frozenset(uid for uid in (getattr(p, 'user_id', None) for p in players) if uid is not None),
            ))
        return out

    @staticmethod
    def _peak_load(overlapping: Sequence[_Booking], slot_start: datetime) -> int:
        """The most players on the floor at once during the slot.

        Load only rises when a match starts, so the slot's own start and every
        overlapping match's start inside it are the only moments to check.
        """
        moments = [slot_start] + [b.start for b in overlapping if b.start > slot_start]
        return max(
            (sum(b.player_count for b in overlapping if b.start <= t < b.end) for t in moments),
            default=0,
        )

    @staticmethod
    def _round_up_to_interval(dt: datetime) -> datetime:
        """Round dt up to the nearest _SLOT_INTERVAL_MIN boundary."""
        minutes = dt.minute
        remainder = minutes % _SLOT_INTERVAL_MIN
        if remainder == 0 and dt.second == 0:
            return dt.replace(second=0, microsecond=0)
        add = _SLOT_INTERVAL_MIN - remainder
        return (dt + timedelta(minutes=add)).replace(second=0, microsecond=0)

    async def _build_availability_map(
        self, player_ids: List[int], start: datetime, end: datetime,
    ) -> dict[int, list]:
        rows = await self.availability_repository.for_users_overlapping(player_ids, start, end)
        out = {}
        for row in rows:
            out.setdefault(row.user_id, []).append(row)
        return out


def _parse_config_dt(value: Optional[str], tz) -> Optional[datetime]:
    """A stored UTC ISO string from the round blob → an aware datetime on ``tz``.

    The blob is schema-validated on the way in, so a bad string here means data
    that predates the validator; it degrades to "no bound" rather than breaking
    the suggestion.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed.astimezone(tz)


def _covers(
    windows: Sequence[PlayerAvailability], start: datetime, end: datetime,
) -> Optional[VolunteerAvailabilityStatus]:
    """Opt-out reading of a player's windows: a slot they have said nothing
    about is one they can play."""
    return PlayerAvailabilityService.covers(windows, start, end)
