"""Reporting endpoints — the programmatic peer of the Admin → Reports tab.

Every route is admin (``can_view_admin``: staff, tournament admin, or crew
coordinator), which is the tab's own gate, except ``volunteer_hour_trends``,
which a volunteer coordinator also reads (``require_volunteer_admin``): the
hours are theirs to manage in Admin → Volunteers. That split is why the role gate
sits on each route; the router only authenticates. Reads only, so a read-only token
works throughout.

Two reports are **reshaped rather than passed through**. ``ReportsService``
builds its payloads for a chart that is about to draw them, so they carry
per-interval and per-match detail — ``stage_utilization``'s even contains
ORM ``Match`` rows, which have no serializable form. ``capacity_forecast``
returns peaks and headroom instead of the full series, and
``stage_utilization`` per-stage totals instead of every booking. The MCP
tools in ``mcpserver/tools/analytics.py`` make the same call for the same
reason; the two surfaces answer identically on purpose.

``volunteer_hour_trends`` carries the ``VOLUNTEERS`` flag at the route rather
than the router: ``AnalyticsService`` has no flag of its own, so without it a
community with volunteers switched off could still read its volunteer hours
here.
"""

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import (
    ServiceErrorRoute,
    require_admin,
    require_api_actor,
    require_feature,
    require_volunteer_admin,
)
from api.schemas.reports import (
    ActiveMatchRow,
    ActivityTrendsResponse,
    CapacityForecastResponse,
    CapacityPeak,
    CrewCoverageResponse,
    CrewParticipationTrendsResponse,
    MatchOperationsResponse,
    StageUtilizationResponse,
    StageUtilizationRow,
    TournamentHealthRow,
    VolunteerHourTrendsResponse,
)
from application.services import AnalyticsService, ReportsService
from models import FeatureFlag, User

router = APIRouter(
    prefix="/reports",
    tags=["Reports"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(require_api_actor)],
)


# How many busiest intervals a forecast reports — enough to see the shape of a
# day's peak, short enough that the answer is still readable.
FORECAST_PEAKS = 5

Bucket = Literal['week', 'month']

StartQuery = Query(..., description="Window start (UTC ISO 8601).")
EndQuery = Query(..., description="Window end (UTC ISO 8601).")
BucketQuery = Query(
    'week', description="Bucket size: `week` (buckets start on Monday) or `month` (start on the 1st).",
)


def _window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if end < start:
        raise ValueError("end must not be before start.")
    return start, end


@router.get(
    "/capacity-forecast",
    dependencies=[Depends(require_admin)],
    response_model=CapacityForecastResponse,
    summary="Concurrent-player load against the configured capacity",
    description=(
        "Samples how many players are expected to be playing at once across the window and compares the "
        "peak with the community's configured maximum. A match counts from check-in (or an hour before its "
        "scheduled time) until it finishes (or its expected length runs out).\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def capacity_forecast(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    service = ReportsService()
    raw = await service.generate_capacity_forecast(
        start=start, end=end, tournament_id=tournament_id, actor=actor,
    )
    counts = raw['player_counts']
    capacity = raw['max_capacity']
    return CapacityForecastResponse(
        start=raw['start'],
        end=raw['end'],
        interval_minutes=raw['interval_minutes'],
        max_capacity=capacity,
        peak_players=max(counts, default=0),
        peak_on_stream_players=max(raw['on_stream_player_counts'], default=0),
        over_capacity_intervals=sum(1 for c in counts if capacity and c > capacity),
        busiest=[
            CapacityPeak(at=at, players=players)
            for at, players in service.peak_times(
                raw['intervals'], counts, top_n=FORECAST_PEAKS,
            )
        ],
    )


@router.get(
    "/stage-utilization",
    dependencies=[Depends(require_admin)],
    response_model=StageUtilizationResponse,
    summary="How hard each stage is worked over a window",
    description=(
        "Totals booked hours, idle gaps and back-to-back matches for each active stage over the window, "
        "and counts stream candidates that still have no stage.\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def stage_utilization(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    stage_id: Optional[int] = Query(None, description="Limit to one stage."),
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    raw = await ReportsService().stage_utilization(
        start=start, end=end,
        tournament_id=tournament_id, stage_id=stage_id, actor=actor,
    )
    return StageUtilizationResponse(
        stages=[
            StageUtilizationRow(
                stage_id=stage['stage_id'],
                stage_name=stage['stage_name'],
                scheduled_hours=stage['scheduled_hours'],
                match_count=len(stage['matches']),
                gap_hours=stage['gap_hours'],
                back_to_back_count=stage['back_to_back_count'],
            )
            for stage in raw['stages']
        ],
        unplaced_candidate_count=raw['unplaced_candidate_count'],
    )


@router.get(
    "/match-operations",
    dependencies=[Depends(require_admin)],
    response_model=MatchOperationsResponse,
    summary="Per-tournament match operations: scheduling, lifecycle, punctuality",
    description=(
        "Lists every match scheduled in the window with its start delay, check-in delay, check-in-to-start "
        "time, length and confirmation lag, plus per-tournament averages and on-time rates (within 5 "
        "minutes of schedule), the check-in figures also split by staged and unstaged matches. Matches "
        "whose times can't be a real race are listed in `flagged_match_ids` and left out of the averages.\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def match_operations(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    return await ReportsService().match_operations(
        start=start, end=end, tournament_id=tournament_id, actor=actor,
    )


@router.get(
    "/crew-coverage",
    dependencies=[Depends(require_admin)],
    response_model=CrewCoverageResponse,
    summary="Per-match crew coverage and per-person contribution",
    description=(
        "Shows commentator and tracker signups against each match's requirement, flags stream "
        "candidates short on approved crew, and totals the hours each crew member covered. `tournament_rows` "
        "counts the matches each person actually crewed per tournament: approved crew on finished "
        "matches that went to a stage. With `user_id` set, only that person's matches and "
        "contribution are returned.\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def crew_coverage(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    user_id: Optional[int] = Query(None, description="Limit to one crew member."),
    approved_only: bool = Query(
        False, description="Count only approved signups toward the hours total."
    ),
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    return await ReportsService().crew_coverage(
        start=start, end=end, tournament_id=tournament_id,
        user_id=user_id, approved_only=approved_only, actor=actor,
    )


@router.get(
    "/matches-active-at",
    dependencies=[Depends(require_admin)],
    response_model=List[ActiveMatchRow],
    summary="Which matches are in progress at one moment",
    description=(
        "Returns the matches running at the given instant, judged the same way as the capacity forecast: "
        "from check-in (or an hour before the scheduled time) until finish (or the expected length).\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def matches_active_at(
    instant: datetime = Query(..., description="The moment to look at (UTC ISO 8601)."),
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    limit: int = Query(50, ge=1, le=200, description="Maximum matches to return."),
    actor: User = Depends(require_api_actor),
):
    matches = await ReportsService().matches_active_at(
        instant=instant, tournament_id=tournament_id, actor=actor,
    )
    return [
        ActiveMatchRow(
            match_id=match.id,
            tournament=match.tournament.name if match.tournament else None,
            scheduled_at=match.scheduled_at,
            players=[p.user.preferred_name for p in match.players if p.user],  # type: ignore[attr-defined]
            stage=match.stage.name if match.stage else None,
        )
        for match in matches[:limit]
    ]


@router.get(
    "/tournament-health",
    dependencies=[Depends(require_admin)],
    response_model=List[TournamentHealthRow],
    summary="Per-tournament health scorecards",
    description=(
        "Scores each tournament with matches in the window on completion, punctuality, crew coverage "
        "and match length, lowest score first. Matches scheduled in the future never count against completion.\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def tournament_health(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    report = await AnalyticsService().tournament_health(
        start=start, end=end, tournament_id=tournament_id, actor=actor,
    )
    return report['rows']


@router.get(
    "/crew-participation-trends",
    dependencies=[Depends(require_admin)],
    response_model=CrewParticipationTrendsResponse,
    summary="Crew signups versus approvals over time",
    description=(
        "Buckets commentator and tracker signups and approvals by the week or month their match is "
        "scheduled in, with the top contributors for the window.\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def crew_participation_trends(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    bucket: Bucket = BucketQuery,
    tournament_id: Optional[int] = Query(None, description="Limit to one tournament."),
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    return await AnalyticsService().crew_participation_trends(
        start=start, end=end, bucket=bucket, tournament_id=tournament_id, actor=actor,
    )


@router.get(
    "/activity-trends",
    dependencies=[Depends(require_admin)],
    response_model=ActivityTrendsResponse,
    summary="Audit-log volume over time, grouped by action namespace",
    description=(
        "Counts audit-log entries per week or month, split by the part of the action before the dot "
        "(`match`, `crew`, `tournament` and so on).\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, tournament admins or crew coordinators."
    ),
)
async def activity_trends(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    bucket: Bucket = BucketQuery,
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    return await AnalyticsService().activity_trends(start=start, end=end, bucket=bucket, actor=actor)


@router.get(
    "/volunteer-hour-trends",
    response_model=VolunteerHourTrendsResponse,
    summary="Scheduled versus checked-in volunteer hours over time",
    description=(
        "Buckets volunteer shift hours by the week or month each shift starts in: hours assigned, "
        "hours checked in, hours needed and the fill rate, with a per-position breakdown and the top volunteers.\n\n"
        "Fails with 400 when `end` is before `start`.\n\n"
        "**Who can call this:** Staff, Volunteer coordinators, tournament admins or crew coordinators."
    ),
    # Feature before role, like every other gated route: a switched-off feature
    # is hidden from everyone, so a non-admin must get the same 404 staff do.
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS)), Depends(require_volunteer_admin)],
)
async def volunteer_hour_trends(
    start: datetime = StartQuery,
    end: datetime = EndQuery,
    bucket: Bucket = BucketQuery,
    actor: User = Depends(require_api_actor),
):
    start, end = _window(start, end)
    return await AnalyticsService().volunteer_hour_trends(
        start=start, end=end, bucket=bucket, actor=actor,
    )
