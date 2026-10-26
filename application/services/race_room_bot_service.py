"""The racetime room bot: what it says and does inside a room it runs.

A tournament with ``racetime_bot_runs_room`` has the category bot sit in each
of its match rooms (``racetimebot/transport.py``). This service is the bot's
judgement there:

* **On arrival** it invites every linked player who isn't in the room yet,
  greets the room once, and posts the seed if it has come due.
* **The seed** goes up ``racetime_seed_post_minutes`` before the start, from
  the race-room worker's tick, or as soon as an async roll completes when that
  is later. ``!forceroll`` posts it early. It goes up once (``seed_posted_at``).
* **Chat commands**: ``!seed`` shows it again, ``!forceroll`` (room monitors
  and the match's staff) posts it now, ``!fpa`` (a runner, mid-race) logs a fair
  play pause, tells on-duty staff, and holds the result for them.

Everything here runs on an already-open room, so like the lifecycle methods it
is not feature-gated: switching racetime rooms off stops new rooms opening and
the bot leaving the open ones (``list_bot_run_rooms``), not a race half run.
Speaking never raises: a room the bot can't reach is a room it stays quiet in.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from application.event_context import event_scope
from application.events import EventType
from application.repositories import RacetimeRoomRepository
from application.repositories._save import save_fields
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.user_service import UserService
from application.utils.clients import racetime_room_actions
from application.utils.discord_messages import is_seed_link, seed_code_line
from application.utils.duration import format_hms
from models import Match, RaceRoomStatus, RacetimeRoom, User
from racetimebot.protocol import entrant_ids
from racetimebot.transport import RaceRoomEvent

logger = logging.getLogger(__name__)

# Runners are untrusted: one !fpa a minute each, and a cap per race, keeps a
# spammed command from turning into a stream of staff pings.
FPA_COOLDOWN_SECONDS = 60
MAX_FPA_CALLS = 10
# !seed repeats the seed at most this often per room, so chat can't be flooded.
SEED_REPEAT_SECONDS = 20

# Process-local throttles; the single-worker deployment (docs/scaling-roadmap.md)
# makes that enough, and a restart only resets a 20-second window.
_last_seed_repeat: Dict[str, float] = {}
# Matches whose seed was rolled by !forceroll on an async randomizer: post it the
# moment the roll lands rather than waiting for the set time.
_forced_ids: set = set()


def seed_due(match: Match, minutes: int, now: datetime) -> bool:
    """Whether the seed should be in the room by ``now``."""
    if match.scheduled_at is None:
        return False
    return now >= match.scheduled_at - timedelta(minutes=max(0, minutes))


class RaceRoomBotService:
    """The bot's side of a racetime room it runs."""

    def __init__(self) -> None:
        self.room_repository = RacetimeRoomRepository()
        self.audit_service = AuditService()

    # ---- arrival ---------------------------------------------------------

    # feature-gate: exempt — lifecycle of an already-open room (see module docstring).
    async def on_joined(self, room: RacetimeRoom, event: RaceRoomEvent) -> None:
        match = await self._match(room)
        if match is None or not match.tournament.racetime_bot_runs_room:
            return
        with event_scope(match.event_id, match.tenant_id):
            if event.status == RaceRoomStatus.OPEN:
                present = set(entrant_ids(event))
                for mp in match.players:
                    rtid = mp.user.racetime_user_id
                    if rtid and rtid not in present:
                        await racetime_room_actions.invite(room.slug, rtid)
            if await self.room_repository.claim_greeting(room, datetime.now(timezone.utc)):
                await racetime_room_actions.say(room.slug, self._greeting(match))
            await self.post_seed_if_due(room, match)

    def _greeting(self, match: Match) -> str:
        minutes = match.tournament.racetime_seed_post_minutes
        when = (
            f'{minutes} minutes before the start' if minutes > 0 else 'at the start'
        ) if match.scheduled_at else 'when staff release it'
        lines = [
            f"Hi! I'm running this room for {match.tournament.name}. "
            f"The seed posts here {when}.",
            'Type !seed to see it again, or !fpa to call a fair play pause during the race.',
        ]
        unlinked = [mp.user.preferred_name for mp in match.players if not mp.user.racetime_user_id]
        if unlinked:
            lines.append(
                f"I can't invite {', '.join(unlinked)} yet: link racetime.gg on your "
                'Wizzrobe profile, then join from this page.'
            )
        return ' '.join(lines)

    # ---- the seed --------------------------------------------------------

    # feature-gate: exempt — a worker step over already-open rooms; skips, never raises.
    async def post_due_seeds(self, now: datetime) -> None:
        """Post every bot-run room's seed that has come due (the worker's tick)."""
        from application.utils.background_loop import for_each_tenant_scoped

        rooms = [
            r for r in await self.room_repository.bot_run_awaiting_seed()
            if r.match is not None
            and seed_due(r.match, r.match.tournament.racetime_seed_post_minutes, now)
        ]

        async def _post(room: RacetimeRoom) -> None:
            await self.post_seed_if_due(room, await self._match(room), now=now)

        await for_each_tenant_scoped(
            rooms, _post,
            tenant_id_of=lambda r: r.tenant_id,
            event_id_of=lambda r: r.match.event_id,
            logger=logger,
            describe=lambda r: f'racetime room {r.slug}',
        )

    async def post_seed_if_due(
        self, room: RacetimeRoom, match: Optional[Match], *, now: Optional[datetime] = None,
    ) -> None:
        if match is None or room.seed_posted_at is not None:
            return
        now = now or datetime.now(timezone.utc)
        if seed_due(match, match.tournament.racetime_seed_post_minutes, now):
            await self._post_seed(room, match)

    async def _post_seed(self, room: RacetimeRoom, match: Match) -> str:
        """Put the seed in the room, rolling it first if there isn't one.

        Returns what happened: ``'posted'``, ``'rolling'`` (an async roll is on
        its way and posts when it lands), or ``'failed'``.
        """
        if match.generated_seed_id is None:
            from application.services.match.match_schedule_service import MatchScheduleService

            system_user = await UserService().get_system_user()
            ok, message, url = await MatchScheduleService().generate_seed(match.id, system_user)
            match = await self._match(room) or match
            if match.generated_seed_id is None:
                if ok or 'in progress' in message:
                    _forced_ids.add(match.id)
                    return 'rolling'
                logger.warning('room bot could not roll the seed for match %s: %s', match.id, message)
                return 'failed'
        if not await self.room_repository.claim_seed_post(room, datetime.now(timezone.utc)):
            return 'posted'
        _forced_ids.discard(match.id)
        await racetime_room_actions.say(room.slug, await self._seed_text(match))
        return 'posted'

    async def _seed_text(self, match: Match) -> str:
        seed = await match.generated_seed
        url = seed.seed_url if seed else ''
        code = seed_code_line(seed.seed_code if seed else None)
        if url and is_seed_link(url):
            return f'Seed: {url}' + (f' · {code}' if code else '')
        from application.services import notification_links

        link = await notification_links.player_match(match.id)
        where = f': {link.url}' if link else ''
        return 'The seed is ready. Copy it from your match on Wizzrobe' + where + (f' · {code}' if code else '')

    # feature-gate: exempt — event-bus subscriber for an already-open room; skips, never raises.
    async def on_seed_rolled(self, match_id: int) -> None:
        """A seed just rolled: post it now if it is due, or !forceroll asked for it."""
        room = await self.room_repository.get_by_match(Match(id=match_id))
        if room is None or room.status not in (RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS):
            return
        match = await self._match(room)
        if match is None or not match.tournament.racetime_bot_runs_room:
            return
        with event_scope(match.event_id, match.tenant_id):
            if match.id in _forced_ids:
                await self._post_seed(room, match)
            else:
                await self.post_seed_if_due(room, match)

    # ---- chat ------------------------------------------------------------

    # feature-gate: exempt — lifecycle of an already-open room (see module docstring).
    async def on_chat(self, room: RacetimeRoom, event: RaceRoomEvent) -> None:
        line = event.chat
        if line is None or not line.text.startswith('!'):
            return
        command, _, rest = line.text.partition(' ')
        command = command.lower()
        if command not in ('!seed', '!forceroll', '!fpa'):
            return
        match = await self._match(room)
        if match is None or not match.tournament.racetime_bot_runs_room:
            return
        with event_scope(match.event_id, match.tenant_id):
            if command == '!seed':
                await self._command_seed(room, match)
            elif command == '!forceroll':
                await self._command_forceroll(room, match, event)
            else:
                await self._command_fpa(room, match, event, rest.strip())

    async def _command_seed(self, room: RacetimeRoom, match: Match) -> None:
        now = time.monotonic()
        if now - _last_seed_repeat.get(room.slug, 0) < SEED_REPEAT_SECONDS:
            return
        _last_seed_repeat[room.slug] = now
        if room.seed_posted_at is not None:
            await racetime_room_actions.say(room.slug, await self._seed_text(match))
            return
        if match.scheduled_at is None:
            await racetime_room_actions.say(room.slug, "The seed isn't out yet.")
            return
        due = match.scheduled_at - timedelta(minutes=match.tournament.racetime_seed_post_minutes)
        minutes = max(1, int((due - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
        await racetime_room_actions.say(
            room.slug, f"The seed posts in {minutes} minute{'s' if minutes != 1 else ''}.",
        )

    async def _command_forceroll(self, room: RacetimeRoom, match: Match, event: RaceRoomEvent) -> None:
        assert event.chat is not None
        if not await self._may_force(match, event):
            await racetime_room_actions.say(
                room.slug, 'Only room monitors and staff can use !forceroll.',
            )
            return
        if room.seed_posted_at is not None:
            await racetime_room_actions.say(room.slug, 'The seed is already out. Type !seed to see it again.')
            return
        outcome = await self._post_seed(room, match)
        if outcome == 'rolling':
            await racetime_room_actions.say(room.slug, "Rolling the seed now. It'll post here when it's ready.")
        elif outcome == 'failed':
            await racetime_room_actions.say(
                room.slug, "The seed didn't roll. Staff can roll it from the match on Wizzrobe.",
            )

    async def _may_force(self, match: Match, event: RaceRoomEvent) -> bool:
        assert event.chat is not None
        if event.chat.user_id and event.chat.user_id in event.monitor_ids:
            return True
        user = await UserService().get_user_by_racetime_id(event.chat.user_id)
        return user is not None and await AuthService.can_run_match(user, match)

    async def _command_fpa(
        self, room: RacetimeRoom, match: Match, event: RaceRoomEvent, note: str,
    ) -> None:
        line = event.chat
        assert line is not None
        if line.user_id not in entrant_ids(event):
            await racetime_room_actions.say(room.slug, 'Only runners in this race can call !fpa.')
            return
        if event.status != RaceRoomStatus.IN_PROGRESS or event.started_at is None:
            await racetime_room_actions.say(room.slug, '!fpa works once the race has started.')
            return
        calls = list(room.fpa_calls or [])
        now = datetime.now(timezone.utc)
        mine = [c for c in calls if c.get('racetime_user_id') == line.user_id]
        if mine and (now - datetime.fromisoformat(mine[-1]['at'])).total_seconds() < FPA_COOLDOWN_SECONDS:
            return
        if len(calls) >= MAX_FPA_CALLS:
            return
        race_seconds = max(0, int((now - event.started_at).total_seconds()))
        calls.append({
            'racetime_user_id': line.user_id,
            'name': line.display_name,
            'at': now.isoformat(),
            'race_seconds': race_seconds,
            'note': note[:300] or None,
        })
        room.fpa_calls = calls
        await save_fields(room, 'fpa_calls')
        race_time = format_hms(race_seconds)
        await racetime_room_actions.say(
            room.slug,
            f'FPA noted at {race_time}. Staff have been told, and this result will wait for them.',
        )
        system_user = await UserService().get_system_user()
        await self.audit_service.write_and_publish(
            system_user, AuditActions.RACE_ROOM_FPA_CALLED,
            {
                'room_id': room.id, 'slug': room.slug, 'match_id': match.id,
                'tournament_id': match.tournament_id, 'runner': line.display_name,
                'race_seconds': race_seconds,
            },
            EventType.RACE_ROOM_FPA_CALLED,
        )
        await self._tell_staff_fpa(match, room, line.display_name, race_time, note or None)

    async def _tell_staff_fpa(
        self, match: Match, room: RacetimeRoom, runner: str, race_time: str, note: Optional[str],
    ) -> None:
        from application.services import notification_links
        from application.services.discord import DiscordService, discord_queue
        from application.services.notification_catalog import RACETIME_FPA
        from application.services.staff_alert_service import StaffAlertService
        from application.utils.discord_embeds import COLOR_RESCHEDULED, match_embed
        from application.utils.discord_messages import racetime_fpa_staff_dm

        try:
            recipients = await StaffAlertService.tagged_match_alert_recipients(
                match, can_act=AuthService.can_run_match,
            )
            if not recipients:
                return
            message = racetime_fpa_staff_dm(runner, match.tournament.name, race_time, note)
            embed = match_embed(
                title='⏸️ Fair play pause called', color=COLOR_RESCHEDULED, description=message,
                tournament=match.tournament.name,
                player_names=[mp.user.preferred_name for mp in match.players],
                when=match.scheduled_at, url=room.url,
            )
            link = notification_links.race_room(room.url, label='Open the race room')
            service = DiscordService()
            for user, why in recipients:
                if user.discord_id:
                    discord_queue.enqueue(service.send_dm(
                        int(user.discord_id), message, embed=embed, link=link,
                        notice=RACETIME_FPA.to(why),
                    ))
        except Exception:
            logger.exception('FPA staff alert failed for match %s', match.id)

    # ---- internals -------------------------------------------------------

    async def _match(self, room: RacetimeRoom) -> Optional[Match]:
        if room.match_id is None:
            return None
        return await Match.get_or_none(
            id=room.match_id, tenant_id=room.tenant_id,
        ).prefetch_related('tournament', 'players', 'players__user')


async def on_seed_rolled_event(event) -> None:
    """Event-bus subscriber (``MATCH_SEED_ROLLED``): post a rolled seed in its bot-run room."""
    match_id = (event.payload or {}).get('match_id')
    if not match_id:
        return
    try:
        await RaceRoomBotService().on_seed_rolled(int(match_id))
    except Exception:
        logger.exception('room bot: posting the rolled seed for match %s failed', match_id)
