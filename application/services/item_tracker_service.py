"""The ALTTP item tracker: crew mark what a runner collects, the stream shows it.

A third party does the tracking, never the runner: an approved Tracker on the
match (or Staff, or a Broadcast Tech as backup) watches the runner's feed and
marks items on one board per runner. Every mark is a change row, which gives the
board an undo log and lets the broadcast copy lag behind by the board's delay,
so the overlay never shows an item before viewers have seen it picked up.

The OBS overlay authorizes on the board's unguessable ``overlay_token`` rather
than a session, since a browser source has no one signed in. It only reads.

Behind ``FeatureFlag.ITEM_TRACKER``. ``sheet_link`` is the one soft integration
point: the match sheet asks it on every open, so it answers ``None`` when the
flag is off instead of raising.
"""

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import ItemTrackerRepository, MatchRepository
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.item_tracker_catalog import (
    DUNGEON_ITEM_MODES,
    DUNGEONS_BY_KEY,
    DungeonItems,
    chest_total,
    describe,
    dungeon_items,
    field_max,
    mode_from_settings,
)
from application.tenant_context import require_tenant_id
from application.utils.app_links import item_tracker_overlay_path, item_tracker_path
from application.utils.tenant_urls import tenant_url
from models import FeatureFlag, ItemTrackerBoard, ItemTrackerChange, Match, Role, User

logger = logging.getLogger(__name__)

ALTTP_RANDOMIZERS = frozenset({'alttpr', 'avianart'})
MAX_DELAY_SECONDS = 600
LOG_LENGTH = 8

GOALS = {
    'ganon': 'Ganon',
    'fast_ganon': 'Fast Ganon',
    'dungeons': 'All Dungeons',
    'pedestal': 'Pedestal',
    'triforce-hunt': 'Triforce Hunt',
    'ganonhunt': 'Ganon Hunt',
}
WORLD_STATES = {'open': 'Open', 'standard': 'Standard', 'inverted': 'Inverted', 'retro': 'Retro'}


@dataclass(frozen=True)
class LogEntry:
    id: int
    at: datetime
    text: str
    who: str
    can_undo: bool


@dataclass
class RunnerBoard:
    board: ItemTrackerBoard
    runner: User
    log: list[LogEntry] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.runner.preferred_name


@dataclass
class TrackerView:
    """Everything the tracker page draws for one match."""

    match: Match
    title: str
    runners: list[RunnerBoard]
    items: DungeonItems
    items_source: str
    seed_tags: list[str]
    delay_seconds: int


@dataclass(frozen=True)
class OverlayView:
    runner_name: str
    state: Mapping[str, int]
    items: DungeonItems
    version: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_alttp(match: Match) -> bool:
    seed = getattr(match, 'generated_seed', None)
    if seed is not None and seed.randomizer:
        return seed.randomizer in ALTTP_RANDOMIZERS
    tournament = getattr(match, 'tournament', None)
    if tournament is None:
        return False
    preset = getattr(tournament, 'preset', None)
    randomizer = getattr(preset, 'randomizer', None) if preset is not None else None
    return (randomizer or tournament.seed_generator or '') in ALTTP_RANDOMIZERS


def _seed_settings(match: Match) -> Optional[Mapping]:
    seed = getattr(match, 'generated_seed', None)
    return getattr(seed, 'settings_snapshot', None) if seed is not None else None


def seed_tags(settings: Optional[Mapping]) -> list[str]:
    """The seed facts a tracker wants in view: world state, goal, crystal counts."""
    if not isinstance(settings, Mapping):
        return []
    tags = []
    if settings.get('mode') in WORLD_STATES:
        tags.append(WORLD_STATES[settings['mode']])
    if settings.get('goal'):
        tags.append(GOALS.get(settings['goal'], str(settings['goal']).replace('_', ' ').title()))
    raw = settings.get('crystals')
    crystals: Mapping = raw if isinstance(raw, Mapping) else {}
    if crystals.get('tower') is not None and crystals.get('ganon') is not None:
        tags.append(f"GT {crystals['tower']} · Ganon {crystals['ganon']}")
    return tags


def rewind(state: Mapping[str, int], changes_newest_first: list[ItemTrackerChange]) -> dict[str, int]:
    """``state`` as it was before ``changes`` (newest first) were made."""
    past = dict(state)
    for change in changes_newest_first:
        past[change.key] = change.before
    return past


class ItemTrackerService:
    """Boards, marks, undo and the delayed broadcast copy."""

    def __init__(self) -> None:
        self.repository = ItemTrackerRepository()
        self.matches = MatchRepository()

    @staticmethod
    async def _can_track(match: Match, user: Optional[User]) -> bool:
        if user is None:
            return False
        if await AuthService.is_staff(user) or await AuthService.has_role(user, Role.STREAM_MANAGER, match.event_id):
            return True
        return any(t.user_id == user.id and t.approved for t in match.trackers)  # type: ignore[attr-defined]

    async def _match(self, match_id: int) -> Match:
        return require_found(await self.matches.get_by_id(match_id), f'Match {match_id}')

    async def _tracked_match(self, match_id: int, actor: User) -> Match:
        match = await self._match(match_id)
        if not is_alttp(match):
            raise ValueError("The item tracker is for A Link to the Past matches, and this isn't one.")
        if not await self._can_track(match, actor):
            raise ValueError("Only this match's approved trackers, staff and broadcast techs can track it.")
        return match

    async def _board_for(self, board_id: int, actor: User) -> tuple[ItemTrackerBoard, Match]:
        board = require_found(await self.repository.get_by_id(board_id), 'Tracker board')
        match = await self._tracked_match(board.match_id, actor)
        return board, match

    @staticmethod
    def _items_for(match: Match, board: Optional[ItemTrackerBoard]) -> tuple[DungeonItems, str]:
        if board is not None and board.dungeon_items:
            return dungeon_items(board.dungeon_items), 'override'
        mode = mode_from_settings(_seed_settings(match))
        return dungeon_items(mode), 'seed' if mode else 'default'

    async def sheet_link(self, match_id: int, user: Optional[User]) -> Optional[str]:  # feature-gate: exempt — soft integration point on every match sheet
        """The tracker page for ``match_id`` if ``user`` can track it, else ``None``."""
        if not await FeatureFlagService().is_enabled(FeatureFlag.ITEM_TRACKER):
            return None
        match = await self.matches.get_by_id(match_id)
        if match is None or not is_alttp(match) or not await self._can_track(match, user):
            return None
        return item_tracker_path(match.id)

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def open_match(self, match_id: int, actor: User) -> TrackerView:
        """The match's boards, one per runner, made on first open."""
        match = await self._tracked_match(match_id, actor)
        runners = await self._runners(match)
        for runner in runners:
            runner.log = await self._log(runner.board)
        items, source = self._items_for(match, runners[0].board)
        names = ' vs '.join(r.name for r in runners)
        tournament = match.tournament.name if match.tournament else ''
        return TrackerView(
            match=match,
            title=' · '.join(p for p in (tournament, match.title or names) if p),
            runners=runners,
            items=items,
            items_source=source,
            seed_tags=seed_tags(_seed_settings(match)),
            delay_seconds=runners[0].board.delay_seconds,
        )

    async def _runners(self, match: Match) -> list[RunnerBoard]:
        """A board per runner, made the first time anyone needs one."""
        runners: list[RunnerBoard] = []
        for player in sorted(match.players, key=lambda p: p.id):  # type: ignore[attr-defined]
            board = await self.repository.get_for_runner(match.id, player.user_id)
            if board is None:
                first = runners[0].board if runners else None
                board = await self.repository.create(
                    match_id=match.id, runner_id=player.user_id, overlay_token=secrets.token_urlsafe(24),
                    delay_seconds=first.delay_seconds if first else 0,
                    dungeon_items=first.dungeon_items if first else None,
                )
            runners.append(RunnerBoard(board, player.user))
        if not runners:
            raise ValueError('This match has no players yet, so there is no one to track.')
        return runners

    async def _log(self, board: ItemTrackerBoard) -> list[LogEntry]:
        changes = await self.repository.recent_changes(board.id, LOG_LENGTH)
        newest: set[str] = set()
        entries = []
        for change in changes:
            latest = change.key not in newest
            newest.add(change.key)
            entries.append(LogEntry(
                id=change.id,
                at=change.created_at,
                text=('Undid: ' if change.is_undo else '') + describe(change.key, change.after),
                who=change.actor.preferred_name if change.actor else '',
                can_undo=latest and not change.undone and not change.is_undo,
            ))
        return entries

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def board(self, board_id: int, actor: User) -> RunnerBoard:
        """One board as it stands now, with its log, for a view that saw it change."""
        board, match = await self._board_for(board_id, actor)
        runner = next(p.user for p in match.players if p.user_id == board.runner_id)  # type: ignore[attr-defined]
        return RunnerBoard(board, runner, await self._log(board))

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def versions(self, match_id: int, actor: User) -> dict[int, int]:
        """``{board id: version}``, the cheap poll an open tracker page repeats."""
        await self._tracked_match(match_id, actor)
        return {b.id: b.version for b in await self.repository.list_for_match(match_id)}

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def set_level(self, board_id: int, key: str, level: int, actor: User) -> ItemTrackerBoard:
        board, match = await self._board_for(board_id, actor)
        items, _ = self._items_for(match, board)
        top = self._max(key, match, board, items)
        if top is None:
            raise ValueError(f"{key} isn't something this board tracks.")
        level = max(0, min(int(level), top))
        before = int(board.state.get(key, 0))
        if before == level:
            return board
        await self.repository.add_change(board.id, actor.id, key, before, level, _now())
        await self.repository.save_state(board, {**board.state, key: level})
        return board

    @staticmethod
    def _max(key: str, match: Match, board: ItemTrackerBoard, items: DungeonItems) -> Optional[int]:
        top = field_max(key, items)
        if top is None:
            return None
        dungeon_key, _, part = key.partition('.')
        if part == 'chests':
            return chest_total(DUNGEONS_BY_KEY[dungeon_key], items)
        if part == 'keys' and not items.small_keys:
            return None
        if part == 'big_key' and not items.big_keys:
            return None
        return top

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def undo(self, board_id: int, change_id: int, actor: User) -> ItemTrackerBoard:
        """Take back one change, if nothing has changed that key since."""
        board, _ = await self._board_for(board_id, actor)
        change = require_found(await self.repository.get_change(board.id, change_id), 'Change')
        latest = await self.repository.latest_change_for_key(board.id, change.key)
        if change.undone or change.is_undo or latest is None or latest.id != change.id:
            raise ValueError('That one has changed since, so it can no longer be undone on its own.')
        await self.repository.add_change(
            board.id, actor.id, change.key, change.after, change.before, _now(), is_undo=True,
        )
        await self.repository.mark_undone(change)
        await self.repository.save_state(board, {**board.state, change.key: change.before})
        return board

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def undo_last(self, board_id: int, actor: User) -> Optional[ItemTrackerBoard]:
        """Undo the newest change on the board that can still be undone."""
        board, _ = await self._board_for(board_id, actor)
        for entry in await self._log(board):
            if entry.can_undo:
                return await self.undo(board_id, entry.id, actor)
        return None

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def reset(self, board_id: int, actor: User) -> ItemTrackerBoard:
        """Clear a board and its log, for a restarted race."""
        board, _ = await self._board_for(board_id, actor)
        await self.repository.clear_changes(board.id)
        await self.repository.save_state(board, {})
        return board

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def set_delay(self, match_id: int, seconds: int, actor: User) -> int:
        """Set how far the stream copy lags, for every runner on the match."""
        match = await self._tracked_match(match_id, actor)
        seconds = max(0, min(int(seconds), MAX_DELAY_SECONDS))
        for runner in await self._runners(match):
            await self.repository.set_fields(runner.board, delay_seconds=seconds)
        return seconds

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def set_dungeon_items(self, match_id: int, mode: Optional[str], actor: User) -> None:
        """Override the seed's dungeon-item mode (``None`` goes back to the seed)."""
        match = await self._tracked_match(match_id, actor)
        if mode is not None and mode not in DUNGEON_ITEM_MODES:
            raise ValueError('Pick one of the dungeon item modes.')
        for runner in await self._runners(match):
            await self.repository.set_fields(runner.board, dungeon_items=mode)

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def overlay_link(self, board_id: int, actor: User) -> str:
        """The absolute OBS browser-source address for one runner's board."""
        from application.services.tenant_service import TenantService

        board, _ = await self._board_for(board_id, actor)
        tenant = require_found(await TenantService.get_by_id(require_tenant_id()), 'Community')
        return tenant_url(tenant, item_tracker_overlay_path(board.overlay_token))

    async def _delayed_state(self, board: ItemTrackerBoard) -> dict[str, int]:
        if board.delay_seconds <= 0:
            return dict(board.state)
        cutoff = _now() - timedelta(seconds=board.delay_seconds)
        return rewind(board.state, await self.repository.changes_since(board.id, cutoff))

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def preview(self, board_id: int, actor: User) -> dict[str, int]:
        """What the stream shows for this board right now."""
        board, _ = await self._board_for(board_id, actor)
        return await self._delayed_state(board)

    @requires_feature(FeatureFlag.ITEM_TRACKER)
    async def overlay(self, token: str) -> Optional[OverlayView]:
        """The broadcast copy for an OBS browser source, or ``None`` for a bad token."""
        if not token or len(token) > 64:
            return None
        board = await self.repository.get_by_token(token)
        if board is None:
            return None
        match = await self._match(board.match_id)
        runner = next((p.user for p in match.players if p.user_id == board.runner_id), None)  # type: ignore[attr-defined]
        items, _ = self._items_for(match, board)
        return OverlayView(
            runner_name=runner.preferred_name if runner else '',
            state=await self._delayed_state(board),
            items=items,
            version=board.version,
        )
