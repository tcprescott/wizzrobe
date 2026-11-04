"""A qualifier live race's room, run by the bot: open on time, post, seed, lock.

A live race with a ``scheduled_at`` opens on its own
``live_race_room_open_minutes`` before the start, open to anyone, and is posted
in the qualifier's Discord channel (``live_race_announce_channel_id``, with an
optional role ping). The bot greets the room, posts the race's pool permalink
``live_race_seed_post_minutes`` before the start, and with
``live_race_lock_at_seed`` (on by default) turns the room invite-only at the
same moment. ``!seed`` repeats it. Results are captured as today by
``AsyncQualifierLiveRaceService.record_finish``.

The worker steps skip a community that has switched off async qualifiers or
racetime rooms; the room steps act on an already-open room and never raise.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from application.event_context import event_scope
from application.events import EventType
from application.repositories import AsyncQualifierLiveRaceRepository, RacetimeRoomRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.group_race_service import announce_room, lock_room
from application.services.user_service import UserService
from application.utils.clients import racetime_room_actions
from models import AsyncQualifier, AsyncQualifierLiveRace, FeatureFlag, RacetimeRoom, User

logger = logging.getLogger(__name__)

GRACE_MINUTES = 15
SEED_REPEAT_SECONDS = 30
_last_seed_repeat: Dict[str, float] = {}


def _qualifier_of(live_race: AsyncQualifierLiveRace) -> AsyncQualifier:
    return live_race.pool.qualifier


def seed_due(live_race: AsyncQualifierLiveRace, qualifier: AsyncQualifier, now: datetime) -> bool:
    if live_race.scheduled_at is None:
        return False
    return now >= live_race.scheduled_at - timedelta(minutes=qualifier.live_race_seed_post_minutes)


def open_due(live_race: AsyncQualifierLiveRace, qualifier: AsyncQualifier, now: datetime) -> bool:
    if live_race.scheduled_at is None:
        return False
    return now >= live_race.scheduled_at - timedelta(minutes=qualifier.live_race_room_open_minutes)


def greeting(qualifier: AsyncQualifier, live_race: AsyncQualifierLiveRace) -> str:
    when = (
        f'{qualifier.live_race_seed_post_minutes} minutes before the start'
        if qualifier.live_race_seed_post_minutes > 0 else 'at the start'
    ) if live_race.scheduled_at else 'when staff post it'
    lock = ' The room locks when the seed goes out, so join before then.' if qualifier.live_race_lock_at_seed else ''
    return (
        f"Hi! I'm running this room for {qualifier.name}. Anyone can join. "
        f'The seed posts here {when}.{lock} Type !seed to see it again. '
        'Link racetime.gg on your Wizzrobe profile so your run counts.'
    )


def seed_text(live_race: AsyncQualifierLiveRace) -> Optional[str]:
    permalink = live_race.permalink
    if permalink is None:
        return None
    return f'Seed: {permalink.url}'


class LiveRaceRoomService:
    """The bot's side of a qualifier live race room."""

    def __init__(self) -> None:
        self.repository = AsyncQualifierLiveRaceRepository()
        self.room_repository = RacetimeRoomRepository()
        self.audit_service = AuditService()

    # feature-gate: exempt — called as an already-opened room is set up; never raises.
    async def announce(
        self, room: RacetimeRoom, live_race: AsyncQualifierLiveRace, qualifier: AsyncQualifier, *, actor: User,
    ) -> bool:
        """Post the room in the qualifier's channel once. ``True`` when it went out."""
        if not qualifier.live_race_announce_channel_id:
            return False
        sent = await announce_room(
            room, channel_id=qualifier.live_race_announce_channel_id,
            role_id=qualifier.live_race_announce_role_id,
            title=live_race.match_title, starts_at=live_race.scheduled_at,
            seed_minutes=qualifier.live_race_seed_post_minutes,
            locks=qualifier.live_race_lock_at_seed,
        )
        if sent:
            await self._audit(actor, AuditActions.RACE_ROOM_ANNOUNCED, EventType.RACE_ROOM_ANNOUNCED, room, live_race, {
                'channel_id': qualifier.live_race_announce_channel_id,
            })
        return sent

    # feature-gate: exempt — a worker step; skips a community with either feature off.
    async def open_due_races(self, now: datetime, max_lead_minutes: int) -> None:
        """Open every scheduled live race whose room-open time has come (the worker's tick)."""
        from application.utils.background_loop import for_each_tenant_scoped

        races = [
            r for r in await self.repository.due_to_open(
                now - timedelta(minutes=GRACE_MINUTES), now + timedelta(minutes=max_lead_minutes),
            )
            if _qualifier_of(r).is_active and open_due(r, _qualifier_of(r), now)
        ]
        if not races:
            return
        actor = await UserService().get_system_user()

        async def _open(live_race: AsyncQualifierLiveRace) -> None:
            from application.services.async_qualifier.async_qualifier_live_race_service import (
                AsyncQualifierLiveRaceService,
            )

            if not await self._features_on():
                return
            opened = await AsyncQualifierLiveRaceService().auto_open(live_race.id, actor=actor)
            if opened is not None:
                logger.info('auto-opened racetime room for qualifier live race %s', live_race.id)

        await for_each_tenant_scoped(
            races, _open,
            tenant_id_of=lambda r: r.tenant_id,  # type: ignore[attr-defined]
            event_id_of=lambda r: _qualifier_of(r).event_id,
            logger=logger,
            describe=lambda r: f'qualifier live race {r.id}',
        )

    # feature-gate: exempt — a worker step over already-open rooms; never raises.
    async def step_open_rooms(self, now: datetime) -> None:
        """Retry a missed Discord post and post each seed that has come due."""
        from application.utils.background_loop import for_each_tenant_scoped

        rooms = await self.room_repository.open_without_match()
        if not rooms:
            return
        actor = await UserService().get_system_user()

        async def _step(room: RacetimeRoom) -> None:
            found = await self._live_race(room)
            if found is None:
                return
            live_race, qualifier = found
            with event_scope(qualifier.event_id):
                if room.announced_at is None:
                    await self.announce(room, live_race, qualifier, actor=actor)
                await self.post_seed_if_due(room, live_race, qualifier, now=now)

        await for_each_tenant_scoped(
            rooms, _step,
            tenant_id_of=lambda r: r.tenant_id,  # type: ignore[attr-defined]
            logger=logger,
            describe=lambda r: f'racetime room {r.slug}',
        )

    # feature-gate: exempt — lifecycle of an already-open room; never raises.
    async def post_seed_if_due(
        self, room: RacetimeRoom, live_race: AsyncQualifierLiveRace, qualifier: AsyncQualifier,
        *, now: Optional[datetime] = None, force: bool = False,
    ) -> bool:
        """Post the permalink once its time has come (or now, with ``force``), then lock if the qualifier asks."""
        now = now or datetime.now(timezone.utc)
        text = seed_text(live_race)
        if room.seed_posted_at is not None or text is None or not (force or seed_due(live_race, qualifier, now)):
            return False
        try:
            if not await self.room_repository.claim_seed_post(room, now):
                return False
            await racetime_room_actions.say(room.slug, text)
        except Exception:
            logger.exception('posting the seed in qualifier room %s failed', room.slug)
            return False
        if qualifier.live_race_lock_at_seed and await lock_room(room):
            actor = await UserService().get_system_user()
            await self._audit(actor, AuditActions.RACE_ROOM_LOCKED, EventType.RACE_ROOM_LOCKED, room, live_race, None)
        return True

    # feature-gate: exempt — lifecycle of an already-open room; never raises.
    async def on_room_event(self, room: RacetimeRoom, kind: str, message: str = '') -> bool:
        """Greet on join and answer ``!seed``. ``False`` when the room isn't a live race."""
        found = await self._live_race(room)
        if found is None:
            return False
        live_race, qualifier = found
        with event_scope(qualifier.event_id):
            if kind == 'joined':
                if await self.room_repository.claim_greeting(room, datetime.now(timezone.utc)):
                    await racetime_room_actions.say(room.slug, greeting(qualifier, live_race))
                await self.post_seed_if_due(room, live_race, qualifier)
            elif kind == 'chat' and message.strip().lower().startswith('!seed'):
                await self._command_seed(room, live_race, qualifier)
        return True

    async def _command_seed(
        self, room: RacetimeRoom, live_race: AsyncQualifierLiveRace, qualifier: AsyncQualifier,
    ) -> None:
        clock = time.monotonic()
        if clock - _last_seed_repeat.get(room.slug, 0) < SEED_REPEAT_SECONDS:
            return
        _last_seed_repeat[room.slug] = clock
        text = seed_text(live_race)
        if room.seed_posted_at is not None and text:
            await racetime_room_actions.say(room.slug, text)
        elif live_race.scheduled_at is None:
            await racetime_room_actions.say(room.slug, "The seed isn't out yet.")
        else:
            due = live_race.scheduled_at - timedelta(minutes=qualifier.live_race_seed_post_minutes)
            minutes = max(1, int((due - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
            await racetime_room_actions.say(
                room.slug, f"The seed posts in {minutes} minute{'s' if minutes != 1 else ''}.",
            )

    async def _live_race(self, room: RacetimeRoom) -> Optional[Tuple[AsyncQualifierLiveRace, AsyncQualifier]]:
        live_race = await self.repository.get_by_racetime_slug(room.slug)
        if live_race is None:
            return None
        return live_race, _qualifier_of(live_race)

    @staticmethod
    async def _features_on() -> bool:
        from application.services.feature_flag_service import FeatureFlagService

        live = await FeatureFlagService().enabled_flags()
        return FeatureFlag.ASYNC_QUALIFIERS in live and FeatureFlag.RACETIME_ROOMS in live

    async def _audit(
        self, actor: User, action: str, event_type: str, room: RacetimeRoom,
        live_race: AsyncQualifierLiveRace, extra: Optional[dict],
    ) -> None:
        detail = {
            'room_id': room.id, 'slug': room.slug, 'category': room.category,
            'live_race_id': live_race.id, 'qualifier_id': _qualifier_of(live_race).id,
        }
        detail.update(extra or {})
        await self.audit_service.write_and_publish(actor, action, detail, event_type)
