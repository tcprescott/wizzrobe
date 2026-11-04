"""The Group races board: every group race and qualifier live race, and staff's actions on one.

A row is either a match in a group race tournament (``kind='match'``) or a
qualifier live race with a start time (``kind='qualifier'``). Both are run by
the same bot steps (open, announce, seed, lock), so the board shows them as one
list and offers the same actions: open the room now, post the seed now, re-post
the Discord announcement, lock the room now, cancel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from application.errors import NotFoundError, require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories import (
    AsyncQualifierLiveRaceRepository,
    AsyncQualifierPermalinkRepository,
    AsyncQualifierPoolRepository,
    AsyncQualifierRepository,
    AsyncQualifierRunRepository,
    MatchRepository,
    RacetimeRoomRepository,
    TournamentRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.group_race_service import lock_room
from application.services.user_service import UserService
from application.utils.timezone import to_local
from models import (
    AsyncQualifier,
    AsyncQualifierLiveRace,
    AsyncQualifierLiveRaceStatus,
    FeatureFlag,
    Match,
    RaceRoomStatus,
    RacetimeRoom,
    User,
)

logger = logging.getLogger(__name__)

KINDS = ('match', 'qualifier')
COMING_BACK_HOURS = 12
COMING_UP_DAYS = 14
FINISHED_DAYS = 14


@dataclass
class GroupRaceEntrant:
    name: str
    linked: bool
    place: Optional[int] = None
    finish_seconds: Optional[int] = None
    status: Optional[str] = None


@dataclass
class GroupRaceView:
    kind: str
    id: int
    title: str
    series: str
    series_id: int
    from_speedgaming: bool
    scheduled_at: Optional[datetime]
    opens_at: Optional[datetime]
    seed_at: Optional[datetime]
    state: str
    lock_at_seed: bool
    channel_id: Optional[str]
    role_id: Optional[str]
    seed_note: str
    room_slug: Optional[str] = None
    room_url: Optional[str] = None
    opened_at: Optional[datetime] = None
    announced_at: Optional[datetime] = None
    seed_posted_at: Optional[datetime] = None
    locked_at: Optional[datetime] = None
    finishers: int = 0
    entrants: List[GroupRaceEntrant] = field(default_factory=list)
    entrants_problem: Optional[str] = None


def _minus(when: Optional[datetime], minutes: int) -> Optional[datetime]:
    return when - timedelta(minutes=max(0, minutes)) if when else None


def _room_state(room: Optional[RacetimeRoom]) -> Optional[str]:
    if room is None or room.status == RaceRoomStatus.CANCELLED:
        return None
    if room.status == RaceRoomStatus.IN_PROGRESS:
        return 'running'
    if room.status == RaceRoomStatus.FINISHED:
        return 'finished'
    return 'open'


def _with_room(view: GroupRaceView, room: Optional[RacetimeRoom]) -> GroupRaceView:
    if room is not None and room.status != RaceRoomStatus.CANCELLED:
        view.room_slug, view.room_url = room.slug, room.url
        view.opened_at, view.announced_at = room.opened_at, room.announced_at
        view.seed_posted_at, view.locked_at = room.seed_posted_at, room.locked_at
    return view


def match_view(match: Match) -> GroupRaceView:
    tournament = match.tournament
    room: Optional[RacetimeRoom] = getattr(match, 'racetime_room', None)
    finishers = sum(1 for p in match.players if p.finish_rank is not None)  # type: ignore[attr-defined]
    finishers += sum(1 for e in (room.unmatched_entrants or []) if e.get('place')) if room else 0
    state = 'finished' if match.finished_at else (_room_state(room) or 'scheduled')
    view = GroupRaceView(
        kind='match', id=match.id,
        title=match.title or tournament.name,
        series=tournament.name, series_id=tournament.id,
        from_speedgaming=match.speedgaming_episode_id is not None,  # type: ignore[attr-defined]
        scheduled_at=match.scheduled_at,
        opens_at=_minus(match.scheduled_at, tournament.room_open_minutes_before or 0),
        seed_at=_minus(match.scheduled_at, tournament.racetime_seed_post_minutes),
        state=state,
        lock_at_seed=tournament.racetime_lock_at_seed,
        channel_id=tournament.racetime_announce_channel_id,
        role_id=tournament.racetime_announce_role_id,
        seed_note="Rolled from the tournament's preset",
        finishers=finishers,
    )
    return _with_room(view, room)


def live_race_view(live_race: AsyncQualifierLiveRace, room: Optional[RacetimeRoom]) -> GroupRaceView:
    qualifier: AsyncQualifier = live_race.pool.qualifier
    status = live_race.status
    if status == AsyncQualifierLiveRaceStatus.FINISHED:
        state = 'finished'
    elif status == AsyncQualifierLiveRaceStatus.IN_PROGRESS:
        state = 'running'
    else:
        state = _room_state(room) or 'scheduled'
    pool = live_race.pool.name
    view = GroupRaceView(
        kind='qualifier', id=live_race.id,
        title=live_race.match_title,
        series=qualifier.name, series_id=qualifier.id,
        from_speedgaming=live_race.episode_id is not None,
        scheduled_at=live_race.scheduled_at,
        opens_at=_minus(live_race.scheduled_at, qualifier.live_race_room_open_minutes),
        seed_at=_minus(live_race.scheduled_at, qualifier.live_race_seed_post_minutes),
        state=state,
        lock_at_seed=qualifier.live_race_lock_at_seed,
        channel_id=qualifier.live_race_announce_channel_id,
        role_id=qualifier.live_race_announce_role_id,
        seed_note=(
            f'The {pool} permalink' if live_race.permalink_id  # type: ignore[attr-defined]
            else f'No permalink picked in {pool} yet'
        ),
    )
    return _with_room(view, room)


@dataclass
class SeriesPick:
    """A series a new race can go in: a group race tournament, or a qualifier pool (with its live race permalinks)."""

    kind: str
    id: int
    name: str
    permalinks: List[Tuple[int, str]] = field(default_factory=list)


class GroupRaceBoardService:
    """Reads and staff actions for the Group races screen."""

    def __init__(self) -> None:
        self.live_races = AsyncQualifierLiveRaceRepository()
        self.rooms = RacetimeRoomRepository()
        self.audit_service = AuditService()

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def list_races(self, actor: Optional[User], *, finished: bool = False) -> List[GroupRaceView]:
        """Coming up (not finished, from 12 hours back) or finished in the last two weeks."""
        await self._ensure_board(actor)
        now = datetime.now(timezone.utc)
        if finished:
            start, end = now - timedelta(days=FINISHED_DAYS), now
        else:
            start, end = now - timedelta(hours=COMING_BACK_HOURS), now + timedelta(days=COMING_UP_DAYS)
        views = [match_view(m) for m in await MatchRepository.group_races_between(start, end)
                 if await self._may_run_tournament(actor, m.tournament_id)]  # type: ignore[attr-defined]
        if await self._qualifiers_on():
            races = [
                r for r in await self.live_races.scheduled_in_event(start, end)
                if r.status != AsyncQualifierLiveRaceStatus.CANCELLED
                and await AuthService.can_admin_qualifier(actor, r.pool.qualifier)
            ]
            rooms = await self.rooms.by_slugs([r.racetime_slug for r in races if r.racetime_slug])
            views += [live_race_view(r, rooms.get(r.racetime_slug or '')) for r in races]
        views = [v for v in views if (v.state == 'finished') == finished]
        views.sort(key=lambda v: (v.scheduled_at or now, v.kind, v.id), reverse=finished)
        return views

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def get_race(self, actor: Optional[User], kind: str, race_id: int) -> GroupRaceView:
        """One race with who's in it: the room's entrants while it's open, the results once it's done."""
        if kind == 'match':
            match = await self._match(actor, race_id)
            view = match_view(match)
            view.entrants, view.entrants_problem = await self._match_entrants(match, view)
            return view
        live_race, room = await self._live_race(actor, race_id)
        view = live_race_view(live_race, room)
        view.entrants, view.entrants_problem = await self._live_race_entrants(live_race, room, view)
        return view

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def open_now(self, actor: Optional[User], kind: str, race_id: int) -> GroupRaceView:
        """Open the room before its time (and post it, as an on-time open would)."""
        if kind == 'match':
            from application.services.race_room_service import RaceRoomService

            match = await self._match(actor, race_id)
            await RaceRoomService().manual_create_room(actor, match.id)
        else:
            from application.services.async_qualifier.async_qualifier_live_race_service import (
                AsyncQualifierLiveRaceService,
            )

            await self._live_race(actor, race_id)
            await AsyncQualifierLiveRaceService().open_room(actor, race_id)
        return await self.get_race(actor, kind, race_id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def post_seed_now(self, actor: Optional[User], kind: str, race_id: int) -> GroupRaceView:
        """Post the seed in the open room now, then lock it if the series locks at seed time."""
        if kind == 'match':
            from application.services.race_room_bot_service import RaceRoomBotService

            match = await self._match(actor, race_id)
            room = self._open_room_of(getattr(match, 'racetime_room', None))
            outcome = await RaceRoomBotService().post_seed_now(room, match)
            if outcome == 'failed':
                raise ValueError("The seed didn't roll. Check the tournament's preset and try again.")
        else:
            from application.services.async_qualifier.async_qualifier_live_race_room import LiveRaceRoomService

            live_race, room = await self._live_race(actor, race_id)
            room = self._open_room_of(room)
            if room.seed_posted_at is None and live_race.permalink_id is None:  # type: ignore[attr-defined]
                raise ValueError('Pick the permalink for this race first.')
            await LiveRaceRoomService().post_seed_if_due(room, live_race, live_race.pool.qualifier, force=True)
        return await self.get_race(actor, kind, race_id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def repost(self, actor: Optional[User], kind: str, race_id: int) -> GroupRaceView:
        """Post the room in Discord again (after a deleted message, a changed channel)."""
        if kind == 'match':
            from application.services.group_race_service import GroupRaceService

            match = await self._match(actor, race_id)
            room = self._open_room_of(getattr(match, 'racetime_room', None))
            if not match.tournament.racetime_announce_channel_id:
                raise ValueError("This series doesn't post in Discord. Pick a channel on its Racetime tab.")
            await self.rooms.release_announcement(room)
            sent = await GroupRaceService().announce(room, match, actor=actor)  # type: ignore[arg-type]
        else:
            from application.services.async_qualifier.async_qualifier_live_race_room import LiveRaceRoomService

            live_race, room = await self._live_race(actor, race_id)
            room = self._open_room_of(room)
            qualifier = live_race.pool.qualifier
            if not qualifier.live_race_announce_channel_id:
                raise ValueError("This qualifier doesn't post its live races in Discord. Pick a channel in its settings.")
            await self.rooms.release_announcement(room)
            sent = await LiveRaceRoomService().announce(room, live_race, qualifier, actor=actor)  # type: ignore[arg-type]
        if not sent:
            raise ValueError("Discord didn't take the post. It'll try again on its own in a minute.")
        return await self.get_race(actor, kind, race_id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def lock_now(self, actor: Optional[User], kind: str, race_id: int) -> GroupRaceView:
        """Turn the room invite-only now, whatever the series' lock setting."""
        if kind == 'match':
            room = self._open_room_of(getattr(await self._match(actor, race_id), 'racetime_room', None))
        else:
            _live_race, room = await self._live_race(actor, race_id)
            room = self._open_room_of(room)
        if room.locked_at is not None:
            raise ValueError('The room is already locked.')
        if not await lock_room(room):
            raise ValueError("racetime didn't take the lock. Try again in a moment.")
        await self.audit_service.write_and_publish(
            actor, AuditActions.RACE_ROOM_LOCKED,  # type: ignore[arg-type]
            {'room_id': room.id, 'slug': room.slug, 'category': room.category, 'kind': kind, 'race_id': race_id},
            EventType.RACE_ROOM_LOCKED,
        )
        return await self.get_race(actor, kind, race_id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def cancel(self, actor: Optional[User], kind: str, race_id: int) -> None:
        """Call the race off: the match (or live race) goes, and its open room is marked cancelled."""
        if kind == 'match':
            from application.services.match.match_service import MatchService

            match = await self._match(actor, race_id)
            if match.finished_at:
                raise ValueError('This race has finished, so there is nothing to cancel.')
            await MatchService().cancel_match(match.id, actor, reason='Group race cancelled')
            return
        from application.services.async_qualifier.async_qualifier_live_race_service import (
            AsyncQualifierLiveRaceService,
        )
        from application.services.race_room_service import RaceRoomService

        live_race, room = await self._live_race(actor, race_id)
        if live_race.status == AsyncQualifierLiveRaceStatus.FINISHED:
            raise ValueError('This race has finished, so there is nothing to cancel.')
        if room is not None and room.status == RaceRoomStatus.OPEN:
            await RaceRoomService().cancel_room(room, actor=actor, reason='Group race cancelled')
        await AsyncQualifierLiveRaceService().cancel_live_race(actor, race_id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def create(
        self, actor: Optional[User], *, title: str, scheduled_at: datetime,
        tournament_id: Optional[int] = None, pool_id: Optional[int] = None,
        permalink_id: Optional[int] = None,
    ) -> GroupRaceView:
        """Add a race by hand: a match in a group race tournament, or a live race in a qualifier pool."""
        title = (title or '').strip()
        if not title:
            raise ValueError('Give the race a title.')
        when = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=timezone.utc)
        if when < datetime.now(timezone.utc):
            raise ValueError('Pick a start time in the future.')
        if (tournament_id is None) == (pool_id is None):
            raise ValueError('Pick a tournament or a qualifier pool for the race.')
        if tournament_id is not None:
            from application.services.match.match_service import MatchService
            from application.services.tournament_service import TournamentService

            tournament = await TournamentService().get_tournament_by_id(tournament_id)
            if tournament is None or not tournament.racetime_group_race:
                raise ValueError("That tournament isn't a group race series. Switch it on its Racetime tab.")
            local = to_local(when)
            match = await MatchService().create_match(
                tournament_id=tournament_id,
                scheduled_date=local.strftime('%Y-%m-%d'),
                scheduled_time=local.strftime('%H:%M'),
                player_ids=[], title=title, actor=actor,
            )
            return await self.get_race(actor, 'match', match.id)
        from application.services.async_qualifier.async_qualifier_live_race_service import (
            AsyncQualifierLiveRaceService,
        )

        live_race = await AsyncQualifierLiveRaceService().create_live_race(
            actor, pool_id,  # type: ignore[arg-type]
            match_title=title, permalink_id=permalink_id, scheduled_at=when,
        )
        return await self.get_race(actor, 'qualifier', live_race.id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def series(self, actor: Optional[User]) -> List[SeriesPick]:
        """Where staff can add a race by hand: group race tournaments, then qualifier pools."""
        await self._ensure_board(actor)
        picks = [
            SeriesPick('tournament', t.id, t.name) for t in await TournamentRepository.group_race_series()
            if await self._may_run_tournament(actor, t.id)
        ]
        if not await self._qualifiers_on():
            return picks
        pools, permalinks = AsyncQualifierPoolRepository(), AsyncQualifierPermalinkRepository()
        for qualifier in await AsyncQualifierRepository().list_active():
            if not await AuthService.can_admin_qualifier(actor, qualifier):
                continue
            for pool in await pools.list_for_qualifier(qualifier.id):
                links = [
                    (p.id, p.notes or p.url) for p in await permalinks.list_for_pool(pool.id) if p.live_race
                ]
                picks.append(SeriesPick('pool', pool.id, f'{qualifier.name} · {pool.name}', links))
        return picks

    async def _ensure_board(self, actor: Optional[User]) -> None:
        allowed = await AuthService.can_view_admin(actor) or await AuthService.can_admin_qualifier(actor, None)
        await AuthService.ensure(allowed, 'Cannot run group races')

    @staticmethod
    async def _may_run_tournament(actor: Optional[User], tournament_id: int) -> bool:
        return await AuthService.is_staff(actor) or await AuthService.is_tournament_admin(actor, tournament_id)

    @staticmethod
    async def _qualifiers_on() -> bool:
        from application.services.feature_flag_service import FeatureFlagService

        return await FeatureFlagService().is_enabled(FeatureFlag.ASYNC_QUALIFIERS)

    async def _match(self, actor: Optional[User], match_id: int) -> Match:
        from application.services.match.match_service import MatchService

        match = await MatchService().get_by_id(match_id)
        if match is None or not match.tournament.racetime_group_race:
            raise NotFoundError('Group race not found')
        await AuthService.ensure(
            await self._may_run_tournament(actor, match.tournament_id),  # type: ignore[attr-defined]
            'Cannot run this group race',
        )
        await match.fetch_related('racetime_room', 'players__user')
        return match

    async def _live_race(
        self, actor: Optional[User], live_race_id: int,
    ) -> Tuple[AsyncQualifierLiveRace, Optional[RacetimeRoom]]:
        if not await self._qualifiers_on():
            raise NotFoundError('Group race not found')
        live_race = require_found(await self.live_races.get_by_id(live_race_id), 'Group race')
        await AuthService.ensure(
            await AuthService.can_admin_qualifier(actor, live_race.pool.qualifier),
            'Cannot run this group race',
        )
        room = None
        if live_race.racetime_slug:
            room = (await self.rooms.by_slugs([live_race.racetime_slug])).get(live_race.racetime_slug)
        return live_race, room

    @staticmethod
    def _open_room_of(room: Optional[RacetimeRoom]) -> RacetimeRoom:
        if room is None or room.status != RaceRoomStatus.OPEN:
            raise ValueError("The room isn't open. Open it first.")
        return room

    async def _match_entrants(
        self, match: Match, view: GroupRaceView,
    ) -> Tuple[List[GroupRaceEntrant], Optional[str]]:
        room: Optional[RacetimeRoom] = getattr(match, 'racetime_room', None)
        if view.state == 'finished':
            linked = [
                GroupRaceEntrant(
                    name=p.user.preferred_name, linked=True,  # type: ignore[attr-defined]
                    place=p.finish_rank, finish_seconds=p.finish_time,
                )
                for p in match.players  # type: ignore[attr-defined]
            ]
            others = [
                GroupRaceEntrant(
                    name=str(e.get('name') or ''), linked=False, place=e.get('place'),
                    finish_seconds=e.get('finish_seconds'), status=e.get('status'),
                )
                for e in ((room.unmatched_entrants if room else None) or [])
            ]
            return _by_place(linked + others), None
        return await self._live_entrants(view)

    async def _live_race_entrants(
        self, live_race: AsyncQualifierLiveRace, room: Optional[RacetimeRoom], view: GroupRaceView,
    ) -> Tuple[List[GroupRaceEntrant], Optional[str]]:
        if view.state == 'finished':
            runs = await AsyncQualifierRunRepository().list_for_live_race(live_race.id)
            return sorted(
                (GroupRaceEntrant(name=r.user.preferred_name, linked=True) for r in runs),
                key=lambda e: e.name.casefold(),
            ), None
        return await self._live_entrants(view)

    @staticmethod
    async def _live_entrants(view: GroupRaceView) -> Tuple[List[GroupRaceEntrant], Optional[str]]:
        """Who's in the room right now, from racetime. Empty, with why, when racetime can't say."""
        if view.room_slug is None or view.state not in ('open', 'running'):
            return [], None
        from application.utils.clients.racetime_client import RacetimeAPIError
        from application.utils.clients.racetime_rooms_client import race_data
        from racetimebot import protocol

        try:
            data = await race_data(view.room_slug)
        except RacetimeAPIError as exc:
            return [], f"Couldn't read the room from racetime: {exc}."
        if not data:
            return [], None
        event = protocol.race_event(data, slug=view.room_slug, category=view.room_slug.split('/')[0])
        users = UserService()
        entrants = []
        for e in event.entrants:
            user = await users.get_user_by_racetime_id(e.user_id) if e.user_id else None
            entrants.append(GroupRaceEntrant(
                name=user.preferred_name if user else e.display_name, linked=user is not None,
                place=e.place, finish_seconds=e.finish_time, status=e.status.value,
            ))
        return _by_place(entrants), None


def _by_place(entrants: List[GroupRaceEntrant]) -> List[GroupRaceEntrant]:
    return sorted(entrants, key=lambda e: (e.place is None, e.place or 0, e.name.casefold()))
