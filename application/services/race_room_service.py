"""Race Room Service — the racetime room lifecycle for scheduled matches (PR 6).

The business layer that both the auto-open worker and the ``racetimebot/``
handlers call. It maps the racetime room lifecycle onto a :class:`~models.Match`:

    create → open (+ attach seed) → in-progress → finish (capture results) → …
                                                 ↘ cancel

Every transition audits and publishes a ``race_room.*`` domain event, and the
service **acts as the system user** (the racetime room, not a human, is driving
the change). Result capture maps racetime entrants back to linked ``User`` rows,
records place (``finish_rank``) + elapsed time (``finish_time``), handles the
terminal states (forfeit / no-show / DQ / one-finisher), and feeds the existing
result-reporting path (Challonge push is an optional downstream step — a
non-Challonge tournament still closes).

Tenant-scoped: callers run inside ``tenant_scope`` (the worker binds
``match.tenant_id``; the handler binds ``room.tenant_id``; the manual-create UI
runs in the request's tenant). ``manual_create_room`` is the one method gated by
an interactive permission (STAFF / ``SYNC_ADMIN``); the rest are system paths.

A room is real before it is recorded: :meth:`create_room_for_match` asks
racetime.gg to open it (the category bot's ``startrace``, with the tournament's
``RaceRoomProfile``), stores the slug racetime returns, and only then writes the
row as OPEN and tells the players. A refused ``startrace`` leaves nothing behind.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from application.errors import require_found
from application.events import Event, EventType, event_bus
from application.feature_flags import requires_feature
from application.repositories import RacetimeRoomRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.user_service import UserService
from application.tenant_context import require_tenant_id
from application.utils.clients import racetime_rooms_client
from application.utils.clients.racetime_client import RacetimeAPIError
from application.utils.clients.racetime_rooms_client import RaceRoomSettings
from application.utils.racetime_entrants import is_scored_finish, unmatched_handle
from models import (
    FeatureFlag,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeRoom,
    Tournament,
    User,
)
from racetimebot.transport import EntrantStatus, RaceEntrant, RaceRoomEvent

logger = logging.getLogger(__name__)

# One opener at a time per room key. The 60s poll and the series push can both
# reach the same match, and with a ``startrace`` round trip between "no room yet"
# and the row being written, two of them would otherwise open two racetime rooms.
# Process-local, which is enough under the single-worker deployment
# (docs/scaling-roadmap.md); weak values so idle keys don't accumulate.
_open_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def room_open_lock(key: str) -> asyncio.Lock:
    """The lock serialising room creation for ``key`` (e.g. ``match:12``)."""
    lock = _open_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _open_locks[key] = lock
    return lock


async def start_racetime_room(bot, settings: RaceRoomSettings) -> str:
    """Open a room on racetime.gg as ``bot`` and return racetime's slug for it.

    A refusal or an unreachable racetime becomes a ``ValueError`` carrying
    racetime's reason, so staff opening one by hand see why, and the auto-open
    worker logs it and tries again on its next tick.
    """
    client = racetime_rooms_client.build_rooms_client()
    try:
        return await client.start_race(
            category=bot.category, client_id=bot.client_id,
            client_secret=bot.client_secret, settings=settings,
        )
    except RacetimeAPIError as exc:
        logger.error('racetime startrace failed for %s: %s', bot.category, exc)
        raise ValueError(f"racetime.gg didn't open the room: {exc}") from exc


class RaceRoomService:
    """Drive a racetime room through its lifecycle, mapped onto a ``Match``."""

    def __init__(self) -> None:
        self.room_repository = RacetimeRoomRepository()
        self.audit_service = AuditService()

    # ---- creation / open -------------------------------------------------

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def create_room_for_match(
        self, match: Match, *, actor: Optional[User] = None, attach_seed: bool = True,
    ) -> RacetimeRoom:
        """Open (or return the existing) racetime room for a match.

        Idempotent — one room per match. Requires the tournament to have an
        authorized racetime bot (its category hosts the room) and a goal (from
        its room profile, else its default goal). The room is opened on
        racetime.gg first; the row records racetime's slug and is OPEN from the
        moment it exists. Each player is DMed a button into it, and the seed is
        attached when configured.
        """
        async with room_open_lock(f'match:{match.id}'):
            existing = await self.room_repository.get_by_match(match)
            if existing is not None:
                return existing

            tournament = await self._tournament_of(match)
            bot = await tournament.racetime_bot
            if bot is None:
                raise ValueError('This tournament has no racetime bot configured.')
            settings = await self._room_settings(tournament, match)

            # Everything fallible goes before startrace: once racetime has opened
            # the room, a failure here would leave it with no row, and the next
            # tick would open a second one.
            actor = actor or await self._system_actor()
            slug = await start_racetime_room(bot, settings)
            try:
                room = await self.room_repository.create(
                    bot_id=bot.id,
                    slug=slug,
                    category=bot.category,
                    room_name=(match.title or f'Match {match.id}'),
                    status=RaceRoomStatus.OPEN,
                    match_id=match.id,
                    opened_at=datetime.now(timezone.utc),
                )
            except Exception:
                logger.exception(
                    'racetime room %s was opened for match %s but not recorded; '
                    'close it on racetime.gg', slug, match.id,
                )
                raise
        await self._audit_and_emit(
            actor, room, match, AuditActions.RACE_ROOM_CREATED, EventType.RACE_ROOM_CREATED,
        )
        await self._audit_and_emit(
            actor, room, match, AuditActions.RACE_ROOM_OPENED, EventType.RACE_ROOM_OPENED,
        )
        await self._notify_players_room_open(match, room)
        if attach_seed:
            await self._attach_seed(match, actor)
        return room

    async def _room_settings(self, tournament: Tournament, match: Match) -> RaceRoomSettings:
        """The ``startrace`` settings: the tournament's profile, plus a goal."""
        profile = await tournament.race_room_profile  # type: ignore[misc]
        goal = self._goal_for(tournament, profile)
        if not goal:
            raise ValueError(
                f'Set a racetime goal on {tournament.name} (or its race room '
                'profile) before opening a room.'
            )
        info = f'{tournament.name} — {match.title}' if match.title else tournament.name
        return RaceRoomSettings.from_profile(profile, goal=goal, info_user=info)

    @staticmethod
    def _goal_for(tournament: Tournament, profile) -> str:
        """The room's goal: the profile's, else the tournament's default, else ''."""
        return ((profile.goal if profile else None) or tournament.racetime_default_goal or '').strip()

    async def _notify_players_room_open(self, match: Match, room: RacetimeRoom) -> None:
        """DM each player that their room is open, with a button into it.

        Best-effort: the room is open whether or not Discord takes the message.
        Players only; crew and watchers have no seat in the race.
        """
        try:
            from application.repositories import MatchRepository
            from application.services import notification_links
            from application.services.discord import DiscordService, discord_queue
            from application.services.tenant_service import TenantService
            from application.utils.discord_embeds import COLOR_STARTED, match_embed, time_field
            from application.utils.discord_messages import race_room_open_dm

            link = notification_links.race_room(room.url)
            if link is None:
                return
            tournament = await self._tournament_of(match)
            players = await MatchRepository.get_players(match.id)
            names = [p.user.preferred_name for p in players]
            body = race_room_open_dm(
                tournament.name, time_field(match.scheduled_at), player_names=names,
            )
            embed = match_embed(
                title='🏁 Your race room is open', color=COLOR_STARTED,
                tournament=tournament.name,
                community_name=await TenantService.current_community_name(),
                player_names=names, when=match.scheduled_at, url=room.url,
            )
            service = DiscordService()
            for player in players:
                user = player.user
                if user.discord_id:
                    discord_queue.enqueue(service.send_dm(
                        int(user.discord_id), body, embed=embed, link=link,
                    ))
        except Exception:
            logger.exception('race room open DM failed for match %s', match.id)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def manual_create_room(self, actor: Optional[User], match_id: int) -> RacetimeRoom:
        """Create a room on demand (STAFF / SYNC_ADMIN), ignoring the auto toggle."""
        await AuthService.ensure_can_manage_sync(actor)
        match = require_found(await self._load_match(match_id), 'Match')
        return await self.create_room_for_match(match, actor=actor)

    # feature-gate: exempt — a worker/series trigger: skips (returns None) rather
    # than raising when RACETIME_ROOMS is off, via its own is_enabled check.
    async def auto_open_if_eligible(
        self, match: Match, *, now: datetime, actor: Optional[User] = None,
    ) -> Optional[RacetimeRoom]:
        """Open ``match``'s room iff every automatic-open condition holds.

        The eligibility rules the auto-open worker applies, as one callable so
        every automatic trigger shares them: the 60s poll
        (``race_room_worker._tick``) and the push that fires the moment a
        best-of-N series' previous game ends. Returns the room when it opened one,
        ``None`` when the match simply isn't eligible yet — not eligible is the
        normal case, not an error, and the caller retries on its next trigger.

        Deliberately **not** folded into :meth:`create_room_for_match`, which
        ``manual_create_room`` also reaches: staff overriding these rules by hand
        is the escape hatch when an automatic path is holding a room back.

        Callers must already be inside the match's ``tenant_scope`` — the feature
        flag resolves against the ambient tenant.
        """
        from application.services.feature_flag_service import FeatureFlagService
        from models import FeatureFlag

        if not await FeatureFlagService().is_enabled(FeatureFlag.RACETIME_ROOMS):
            return None  # tenant has racetime rooms disabled
        lead = match.tournament.room_open_minutes_before or 30
        if match.scheduled_at is None or match.scheduled_at > now + timedelta(minutes=lead):
            return None  # not yet within this tournament's lead window
        if await self.room_repository.get_by_match(match) is not None:
            return None  # idempotent: a room already exists
        bot = await match.tournament.racetime_bot
        if bot is None:
            return None  # no authorized bot to host the room
        if not self._goal_for(match.tournament, await match.tournament.race_room_profile):
            # Staff opening one by hand get the reason; the worker would only
            # log the same refusal every tick.
            logger.info('auto-open skipped for match %s: no racetime goal set', match.id)
            return None
        players = list(match.players)
        if not players or not all(
            getattr(p.user, 'racetime_user_id', None) for p in players
        ):
            # Eligibility gate: every entrant must have linked racetime.
            logger.info(
                'auto-open skipped for match %s: not all entrants have '
                'linked racetime', match.id,
            )
            return None
        actor = actor or await self._system_actor()
        return await self.create_room_for_match(match, actor=actor)

    @requires_feature(FeatureFlag.RACETIME_ROOMS)
    async def open_rooms_for_player(self, user: User) -> List[RacetimeRoom]:
        """The open or running rooms on matches ``user`` plays in, soonest first.

        For My Schedule: a player whose match is raced online needs the way into
        its room where they look for the match, not only in a DM.
        """
        return await self.room_repository.open_for_player(user.id)

    # ---- transitions -----------------------------------------------------
    # Driven by racetime for a room that already exists, so deliberately not
    # gated: switching RACETIME_ROOMS off mid-race must not drop its result or
    # leave a match unable to cancel its room.

    # feature-gate: exempt — lifecycle of an already-open room (see above).
    async def mark_in_progress(self, room: RacetimeRoom, *, actor: Optional[User] = None) -> None:
        actor = actor or await self._system_actor()
        now = datetime.now(timezone.utc)
        match = await self._match_of(room)
        if match is not None and match.started_at is None:
            if match.seated_at is None:
                match.seated_at = now
            match.started_at = now
            await match.save()
        await self.room_repository.update(
            room, status=RaceRoomStatus.IN_PROGRESS,
            opened_at=(room.opened_at or now),
        )
        await self._audit_and_emit(
            actor, room, match, AuditActions.RACE_ROOM_STARTED, EventType.RACE_ROOM_STARTED,
        )

    # feature-gate: exempt — lifecycle of an already-open room; also called by
    # match cancellation, an unrelated flow.
    async def cancel_room(
        self, room: RacetimeRoom, *, actor: Optional[User] = None, reason: Optional[str] = None,
    ) -> None:
        actor = actor or await self._system_actor()
        match = await self._match_of(room)
        await self.room_repository.update(room, status=RaceRoomStatus.CANCELLED)
        await self._audit_and_emit(
            actor, room, match, AuditActions.RACE_ROOM_CANCELLED, EventType.RACE_ROOM_CANCELLED,
            extra={'reason': reason} if reason else None,
        )

    # feature-gate: exempt — lifecycle of an already-open room (see above).
    async def cancel_room_as(
        self, room: RacetimeRoom, *, actor: Optional[User], reason: Optional[str] = None,
    ) -> None:
        """A person calling a room off by hand: staff or a sync admin. Calling off
        the match (``cancel_room`` from match cancellation) needs no sync role."""
        await AuthService.ensure(
            await AuthService.can_manage_sync(actor),
            'You do not have permission to cancel a racetime room.',
        )
        await self.cancel_room(room, actor=actor, reason=reason)

    # feature-gate: exempt — lifecycle of an already-open room (see above).
    async def record_finish(
        self, room: RacetimeRoom, entrants: List[RaceEntrant], *, actor: Optional[User] = None,
    ) -> None:
        """Capture a finished race: map entrants → players, record results, close.

        Handles the terminal states (forfeit / no-show / DQ / one-finisher):
        finishers are placed by racetime's reported place (or elapsed time),
        non-finishers get a null place/time. Entrants whose racetime handle isn't
        linked to a ``User`` are surfaced in the audit detail for staff reconcile.
        """
        actor = actor or await self._system_actor()
        now = datetime.now(timezone.utc)
        match = await self._match_of(room, with_players=True)

        results, unmatched = self._map_results(match, entrants)
        for mp, (rank, ftime) in results.items():
            mp.finish_rank = rank
            mp.finish_time = ftime
            await mp.save()

        if match is not None:
            if match.started_at is None:
                match.started_at = now
            match.finished_at = now
            await match.save()

        await self.room_repository.update(room, status=RaceRoomStatus.FINISHED)

        detail = {
            'ranks': {str(mp.id): rank for mp, (rank, _t) in results.items()},
            'unmatched_handles': unmatched,
        }
        await self._audit_and_emit(
            actor, room, match, AuditActions.RACE_ROOM_FINISHED, EventType.RACE_ROOM_FINISHED,
        )
        await self._audit_and_emit(
            actor, room, match,
            AuditActions.RACE_ROOM_RESULT_RECORDED, EventType.RACE_ROOM_RESULT_RECORDED,
            extra=detail,
        )
        # Feed the existing reporting path so subscribers and downstream systems
        # react exactly as they do for a manually-recorded result.
        if match is not None:
            self._publish_match_result(match, results, actor)
            await self._push_challonge(match, actor)
            await self._settle_bracket(match, actor)

    # ---- result mapping --------------------------------------------------

    @staticmethod
    def _map_results(
        match: Optional[Match], entrants: List[RaceEntrant],
    ) -> Tuple[Dict[MatchPlayers, Tuple[Optional[int], Optional[int]]], List[str]]:
        """Return ``{MatchPlayers: (finish_rank, finish_time)}`` and unmatched handles."""
        results: Dict[MatchPlayers, Tuple[Optional[int], Optional[int]]] = {}
        unmatched: List[str] = []
        by_rtid: Dict[str, MatchPlayers] = {}
        if match is not None:
            for mp in match.players:
                rtid = getattr(mp.user, 'racetime_user_id', None)
                if rtid:
                    by_rtid[rtid] = mp

        finishers = [e for e in entrants if is_scored_finish(e)]
        finishers.sort(key=lambda e: (e.place if e.place is not None else e.finish_time))

        seen: set = set()
        for index, entrant in enumerate(finishers, start=1):
            rank = entrant.place if entrant.place is not None else index
            mp = by_rtid.get(entrant.user_id)
            if mp is not None:
                results[mp] = (rank, entrant.finish_time)
                seen.add(entrant.user_id)
            else:
                unmatched.append(unmatched_handle(entrant))

        for entrant in entrants:
            if entrant.user_id in seen:
                continue
            mp = by_rtid.get(entrant.user_id)
            if mp is not None:
                results[mp] = (None, None)  # forfeit / no-show / DQ
            elif entrant.status != EntrantStatus.DONE:
                unmatched.append(unmatched_handle(entrant))
        return results, unmatched

    # ---- internals -------------------------------------------------------

    async def _attach_seed(self, match: Match, actor: User) -> None:
        """Roll and attach the tournament's seed (best-effort, non-blocking failure)."""
        tournament = await self._tournament_of(match)
        if not (tournament.preset_id or tournament.seed_generator):
            return
        try:
            from application.services.match.match_schedule_service import MatchScheduleService

            ok, message, _url = await MatchScheduleService().generate_seed(match.id, actor)
            if not ok:
                logger.warning('seed attach for match %s refused: %s', match.id, message)
        except Exception:
            logger.exception('seed attach failed for match %s', match.id)

    def _publish_match_result(self, match: Match, results, actor: User) -> None:
        from application.events import match_live

        ranks = {str(mp.id): rank for mp, (rank, _t) in results.items()}
        match_live.publish(match.id)
        event_bus.publish(Event.create(EventType.MATCH_RESULT_RECORDED, {
            'match_id': match.id,
            'tournament_id': match.tournament_id,
            'ranks': ranks,
            'source': 'racetime',
        }, actor))

    async def _settle_bracket(self, match: Match, actor: User) -> None:
        """Record this race as its bracket series game, if it backs one.

        Peer of :meth:`_push_challonge`, and necessary for the same reason it
        exists: a racetime finish stamps ``finished_at`` but never *confirms* the
        match, and ``advance_if_linked`` hangs off ``confirm_match``, whose only
        callers are human. Without this a best-of series on an auto-room
        tournament would sit un-clinched — and its unneeded games un-cancelled —
        until someone clicked Confirm on every game.

        Settling is a compare-and-swap on the game row, so a later human confirm
        is a no-op rather than a double count.
        """
        try:
            from application.services.bracket_service import BracketService

            await BracketService().settle_game_if_linked(match, actor)
        except Exception:
            logger.exception('bracket settle failed for match %s', match.id)

    async def _push_challonge(self, match: Match, actor: User) -> None:
        try:
            from application.services.challonge_service import ChallongeService

            await ChallongeService().push_result_if_linked(match, actor)
        except Exception:
            logger.exception('challonge push failed for match %s', match.id)

    async def _system_actor(self) -> User:
        return await UserService().get_system_user()

    async def _tournament_of(self, match: Match) -> Tournament:
        tournament = getattr(match, 'tournament', None)
        if isinstance(tournament, Tournament):
            return tournament
        return await Tournament.get(id=match.tournament_id, tenant_id=require_tenant_id())

    async def _match_of(self, room: RacetimeRoom, *, with_players: bool = False) -> Optional[Match]:
        if room.match_id is None:
            return None
        query = Match.get_or_none(id=room.match_id, tenant_id=require_tenant_id())
        if with_players:
            query = query.prefetch_related('players', 'players__user', 'tournament')
        else:
            query = query.prefetch_related('tournament')
        return await query

    async def _load_match(self, match_id: int) -> Optional[Match]:
        return await Match.get_or_none(
            id=match_id, tenant_id=require_tenant_id(),
        ).prefetch_related('tournament', 'players', 'players__user')

    async def _audit_and_emit(
        self, actor: User, room: RacetimeRoom, match: Optional[Match],
        audit_action: str, event_type: str, *, extra: Optional[dict] = None,
    ) -> None:
        detail = {
            'room_id': room.id,
            'slug': room.slug,
            'category': room.category,
            'match_id': room.match_id,
        }
        if match is not None:
            detail['tournament_id'] = match.tournament_id
        if extra:
            detail.update(extra)
        # Builds this domain's shared room-detail payload, then hands the
        # audit-then-publish pairing to the one shared implementation rather than
        # repeating it here.
        await self.audit_service.write_and_publish(
            actor, audit_action, detail, event_type,
        )


# Lifecycle adapter the racetimebot/ handler injects: translates a transport
# RaceRoomEvent into the matching RaceRoomService transition. Lives here (not in
# racetimebot/) so the handler stays a thin presentation shim.
class RaceRoomLifecycle:
    """Route a transport room event to the right :class:`RaceRoomService` call."""

    def __init__(self, service: Optional[RaceRoomService] = None) -> None:
        self.service = service or RaceRoomService()

    async def handle_event(self, room: RacetimeRoom, event: RaceRoomEvent) -> None:
        # A room with no match is a qualifier live race (PR 10); route it to the
        # qualifier capture path instead of the match path.
        if room.match_id is None and await self._route_qualifier(room, event):
            return
        if event.status == RaceRoomStatus.IN_PROGRESS:
            await self.service.mark_in_progress(room)
        elif event.status == RaceRoomStatus.FINISHED:
            await self.service.record_finish(room, event.entrants)
        elif event.status == RaceRoomStatus.CANCELLED:
            await self.service.cancel_room(room)
        # OPEN is a no-op: the room already exists in that state.

    async def _route_qualifier(self, room: RacetimeRoom, event: RaceRoomEvent) -> bool:
        """Drive a qualifier live race if the room maps to one; else return False.

        Runs inside the handler's ``tenant_scope(room.tenant_id)`` so the scoped
        by-slug lookup resolves the tenant's own live race.
        """
        from application.services.async_qualifier.async_qualifier_live_race_service import (
            AsyncQualifierLiveRaceService,
        )

        service = AsyncQualifierLiveRaceService()
        live_race = await service.repository.get_by_racetime_slug(room.slug)
        if live_race is None:
            return False
        if event.status == RaceRoomStatus.IN_PROGRESS:
            await service.mark_in_progress(live_race)
            await self.service.room_repository.update(room, status=RaceRoomStatus.IN_PROGRESS)
        elif event.status == RaceRoomStatus.FINISHED:
            await service.record_finish(live_race, event.entrants)
            await self.service.room_repository.update(room, status=RaceRoomStatus.FINISHED)
        elif event.status == RaceRoomStatus.CANCELLED:
            # The race itself, not only its room: without this the qualifier's live
            # race stays at scheduled or in-progress with nothing to distinguish it
            # from one still to come.
            await service.mark_cancelled(live_race)
            await self.service.room_repository.update(room, status=RaceRoomStatus.CANCELLED)
        return True
