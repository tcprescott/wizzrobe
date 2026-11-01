"""What the app's Home and Schedule tabs show a player, as one read each.

The NiceGUI My Schedule builds these lists inside the page from half a dozen
services. The app gets them already merged: the viewer's matches, crew slots
and shifts in time order, the "Needs you" to-dos, and one booking path that
hides which bracket site a matchup came from.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from application.repositories import MatchAcknowledgmentRepository, MatchRepository
from application.services import bracket_sources
from application.services.bracket_service import BracketService
from application.services.challonge_service import ChallongeService
from application.services.event_service import EventService, with_event_name
from application.services.feature_flag_service import FeatureFlagService
from application.services.match.match_suggestion_service import MatchSuggestionService
from application.services.match.player_late_service import late_window_open
from application.services.match_reschedule_service import MatchRescheduleService
from application.services.my_timeline_service import CREW, MATCH, SHIFT, MyTimelineService, TimelineItem
from application.services.round_schedule_service import RoundScheduleService, RoundWindowError
from application.services.timezone_service import MODE_PINNED, TimezoneService
from application.timezone_context import is_valid_timezone
from application.utils.game_order import remaining_slots
from application.utils.match_labels import challonge_round_label, entrant_name
from application.utils.timezone import to_local, to_utc_aware
from models import FeatureFlag, Match, User

logger = logging.getLogger(__name__)

BRACKET = 'bracket'
CHALLONGE = 'challonge'
SOURCES = (BRACKET, CHALLONGE, 'startgg')

NEED_BOOK = 'book'
NEED_AGREE = 'agree'
NEED_ACKNOWLEDGE = 'acknowledge'

STATUS_SCHEDULED = 'scheduled'
STATUS_CHECKED_IN = 'checked_in'
STATUS_LIVE = 'live'
STATUS_FINISHED = 'finished'


@dataclass
class ScheduleEntry:
    kind: str
    starts_at: datetime
    ends_at: datetime
    title: str
    detail: str
    match_id: Optional[int] = None
    assignment_id: Optional[int] = None
    status: Optional[str] = None
    stage: Optional[str] = None
    station: Optional[str] = None
    room_url: Optional[str] = None
    needs_acknowledgment: bool = False
    late_minutes: Optional[int] = None
    can_report_late: bool = False
    approved: Optional[bool] = None
    checked_in: bool = False


@dataclass
class Bookable:
    """One matchup waiting on the player to pick a time, whichever site it lives on."""

    source: str
    key: int
    tournament_id: int
    tournament: str
    source_label: Optional[str]
    opponent: str
    round_label: str
    window_text: Optional[str] = None
    blocked: Optional[str] = None
    if_needed: bool = False
    player_ids: List[int] = field(default_factory=list)
    best_of: int = 1
    game_number: Optional[int] = None
    event_id: Optional[int] = None


@dataclass
class Need:
    kind: str
    title: str
    detail: str
    match_id: Optional[int] = None
    request_id: Optional[int] = None
    bookable: Optional[Bookable] = None
    proposed_at: Optional[datetime] = None
    original_at: Optional[datetime] = None
    reason: Optional[str] = None
    agreed: bool = False


@dataclass
class HomeView:
    zone: Optional[str]
    next_up: Optional[ScheduleEntry]
    needs_you: List[Need]
    today: List[ScheduleEntry]


@dataclass
class TimeCheck:
    refusal: Optional[str]
    clashes: List[str]


def _status(match: Match) -> str:
    if match.finished_at is not None or match.confirmed_at is not None:
        return STATUS_FINISHED
    if match.started_at is not None:
        return STATUS_LIVE
    if match.seated_at is not None:
        return STATUS_CHECKED_IN
    return STATUS_SCHEDULED


def _room_url(match: Match) -> Optional[str]:
    room = getattr(match, 'racetime_room', None)
    if room is None or getattr(room, 'status', None) is None:
        return None
    if str(getattr(room.status, 'value', room.status)) not in ('open', 'in_progress'):
        return None
    return room.url


class PlayerHomeService:
    def __init__(self) -> None:
        self.timeline = MyTimelineService()
        self.match_repository = MatchRepository()
        self.ack_repository = MatchAcknowledgmentRepository()
        self.reschedule_service = MatchRescheduleService()

    @staticmethod
    async def viewer_zone(user: User) -> Optional[str]:
        """The zone the app should read times on, or None for the device's own.

        A pinned community clock wins; otherwise the person's saved choice.
        Without either, the device's zone is the browser-detected one the
        website would use.
        """
        settings = await TimezoneService.get_settings()
        if settings.get('mode') == MODE_PINNED:
            return settings['name']
        preferred = getattr(user, 'timezone', None)
        return preferred if is_valid_timezone(preferred) else None

    async def schedule(self, user: User, *, past: bool = False) -> List[ScheduleEntry]:
        """The viewer's matches, crew slots and shifts in this event, soonest first.

        ``past`` flips to what has already ended, latest first.
        """
        live = await FeatureFlagService().enabled_flags()
        items = await self.timeline.items(user, include_shifts=FeatureFlag.VOLUNTEERS in live)
        items = [i for i in items if i.kind in (MATCH, CREW, SHIFT)]
        now = datetime.now(timezone.utc)
        entries = await self._entries(user, items)
        if past:
            done = [e for e in entries if e.ends_at <= now or e.status == STATUS_FINISHED]
            return sorted(done, key=lambda e: e.starts_at, reverse=True)
        return [e for e in entries if e.ends_at > now and e.status != STATUS_FINISHED]

    async def home(self, user: User) -> HomeView:
        upcoming = await self.schedule(user)
        mine = [e for e in upcoming if e.kind == MATCH]
        next_up = mine[0] if mine else None
        now_local = to_local(datetime.now(timezone.utc))
        today = [
            e for e in upcoming
            if e is not next_up and (to_local(e.starts_at) or e.starts_at).date() == now_local.date()  # type: ignore[union-attr]
        ]
        return HomeView(
            zone=await self.viewer_zone(user),
            next_up=next_up,
            needs_you=await self.needs_you(user, upcoming),
            today=today,
        )

    async def needs_you(self, user: User, upcoming: Optional[List[ScheduleEntry]] = None) -> List[Need]:
        if upcoming is None:
            upcoming = await self.schedule(user)
        needs: List[Need] = []
        for item in await self.bookable(user):
            needs.append(Need(
                kind=NEED_BOOK,
                title=f'Pick a time vs {item.opponent}',
                detail=' · '.join(p for p in (item.tournament, item.round_label, item.window_text) if p),
                bookable=item,
            ))
        for row in await self.reschedule_service.list_awaiting_agreement(user):
            match = row.match
            by = getattr(row.requested_by, 'preferred_name', None) or 'Your opponent'
            cancel = str(getattr(row.kind, 'value', row.kind)) == 'cancel'
            needs.append(Need(
                kind=NEED_AGREE,
                title=f'{by} asked to {"call off" if cancel else "move"} your match',
                detail=match.tournament.short_label if getattr(match, 'tournament', None) else '',
                match_id=match.id,
                request_id=row.id,
                proposed_at=to_utc_aware(row.proposed_at) if row.proposed_at else None,
                original_at=to_utc_aware(row.original_scheduled_at) if row.original_scheduled_at else None,
                reason=row.reason,
                agreed=row.opponent_agreed_at is not None,
            ))
        for entry in upcoming:
            if entry.kind == MATCH and entry.needs_acknowledgment:
                needs.append(Need(
                    kind=NEED_ACKNOWLEDGE,
                    title=f'Confirm you can play {entry.title}',
                    detail=entry.detail,
                    match_id=entry.match_id,
                ))
        return needs

    async def bookable(self, user: User) -> List[Bookable]:
        """The matchups waiting on ``user`` to pick a time.

        Each source only where the community runs it, as My Schedule lists
        them. A Challonge best-of-1 whose opponent has no account is listed
        with the reason it can't be booked yet.
        """
        live = await FeatureFlagService().enabled_flags()
        out: List[Bookable] = []
        external = await bracket_sources.list_bookable_for_user(user)
        sets = {x.key for x in external if x.source == CHALLONGE}
        if FeatureFlag.CHALLONGE in live and ChallongeService.is_configured():
            rounds = RoundScheduleService()
            for cm in await ChallongeService().list_unscheduled_matches_for_user(user):
                if cm.id in sets:
                    continue
                me_first = cm.participant1 is not None and cm.participant1.user_id == user.id
                opponent = cm.participant2 if me_first else cm.participant1
                linked = opponent is not None and opponent.user_id is not None
                name = entrant_name(opponent)
                out.append(Bookable(
                    source=CHALLONGE, key=cm.id,
                    tournament_id=cm.tournament.id, tournament=cm.tournament.name,
                    source_label='Challonge', opponent=name,
                    round_label=challonge_round_label(cm.round),
                    window_text=await rounds.describe_for_challonge_match(cm),
                    blocked=None if linked else f"Can't book yet: {name} isn't matched to a Wizzrobe account.",
                    player_ids=[p.user_id for p in (cm.participant1, cm.participant2) if p and p.user_id],
                    event_id=cm.tournament.event_id,
                ))
        for x in external:
            out.append(Bookable(
                source=x.source, key=x.key,
                tournament_id=x.tournament.id, tournament=x.tournament.name,
                source_label=x.source_label, opponent=x.opponent_name,
                round_label=x.round_label, window_text=x.window_text, if_needed=x.if_needed,
                player_ids=[p for p in x.player_ids if p], best_of=x.best_of,
                event_id=x.tournament.event_id,
            ))
        if FeatureFlag.BRACKETS in live:
            rounds = RoundScheduleService()
            open_matches: List[Any] = await BracketService().list_open_matches_for_user(user.id)
            for bm in open_matches:
                me_first = bm.entry1.entrant.user_id == user.id
                opponent = bm.entry2 if me_first else bm.entry1
                best_of = BracketService.resolve_best_of(bm.bracket, bm)
                taken = {g.game_number for g in bm.games}
                number = next((n for n in range(1, best_of + 1) if n not in taken), best_of)
                left = remaining_slots(best_of, len(taken), False) or [False]
                rnd = bm.round
                base = f'Losers round {abs(rnd)}' if rnd < 0 else f'Round {rnd}'
                out.append(Bookable(
                    source=BRACKET, key=bm.id,
                    tournament_id=bm.bracket.tournament_id, tournament=bm.bracket.tournament.name,
                    source_label=None, opponent=opponent.entrant.display_name,
                    round_label=bracket_sources.set_game_label(base, best_of, len(taken) + 1),
                    window_text=await rounds.describe_for_bracket_match(bm),
                    if_needed=bool(left[0]),
                    player_ids=[bm.entry1.entrant.user_id, bm.entry2.entrant.user_id],
                    best_of=best_of, game_number=number,
                    event_id=bm.bracket.tournament.event_id,
                ))
        names = await EventService.names_if_mixed(i.event_id for i in out)
        for item in out:
            if item.event_id in names:
                item.tournament = with_event_name(item.tournament, item.event_id, names)
        return out

    async def _own(self, user: User, source: str, key: int) -> Bookable:
        if source not in SOURCES:
            raise ValueError('Unknown bracket source.')
        found = next(
            (b for b in await self.bookable(user) if b.source == source and b.key == key), None,
        )
        if found is None:
            raise ValueError("That match isn't waiting on you to pick a time any more.")
        if found.blocked:
            raise ValueError(found.blocked)
        return found

    async def check_time(self, user: User, source: str, key: int, at: datetime) -> TimeCheck:
        """Why ``at`` might not work for this matchup, before anything is booked.

        ``refusal`` is the booking window the save would refuse outright;
        ``clashes`` are the warnings the website's "Check this time" sheet shows.
        """
        item = await self._own(user, source, key)
        refusal: Optional[str] = None
        ref: Dict[str, int] = {
            {BRACKET: 'bracket_match_id', CHALLONGE: 'challonge_match_id'}.get(source, 'startgg_set_id'): key,
        }
        try:
            await RoundScheduleService().check_reference(at, user, **ref)
        except RoundWindowError as e:
            refusal = None if e.can_override else str(e)
        clashes: List[str] = []
        try:
            found = await MatchSuggestionService().find_clashes(
                item.tournament_id, item.player_ids, at, actor=user,
            )
            clashes = [c.message for c in found]
        except Exception:
            logger.warning('Clash check failed for %s %s', source, key, exc_info=True)
        return TimeCheck(refusal=refusal, clashes=clashes)

    async def book(self, user: User, source: str, key: int, at: datetime) -> Match:
        """Book the viewer's next game on this matchup at ``at``.

        Best-of sets book one game per call, as a player does on the website.
        """
        item = await self._own(user, source, key)
        local = to_local(at)
        if local is None:
            raise ValueError('Choose a date and a time.')
        day, clock = local.strftime('%Y-%m-%d'), local.strftime('%H:%M')
        if source == BRACKET:
            return await BracketService().schedule_bracket_match(
                user, key, scheduled_date=day, scheduled_time=clock,
                game_number=item.game_number if item.best_of <= 1 else None,
            )
        if item.best_of > 1:
            return await bracket_sources.book_set_game(source, key, day, clock, user)
        return await bracket_sources.schedule(source, key, day, clock, user)

    async def _entries(self, user: User, items: List[TimelineItem]) -> List[ScheduleEntry]:
        match_ids = [i.match_id for i in items if i.match_id]
        matches: Dict[int, Any] = {m.id: m for m in await self.match_repository.get_for_player_cards(match_ids)}
        acks: Dict[int, List[Any]] = await self.ack_repository.list_for_matches(match_ids)
        now = datetime.now(timezone.utc)
        out: List[ScheduleEntry] = []
        for item in items:
            entry = ScheduleEntry(
                kind=item.kind, starts_at=item.starts_at, ends_at=item.ends_at,
                title=item.title, detail=item.detail,
            )
            match = matches.get(item.match_id) if item.match_id else None
            if match is not None:
                entry.match_id = match.id
                entry.status = _status(match)
                entry.stage = match.stage.name if match.stage_id and match.stage else None
                entry.room_url = _room_url(match)
                if item.kind == MATCH:
                    me = next((p for p in match.players if p.user_id == user.id), None)
                    entry.station = me.assigned_station if me else None
                    entry.late_minutes = me.late_minutes if me else None
                    entry.can_report_late = bool(me) and late_window_open(match, now)
                    entry.needs_acknowledgment = any(
                        a.user_id == user.id and a.acknowledged_at is None and not a.auto_acknowledged
                        for a in acks.get(match.id, [])
                    ) and entry.status == STATUS_SCHEDULED
                else:
                    entry.approved = item.approved
            assignment = item.assignment
            if assignment is not None:
                entry.assignment_id = assignment.id
                entry.checked_in = assignment.checked_in_at is not None
                entry.late_minutes = assignment.late_minutes
            out.append(entry)
        return out



__all__ = [
    'NEED_ACKNOWLEDGE',
    'NEED_AGREE',
    'NEED_BOOK',
    'Bookable',
    'HomeView',
    'Need',
    'PlayerHomeService',
    'ScheduleEntry',
    'TimeCheck',
]
