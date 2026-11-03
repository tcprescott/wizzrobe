"""What the app's Event tab shows: the hub, the run sheet and the Watch page.

Each read returns what one screen draws. The stage cards and tournament cards
are shared by the hub and Watch; the run sheet rows are cut down from
``MatchDisplayService``'s board rows to what a spectator, player or crew
member acts on. Times are UTC instants; the app prints them on its own clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from application.services.bracket_service import BracketService
from application.services.event_info_service import prize_fields
from application.services.feature_flag_service import FeatureFlagService
from application.services.match.match_display_service import MatchDisplayService
from application.services.match.match_service import MatchService
from application.services.match.match_watcher_service import MatchWatcherService
from application.services.stage_service import StageService
from application.services.tournament_service import TournamentService
from application.utils.match_labels import is_undecided_side
from application.utils.safe_url import http_url_or_empty
from application.utils.timezone import to_utc_aware
from application.utils.twitch_embed import twitch_channel, twitch_channel_name
from models import BracketState, FeatureFlag, Match, User

MAX_WINDOW = timedelta(hours=48)
UP_NEXT = 5
COMING_UP = 12
COMING_UP_DAYS = timedelta(days=7)

STATUS = {
    'Scheduled': 'scheduled',
    'Checked In': 'checked_in',
    'Started': 'live',
    'Finished': 'finished',
    'Confirmed': 'finished',
}

CREW_ROLES = (
    ('commentators', 'commentator', 'Comm'),
    ('trackers', 'tracker', 'Tracker'),
    ('broadcasters', 'broadcaster', 'Broadcast'),
)

_CHALLONGE_STATUS = {
    'pending': ('Not started', 'neutral'),
    'checking_in': ('Not started', 'neutral'),
    'checked_in': ('Not started', 'neutral'),
    'accepting_predictions': ('Not started', 'neutral'),
    'group_stages_underway': ('Groups underway', 'live'),
    'group_stages_finalized': ('Groups finished', 'pending'),
    'underway': ('Bracket underway', 'live'),
    'awaiting_review': ('Results coming', 'pending'),
    'complete': ('Complete', 'ok'),
}


@dataclass
class Side:
    name: str
    undecided: bool = False


@dataclass
class CrewSlot:
    role: str
    label: str
    needed: int
    filled: int
    names: List[str]
    yours: Optional[str] = None
    can_sign_up: bool = False


@dataclass
class RunSheetRow:
    match_id: int
    scheduled_at: Optional[datetime]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    status: str
    tournament_id: Optional[int]
    tournament: str
    round: str
    sides: List[Side]
    stage: str
    stage_id: Optional[int]
    stations: List[str]
    streamed: bool
    crew: List[CrewSlot]
    playing: bool = False
    watching: bool = False


@dataclass
class StreamMatch:
    match_id: int
    scheduled_at: Optional[datetime]
    started_at: Optional[datetime]
    tournament_id: Optional[int]
    tournament: str
    sides: List[Side]
    stage: str = ''


@dataclass
class StageCard:
    id: int
    name: str
    stream_url: Optional[str]
    channel: Optional[str]
    channel_name: Optional[str]
    on_now: Optional[StreamMatch]
    up_next: List[StreamMatch] = field(default_factory=list)


@dataclass
class TournamentCard:
    id: int
    name: str
    format: str
    status: str
    tone: str
    has_bracket: bool
    bracket_url: Optional[str]
    rules_url: Optional[str]
    prize_total: Optional[Any] = None


@dataclass
class HubView:
    stages: List[StageCard]
    up_next: List[RunSheetRow]
    tournaments: List[TournamentCard]


@dataclass
class WatchView:
    stages: List[StageCard]
    coming_up: List[StreamMatch]
    tournaments: List[TournamentCard]


def _instant(ts: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None


def _sides(names: List[str], placeholders: List[str]) -> List[Side]:
    return [Side(n) for n in names] + [Side(p, is_undecided_side(p)) for p in placeholders]


def _match_sides(match: Match) -> List[Side]:
    return _sides(
        [p.user.preferred_name for p in match.players],  # type: ignore[attr-defined]
        list(getattr(match, 'bracket_placeholders', None) or []),
    )


def _round(row: Dict[str, Any]) -> str:
    if row.get('title'):
        return row['title']
    if row.get('set_label'):
        return row['set_label']
    ref = row.get('bracket')
    if not ref:
        return ''
    parts = [ref['name']]
    if ref.get('game'):
        parts.append(f"Game {ref['game']} of {ref['best_of']}" if ref.get('best_of', 1) > 1 else f"Game {ref['game']}")
    if ref.get('standing'):
        parts.append(ref['standing'])
    return ' · '.join(parts)


def _crew(row: Dict[str, Any], viewer: Optional[User], playing: bool) -> List[CrewSlot]:
    slots: List[CrewSlot] = []
    required = row.get('crew_required') or {}
    wanted = row.get('crew_wanted') or {}
    for key, role, label in CREW_ROLES:
        people = row.get(key) or []
        needed = int(required.get(key) or 0)
        if not needed and not people:
            continue
        approved = [p['name'] for p in people if p['approved']]
        mine = next((p for p in people if viewer is not None and p['user_id'] == viewer.id), None)
        yours = None if mine is None else ('approved' if mine['approved'] else 'pending')
        slots.append(CrewSlot(
            role=role, label=label, needed=needed, filled=len(approved), names=approved, yours=yours,
            can_sign_up=bool(
                viewer is not None and yours is None and not playing
                and row.get('crew_signup_open') and wanted.get(key)
            ),
        ))
    return slots


def run_sheet_row(row: Dict[str, Any], viewer: Optional[User], watching: set) -> RunSheetRow:
    playing = viewer is not None and any(p['user_id'] == viewer.id for p in row['players'])
    stations = sorted({p['station'] for p in row['players'] if p.get('station')})
    return RunSheetRow(
        match_id=row['id'],
        scheduled_at=_instant(row.get('scheduled_ts')),
        started_at=_instant(row.get('started_ts')),
        finished_at=_instant(row.get('finished_ts')),
        status=STATUS.get(row['state'], 'scheduled'),
        tournament_id=row.get('tournament_id'),
        tournament=row.get('tournament') or '',
        round=_round(row),
        sides=_sides([p['name'] for p in row['players']], row.get('placeholders') or []),
        stage=row.get('stage') or '',
        stage_id=row.get('stage_id'),
        stations=stations,
        streamed=bool(row.get('stage_url')),
        crew=_crew(row, viewer, playing),
        playing=playing,
        watching=row['id'] in watching,
    )


def _stream_match(match: Match, stage: str = '') -> StreamMatch:
    return StreamMatch(
        match_id=match.id,
        scheduled_at=to_utc_aware(match.scheduled_at) if match.scheduled_at else None,
        started_at=to_utc_aware(match.started_at) if match.started_at else None,
        tournament_id=match.tournament_id,  # type: ignore[attr-defined]
        tournament=match.tournament.name if match.tournament else '',
        sides=_match_sides(match),
        stage=stage,
    )


def _stage_order(card: StageCard) -> tuple:
    """Channel in natural order (SpeedGaming, SpeedGaming2 … SpeedGaming10), unstreamed stages last."""
    channel = (card.channel or '').lower()
    parts = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in re.split(r'(\d+)', channel) if p)
    return (not channel, parts, card.name.lower())


def _tournament_status(tournament, stages: List) -> tuple[str, str]:
    if stages:
        states = {b.state for b in stages}
        if BracketState.ACTIVE in states:
            return 'Bracket underway', 'live'
        if states <= {BracketState.COMPLETE, BracketState.CANCELLED}:
            return 'Complete', 'ok'
        return 'Not started', 'neutral'
    return _CHALLONGE_STATUS.get(tournament.challonge_state or '', ('', 'neutral'))


class EventHubService:
    """Screen-shaped reads for the app's Event tab."""

    async def run_sheet(self, viewer: Optional[User], start: datetime, end: datetime) -> List[RunSheetRow]:
        """Every match scheduled in ``[start, end)``, soonest first. The window is capped at 48 hours."""
        start, end = to_utc_aware(start), to_utc_aware(end)
        if end <= start:
            raise ValueError('The end has to be after the start.')
        if end - start > MAX_WINDOW:
            raise ValueError('Ask for 48 hours or less at a time.')
        rows = await MatchDisplayService().get_matches_for_display(scheduled_window=(start, end))
        watching = set(await MatchWatcherService().list_watched_match_ids(viewer)) if viewer else set()
        out = [run_sheet_row(r, viewer, watching) for r in rows if not r.get('crew_hold')]
        far = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(out, key=lambda r: (r.scheduled_at or far, r.match_id))

    async def stages(self, matches: Optional[List[Match]] = None) -> List[StageCard]:
        """Each active stage with what's on it now and next, from now until the same time tomorrow."""
        if matches is None:
            matches = await self._streamed(timedelta(days=1))
        by_stage: Dict[int, List[Match]] = {}
        for match in matches:
            by_stage.setdefault(match.stage_id, []).append(match)  # type: ignore[attr-defined]
        cards: List[StageCard] = []
        for stage in await StageService().get_all_stages(active_only=True):
            todays = by_stage.get(stage.id, [])
            if not stage.stream_url and not todays:
                continue
            seated = [m for m in todays if m.is_seated or m.is_started]
            waiting = [m for m in todays if not (m.is_seated or m.is_started)]
            cards.append(StageCard(
                id=stage.id,
                name=stage.name,
                stream_url=http_url_or_empty(stage.stream_url) or None,
                channel=twitch_channel(stage.stream_url),
                channel_name=twitch_channel_name(stage.stream_url),
                on_now=_stream_match(seated[-1]) if seated else None,
                up_next=[_stream_match(m) for m in waiting[:2]],
            ))
        return sorted(cards, key=_stage_order)

    async def tournaments(self) -> List[TournamentCard]:
        """The event's active tournaments, each with its bracket status and links."""
        flags = await FeatureFlagService().enabled_flags()
        published: Dict[int, List] = {}
        if FeatureFlag.BRACKETS in flags:
            for bracket in await BracketService().list_all_visible_brackets(None):
                published.setdefault(bracket.tournament_id, []).append(bracket)
        cards: List[TournamentCard] = []
        for t in await TournamentService().get_all_tournaments(active_only=True):
            if t.is_test:
                continue
            stages = published.get(t.id, [])
            site = (
                (FeatureFlag.STARTGG in flags and bool(t.startgg_event_slug))
                or (FeatureFlag.CHALLONGE in flags and bool(t.challonge_tournament_id))
            )
            status, tone = _tournament_status(t, stages)
            prize = prize_fields(t) if FeatureFlag.PAYOUTS in flags else {}
            cards.append(TournamentCard(
                id=t.id,
                name=t.name,
                format=t.tournament_format or '',
                status=status,
                tone=tone,
                has_bracket=bool(stages) or site,
                bracket_url=(
                    http_url_or_empty(t.bracket_url) or http_url_or_empty(t.challonge_tournament_url) or None
                ),
                rules_url=http_url_or_empty(t.rules_url) or None,
                prize_total=prize.get('prize_total'),
            ))
        return sorted(cards, key=lambda c: c.name.lower())

    async def hub(self, viewer: Optional[User]) -> HubView:
        now = datetime.now(timezone.utc)
        rows = await self.run_sheet(viewer, now - timedelta(hours=6), now + timedelta(hours=24))
        up_next = [r for r in rows if r.status in ('scheduled', 'checked_in') and r.scheduled_at and r.scheduled_at >= now - timedelta(hours=1)]
        return HubView(
            stages=await self.stages(),
            up_next=up_next[:UP_NEXT],
            tournaments=await self.tournaments(),
        )

    async def watch(self) -> WatchView:
        matches = await self._streamed(COMING_UP_DAYS)
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        soon = [m for m in matches if to_utc_aware(m.scheduled_at) < tomorrow]
        stages = await self.stages(soon)
        shown = {m.match_id for s in stages for m in ([s.on_now] if s.on_now else []) + s.up_next}
        names = {s.id: s.name for s in await StageService().get_all_stages(active_only=True)}
        coming = [
            _stream_match(m, names.get(m.stage_id, ''))  # type: ignore[attr-defined]
            for m in matches if m.id not in shown and not (m.is_seated or m.is_started)
        ][:COMING_UP]
        return WatchView(stages=stages, coming_up=coming, tournaments=await self.tournaments())

    @staticmethod
    async def _streamed(span: timedelta) -> List[Match]:
        now = datetime.now(timezone.utc)
        matches = await MatchService().streamed_between(now - timedelta(hours=12), now + span)
        return sorted(matches, key=lambda m: (to_utc_aware(m.scheduled_at), m.id))
