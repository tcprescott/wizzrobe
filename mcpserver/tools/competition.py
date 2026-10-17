"""Bracket and async-qualifier reads.

Both domains are behind per-tenant feature flags, so these tools also serve as
the working proof that the flag path behaves: a community without the flag gets
`not_found`, identical to asking for a bracket that does not exist, rather than
a message confirming the feature exists but is switched off.
"""

from typing import Any, Dict, List

from mcp.server.mcpserver import MCPServer

from application.errors import require_found
from application.services import BracketService
from application.services.async_qualifier.async_qualifier_live_race_service import (
    AsyncQualifierLiveRaceService,
)
from application.services.async_qualifier.async_qualifier_service import (
    AsyncQualifierService,
)
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    BracketDetail,
    BracketEntrantInfo,
    BracketMatchInfo,
    LeaderboardRow,
    LiveRaceInfo,
    QualifierDetail,
    QualifierPoolInfo,
    TenantArg,
)
from models import FeatureFlag


async def list_brackets(
    tournament_id: int,
    tenant: TenantArg = None,
) -> List[Dict[str, Any]]:
    """List a tournament's brackets."""
    brackets = await BracketService().list_visible_brackets(current_actor().user, tournament_id)
    return [
        {
            'id': b.id,
            'name': b.name,
            'format': getattr(b.format, 'value', b.format),
            'state': getattr(b.state, 'value', b.state),
        }
        for b in brackets
    ]


async def get_bracket_standings(
    bracket_id: int,
    tenant: TenantArg = None,
) -> List[Dict[str, Any]]:
    """Get current standings for a bracket, grouped as the bracket format defines."""
    service = BracketService()
    require_found(await service.get_visible_bracket(current_actor().user, bracket_id), 'Bracket')
    groups = await service.standings(bracket_id)
    return [
        {
            'group': getattr(group, 'name', None),
            'rows': [
                {
                    'rank': getattr(row, 'rank', None),
                    'entrant': getattr(row, 'label', None) or getattr(row, 'name', None),
                    'wins': getattr(row, 'wins', None),
                    'losses': getattr(row, 'losses', None),
                }
                for row in getattr(group, 'rows', [])
            ],
        }
        for group in groups
    ]


async def get_bracket(
    bracket_id: int,
    tenant: TenantArg = None,
) -> BracketDetail:
    """Get one bracket stage: its format, lifecycle state, and how many are entered.

    Use `list_bracket_matches` for the pairings and `get_bracket_standings` for
    the table.
    """
    service = BracketService()
    bracket = require_found(
        await service.get_visible_bracket(current_actor().user, bracket_id), 'Bracket',
    )
    entries = await service.list_entries(bracket_id)
    return BracketDetail(
        id=bracket.id,
        tournament_id=bracket.tournament_id,
        name=bracket.name,
        format=getattr(bracket.format, 'value', bracket.format),
        state=getattr(bracket.state, 'value', bracket.state),
        stage_order=bracket.stage_order,
        entry_count=len(entries),
    )


async def _entry_labels(service: BracketService, bracket) -> Dict[int, str]:
    """Map ``BracketEntry`` id -> entrant display name.

    Built from two list reads rather than prefetching each match's entries: a
    fixed pair of queries covers the whole field, whatever its size.
    """
    entrants = {
        e.id: e.display_name
        for e in await service.list_entrants(bracket.tournament_id)
    }
    return {
        entry.id: entrants.get(entry.entrant_id, f'Entrant {entry.entrant_id}')
        for entry in await service.list_entries(bracket.id)
    }


async def list_bracket_matches(
    bracket_id: int,
    tenant: TenantArg = None,
) -> List[BracketMatchInfo]:
    """List a bracket's pairings in round order, with entrant names and scores.

    A pairing whose slot is not yet decided reports a null entrant — that is a
    match waiting on an earlier round, not missing data.
    """
    service = BracketService()
    bracket = require_found(
        await service.get_visible_bracket(current_actor().user, bracket_id), 'Bracket',
    )
    labels = await _entry_labels(service, bracket)
    return [
        BracketMatchInfo(
            id=m.id,
            round=m.round,
            position=m.position,
            group_number=m.group_number,
            state=getattr(m.state, 'value', m.state),
            entry1=labels.get(m.entry1_id),
            entry2=labels.get(m.entry2_id),
            entry1_score=m.entry1_score,
            entry2_score=m.entry2_score,
            winner=labels.get(m.winner_id),
            forfeit=m.forfeit,
            best_of=m.best_of,
        )
        for m in await service.list_matches(bracket_id)
    ]


async def list_bracket_entrants(
    tournament_id: int,
    tenant: TenantArg = None,
) -> List[BracketEntrantInfo]:
    """List everyone enrolled in a tournament's brackets, dropped entrants included.

    Entrants belong to the tournament, not to one stage, so the same person
    carries across every bracket in it.
    """
    return [
        BracketEntrantInfo(
            id=e.id,
            display_name=e.display_name,
            status=getattr(e.status, 'value', e.status),
            user_id=e.user_id,
        )
        for e in await _visible_entrants(tournament_id)
    ]


async def _visible_entrants(tournament_id: int):
    """A tournament's roster, or ``not_found`` if the tournament is not public to this caller."""
    service = BracketService()
    require_found(
        await service.get_visible_tournament(current_actor().user, tournament_id), 'Tournament',
    )
    return await service.list_entrants(tournament_id)


async def list_async_qualifiers(
    tenant: TenantArg = None,
) -> List[Dict[str, Any]]:
    """List the community's async qualifiers.

    A qualifier admin or reviewer gets every qualifier they administer; anyone
    else gets the open ones, as the Qualifiers page lists them.
    """
    service = AsyncQualifierService()
    try:
        qualifiers = await service.list_qualifiers(current_actor().user)
    except PermissionError:
        qualifiers = await service.list_open_qualifiers()
    return [
        {
            'id': q.id,
            'name': q.name,
            'active': q.is_active,
            'event_name': q.event_name,
            'opens_at': q.opens_at,
            'closes_at': q.closes_at,
        }
        for q in qualifiers
    ]


async def get_async_qualifier(
    qualifier_id: int,
    tenant: TenantArg = None,
) -> QualifierDetail:
    """Get one async qualifier's configuration and its pools.

    `results_public` says whether the leaderboard is visible to entrants yet;
    while it is false only a qualifier admin can read the standings. `pools` is
    filled for the qualifier's admins only; everyone else gets the empty list.
    """
    service = AsyncQualifierService()
    actor = current_actor()
    try:
        qualifier = await service.get_qualifier(actor.user, qualifier_id)
        pools = await service.list_pools(actor.user, qualifier_id)
    except PermissionError:
        qualifier = await service.get_qualifier_for_player(qualifier_id)
        pools = []
    return QualifierDetail(
        id=qualifier.id,
        name=qualifier.name,
        description=qualifier.description,
        event_name=qualifier.event_name,
        is_active=qualifier.is_active,
        opens_at=qualifier.opens_at,
        closes_at=qualifier.closes_at,
        runs_per_pool=qualifier.runs_per_pool,
        allowed_reattempts=qualifier.allowed_reattempts,
        results_public=service.is_results_public(qualifier),
        pools=[
            QualifierPoolInfo(
                id=pool.id,
                name=pool.name,
                preset=pool.preset.name if pool.preset else None,
                permalink_count=len(pool.permalinks),
            )
            for pool in pools
        ],
    )


async def get_async_qualifier_leaderboard(
    qualifier_id: int,
    tenant: TenantArg = None,
) -> List[LeaderboardRow]:
    """Get an async qualifier's standings, best total first.

    While a qualifier is still open its leaderboard is hidden from everyone but
    its admins, so a `forbidden` here means "results are not public yet", not
    "you asked wrongly".
    """
    entries = await AsyncQualifierService().get_leaderboard(
        current_actor().user, qualifier_id
    )
    return [
        LeaderboardRow(
            # The service's own rank, not this loop's index: equal totals share one,
            # and three surfaces deriving it separately is how a tie came to read
            # 1, 2, 3 in alphabetical order.
            rank=entry.rank,
            user_id=entry.user_id,
            name=entry.username,
            actual=entry.actual,
            estimate=entry.estimate,
            slots_filled=entry.slots_filled,
            slots_total=entry.slots_total,
        )
        for entry in entries
    ]


async def list_async_qualifier_live_races(
    qualifier_id: int,
    tenant: TenantArg = None,
) -> List[LiveRaceInfo]:
    """List the live races scheduled against one qualifier's pools, newest first.

    Requires qualifier-admin access, as the REST endpoint does.
    """
    races = await AsyncQualifierLiveRaceService().list_live_races(
        current_actor().user, qualifier_id
    )
    return [
        LiveRaceInfo(
            id=race.id,
            match_title=race.match_title,
            status=getattr(race.status, 'value', race.status),
            pool=race.pool.name if race.pool else None,
            racetime_slug=race.racetime_slug,
        )
        for race in races
    ]


def register_tools(mcp: MCPServer) -> None:
    register(
        mcp, list_brackets, gate=Gate.ACTOR,
        feature=FeatureFlag.BRACKETS, title='List brackets',
    )
    register(
        mcp, get_bracket, gate=Gate.ACTOR,
        feature=FeatureFlag.BRACKETS, title='Get bracket',
    )
    register(
        mcp, list_bracket_matches, gate=Gate.ACTOR,
        feature=FeatureFlag.BRACKETS, title='List bracket matches',
    )
    register(
        mcp, list_bracket_entrants, gate=Gate.ACTOR,
        feature=FeatureFlag.BRACKETS, title='List bracket entrants',
    )
    register(
        mcp, get_bracket_standings, gate=Gate.ACTOR,
        feature=FeatureFlag.BRACKETS, title='Bracket standings',
    )
    register(
        mcp, list_async_qualifiers, gate=Gate.ACTOR,
        feature=FeatureFlag.ASYNC_QUALIFIERS, title='List async qualifiers',
    )
    register(
        mcp, get_async_qualifier, gate=Gate.ACTOR,
        feature=FeatureFlag.ASYNC_QUALIFIERS, title='Get async qualifier',
    )
    register(
        mcp, get_async_qualifier_leaderboard, gate=Gate.ACTOR,
        feature=FeatureFlag.ASYNC_QUALIFIERS, title='Qualifier leaderboard',
    )
    register(
        mcp, list_async_qualifier_live_races, gate=Gate.ACTOR,
        feature=FeatureFlag.ASYNC_QUALIFIERS, title='List qualifier live races',
    )
