"""The opt-in DMs: messages nobody gets until they turn them on under Profile →
Notifications (``notification_catalog._OPT_IN``).

Each sender is a coroutine the caller hands to ``discord_queue.enqueue`` (or
awaits from a worker), so it loads what it needs itself and runs inside the
caller's community. Every one starts from who opted in, so a message nobody
chose costs one query and sends nothing. They never raise.
"""

import logging
from typing import Iterable, Optional, Sequence, Set, Tuple

from application.repositories import (
    AsyncQualifierRunRepository,
    BracketRepository,
    MatchRepository,
    TournamentRepository,
    UserRepository,
)
from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import (
    ADDED_TO_TOURNAMENT,
    ADMINS,
    BRACKET_RESULT,
    MATCH_FLAGGED,
    MEMBERS,
    OPPONENT_ACKNOWLEDGED,
    PLAYER_WITHDREW,
    PLAYERS,
    QUALIFIER_CLOSED,
    QUALIFIER_OPENED,
    REMOVED_FROM_TOURNAMENT,
    REQUEST_WITHDRAWN,
    SEED_FAILED,
    STATION_ASSIGNED,
    STREAM_OFFER,
    VOLUNTEER_ARRIVED,
    Notice,
)
from application.services.notification_preference_service import NotificationPreferenceService
from application.utils import discord_messages_opt_in as text
from application.utils.discord_embeds import (
    COLOR_CANCELLED,
    COLOR_CONFIRMED,
    COLOR_RESCHEDULED,
    COLOR_SCHEDULED,
    COLOR_SEED,
    COLOR_STREAM,
    COLOR_VOLUNTEER,
    match_embed,
    notification_embed,
)
from application.utils.discord_messages import DMLink
from models import BracketEntryStatus, Match, NotificationRecipient, User

logger = logging.getLogger(__name__)

Tagged = Tuple[User, NotificationRecipient]


async def _any_opted_in(notice: Notice, recipients: Iterable[NotificationRecipient]) -> Set[int]:
    chosen: Set[int] = set()
    for recipient in recipients:
        chosen |= await NotificationPreferenceService.opted_in_user_ids(notice.to(recipient))
    return chosen


async def _send(
    notice: Notice, recipients: Sequence[Tagged], message: str, embed, link: Optional[DMLink],
) -> None:
    discord_service = DiscordService()
    for user, why in recipients:
        if not user.discord_id:
            continue
        ok, err = await discord_service.send_dm(
            user.discord_id, message, embed=embed, link=link, notice=notice.to(why),
        )
        if not ok:
            logger.debug('%s DM to %s not sent: %s', notice.key, user.discord_id, err)


async def _community() -> str:
    from application.services.match._dm_context import _community_name
    return await _community_name()


async def _match(match_id: int) -> Optional[Match]:
    return await MatchRepository.get_by_id(match_id)


def _names(match: Match) -> list:
    return [p.user.preferred_name for p in match.players]  # type: ignore[attr-defined]


async def _match_embed(match: Match, title: str, color: int, message: str):
    return match_embed(
        title=title, color=color, description=message, tournament=match.tournament.name,
        community_name=await _community(), player_names=_names(match), when=match.scheduled_at,
    )


async def opponent_acknowledged(match_id: int, user_id: int) -> None:
    """Tell the other players of a match that ``user_id`` acknowledged it."""
    from application.services import notification_links
    try:
        chosen = await _any_opted_in(OPPONENT_ACKNOWLEDGED, [PLAYERS])
        match = await _match(match_id) if chosen else None
        if match is None:
            return
        players = [p.user for p in match.players]  # type: ignore[attr-defined]
        acker = next((u for u in players if u.id == user_id), None)
        recipients = [(u, PLAYERS) for u in players if u.id != user_id and u.id in chosen]
        if acker is None or not recipients:
            return
        message = text.opponent_acknowledged_dm(acker.preferred_name, match.tournament.name)
        embed = await _match_embed(match, '👍 Opponent acknowledged', COLOR_CONFIRMED, message)
        await _send(OPPONENT_ACKNOWLEDGED, recipients, message, embed,
                    await notification_links.player_match(match_id))
    except Exception:
        logger.exception('opponent_acknowledged DM failed for match %s', match_id)


async def stations_assigned(match_id: int) -> None:
    """Tell each player with a station which one it is."""
    from application.services import notification_links
    try:
        chosen = await _any_opted_in(STATION_ASSIGNED, [PLAYERS])
        match = await _match(match_id) if chosen else None
        if match is None:
            return
        link = await notification_links.player_match(match_id)
        for player in match.players:  # type: ignore[attr-defined]
            if player.user_id not in chosen or not player.assigned_station:
                continue
            message = text.station_assigned_dm(match.tournament.name, player.assigned_station)
            embed = await _match_embed(match, '🎮 Station assigned', COLOR_SCHEDULED, message)
            await _send(STATION_ASSIGNED, [(player.user, PLAYERS)], message, embed, link)
    except Exception:
        logger.exception('stations_assigned DM failed for match %s', match_id)


async def _staff_alert(
    notice: Notice, match_id: int, title: str, color: int, message_for, *, exclude: Iterable[int] = (),
    can_act=None,
) -> None:
    from application.services import notification_links
    from application.services.auth_service import AuthService
    from application.services.staff_alert_service import StaffAlertService
    chosen = await _any_opted_in(notice, notice.defaults)
    match = await _match(match_id) if chosen else None
    if match is None:
        return
    tagged = await StaffAlertService.tagged_match_alert_recipients(
        match, can_act=can_act or AuthService.can_run_match, exclude=exclude,
    )
    recipients = [(u, why) for u, why in tagged if u.id in chosen]
    if not recipients:
        return
    message = message_for(match)
    embed = await _match_embed(match, title, color, message)
    await _send(notice, recipients, message, embed, await notification_links.admin_match(match_id))


async def match_flagged(match_id: int, actor_id: int, actor_name: str) -> None:
    from application.services.auth_service import AuthService
    try:
        await _staff_alert(
            MATCH_FLAGGED, match_id, '🚩 Flagged for review', COLOR_RESCHEDULED,
            lambda m: text.match_flagged_dm(actor_name, m.tournament.name),
            exclude=(actor_id,), can_act=AuthService.can_confirm_match,
        )
    except Exception:
        logger.exception('match_flagged DM failed for match %s', match_id)


async def seed_failed(match_id: int, error: Optional[str]) -> None:
    try:
        await _staff_alert(
            SEED_FAILED, match_id, "🎲 Seed didn't roll", COLOR_SEED,
            lambda m: text.seed_failed_dm(m.tournament.name, (error or '')[:300] or None),
        )
    except Exception:
        logger.exception('seed_failed DM failed for match %s', match_id)


async def stream_offer(match_id: int, user_id: int, user_name: str) -> None:
    from application.services.auth_service import AuthService
    try:
        await _staff_alert(
            STREAM_OFFER, match_id, '📺 Offered for stream', COLOR_STREAM,
            lambda m: text.stream_offer_dm(user_name, m.tournament.name),
            exclude=(user_id,), can_act=AuthService.can_assign_match_stream,
        )
    except Exception:
        logger.exception('stream_offer DM failed for match %s', match_id)


async def request_withdrawn(match_id: int, requester_id: int, cancel: bool) -> None:
    """Tell the opponent a request they were asked about is gone."""
    from application.services import notification_links
    try:
        chosen = await _any_opted_in(REQUEST_WITHDRAWN, [PLAYERS])
        match = await _match(match_id) if chosen else None
        if match is None:
            return
        players = [p.user for p in match.players]  # type: ignore[attr-defined]
        requester = next((u for u in players if u.id == requester_id), None)
        opponents = [u for u in players if u.id != requester_id]
        # The opponent was only asked on a head-to-head match.
        if requester is None or len(opponents) != 1 or opponents[0].id not in chosen:
            return
        message = text.request_withdrawn_dm(requester.preferred_name, match.tournament.name, cancel)
        embed = await _match_embed(match, '↩️ Request withdrawn', COLOR_SCHEDULED, message)
        await _send(REQUEST_WITHDRAWN, [(opponents[0], PLAYERS)], message, embed,
                    await notification_links.player_match(match_id))
    except Exception:
        logger.exception('request_withdrawn DM failed for match %s', match_id)


async def enrolment_changed(tournament_id: int, player_id: int, actor_id: int, *, added: bool) -> None:
    """Staff added a player to a tournament or took them out of it; or, when the
    player did it themselves, a withdrawal goes to the tournament's admins."""
    from application.services import notification_links
    try:
        if actor_id == player_id:
            if not added:
                await _player_withdrew(tournament_id, player_id)
            return
        notice = ADDED_TO_TOURNAMENT if added else REMOVED_FROM_TOURNAMENT
        if player_id not in await _any_opted_in(notice, [PLAYERS]):
            return
        player = await UserRepository.get_by_id(player_id)
        actor = await UserRepository.get_by_id(actor_id)
        tournament = await TournamentRepository.get_by_id(tournament_id)
        if player is None or actor is None or tournament is None:
            return
        actor_name = actor.preferred_name
        if added:
            message = text.added_to_tournament_dm(tournament.name, actor_name)
            title, color = '🏁 Added to a tournament', COLOR_SCHEDULED
        else:
            message = text.removed_from_tournament_dm(tournament.name, actor_name)
            title, color = '🏁 Removed from a tournament', COLOR_CANCELLED
        embed = notification_embed(title=title, color=color, description=message, community_name=await _community())
        await _send(notice, [(player, PLAYERS)], message, embed, await notification_links.home_tournaments())
    except Exception:
        logger.exception('enrolment DM failed for tournament %s', tournament_id)


async def _player_withdrew(tournament_id: int, player_id: int) -> None:
    from application.services import notification_links
    from application.services.staff_alert_service import StaffAlertService
    chosen = await _any_opted_in(PLAYER_WITHDREW, [ADMINS])
    if not chosen:
        return
    tournament = await TournamentRepository.get_by_id(tournament_id)
    player = await UserRepository.get_by_id(player_id)
    if tournament is None or player is None:
        return
    admins = await StaffAlertService.tournament_alert_admins(tournament)
    recipients = [(u, ADMINS) for u in admins if u.id in chosen and u.id != player_id]
    if not recipients:
        return
    message = text.player_withdrew_dm(player.preferred_name, tournament.name)
    embed = notification_embed(
        title='🏁 Player withdrew', color=COLOR_CANCELLED, description=message, community_name=await _community(),
    )
    await _send(PLAYER_WITHDREW, recipients, message, embed,
                await notification_links.admin_tournaments())


async def bracket_result(
    tournament_name: str, round_name: str, winner_entry_id: int, loser_entry_id: int,
) -> None:
    """Tell both players of a decided Wizzrobe bracket match where it leaves them."""
    from application.services import notification_links
    try:
        chosen = await _any_opted_in(BRACKET_RESULT, [PLAYERS])
        if not chosen:
            return
        repository = BracketRepository()
        winner = await repository.get_entry(winner_entry_id)
        loser = await repository.get_entry(loser_entry_id)
        if winner is None or loser is None:
            return
        await winner.fetch_related('entrant__user')
        await loser.fetch_related('entrant__user')
        winner_name, loser_name = winner.entrant.display_name, loser.entrant.display_name
        link = await notification_links.home_tournaments(label='View tournaments')
        community = await _community()
        if winner.entrant.user and winner.entrant.user.id in chosen:
            message = text.bracket_won_dm(loser_name, round_name, tournament_name)
            embed = notification_embed(title='🏆 You won', color=COLOR_CONFIRMED, description=message,
                                       community_name=community)
            await _send(BRACKET_RESULT, [(winner.entrant.user, PLAYERS)], message, embed, link)
        if loser.entrant.user and loser.entrant.user.id in chosen:
            out = loser.status == BracketEntryStatus.ELIMINATED
            message = text.bracket_lost_dm(winner_name, round_name, tournament_name, out=out)
            embed = notification_embed(title='Bracket result', color=COLOR_SCHEDULED, description=message,
                                       community_name=community)
            await _send(BRACKET_RESULT, [(loser.entrant.user, PLAYERS)], message, embed, link)
    except Exception:
        logger.exception('bracket_result DM failed for entries %s/%s', winner_entry_id, loser_entry_id)


async def volunteer_arrived(shift, volunteer_name: str) -> None:
    from application.services.volunteer.volunteer_coordinator_dm import notify_coordinators
    try:
        if not await _any_opted_in(VOLUNTEER_ARRIVED, VOLUNTEER_ARRIVED.defaults):
            return
        await shift.fetch_related('position')
        message = text.volunteer_arrived_dm(volunteer_name, shift.position.name)
        embed = notification_embed(
            title='✅ Volunteer arrived', color=COLOR_VOLUNTEER, description=message,
            community_name=await _community(),
        )
        await notify_coordinators(shift, message, embed, link_label='Open the shift', notice=VOLUNTEER_ARRIVED)
    except Exception:
        logger.exception('volunteer_arrived DM failed for shift %s', getattr(shift, 'id', None))


async def qualifier_window(qualifier, *, opened: bool) -> None:
    """Tell whoever opted in that a qualifier opened, or its runners that it closed."""
    from application.services import notification_links
    try:
        notice, recipient = (QUALIFIER_OPENED, MEMBERS) if opened else (QUALIFIER_CLOSED, PLAYERS)
        chosen = await _any_opted_in(notice, [recipient])
        if not chosen:
            return
        if not opened:
            runs = await AsyncQualifierRunRepository().list_valid_for_qualifier(qualifier.id)
            chosen &= {run.user_id for run in runs}  # type: ignore[attr-defined]
        users = await UserRepository.get_by_ids(sorted(chosen))
        recipients = [(u, recipient) for u in users.values() if u.is_active]
        if not recipients:
            return
        await qualifier.fetch_related('tenant')
        if opened:
            message = text.qualifier_opened_dm(qualifier.name)
            link = notification_links.player_qualifier(qualifier.tenant, qualifier.id, label='Start a run')
        else:
            message = text.qualifier_closed_dm(qualifier.name)
            link = notification_links.player_qualifier(qualifier.tenant, qualifier.id, label='See the leaderboard')
        embed = notification_embed(
            title='⏱️ Qualifier open' if opened else '⏱️ Qualifier closed',
            color=COLOR_SEED, description=message, community_name=qualifier.tenant.name,
        )
        await _send(notice, recipients, message, embed, link)
    except Exception:
        logger.exception('qualifier_window DM failed for qualifier %s', getattr(qualifier, 'id', None))
