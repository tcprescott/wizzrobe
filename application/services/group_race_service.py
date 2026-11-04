"""Group races: open racetime rooms with no player list, announced in Discord.

A tournament with ``racetime_group_race`` (a daily, a weekly) has matches with
no players. Its rooms open on their own like any other, but open to anyone;
when one opens the bot posts it in the tournament's Discord channel with an
optional role ping. The seed goes up like any bot-run room's, and with
``racetime_lock_at_seed`` the room turns invite-only at the same moment. On
finish, every entrant who linked racetime here becomes a player on the match
with their place and time; the rest are kept on the room by racetime name.
Nothing is held for staff.

Like the room bot, every step here acts on an already-open room, so none of it
is feature-gated (``RACETIME_ROOMS`` stops new rooms opening). Announcing and
locking never raise: a Discord or racetime hiccup leaves the race running.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from application.events import EventType
from application.repositories import MatchRepository, RacetimeRoomRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.user_service import UserService
from application.utils.clients import racetime_room_actions
from application.utils.racetime_entrants import is_scored_finish, unmatched_handle
from models import Match, RaceRoomProfile, RacetimeBot, RacetimeRoom, Tournament, User
from racetimebot.transport import RaceEntrant

logger = logging.getLogger(__name__)

LOCKED_MESSAGE = 'This room is now locked. Late entries are not permitted.'


def make_invitational_action() -> dict:
    return {'action': 'make_invitational'}


def is_group_race(tournament: Optional[Tournament]) -> bool:
    return bool(tournament is not None and tournament.racetime_group_race)


async def tournament_of(match: Match) -> Tournament:
    loaded = getattr(match, 'tournament', None)
    if isinstance(loaded, Tournament):
        return loaded
    return await Tournament.get(id=match.tournament_id, tenant_id=match.tenant_id)  # type: ignore[attr-defined]


def greeting(tournament: Tournament, scheduled: bool) -> str:
    minutes = tournament.racetime_seed_post_minutes
    when = (
        f'{minutes} minutes before the start' if minutes > 0 else 'at the start'
    ) if scheduled else 'when staff release it'
    lock = ' The room locks when the seed goes out, so join before then.' if tournament.racetime_lock_at_seed else ''
    return (
        f"Hi! I'm running this room for {tournament.name}. Anyone can join. "
        f'The seed posts here {when}.{lock} '
        'Type !seed to see it again, or !fpa to call a fair play pause during the race. '
        'Link racetime.gg on your Wizzrobe profile to have this race on your history.'
    )


async def announce_room(
    room: RacetimeRoom, *, channel_id: str, role_id: Optional[str], title: str,
    starts_at: Optional[datetime], seed_minutes: int, locks: bool,
) -> bool:
    """Post an open group race room in a Discord channel, once per room. Never raises.

    Shared by tournament group races and qualifier live races. ``True`` when
    the post went out; a failed post releases the claim so the worker retries.
    """
    repository = RacetimeRoomRepository()
    try:
        from application.services.tenant_service import TenantService

        tenant = await TenantService.get_by_id(room.tenant_id)  # type: ignore[attr-defined]
        if tenant is None or tenant.discord_guild_id is None:
            logger.debug('group race room %s not announced: no Discord server linked', room.slug)
            return False
        if not await repository.claim_announcement(room, datetime.now(timezone.utc)):
            return False
        ok, why = await _post(
            int(tenant.discord_guild_id), room, channel_id=channel_id, role_id=role_id,
            title=title, starts_at=starts_at, seed_minutes=seed_minutes, locks=locks,
        )
        if not ok:
            logger.warning('group race room %s announcement failed: %s', room.slug, why)
            await repository.release_announcement(room)
            return False
        return True
    except Exception:
        logger.exception('group race room %s announcement crashed', room.slug)
        return False


async def _post(
    guild_id: int, room: RacetimeRoom, *, channel_id: str, role_id: Optional[str], title: str,
    starts_at: Optional[datetime], seed_minutes: int, locks: bool,
):
    from application.services import notification_links
    from application.services.discord import DiscordService
    from application.services.tenant_service import TenantService
    from application.utils.discord_embeds import COLOR_STARTED, notification_embed, time_field

    lines = []
    if starts_at:
        lines.append(f'Starts {time_field(starts_at)}')
        seed_at = starts_at - timedelta(minutes=seed_minutes)
        lock_note = ', and the room locks then' if locks else ''
        lines.append(f'Seed posts <t:{int(seed_at.timestamp())}:t>{lock_note}')
    embed = notification_embed(
        title=f'🏁 {title}', color=COLOR_STARTED, url=room.url,
        description='\n'.join(lines) or None,
        community_name=await TenantService.current_community_name(),
    )
    ping = f'<@&{role_id}> ' if role_id else ''
    message = f'{ping}The race room for {title} is open. Anyone can join.'
    return await DiscordService().post_to_channel(
        guild_id, int(channel_id), message,
        embed=embed, link=notification_links.race_room(room.url),
        ping_role_id=int(role_id) if role_id else None,
    )


async def lock_room(room: RacetimeRoom) -> bool:
    """Turn an open room invite-only, once, and say so in the room. Never raises."""
    try:
        if not await RacetimeRoomRepository().claim_lock(room, datetime.now(timezone.utc)):
            return False
        await racetime_room_actions.send(room.slug, make_invitational_action())
        await racetime_room_actions.say(room.slug, LOCKED_MESSAGE)
        return True
    except Exception:
        logger.exception('locking group race room %s failed', room.slug)
        return False


@dataclass
class RoomOptions:
    """What a racetime settings form can pick from."""

    bots: List[RacetimeBot]
    profiles: List[RaceRoomProfile]
    channels: List[dict]
    roles: List[dict]
    discord_problem: Optional[str]


class GroupRaceService:
    """What a group race does beyond a match room: announce, lock, record everyone."""

    def __init__(self) -> None:
        self.room_repository = RacetimeRoomRepository()
        self.audit_service = AuditService()

    # feature-gate: exempt — a read of pickable names for the tournament and qualifier forms.
    async def room_options(self, actor: Optional[User]) -> RoomOptions:
        """The bots, room profiles, Discord channels and roles a settings form offers.

        Discord being unreachable isn't an error: the lists come back empty with
        ``discord_problem`` saying why, so the form can still save.
        """
        from application.services.auth_service import AuthService
        from application.services.discord import DiscordService
        from application.services.race_room_profile_service import RaceRoomProfileService
        from application.services.racetime_bot_service import RacetimeBotService
        from application.services.tenant_service import TenantService
        from application.tenant_context import require_tenant_id

        allowed = await AuthService.can_view_admin(actor) or await AuthService.can_admin_qualifier(actor, None)
        await AuthService.ensure(allowed, 'Cannot edit racetime settings')
        tenant_id = require_tenant_id()
        bots = await RacetimeBotService().list_authorized_for_tenant(tenant_id)
        profiles = await RaceRoomProfileService().list_selectable()
        tenant = await TenantService.get_by_id(tenant_id)
        if tenant is None or tenant.discord_guild_id is None:
            return RoomOptions(bots, profiles, [], [], 'No Discord server is connected.')
        discord = DiscordService()
        guild_id = int(tenant.discord_guild_id)
        ok_channels, channels = await discord.list_text_channels(guild_id)
        ok_roles, roles = await discord.list_guild_roles(guild_id)
        problem = None
        if not ok_channels or not ok_roles:
            problem = str(channels if not ok_channels else roles)
        role_list = sorted(
            (r for r in (roles if ok_roles and not isinstance(roles, str) else []) if r.get('name') != '@everyone'),
            key=lambda r: str(r['name']).casefold(),
        )
        return RoomOptions(
            bots, profiles,
            [{'id': str(c['id']), 'name': str(c['name'])} for c in (channels if ok_channels and not isinstance(channels, str) else [])],
            [{'id': str(r['id']), 'name': str(r['name'])} for r in role_list],
            problem,
        )

    # feature-gate: exempt — called as an already-opened room is set up; never raises.
    async def announce(self, room: RacetimeRoom, match: Match, *, actor: User) -> bool:
        """Post the room in the tournament's channel once. ``True`` when it went out."""
        tournament = await tournament_of(match)
        if not is_group_race(tournament) or not tournament.racetime_announce_channel_id:
            return False
        sent = await announce_room(
            room, channel_id=tournament.racetime_announce_channel_id,
            role_id=tournament.racetime_announce_role_id,
            title=match.title or tournament.name, starts_at=match.scheduled_at,
            seed_minutes=tournament.racetime_seed_post_minutes,
            locks=tournament.racetime_lock_at_seed,
        )
        if sent:
            await self._audit(actor, AuditActions.RACE_ROOM_ANNOUNCED, EventType.RACE_ROOM_ANNOUNCED, room, match, {
                'channel_id': tournament.racetime_announce_channel_id,
            })
        return sent

    # feature-gate: exempt — a worker step over already-open rooms; skips, never raises.
    async def announce_pending(self) -> None:
        """Retry every open group race room whose announcement hasn't gone out."""
        from application.utils.background_loop import for_each_tenant_scoped

        rooms = await self.room_repository.group_race_awaiting_announcement()
        if not rooms:
            return
        actor = await UserService().get_system_user()

        async def _announce(room: RacetimeRoom) -> None:
            await self.announce(room, room.match, actor=actor)  # type: ignore[arg-type]

        await for_each_tenant_scoped(
            rooms, _announce,
            tenant_id_of=lambda r: r.tenant_id,  # type: ignore[attr-defined]
            event_id_of=lambda r: r.match.event_id,  # type: ignore[union-attr]
            logger=logger,
            describe=lambda r: f'group race room {r.slug}',
        )

    # feature-gate: exempt — lifecycle of an already-open room; never raises.
    async def lock_if_set(self, room: RacetimeRoom, match: Match) -> bool:
        """Turn the room invite-only after the seed posts, when the tournament asks for it."""
        tournament = await tournament_of(match)
        if not is_group_race(tournament) or not tournament.racetime_lock_at_seed:
            return False
        if not await lock_room(room):
            return False
        actor = await UserService().get_system_user()
        await self._audit(actor, AuditActions.RACE_ROOM_LOCKED, EventType.RACE_ROOM_LOCKED, room, match, None)
        return True

    # feature-gate: exempt — lifecycle of an already-open room (see module docstring).
    async def add_linked_entrants(self, match: Match, entrants: List[RaceEntrant]) -> List[dict]:
        """Make each linked entrant a player on ``match``; return the rest as plain records.

        Returns ``[{name, place, finish_seconds, status}]`` for entrants who
        haven't linked racetime here, in finishing order then by name.
        """
        users = UserService()
        have = {mp.user_id for mp in match.players}  # type: ignore[attr-defined]
        unmatched: List[dict] = []
        for entrant in entrants:
            user = await users.get_user_by_racetime_id(entrant.user_id)
            if user is None:
                finished = is_scored_finish(entrant)
                unmatched.append({
                    'name': unmatched_handle(entrant)[:100],
                    'place': entrant.place if finished else None,
                    'finish_seconds': entrant.finish_time if finished else None,
                    'status': entrant.status.value,
                })
                continue
            if user.id not in have:
                await MatchRepository.add_player(match, user)
                have.add(user.id)
        unmatched.sort(key=lambda e: (e['place'] is None, e['place'] or 0, e['name'].lower()))
        return unmatched

    async def _audit(
        self, actor: User, action: str, event_type: str, room: RacetimeRoom, match: Match,
        extra: Optional[dict],
    ) -> None:
        detail = {
            'room_id': room.id, 'slug': room.slug, 'category': room.category,
            'match_id': match.id, 'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
        }
        detail.update(extra or {})
        await self.audit_service.write_and_publish(actor, action, detail, event_type)
