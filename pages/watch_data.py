"""What the Watch page (``/live``) shows, loaded once per change for everyone.

A crowd watching the same event reads the same page, so the queries run once
per change and every reader, live or static, renders from the result. Freshness
works the way :mod:`pages.static_brackets` does it: a per-tenant version bumped
by ``MATCH_*``/``BRACKET_*``/``TOURNAMENT_*`` events, plus a short TTL for what
no event announces (a match's time passing, a Challonge sync). Live pages poll
:func:`watch_version` and repaint when it moves, which costs a dict lookup and
leaves the one rebuild to whoever asks first.

Player names are public here, like everywhere a spectator looks.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from application.events import EventType, event_bus
from application.services import BracketService, MatchService, StageService, TournamentService
from application.services.event_info_service import prize_fields
from application.services.feature_flag_service import FeatureFlagService
from application.services.system_config_service import (
    DEFAULT_WATCH_AUTO_THRESHOLD,
    KEY_WATCH_AUTO_STATIC,
    KEY_WATCH_AUTO_THRESHOLD,
    KEY_WATCH_MODE,
    SystemConfigService,
)
from application.services.tenant_service import TenantService
from application.services.tenant_theme_service import TenantThemeService
from application.services.timezone_service import TimezoneService
from application.tenant_context import tenant_scope
from application.timezone_context import tz_scope
from application.utils.safe_url import http_url_or_empty
from application.utils.timezone import (
    format_local_time,
    timezone_label,
    to_local,
    today_local,
)
from application.utils.twitch_embed import twitch_channel, twitch_channel_name
from models import BracketState, FeatureFlag
from theme.join_page import roster_label
from theme.watch_view import (
    COMING_UP_LIMIT,
    UP_NEXT_LIMIT,
    WatchDay,
    WatchMatch,
    WatchStage,
    WatchTournament,
    WatchView,
)

logger = logging.getLogger(__name__)

#: Days of streamed matches the page looks ahead, today included.
COMING_UP_DAYS = 7
_TTL_SECONDS = 30
#: How long a read of the mode settings is reused. Every arrival needs them,
#: and a rush is exactly when three queries per request would hurt.
_SETTINGS_TTL_SECONDS = 5

#: Challonge's bracket states, in a spectator's words and a chip tone.
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

_INVALIDATING_EVENTS = tuple(
    name for name in EventType.ALL
    if name.startswith(('match.', 'bracket.', 'tournament.'))
)


@dataclass
class _Entry:
    view: WatchView
    version: int
    stored_at: float


_entries: Dict[int, _Entry] = {}
_versions: Dict[Optional[int], int] = {}
_locks: Dict[int, asyncio.Lock] = {}
_subscribed = False


def watch_version(tenant_id: int) -> int:
    """Moves whenever something the page shows may have changed."""
    return _versions.get(tenant_id, 0) + _versions.get(None, 0)


def invalidate(tenant_id: Optional[int]) -> None:
    """Mark one tenant's content stale (``None``: every tenant's)."""
    _versions[tenant_id] = _versions.get(tenant_id, 0) + 1


def _on_change(event) -> None:
    invalidate(event.tenant_id)


def subscribe() -> None:
    global _subscribed
    if not _subscribed:
        event_bus.subscribe_sync(_on_change, _INVALIDATING_EVENTS)
        _subscribed = True


def _fresh(entry: Optional[_Entry], tenant_id: int) -> bool:
    return (
        entry is not None
        and entry.version == watch_version(tenant_id)
        and time.monotonic() - entry.stored_at <= _TTL_SECONDS
    )


async def get_watch_view(tenant_id: int) -> WatchView:
    """The current content, built at most once per change however many ask."""
    entry = _entries.get(tenant_id)
    if _fresh(entry, tenant_id):
        return entry.view  # type: ignore[union-attr]
    lock = _locks.setdefault(tenant_id, asyncio.Lock())
    async with lock:
        entry = _entries.get(tenant_id)
        if _fresh(entry, tenant_id):
            return entry.view  # type: ignore[union-attr]
        version = watch_version(tenant_id)
        with tenant_scope(tenant_id):
            view = await build_watch_view(tenant_id)
        _entries[tenant_id] = _Entry(view, version, time.monotonic())
        return view


@dataclass(frozen=True)
class WatchSettings:
    mode: str = 'live'
    auto_static: bool = True
    threshold: int = DEFAULT_WATCH_AUTO_THRESHOLD


_settings: Dict[int, tuple[float, WatchSettings]] = {}


async def watch_settings(tenant_id: int) -> WatchSettings:
    """Staff's Watch page settings, reused for a few seconds."""
    cached = _settings.get(tenant_id)
    if cached is not None and time.monotonic() - cached[0] <= _SETTINGS_TTL_SECONDS:
        return cached[1]
    with tenant_scope(tenant_id):
        settings = WatchSettings(
            mode=(await SystemConfigService.get_raw(KEY_WATCH_MODE)) or 'live',
            auto_static=await SystemConfigService.get_bool(KEY_WATCH_AUTO_STATIC, default=True),
            threshold=await SystemConfigService.get_int(
                KEY_WATCH_AUTO_THRESHOLD, DEFAULT_WATCH_AUTO_THRESHOLD,
            ) or DEFAULT_WATCH_AUTO_THRESHOLD,
        )
    _settings[tenant_id] = (time.monotonic(), settings)
    return settings


def forget_settings(tenant_id: int) -> None:
    """Drop the cached settings, so a save takes effect on the next arrival."""
    _settings.pop(tenant_id, None)


def _crew(signups) -> str:
    return ', '.join(c.user.preferred_name for c in (signups or []) if c.approved)


def _row(match, stage_name: str = '') -> WatchMatch:
    return WatchMatch(
        time=format_local_time(match.scheduled_at) if match.scheduled_at else 'TBD',
        players=roster_label(match),
        tournament=match.tournament.name if match.tournament else '',
        stage=stage_name,
        commentators=_crew(match.commentators),
        trackers=_crew(match.trackers),
        match_id=match.id,
        tournament_id=match.tournament_id,
        started_at=match.started_at,
    )


def _stage_order(stage: WatchStage) -> tuple:
    """Channel name in natural order (SpeedGaming, SpeedGaming2 … SpeedGaming10), unstreamed stages last."""
    channel = (stage.channel or '').lower()
    parts = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in re.split(r'(\d+)', channel) if p)
    return (not channel, parts, stage.name.lower())


def _status(tournament, stages: List) -> tuple[str, str]:
    if stages:
        states = {b.state for b in stages}
        if BracketState.ACTIVE in states:
            return 'Bracket underway', 'live'
        if states <= {BracketState.COMPLETE, BracketState.CANCELLED}:
            return 'Complete', 'ok'
        return 'Not started', 'neutral'
    return _CHALLONGE_STATUS.get(tournament.challonge_state or '', ('', 'neutral'))


async def _tournaments(streamed_ids: set[int]) -> List[WatchTournament]:
    published: Dict[int, List] = {}
    if await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS):
        # Anonymous, always: a DRAFT or CANCELLED stage is not public.
        for bracket in await BracketService().list_all_visible_brackets(None):
            published.setdefault(bracket.tournament_id, []).append(bracket)

    payouts_live = await FeatureFlagService().is_enabled(FeatureFlag.PAYOUTS)
    rows: List[WatchTournament] = []
    for t in await TournamentService().get_all_tournaments(active_only=True):
        stages = published.get(t.id, [])
        if stages:
            bracket = f'/live/tournament/{t.id}/brackets'
        else:
            bracket = http_url_or_empty(t.bracket_url) or http_url_or_empty(t.challonge_tournament_url)
        # Listed when a spectator has something to follow: a bracket to open or
        # a match on stream. A tournament with neither is staff's business yet.
        if not bracket and t.id not in streamed_ids:
            continue
        status, tone = _status(t, stages)
        prize = prize_fields(t) if payouts_live else {}
        rows.append(WatchTournament(
            id=t.id, name=t.name, format=t.tournament_format or '',
            status=status, tone=tone, bracket_url=bracket,
            rules_url=http_url_or_empty(t.rules_url),
            prize_total=prize.get('prize_total'),
            prize_synced=prize.get('prize_synced_at') is not None,
        ))
    return sorted(rows, key=lambda r: r.name.lower())


async def build_watch_view(tenant_id: int) -> WatchView:
    """Load everything the Watch page shows, on the community's clock."""
    tz = await TimezoneService.tenant_timezone_name(tenant_id)
    stages = sorted(await StageService().get_all_stages(active_only=True), key=lambda s: s.name)

    with tz_scope(tz):
        today = today_local()
        matches = await MatchService().get_streamed_matches(today, COMING_UP_DAYS)
        by_stage: Dict[int, list] = {}
        later = []
        for match in matches:
            if to_local(match.scheduled_at).date() == today:  # type: ignore[union-attr]
                by_stage.setdefault(match.stage_id, []).append(match)  # type: ignore[attr-defined]
            else:
                later.append(match)

        watch_stages: List[WatchStage] = []
        overflow = []
        for stage in stages:
            todays = by_stage.get(stage.id, [])
            if not stage.stream_url and not todays:
                continue
            # One stream shows one match. A second seated match on the same
            # stage is a result nobody recorded yet, not a second broadcast.
            seated = [m for m in todays if m.is_seated or m.is_started]
            waiting = [m for m in todays if not (m.is_seated or m.is_started)]
            watch_stages.append(WatchStage(
                id=stage.id,
                name=stage.name,
                stream_url=http_url_or_empty(stage.stream_url),
                channel=twitch_channel(stage.stream_url),
                channel_name=twitch_channel_name(stage.stream_url),
                on_now=_row(seated[-1]) if seated else None,
                up_next=[_row(m) for m in waiting[:UP_NEXT_LIMIT]],
            ))
            overflow.extend(waiting[UP_NEXT_LIMIT:])

        watch_stages.sort(key=_stage_order)
        stage_name = {s.id: s.name for s in stages}
        coming: List[WatchDay] = []
        upcoming = sorted(overflow, key=lambda m: m.scheduled_at) + later
        for match in upcoming[:COMING_UP_LIMIT]:
            day = to_local(match.scheduled_at).date()  # type: ignore[union-attr]
            label = 'Later today' if day == today else f'{day:%A}, {day:%B} {day.day}'
            if not coming or coming[-1].label != label:
                coming.append(WatchDay(label=label))
            coming[-1].matches.append(_row(match, stage_name.get(match.stage_id, '')))
        zone = timezone_label()

    try:
        primary = (await TenantThemeService.get_current_theme()).get('primary')
    except Exception:
        logger.debug('Watch page: falling back to the default palette', exc_info=True)
        primary = None

    return WatchView(
        community=await TenantService.current_community_name() or 'Wizzrobe',
        stages=watch_stages,
        coming=coming,
        tournaments=await _tournaments({m.tournament_id for m in matches}),  # type: ignore[attr-defined]
        timezone_label=zone,
        primary_color=primary,
        generated_at=datetime.now(timezone.utc),
    )
