"""Staff dropping a player from a tournament, mixed into ``TournamentService``.

Taking someone off the roster used to leave their booked matches standing, tell
the bracket site nothing, and get undone by the next Challonge sync. A drop does
all of it in one step: the bracket site first (so a refusal changes nothing),
then every match they still owe, then the roster, then one DM to the player.
"""

import logging
from dataclasses import dataclass, field
from typing import List

from application.events import EventType
from application.repositories import MatchRepository
from application.services._tournament_signup import record_enrolment_change
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
from application.services.match.match_status import has_recorded_result
from application.services.notification_catalog import DROPPED_FROM_TOURNAMENT, PLAYERS
from models import FeatureFlag, ForfeitReason, Match, Tournament, User

logger = logging.getLogger(__name__)


@dataclass
class DropPlan:
    """What a drop would touch, for the dialog to show before anyone confirms."""

    head_to_head: List[Match] = field(default_factory=list)
    group: List[Match] = field(default_factory=list)
    on_bracket_site: bool = False


@dataclass
class DropResult:
    forfeited: int = 0
    called_off: int = 0
    removed_from: int = 0
    on_bracket_site: bool = False


class TournamentDropMixin:
    """``TournamentService`` methods for dropping a player."""

    async def plan_drop(self, tournament: Tournament, target: User, actor: User) -> DropPlan:
        """The matches a drop would settle: unconfirmed and with no result on the board.

        A match already played and waiting on confirmation keeps its result.
        """
        await self._ensure_can_drop(tournament, actor)
        plan = DropPlan()
        for match in await MatchRepository.unconfirmed_for_player(tournament.id, target.id):
            if has_recorded_result(match.players):  # type: ignore[attr-defined]
                continue
            (plan.head_to_head if len(match.players) == 2 else plan.group).append(match)  # type: ignore[attr-defined]
        plan.on_bracket_site = await self._on_challonge(tournament, target)
        return plan

    async def drop_player(
        self,
        tournament: Tournament,
        target: User,
        actor: User,
        *,
        reason: ForfeitReason,
        forfeit_matches: bool,
    ) -> DropResult:
        """Drop ``target``: their head-to-head matches are forfeited to the
        opponent (or called off), and they come off any larger match's line-up."""
        from application.services.challonge_service import ChallongeService
        from application.services.match.match_service import MatchService

        plan = await self.plan_drop(tournament, target, actor)
        enrolled = await self.repository.is_player_enrolled(tournament, target)  # type: ignore[attr-defined]
        if not (enrolled or plan.head_to_head or plan.group or plan.on_bracket_site):
            raise ValueError(f'{target.preferred_name} is not in {tournament.name}.')

        result = DropResult()
        if plan.on_bracket_site:
            result.on_bracket_site = await ChallongeService().drop_participant(tournament, target, actor)

        matches = MatchService()
        why = f'{target.preferred_name} was dropped from the tournament.'
        for match in plan.head_to_head:
            try:
                if forfeit_matches:
                    forfeiter = next(p for p in match.players if p.user_id == target.id)  # type: ignore[attr-defined]
                    await matches._forfeit(match, forfeiter, reason, actor, tell_forfeiter=False)
                    result.forfeited += 1
                else:
                    await matches.cancel_match(match.id, actor, reason=why)
                    result.called_off += 1
            except ValueError:
                # One match that changed under us (confirmed a moment ago, a
                # settled bracket game) must not strand the rest of the drop.
                logger.warning('drop: match %s left as it was', match.id, exc_info=True)
        for match in plan.group:
            others = [p.user_id for p in match.players if p.user_id != target.id]  # type: ignore[attr-defined]
            if others:
                await matches.update_match(match.id, player_ids=others, actor=actor)
                result.removed_from += 1
            else:
                await matches.cancel_match(match.id, actor, reason=why)
                result.called_off += 1

        if enrolled:
            await self.repository.unenroll_player(tournament, target)  # type: ignore[attr-defined]
            await record_enrolment_change(
                self.audit_service, actor, target, tournament.id, added=False,  # type: ignore[attr-defined]
            )
        await self.audit_service.write_and_publish(  # type: ignore[attr-defined]
            actor,
            AuditActions.TOURNAMENT_PLAYER_DROPPED,
            {
                'tournament_id': tournament.id,
                'target_user_id': target.id,
                'reason': reason.value,
                'forfeit_matches': forfeit_matches,
                'forfeited': result.forfeited,
                'called_off': result.called_off,
                'removed_from': result.removed_from,
                'on_bracket_site': result.on_bracket_site,
            },
            EventType.TOURNAMENT_PLAYER_DROPPED,
        )
        discord_queue.enqueue(_tell_dropped_player(tournament.name, target, reason, result))
        return result

    async def _ensure_can_drop(self, tournament: Tournament, actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            "Only staff or this tournament's admins can drop a player",
        )

    @staticmethod
    async def _on_challonge(tournament: Tournament, target: User) -> bool:
        from application.repositories import ChallongeRepository
        from application.services.feature_flag_service import FeatureFlagService

        if not tournament.challonge_tournament_id:
            return False
        if not await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE):
            return False
        participant = await ChallongeRepository.get_participant_for_user(tournament, target)
        return participant is not None and participant.active


async def _tell_dropped_player(
    tournament_name: str, target: User, reason: ForfeitReason, result: DropResult,
) -> None:
    from application.services import notification_links
    from application.services.discord.discord_service import DiscordService
    from application.services.match._dm_context import _community_name
    from application.utils.discord_embeds import COLOR_CANCELLED, notification_embed
    from application.utils.discord_messages_forfeit import dropped_from_tournament_dm

    if not target.discord_id:
        return
    try:
        message = dropped_from_tournament_dm(
            tournament_name, reason_label=reason.label,
            forfeited=result.forfeited, called_off=result.called_off,
        )
        embed = notification_embed(
            title='🏁 Dropped from a tournament', color=COLOR_CANCELLED,
            description=message, community_name=await _community_name(),
        )
        await DiscordService().send_dm(
            target.discord_id, message, embed=embed,
            link=await notification_links.home_tournaments(),
            notice=DROPPED_FROM_TOURNAMENT.to(PLAYERS),
        )
    except Exception:
        logger.exception('dropped-player DM failed for user %s', target.id)
