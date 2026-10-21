"""Match and schedule reads.

The list/detail split here is deliberate and is the main design decision in the
tool catalogue:

* ``get_match`` reuses ``api/_match_view.serialize_match``, so the rule baked
  into it — unapproved crew are never disclosed — cannot drift between the REST
  API and this surface. That rule is exactly the kind that decays when two
  serializers exist.
* ``list_matches`` returns a compact shape instead. A hundred full match records
  is mostly padding, and padding is the expensive kind of wrong for an LLM
  consumer: it buries the answer and burns the context the model needs to reason.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from api._match_view import MATCH_PREFETCH, serialize_match
from api.schemas.matches import MatchResponse
from api.schemas.reschedule_requests import RescheduleRequestResponse
from application.errors import require_found
from application.services import MatchRescheduleService, MatchService, ReportsService, bracket_sources
from application.services.match.match_status import resolve as resolve_status
from application.tenant_context import require_tenant_id
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    MatchSummary,
    StageBlock,
    TenantArg,
    UnbookedMatchupInfo,
    UnbookedMatchups,
)

# One page of matches. The cap is not politeness: an unbounded list is how a
# tool call silently becomes 200k tokens and the model loses the thread.
MAX_MATCHES = 200


def _summarize(match) -> MatchSummary:
    return MatchSummary(
        id=match.id,
        tournament=match.tournament.name if match.tournament else None,
        scheduled_at=match.scheduled_at,
        status=resolve_status(match=match).value,
        players=[p.user.preferred_name for p in match.players if p.user],
        stage=match.stage.name if match.stage else None,
        restream_url=match.stage.stream_url if match.stage else None,
    )


async def list_matches(
    tenant: TenantArg = None,
    tournament_id: Optional[List[int]] = None,
    stage_id: Optional[List[int]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 50,
) -> List[MatchSummary]:
    """List matches, filtered by tournament, stage, and scheduled window.

    Returns a compact summary per match, ordered by scheduled time. Use
    `get_match` for full detail on one. Times are UTC.
    """
    from models import Match

    limit = max(1, min(limit, MAX_MATCHES))
    query = Match.filter(tenant_id=require_tenant_id())
    if tournament_id:
        query = query.filter(tournament_id__in=tournament_id)
    if stage_id:
        query = query.filter(stage_id__in=stage_id)
    if start is not None:
        query = query.filter(scheduled_at__gte=start)
    if end is not None:
        query = query.filter(scheduled_at__lte=end)
    matches = await (
        query.prefetch_related(*MATCH_PREFETCH).order_by('scheduled_at').limit(limit)
    )
    return [_summarize(m) for m in matches]


async def list_unbooked_matches(
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
    ready_only: bool = False,
    limit: int = 50,
) -> UnbookedMatchups:
    """List open bracket matchups nobody has booked yet: Match Schedule's "Not booked yet" view.

    Covers Challonge, start.gg and native brackets. Staff see the whole
    community and a tournament admin their own tournaments; anyone else gets
    an empty list. The totals count every matchup; `matchups` stops at `limit`.
    """
    rows = await bracket_sources.list_unbooked(current_actor().user)
    if tournament_id is not None:
        rows = [r for r in rows if r.tournament.id == tournament_id]
    by_tournament: Dict[str, int] = {}
    for r in rows:
        by_tournament[r.tournament.name] = by_tournament.get(r.tournament.name, 0) + 1
    ready = sum(1 for r in rows if r.ready)
    shown = [r for r in rows if r.ready] if ready_only else rows
    limit = max(1, min(limit, MAX_MATCHES))
    return UnbookedMatchups(
        total=len(rows),
        ready=ready,
        needs_account=len(rows) - ready,
        by_tournament=by_tournament,
        matchups=[
            UnbookedMatchupInfo(
                source=r.source,
                key=r.key,
                tournament_id=r.tournament.id,
                tournament=r.tournament.name,
                round=r.round_label,
                players=r.player_names,
                ready=r.ready,
                unlinked=r.unlinked,
                window=r.window_text,
                same_player_match_ids=[m.id for m in r.same_player_matches],
            )
            for r in shown[:limit]
        ],
    )


async def get_match(
    match_id: int,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Get full detail for one match: players, seed, stage, and crew.

    Only approved crew are listed. Use `list_match_crew` (admin) to see pending
    signups as well.
    """
    from models import Match

    match = require_found(
        await Match.filter(id=match_id, tenant_id=require_tenant_id())
        .prefetch_related(*MATCH_PREFETCH)
        .first(),
        'Match',
    )
    return serialize_match(match)


async def get_schedule(
    date: str,
    tenant: TenantArg = None,
) -> List[StageBlock]:
    """Get one day's schedule, grouped by stage.

    `date` is YYYY-MM-DD, interpreted in the community's own timezone.
    """
    try:
        parsed = datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ValueError('date must be in YYYY-MM-DD format') from exc

    service = MatchService()
    matches = await service.get_matches_for_date(parsed)
    grouped = await service.group_matches_by_stage(matches)
    return [
        StageBlock(
            stage=stage.name if stage else None,
            matches=[_summarize(m) for m in stage_matches],
        )
        for stage, stage_matches in grouped.values()
    ]


async def match_operations_report(
    start: datetime,
    end: datetime,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Operational statistics for matches in a window: start delays, durations.

    Requires admin access.
    """
    return await ReportsService().match_operations(
        start=start, end=end, tournament_id=tournament_id, actor=current_actor().user,
    )


async def my_reschedule_requests(
    tenant: TenantArg = None,
    limit: int = 25,
) -> List[RescheduleRequestResponse]:
    """Your own requests to move or cancel a match, newest first, and what became of each.

    Mirrors `GET /api/reschedule-requests/mine`. `status` is `pending`,
    `approved`, `declined` or `withdrawn`.
    """
    rows = await MatchRescheduleService().list_mine(
        current_actor().user, limit=max(1, min(limit, 100)),
    )
    return [RescheduleRequestResponse.from_row(row) for row in rows]


def register_tools(mcp: MCPServer) -> None:
    register(mcp, my_reschedule_requests, gate=Gate.ACTOR, title='My reschedule requests')
    register(mcp, list_matches, gate=Gate.ACTOR, title='List matches')
    register(mcp, get_match, gate=Gate.ACTOR, title='Get match')
    register(mcp, list_unbooked_matches, gate=Gate.ACTOR, title='List unbooked matchups')
    register(mcp, get_schedule, gate=Gate.ACTOR, title='Get daily schedule')
    register(
        mcp, match_operations_report, gate=Gate.ADMIN,
        title='Match operations report',
    )
