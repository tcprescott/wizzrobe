"""The "Result in" DM: a tournament's admins hear each match result as it lands,
so nobody has to keep the bracket open to see who won.

Two ways in. A result recorded in Wizzrobe (by hand, from the race room, or a
forfeit) calls :func:`match_result_in` once, the first time the match has a
winner. A result reported straight on Challonge or start.gg reaches us on the
next sync, which hands the newly finished matchups to :func:`site_results_in`;
a matchup whose booked Wizzrobe match already holds a result is skipped, since
that result was announced when it was recorded.

Each sender is a coroutine for ``discord_queue.enqueue``: it loads what it
needs, runs inside the caller's community, and never raises.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Sequence, Set

from application.repositories import (
    ChallongeRepository,
    MatchRepository,
    StartggRepository,
    TournamentRepository,
    UserRepository,
)
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.match.match_status import has_recorded_result
from application.services.notification_catalog import ADMINS, RESULT_IN
from application.services.round_schedule_service import RoundScheduleService
from application.services.staff_alert_service import StaffAlertService
from application.utils import discord_messages_results as text
from application.utils.clients.startgg_client import event_url
from application.utils.discord_embeds import COLOR_CONFIRMED, notification_embed
from application.utils.discord_messages import DMLink
from application.utils.discord_messages_results import Placing
from application.utils.match_labels import entrant_name
from application.utils.timezone import to_utc_aware
from models import Match, Tournament, User

logger = logging.getLogger(__name__)

RECORDED = 'recorded'
RACE_ROOM = 'race_room'
FORFEIT = 'forfeit'

# A sync that finds more results than this for one tournament sends one roundup
# instead, so an hourly sync after a busy hour doesn't buzz through ten cards.
ROUNDUP_OVER = 3


@dataclass(frozen=True)
class SiteResultIn:
    """One matchup a bracket site reported finished since the last sync."""

    placings: Sequence[Placing]
    round_label: Optional[str] = None
    played_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    match_id: Optional[int] = None
    player_user_ids: frozenset = frozenset()


async def _community() -> str:
    from application.services.match._dm_context import _community_name
    return await _community_name()


async def _recipients(tournament: Tournament, exclude: Iterable[Optional[int]]) -> List[User]:
    skip = {uid for uid in exclude if uid is not None}
    admins = await StaffAlertService.tournament_alert_admins(tournament)
    return [u for u in admins if u.id not in skip and u.discord_id]


async def _send(recipients: Sequence[User], message: str, embed, link: Optional[DMLink]) -> None:
    discord_service = DiscordService()
    for user in recipients:
        ok, err = await discord_service.send_dm(
            user.discord_id, message, embed=embed, link=link, notice=RESULT_IN.to(ADMINS),
        )
        if not ok:
            logger.debug('result_in DM to %s not sent: %s', user.discord_id, err)


def _embed(title: str, description: str, community: str, url: Optional[str] = None):
    return notification_embed(
        title=title, color=COLOR_CONFIRMED, description=description, community_name=community, url=url,
    )


def _title(tournament: str, round_label: Optional[str]) -> str:
    return f'🏁 {tournament} · {round_label}' if round_label else f'🏁 {tournament}'


def _source_line(source: str, actor: Optional[User]) -> str:
    name = actor.preferred_name if actor else None
    if source == RACE_ROOM:
        return text.RACE_ROOM
    if source == FORFEIT:
        return text.forfeit_by(name)
    return text.recorded_by(name)


async def match_result_in(match_id: int, actor_id: Optional[int], source: str = RECORDED) -> None:
    """Tell the tournament's admins a Wizzrobe match has its first result.

    ``actor_id`` recorded it and gets no copy; nor do the match's own players,
    an admin playing in it already knows.
    """
    from application.services import notification_links
    from application.services.match._dm_context import bracket_line_for
    try:
        match = await MatchRepository.get_by_id(match_id)
        if match is None or match.tournament is None or not has_recorded_result(match.players):  # type: ignore[attr-defined]
            return
        players = list(match.players)  # type: ignore[attr-defined]
        recipients = await _recipients(match.tournament, [actor_id, *(p.user_id for p in players)])
        if not recipients:
            return
        actor = await UserRepository.get_by_id(actor_id) if actor_id is not None else None
        placings = [Placing(p.user.preferred_name, p.finish_rank, p.finish_time) for p in players]
        source_line = _source_line(source, actor)
        description = '\n\n'.join([
            text.result_board(placings),
            text.when_line(match.started_at or match.scheduled_at, datetime.now(timezone.utc)),
            source_line,
        ])
        round_label = await bracket_line_for(match_id)
        embed = _embed(_title(match.tournament.name, round_label), description, await _community())
        message = text.result_text(match.tournament.name, placings, source_line)
        await _send(recipients, message, embed, await notification_links.admin_match(match_id))
    except Exception:
        logger.exception('result_in DM failed for match %s', match_id)


async def site_results_in(
    tournament_id: int, site: str, site_url: Optional[str], results: Sequence[SiteResultIn],
) -> None:
    """Tell the tournament's admins about results a sync found on ``site``.

    One DM per result, or a single roundup when the sync found more than
    :data:`ROUNDUP_OVER`.
    """
    from application.services import notification_links
    try:
        if not results:
            return
        tournament = await TournamentRepository.get_by_id(tournament_id)
        if tournament is None:
            return
        community = await _community()
        site_link = DMLink(f'Open on {site}', site_url) if site_url else None
        if len(results) > ROUNDUP_OVER:
            recipients = await _recipients(tournament, [])
            if not recipients:
                return
            description = '\n'.join(text.roundup_line(r.placings, r.round_label) for r in results)
            embed = _embed(text.roundup_title(tournament.name, len(results), site), description, community, site_url)
            await _send(recipients, text.roundup_text(tournament.name, len(results), site), embed, site_link)
            return
        source_line = text.reported_on(site)
        for result in results:
            recipients = await _recipients(tournament, result.player_user_ids)
            if not recipients:
                continue
            description = '\n\n'.join([
                text.result_board(result.placings),
                text.when_line(result.played_at, result.completed_at or datetime.now(timezone.utc)),
                source_line,
            ])
            embed = _embed(_title(tournament.name, result.round_label), description, community, site_url)
            link = (
                await notification_links.admin_match(result.match_id)
                if result.match_id is not None else site_link
            )
            await _send(recipients, text.result_text(tournament.name, result.placings, source_line), embed, link)
    except Exception:
        logger.exception('result_in DMs failed for tournament %s', tournament_id)


def _announced_already(match: Optional[Match]) -> bool:
    """Whether a matchup's booked Wizzrobe match (``players`` loaded) already has
    its result, and so was announced when it was recorded (and is usually the
    result we sent the site)."""
    return match is not None and has_recorded_result(match.players)  # type: ignore[attr-defined]


async def challonge_results_in(
    tournament: Tournament, unfinished_before: Set[str], remote_matches: Sequence[Dict[str, Any]],
) -> None:
    """Queue the DMs for matches a Challonge sync found newly complete.

    ``unfinished_before`` is the remote ids that weren't complete before the
    sync: Challonge gives no completion time, so a match counts as new only when
    this sync saw it change. A bracket's first sync announces nothing.
    """
    finished = {
        rm['match_id']: rm.get('scores') or {} for rm in remote_matches
        if rm['match_id'] in unfinished_before and rm.get('state') == 'complete'
    }
    if not finished:
        return
    results = []
    for cm in await ChallongeRepository.matches_by_remote_ids(tournament, finished):
        winner = cm.winner_participant
        sides = [p for p in (cm.participant1, cm.participant2) if p is not None]
        if winner is None or len(sides) != 2 or _announced_already(cm.match):
            continue
        scores = finished[cm.challonge_match_id]
        results.append(SiteResultIn(
            placings=[
                Placing(entrant_name(p), 1 if p.id == winner.id else 2, score=scores.get(p.challonge_participant_id))
                for p in sides
            ],
            round_label=await _round_label(RoundScheduleService().round_label_for_challonge_match(cm)),
            played_at=cm.match.scheduled_at if cm.match else None,
            match_id=cm.match.id if cm.match else None,
            player_user_ids=frozenset(p.user_id for p in sides if p.user_id),
        ))
    if results:
        discord_queue.enqueue(site_results_in(
            tournament.id, 'Challonge', tournament.challonge_tournament_url, results,
        ))


async def startgg_results_in(tournament: Tournament, full: Dict[str, Any]) -> None:
    """Queue the DMs for sets start.gg reports finished since the last sync.

    Run before the sync records its own time, so ``startgg_last_synced_at`` is
    still the previous sync's; a first sync (none yet) announces nothing.
    """
    since = to_utc_aware(tournament.startgg_last_synced_at) if tournament.startgg_last_synced_at else None
    if since is None:
        return
    set_ids = [
        s['set_id'] for detail in full['pools'].values() for s in detail['sets']
        if s['complete'] and s.get('completed_at') and to_utc_aware(s['completed_at']) > since
    ]
    if not set_ids:
        return
    results = []
    for row in await StartggRepository.sets_by_ids(tournament, set_ids):
        sides = [(e, score) for e, score in ((row.entrant1, row.score1), (row.entrant2, row.score2)) if e]
        winner_id = row.winner_id  # type: ignore[attr-defined]
        if winner_id is None or len(sides) != 2 or _announced_already(row.match):
            continue
        results.append(SiteResultIn(
            placings=[
                Placing(entrant_name(e), 1 if e.id == winner_id else 2,
                        score=score if score is not None and score >= 0 else None)
                for e, score in sides
            ],
            round_label=await _round_label(RoundScheduleService().round_label_for_startgg_set(row)),
            played_at=row.match.scheduled_at if row.match else None,
            completed_at=row.completed_at,
            match_id=row.match.id if row.match else None,
            player_user_ids=frozenset(e.user_id for e, _ in sides if e.user_id),
        ))
    if results:
        url = event_url(tournament.startgg_event_slug) if tournament.startgg_event_slug else None
        discord_queue.enqueue(site_results_in(tournament.id, 'start.gg', url, results))


async def _round_label(lookup: Awaitable[Optional[str]]) -> Optional[str]:
    """A matchup's round name, or none: a DM isn't lost over its title."""
    try:
        return await lookup
    except Exception:
        logger.exception('round label lookup failed')
        return None
