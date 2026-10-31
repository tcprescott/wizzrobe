"""Crew signups on bracket matchups nobody has booked yet - Business Logic Layer.

Commentators, trackers and broadcasters want to claim a final while its semifinals are still
unplayed, and a matchup only had a match to sign up on once someone booked it
with a time. So the first crew signup on an open matchup makes a **crew hold**:
a ``Match`` with no players and no time (``Match.crew_hold_source`` /
``crew_hold_key`` name the matchup), which carries the signups exactly as a
booked match does: approval, withdrawal, the owners' DMs, My Schedule.

The hold is never linked to the matchup, so the matchup stays unbooked for the
players and for staff. Booking it (any game of a set first) calls
:meth:`CrewHoldService.absorb`, which moves the signups onto the booking, keeps
their approvals, deletes the hold and tells the crew's owners about any approved
member who is busy at the booked time. Crew themselves hear nothing new: they
are told once the match is on a stage, as on every match.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from application.event_context import get_current_event_id
from application.events import Event, EventType, event_bus, match_live
from application.repositories import (
    BroadcasterRepository,
    CommentaryInviteRepository,
    CommentatorRepository,
    MatchRepository,
    TrackerRepository,
)
from application.repositories.bracket_repository import BracketRepository
from application.repositories.challonge_repository import ChallongeRepository
from application.repositories.startgg_repository import StartggRepository
from application.services import bracket_sources, notification_links
from application.services.audit_service import AuditActions, AuditService
from application.services.bracket_sources import UnbookedMatch
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.event_service import EventService
from application.services.notification_catalog import ADMINS, CREW_BOOKED_BUSY
from application.services.tenant_service import TenantService
from application.utils.discord_embeds import COLOR_CANCELLED, notification_embed
from models import Match, User

logger = logging.getLogger(__name__)

@dataclass
class FutureMatch:
    """One open bracket matchup crew can sign up for, with its hold once someone has."""

    matchup: UnbookedMatch
    hold: Optional[Match] = None

    @property
    def ref(self) -> Tuple[str, int]:
        return self.matchup.source, self.matchup.key


def _wants_crew(matchup: UnbookedMatch, broadcaster_events: Set[int]) -> bool:
    t = matchup.tournament
    return bool(t.required_commentators or t.required_trackers
                or (t.required_broadcasters and t.event_id in broadcaster_events))


class CrewHoldService:
    def __init__(self) -> None:
        self.repository = MatchRepository()
        self.audit_service = AuditService()

    async def list_future(self) -> List[FutureMatch]:
        """Every open matchup crew can sign up for ahead, each with its hold if it has one.

        Also the safety net for :meth:`absorb`: a hold whose matchup has since
        been booked by a path that didn't hand its crew over gets it here, and a
        hold whose label has gone stale (a sync filled in a side) is relabelled.
        """
        candidates = await bracket_sources.list_open_for_crew()
        broadcaster_events = await EventService.broadcaster_events(m.tournament.event_id for m in candidates)
        open_rows = [m for m in candidates if _wants_crew(m, broadcaster_events)]
        holds = {(h.crew_hold_source, h.crew_hold_key): h for h in await self.repository.list_crew_holds()}
        out: List[FutureMatch] = []
        for matchup in open_rows:
            item = FutureMatch(matchup, holds.pop((matchup.source, matchup.key), None))
            hold = item.hold
            if hold is not None and not (list(hold.commentators) or list(hold.trackers)  # type: ignore[attr-defined]
                                         or list(hold.broadcasters)):  # type: ignore[attr-defined]
                await self.repository.delete(hold)
                item.hold = None
            if item.hold is not None:
                await self._relabel(item.hold, matchup)
            out.append(item)
        for source, key in holds:
            booked = await _booked_match(source, key)
            if booked is not None:
                await self.absorb(booked, None)
        return out

    async def hold_ids(self) -> Dict[int, FutureMatch]:
        """``hold match id -> FutureMatch`` for the holds still waiting on a booking."""
        return {f.hold.id: f for f in await self.list_future() if f.hold is not None}

    async def dress_schedule_rows(
        self, rows: List[Dict], tournament_ids: Optional[List[int]] = None,
    ) -> List[Dict]:
        """The schedule's rows with every matchup crew can sign up for ahead.

        A hold's own row (it is a match) is tagged with its matchup and kept
        only while the matchup is still open; every other open matchup gets a
        line of its own (:func:`_future_row`). ``tournament_ids`` narrows the
        added lines the way the board's Tournament filter narrows its matches.
        """
        futures = await self.list_future()
        broadcaster_events = await EventService.broadcaster_events(
            f.matchup.tournament.event_id for f in futures)
        order = {f.ref: n for n, f in enumerate(futures, start=1)}
        by_hold = {f.hold.id: f for f in futures if f.hold is not None}
        blocked = await _player_discord_ids(futures)
        out: List[Dict] = []
        for row in rows:
            if not row.get('crew_hold'):
                out.append(row)
                continue
            future = by_hold.get(row['id'])
            if future is None:
                continue
            row['_future_ref'] = f'{future.matchup.source}:{future.matchup.key}'
            row['_players_tbd'] = future.matchup.players_tbd
            row['_crew_blocked'] = blocked.get(future.ref, [])
            row['_future_order'] = order[future.ref]
            row['set_label'] = future.matchup.round_label or None
            out.append(row)
        event_id = get_current_event_id()
        for future in futures:
            if future.hold is not None:
                continue
            if tournament_ids is not None and future.matchup.tournament.id not in tournament_ids:
                continue
            if event_id is not None and future.matchup.tournament.event_id != event_id:
                continue
            row = _future_row(future.matchup, blocked.get(future.ref, []), broadcaster_events)
            row['_future_order'] = order[future.ref]
            out.append(row)
        return out

    async def signup(self, source: str, key: int, user: User, role: str) -> Match:
        """Sign ``user`` up as ``role`` on an unbooked matchup. Returns the hold.

        Every rule of :meth:`CrewService.signup_crew` applies, plus one it can't
        see on a hold: a player already through to the matchup can't crew it.
        """
        from application.services.crew_service import CREW_ROLES, CrewService

        if role not in CREW_ROLES:
            raise ValueError(f"Invalid role: {role}. Must be 'commentator', 'tracker' or 'broadcaster'")
        matchup = next(
            (m.matchup for m in await self.list_future() if m.ref == (source, key)), None,
        )
        if matchup is None:
            raise ValueError("This match has been booked or isn't open anymore. Refresh to see where it went.")
        if user.id in matchup.player_ids:
            raise ValueError("You can't crew a match you're playing in.")
        hold, created = await self._hold_for(matchup)
        try:
            await CrewService().signup_crew(hold.id, user, role)
        except Exception:
            if created:
                await self.repository.delete(hold)
            raise
        return hold

    async def absorb(self, match: Match, actor: Optional[User]) -> int:
        """Move the crew holding ``match``'s matchup onto ``match``. Returns how many moved.

        Called after a matchup's first game is booked or linked. Approvals and
        acknowledgments come along; a signup from someone now playing the
        match, or already on it in that role, is dropped. The hold is deleted.
        Best-effort: a booking never fails over its crew.
        """
        try:
            ref = await _matchup_of(match)
            if ref is None:
                return 0
            hold = await self.repository.get_crew_hold(*ref)
            if hold is None or hold.id == match.id:
                return 0
            return await self._move(hold, match, actor)
        except Exception:
            logger.exception('Moving held crew onto match %s failed', match.id)
            return 0

    async def _move(self, hold: Match, match: Match, actor: Optional[User]) -> int:
        from application.services.crew_service import CrewService
        from application.services.user_service import UserService

        players = {p.user_id for p in await self.repository.get_players(match)}  # type: ignore[attr-defined]
        moved: List[Tuple[str, Any]] = []
        dropped = 0
        repositories: Tuple[Tuple[str, Any], ...] = (
            ('commentator', CommentatorRepository), ('tracker', TrackerRepository),
            ('broadcaster', BroadcasterRepository),
        )
        for role, repository in repositories:
            on_match = {c.user_id for c in await repository.get_by_match(match)}
            for crew in await repository.get_by_match(hold):
                if crew.user_id in players or crew.user_id in on_match:
                    await repository.delete(crew)
                    dropped += 1
                    continue
                await repository.move_to_match(crew, match)
                moved.append((role, crew))
        await CommentaryInviteRepository.move_to_match(hold.id, match.id)
        await self.repository.delete(hold)
        actor = actor or await UserService().get_system_user()
        await self.audit_service.write_log(actor, AuditActions.CREW_MOVED_TO_BOOKING, {
            'match_id': match.id, 'hold_match_id': hold.id,
            'source': hold.crew_hold_source, 'key': hold.crew_hold_key,
            'moved': len(moved), 'dropped': dropped,
        })
        match_live.publish(match.id)
        crew_service = CrewService()
        for role, crew in moved:
            if not crew.approved:
                continue
            _announce_approved(match, crew, role, actor)
            await crew.fetch_related('user')
            clashes = await crew_service.find_scheduling_conflicts(crew.user, match, role)
            if clashes:
                await self._notify_busy(match, crew, role, clashes)
        return len(moved)

    async def _hold_for(self, matchup: UnbookedMatch) -> Tuple[Match, bool]:
        hold = await self.repository.get_crew_hold(matchup.source, matchup.key)
        if hold is not None:
            return hold, False
        try:
            return await self.repository.create_crew_hold(
                matchup.tournament.id, matchup.source, matchup.key,
                matchup.round_label, list(matchup.player_names),
            ), True
        except Exception:
            # Two people signing up at once: the other one made it.
            hold = await self.repository.get_crew_hold(matchup.source, matchup.key)
            if hold is None:
                raise
            return hold, False

    async def _relabel(self, hold: Match, matchup: UnbookedMatch) -> None:
        names = list(matchup.player_names)
        if hold.title != matchup.round_label or hold.bracket_placeholders != names:
            await self.repository.update(hold, title=matchup.round_label, bracket_placeholders=names)

    async def _notify_busy(self, match: Match, crew, role: str, clashes: List[str]) -> None:
        """Tell the match's crew owners an approved member is busy at the time it was booked for."""
        from application.repositories import TournamentRepository
        from application.services._crew_signup_dms import crew_dm_parts

        try:
            recipients: Dict[int, User] = {
                o.id: o for o in await TournamentRepository.get_crew_owners(match.tournament_id)  # type: ignore[attr-defined]
            }
            approver_id = getattr(crew, 'approved_by_id', None)
            if approver_id and approver_id not in recipients:
                approver = await User.get_or_none(id=approver_id)
                if approver is not None:
                    recipients[approver.id] = approver
            recipients.pop(crew.user.id, None)
            if not recipients:
                return
            await match.fetch_related('stage')
            _, fields = await crew_dm_parts(match)
            name = crew.user.preferred_name
            busy = '; '.join(clashes)
            message = f'{name} is approved as {role} for a match that just got a time, but is busy then: {busy}.'
            embed = notification_embed(
                title=f'🎙️ Approved {role} is busy then',
                color=COLOR_CANCELLED,
                community_name=await TenantService.current_community_name(),
                description=f'**{name}** signed up for this match before it had a time. '
                            f"It's now booked, and they're busy then: {busy}.",
                fields=fields,
            )
            link = await notification_links.admin_match(match.id, label='Check the crew')
            notice = CREW_BOOKED_BUSY.to(ADMINS)
            discord = DiscordService()
            for recipient in recipients.values():
                discord_id = getattr(recipient, 'discord_id', None)
                if discord_id:
                    discord_queue.enqueue(
                        discord.send_dm(int(discord_id), message, embed=embed, link=link, notice=notice)
                    )
        except Exception:
            logger.exception('Busy-crew notice failed for match %s', match.id)


def _announce_approved(match: Match, crew, role: str, actor: User) -> None:
    """An approved member now on the booking reads, to subscribers, as approved on it."""
    event_bus.publish(Event.create(EventType.CREW_APPROVAL_CHANGED, {
        'match_id': match.id, 'crew_type': role, 'user_id': crew.user_id, 'approved': True,
    }, actor))


async def _matchup_of(match: Match) -> Optional[Tuple[str, int]]:
    """The bracket matchup ``match`` is booked for, as ``(source, key)``."""
    cm = await ChallongeRepository.get_challonge_match_for_match(match)
    if cm is not None:
        return 'challonge', cm.id
    sets = await StartggRepository.sets_for_match(match)
    if sets:
        return 'startgg', sets[0].id
    game = await BracketRepository().get_game_for_match(match.id)
    if game is not None:
        return 'bracket', game.bracket_match_id  # type: ignore[attr-defined]
    return None


async def _booked_match(source: str, key: int) -> Optional[Match]:
    """The match a matchup is booked as now, if any."""
    match_id: Optional[int] = None
    if source == 'challonge':
        cm = await ChallongeRepository.get_match(key)
        match_id = getattr(cm, 'match_id', None)
    elif source == 'startgg':
        row = await StartggRepository.get_set(key)
        match_id = getattr(row, 'match_id', None)
    elif source == 'bracket':
        match_id = await BracketRepository().first_booked_match_id(key)
    if match_id is None:
        return None
    return await MatchRepository().get_by_id(match_id, prefetch_relations=False)


async def _player_discord_ids(futures: List[FutureMatch]) -> Dict[Tuple[str, int], List[str]]:
    """The Discord ids of players already through to each matchup, who can't crew it."""
    ids = {uid for f in futures for uid in f.matchup.player_ids if uid is not None}
    if not ids:
        return {}
    users = {u.id: u for u in await User.filter(id__in=ids)}
    return {
        f.ref: [str(users[uid].discord_id) for uid in f.matchup.player_ids
                if uid in users and users[uid].discord_id]
        for f in futures
    }


def _future_row(matchup: UnbookedMatch, blocked: List[str], broadcaster_events: Set[int]) -> Dict:
    """A schedule line for a matchup nobody has signed up for yet, shaped like a match's.

    ``id`` is ``future:<source>:<key>``, never a match id: the board's Sign up
    hands it to :meth:`CrewHoldService.signup`, which makes the hold.
    """
    t = matchup.tournament
    return {
        'id': f'future:{matchup.source}:{matchup.key}',
        '_future_ref': f'{matchup.source}:{matchup.key}',
        'crew_hold': True,
        '_players_tbd': matchup.players_tbd,
        '_crew_blocked': blocked,
        'title': matchup.round_label,
        'description': '', 'description_text': '',
        'tournament_id': t.id, 'tournament': t.name, 'tournament_short': t.short_label,
        'bracket': None, 'set_label': matchup.round_label or None,
        'scheduled_at': '', 'scheduled_ts': None, 'started_ts': None, 'finished_ts': None,
        'match_length': t.average_match_duration, 'is_overdue': False,
        'state': 'Scheduled', 'needs_review': False, 'has_result': False, 'forfeit': '',
        'state_timestamp': '', 'state_time': '',
        'players': [], 'placeholders': list(matchup.player_names), 'bracket_link_lost': False,
        'late': [], 'late_window': False, 'acknowledgments': [],
        'stage': '', 'stage_id': None, 'stage_url': '', 'is_stream_candidate': False,
        'stream_volunteers': [], 'is_racetime': t.is_racetime_enabled,
        'seed': '', 'generated_seed': '', 'seed_code': '', 'tournament_seed_generator': t.seed_generator,
        'seed_rolling_since': None, 'seed_rolling_label': '', 'seed_roll_error': '', 'seed_dm_blocked': [],
        'crew_signup_open': True,
        'crew_wanted': {
            'commentators': t.required_commentators > 0, 'trackers': t.required_trackers > 0,
            'broadcasters': t.event_id in broadcaster_events and t.required_broadcasters > 0,
        },
        'crew_required': {'commentators': 0, 'trackers': 0, 'broadcasters': 0},
        'commentators': [], 'trackers': [], 'broadcasters': [],
    }
