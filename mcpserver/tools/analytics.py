"""Trend and capacity reports, at the admin gate its siblings already use.

These mirror the Admin → Reports tab, whose own gate is
``is_staff or tournament-admin or crew-coordinator`` — exactly what
``can_view_admin`` answers, so ADMIN is the faithful translation.

Two of them are reshaped rather than passed through. ``ReportsService`` builds
its payloads for a chart that is about to draw them, so they carry per-interval
and per-match detail — ``stage_utilization`` even returns ORM ``Match``
rows. Handing a model a few thousand interval entries buries the answer it was
asked for, so the tools return the shape a question actually needs: totals,
peaks, and where the pressure is.
"""

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from application.services import AnalyticsService, ReportsService
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import TenantArg
from mcpserver.tools._args import clamp, parse_ts, window

# How many busiest intervals a forecast reports. Enough to see the shape of a
# day's peak; short enough that the answer is still readable.
FORECAST_PEAKS = 5


async def capacity_forecast(
    start: str,
    end: str,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Concurrent-player load across a window, against the configured capacity.

    Requires admin access. `start` and `end` are ISO 8601 timestamps. Reports
    the peak, the busiest intervals, and how many intervals exceed capacity —
    not the full per-interval series.
    """
    start_dt, end_dt = window(start, end)
    service = ReportsService()
    raw = await service.generate_capacity_forecast(
        start=start_dt, end=end_dt, tournament_id=tournament_id, actor=current_actor().user,
    )
    intervals = raw['intervals']
    counts = raw['player_counts']
    capacity = raw['max_capacity']
    return {
        'start': raw['start'],
        'end': raw['end'],
        'interval_minutes': raw['interval_minutes'],
        'max_capacity': capacity,
        'peak_players': max(counts, default=0),
        'peak_on_stream_players': max(raw['on_stream_player_counts'], default=0),
        'over_capacity_intervals': (
            sum(1 for c in counts if capacity and c > capacity)
        ),
        'busiest': [
            {'at': at, 'players': players}
            for at, players in service.peak_times(
                intervals, counts, top_n=FORECAST_PEAKS,
            )
        ],
    }


async def stage_utilization(
    start: str,
    end: str,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
    stage_id: Optional[int] = None,
) -> Dict[str, Any]:
    """How hard each stage is worked over a window, and what is unplaced.

    Requires admin access. Per stage: scheduled hours, match count, idle gap
    hours, and back-to-back transitions (under 15 minutes between matches).
    `unplaced_candidate_count` is stream-worthy matches with no stage assigned.
    """
    start_dt, end_dt = window(start, end)
    raw = await ReportsService().stage_utilization(
        start=start_dt, end=end_dt,
        tournament_id=tournament_id, stage_id=stage_id, actor=current_actor().user,
    )
    return {
        'stages': [
            {
                'stage_id': stage['stage_id'],
                'stage_name': stage['stage_name'],
                'scheduled_hours': stage['scheduled_hours'],
                'match_count': len(stage['matches']),
                'gap_hours': stage['gap_hours'],
                'back_to_back_count': stage['back_to_back_count'],
            }
            for stage in raw['stages']
        ],
        'unplaced_candidate_count': raw['unplaced_candidate_count'],
    }


async def tournament_health(
    start: str,
    end: str,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Per-tournament scorecards for matches scheduled in a window.

    Requires admin access. Completion is measured only against matches whose
    scheduled time has passed, so upcoming matches never drag a score down.
    """
    start_dt, end_dt = window(start, end)
    report = await AnalyticsService().tournament_health(
        start=start_dt, end=end_dt, tournament_id=tournament_id, actor=current_actor().user,
    )
    return report['rows']


async def crew_participation_trends(
    start: str,
    end: str,
    tenant: TenantArg = None,
    bucket: str = 'week',
    tournament_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Commentator and tracker signups versus approvals over time.

    Requires admin access. `bucket` is `week` or `month`. Participation is
    bucketed by when the match happens, not when someone signed up.
    """
    start_dt, end_dt = window(start, end)
    return await AnalyticsService().crew_participation_trends(
        start=start_dt, end=end_dt, bucket=bucket, tournament_id=tournament_id, actor=current_actor().user,
    )


async def activity_trends(
    start: str,
    end: str,
    tenant: TenantArg = None,
    bucket: str = 'week',
) -> Dict[str, Any]:
    """Audit-log volume over time, grouped by action namespace.

    Requires admin access. `bucket` is `week` or `month`. The namespace is the
    part of a `verb.object` action before the dot, e.g. `match` or `crew`.
    """
    start_dt, end_dt = window(start, end)
    return await AnalyticsService().activity_trends(
        start=start_dt, end=end_dt, bucket=bucket, actor=current_actor().user,
    )


async def matches_active_at(
    instant: str,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Which matches are in progress at one moment — the "what is live now" read.

    Requires admin access. `instant` is an ISO 8601 timestamp. A match counts as
    active from its scheduled start until its expected end.
    """
    moment = parse_ts('instant', instant)
    if moment is None:
        raise ValueError('instant must be an ISO 8601 timestamp')
    matches = await ReportsService().matches_active_at(
        instant=moment, tournament_id=tournament_id, actor=current_actor().user,
    )
    return [
        {
            'match_id': match.id,
            'tournament': match.tournament.name if match.tournament else None,
            'scheduled_at': match.scheduled_at,
            'players': [p.user.preferred_name for p in match.players if p.user],
            'stage': match.stage.name if match.stage else None,
        }
        for match in matches[:clamp(limit, low=1, high=200)]
    ]


def register_tools(mcp: MCPServer) -> None:
    register(mcp, capacity_forecast, gate=Gate.ADMIN, title='Capacity forecast')
    register(
        mcp, stage_utilization, gate=Gate.ADMIN,
        title='Stage utilization',
    )
    register(mcp, tournament_health, gate=Gate.ADMIN, title='Tournament health')
    register(
        mcp, crew_participation_trends, gate=Gate.ADMIN,
        title='Crew participation trends',
    )
    register(mcp, activity_trends, gate=Gate.ADMIN, title='Activity trends')
    register(mcp, matches_active_at, gate=Gate.ADMIN, title='Matches active at')
