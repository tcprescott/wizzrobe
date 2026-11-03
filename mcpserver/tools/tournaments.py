"""Tournament and stage reads.

Mirrors the gates on ``api/routers/tournaments.py`` and
``api/routers/stages.py``: a live token is the bar, and the tenant binding
around the call does the rest.
"""

from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.tournaments import TournamentResponse
from application.api_scopes import ApiScope
from application.errors import require_found
from application.services import (
    MatchSuggestionService,
    PayoutService,
    StageService,
    TournamentService,
)
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    MatchTimeSuggestion,
    OperationResult,
    PayoutEntry,
    StageInfo,
    TenantArg,
    TournamentPayouts,
    TournamentSignupInfo,
    TournamentSummary,
    UserSummary,
)
from models import FeatureFlag


async def list_tournaments(
    tenant: TenantArg = None,
    active_only: bool = False,
) -> List[TournamentSummary]:
    """List a community's tournaments.

    Returns a compact summary per tournament; use `get_tournament` for full
    detail on one. Set `active_only` to skip finished and archived events.
    Inactive tournaments are listed only for admins.
    """
    tournaments = await TournamentService().list_for_viewer(
        current_actor().user, active_only=active_only,
    )
    return [TournamentSummary.model_validate(t, from_attributes=True) for t in tournaments]


async def get_tournament(
    tournament_id: int,
    tenant: TenantArg = None,
) -> TournamentResponse:
    """Get full detail for one tournament, including its configuration.

    An inactive tournament is not found unless you're an admin.
    """
    tournament = require_found(
        await TournamentService().get_for_viewer(current_actor().user, tournament_id),
        'Tournament',
    )
    return TournamentResponse.model_validate(tournament, from_attributes=True)


async def list_tournament_signups(
    tenant: TenantArg = None,
) -> List[TournamentSignupInfo]:
    """List the community's active tournaments with your own signup state on each.

    Says whether you are already entered, how many entrants there are, where the
    signup window stands, and whether `sign_up_for_tournament` would be accepted
    right now — check `can_sign_up` rather than reading the window yourself.
    """
    actor = current_actor().user
    cards = await TournamentService().list_signup_cards(actor)
    return [
        TournamentSignupInfo(
            id=card.tournament.id,
            name=card.tournament.name,
            window=card.window.value,
            enrolled=card.enrolled,
            entrant_count=card.entrant_count,
            can_sign_up=card.can_sign_up,
            can_withdraw=card.can_withdraw,
            signups_open_at=card.tournament.signups_open_at,
            signups_close_at=card.tournament.signups_close_at,
        )
        for card in cards
    ]


async def list_tournament_players(
    tournament_id: int,
    tenant: TenantArg = None,
) -> List[UserSummary]:
    """List everyone enrolled in a tournament. Staff or that tournament's admins."""
    tournament = require_found(
        await TournamentService().get_tournament_by_id(tournament_id), 'Tournament'
    )
    players = await TournamentService().list_players(tournament, current_actor().user)
    return [UserSummary.model_validate(u, from_attributes=True) for u in players]


async def sign_up_for_tournament(
    tournament_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Sign yourself up to play in a tournament.

    Enters the person this connection belongs to, never anyone else. Refused
    outside the signup window and for a non-member. A tournament linked to
    Challonge adds you to its Challonge bracket under your linked Challonge
    account, and is refused if you have not linked one or the bracket has
    started.
    """
    actor = current_actor().user
    tournament = await TournamentService().self_enroll(tournament_id, actor)
    return OperationResult(detail=f'Signed up for {tournament.name}.')


async def withdraw_from_tournament(
    tournament_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Withdraw yourself from a tournament, while its signup window is open."""
    actor = current_actor().user
    tournament = await TournamentService().self_withdraw(tournament_id, actor)
    return OperationResult(detail=f'Withdrew from {tournament.name}.')


async def get_tournament_payouts(
    tournament_id: int,
    tenant: TenantArg = None,
) -> TournamentPayouts:
    """Get a tournament's prize split — each placement's share and what it pays.

    Percentage lines are computed from the pool plus the bonus at read time, so
    they always match the pool as it stands now; fixed lines pay their amount. Several rows may share a place:
    joint placings are paid separately, not halved. Requires staff, or being an
    admin of this tournament.
    """
    split = await PayoutService().get_split(tournament_id, current_actor().user)
    return TournamentPayouts(
        tournament_id=tournament_id,
        prize_pool=split.pool,
        prize_bonus=split.bonus,
        total=split.total,
        lines=[
            PayoutEntry(
                place=line.place,
                percentage=line.percentage,
                is_fixed=line.is_fixed,
                amount=line.amount,
                entrant_name=line.entrant_name,
                matcherino_username=line.matcherino_username,
                note=line.payout.note,
            )
            for line in split.lines
        ],
    )


async def list_stages(
    tenant: TenantArg = None,
    active_only: bool = False,
) -> List[StageInfo]:
    """List the community's stages (restream channels matches are assigned to)."""
    stages = await StageService().get_all_stages(active_only=active_only)
    return [StageInfo.model_validate(r, from_attributes=True) for r in stages]


async def get_stage(
    stage_id: int,
    tenant: TenantArg = None,
) -> StageInfo:
    """Get one stage by id."""
    stage = require_found(
        await StageService().get_stage_by_id(stage_id), 'Stage'
    )
    return StageInfo.model_validate(stage, from_attributes=True)


async def suggest_match_time(
    tournament_id: int,
    player_ids: List[int],
    tenant: TenantArg = None,
    bracket_match_id: Optional[int] = None,
) -> MatchTimeSuggestion:
    """Suggest a UTC start time for a match between the given players.

    Searches the tournament's configured hours for the earliest slot where every
    player is free (by declared availability and their other matches) and the
    room stays under its concurrent-player cap. Pass
    `bracket_match_id` to confine the search to that matchup's round window.
    Errors with `invalid_request` when no slot fits at all.
    """
    return MatchTimeSuggestion(
        suggested_at=await MatchSuggestionService().suggest_match_time(
            tournament_id, player_ids, bracket_match_id=bracket_match_id,
            actor=current_actor().user,
        )
    )


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_tournaments, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, title='List tournaments', event=True)
    register(mcp, get_tournament, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, title='Get tournament', event=True)
    register(mcp, list_tournament_signups, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, title='List tournament signups', event=True)
    register(mcp, list_tournament_players, gate=Gate.ACTOR, scope=ApiScope.STAFF_READ, title='List tournament players', event=True)
    register(
        mcp, sign_up_for_tournament, gate=Gate.ACTOR, scope=ApiScope.PLAY, write=True,
        title='Sign up for tournament',
        event=True,
    )
    register(
        mcp, withdraw_from_tournament, gate=Gate.ACTOR, scope=ApiScope.PLAY, write=True,
        title='Withdraw from tournament', destructive=True,
        event=True,
    )
    register(
        mcp, get_tournament_payouts, gate=Gate.ACTOR, scope=ApiScope.STAFF_READ,
        feature=FeatureFlag.PAYOUTS, title='Get tournament payouts',
        event=True,
    )
    register(mcp, list_stages, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, title='List stages', event=True)
    register(mcp, get_stage, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, title='Get stage', event=True)
    register(mcp, suggest_match_time, gate=Gate.ACTOR, scope=ApiScope.SCHEDULE_READ, title='Suggest a match time', event=True)
