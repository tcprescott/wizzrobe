"""The app's Admin › Reports screen: the menu, the Overview, and the reads the
existing ``/reports`` routes reshape away (chart series, stage bookings) or
never had (volunteer coverage by date, the exports picker, the telemetry log).
"""

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import (
    ServiceErrorRoute,
    event_binding,
    event_filter,
    require_admin,
    require_api_actor,
    require_feature,
    require_staff,
)
from api.schemas.admin_reports import (
    ActivityExtentResponse,
    AuditAreaResponse,
    AuditChangeResponse,
    AuditFact,
    AuditPageResponse,
    AuditRowResponse,
    CapacitySeriesResponse,
    ExportableTournament,
    OverviewKpiResponse,
    RangeJumpResponse,
    ReportEntryResponse,
    ReportsMenuResponse,
    StageBookingResponse,
    StageBookingsResponse,
    TelemetryEventPage,
    TelemetryEventRow,
    VolunteerCoverageRow,
)
from application.services import AnalyticsService, DataExportService, ReportsScreenService, TelemetryService
from models import FeatureFlag, User

router = APIRouter(
    prefix='/admin/reports',
    tags=['Reports'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding()), Depends(require_api_actor)],
)

StartDate = Query(..., description='First day of the range, on the community clock (YYYY-MM-DD).')
EndDate = Query(..., description='Last day of the range, inclusive (YYYY-MM-DD).')


@router.get(
    '',
    response_model=ReportsMenuResponse,
    summary='The reports you can open, and the default date range',
    description=(
        'Lists the reports this viewer gets in display order, whether they get the Overview, the '
        "event's dates (the default range) and range shortcuts. A volunteer coordinator who isn't "
        'otherwise an admin gets only Volunteer Coverage and Exports, and no Overview.\n\n'
        '**Who can call this:** Staff, tournament admins, crew coordinators, volunteer coordinators.'
    ),
)
async def reports_menu(actor: User = Depends(require_api_actor)):
    menu = await ReportsScreenService.menu(actor)
    return ReportsMenuResponse(
        overview=menu.overview,
        reports=[ReportEntryResponse(key=r.key, title=r.title, icon=r.icon, description=r.description) for r in menu.reports],
        event_start=menu.event_start, event_end=menu.event_end,
        jumps=[RangeJumpResponse(label=j.label, start=j.start, end=j.end) for j in menu.jumps],
        zone=menu.zone,
    )


@router.get(
    '/overview',
    response_model=List[OverviewKpiResponse],
    summary='The Overview: four headline numbers for a date range',
    description=(
        'Peak players against capacity, peak stages used, matches (in flight and finished) and '
        'stream candidate crew coverage, each naming the report behind it.\n\n'
        '**Who can call this:** Staff, tournament admins, crew coordinators.'
    ),
)
async def overview(start: date = StartDate, end: date = EndDate, actor: User = Depends(require_admin)):
    return [OverviewKpiResponse(**k.__dict__) for k in await ReportsScreenService.overview(actor, start, end)]


@router.get(
    '/capacity',
    response_model=CapacitySeriesResponse,
    summary='Players at once across a date range, point by point',
    description=(
        'The Capacity Forecast chart: players in matches at each point, split on and off stage, '
        'with the configured capacity. Points are 15, 30 or 60 minutes apart depending on the '
        'range.\n\n**Who can call this:** Staff, tournament admins, crew coordinators.'
    ),
)
async def capacity(
    start: date = StartDate, end: date = EndDate,
    tournament_id: Optional[int] = Query(None, description='Only this tournament.'),
    actor: User = Depends(require_admin),
):
    series = await ReportsScreenService.capacity_series(actor, start, end, tournament_id)
    return CapacitySeriesResponse(**series.__dict__)


@router.get(
    '/stage-bookings',
    response_model=StageBookingsResponse,
    summary="Each stage's bookings across a date range",
    description=(
        'The Stage Utilization timeline: every match booked on each active stage, clipped to '
        'the range. Totals, gaps and unplaced candidates come from `GET /reports/stage-utilization`.'
        '\n\n**Who can call this:** Staff, tournament admins, crew coordinators.'
    ),
)
async def stage_bookings(start: date = StartDate, end: date = EndDate, actor: User = Depends(require_admin)):
    stages = await ReportsScreenService.stage_bookings(actor, start, end)
    return StageBookingsResponse(
        stages={sid: [StageBookingResponse(**b) for b in rows] for sid, rows in stages.items()},
    )


@router.get(
    '/volunteer-coverage',
    response_model=List[VolunteerCoverageRow],
    summary='Filled vs needed for every volunteer shift in a date range',
    description=(
        'One row per shift that overlaps the range, flagging understaffed ones. Drafts count as '
        'filled; `drafts` and `acknowledged` break that down.\n\n'
        '**Who can call this:** Staff, tournament admins, crew coordinators, volunteer coordinators.'
    ),
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS))],
)
async def volunteer_coverage(start: date = StartDate, end: date = EndDate, actor: User = Depends(require_api_actor)):
    return [VolunteerCoverageRow(**r) for r in await ReportsScreenService.volunteer_coverage(actor, start, end)]


@router.get(
    '/exportable-tournaments',
    response_model=List[ExportableTournament],
    summary='Tournaments you can export, with counts',
    description=(
        'The Exports picker: each tournament you may export with its player and match counts and '
        'where its bracket lives. Queue an export with `POST /exports/tournaments`.\n\n'
        '**Who can call this:** Staff and tournament admins (their own tournaments).'
    ),
)
async def exportable_tournaments(actor: User = Depends(require_api_actor)):
    return [ExportableTournament(**r) for r in await DataExportService().tournament_overview(actor)]


@router.get(
    '/activity-extent',
    response_model=ActivityExtentResponse,
    summary='The first and last days with any activity',
    description=(
        'Where Insights & Trends should open so its charts have data in them. Both null for a '
        'community with no history.\n\n**Who can call this:** Staff, tournament admins, crew coordinators.'
    ),
)
async def activity_extent(
    tournament_id: Optional[int] = Query(None, description='Only this tournament (matches only).'),
    actor: User = Depends(require_admin),
):
    first, last = await AnalyticsService().activity_extent(tournament_id=tournament_id, actor=actor)
    return ActivityExtentResponse(first=first, last=last)


@router.get(
    '/telemetry-events',
    response_model=TelemetryEventPage,
    summary='The engagement telemetry log, one page at a time',
    description=(
        'Telemetry rows newest first with the total matching the same filters, for the '
        'Engagement Telemetry event log. Leaderboards come from `GET /telemetry/top`.\n\n'
        '**Who can call this:** Staff.'
    ),
)
async def telemetry_events(
    start: Optional[datetime] = Query(None, description='Only rows at or after this time (UTC ISO 8601).'),
    end: Optional[datetime] = Query(None, description='Only rows before this time (UTC ISO 8601).'),
    category: Optional[str] = Query(None, description='Only this category, e.g. `page`.'),
    user_id: Optional[int] = Query(None, description="Only this person's rows."),
    path_contains: Optional[str] = Query(None, description='Only rows whose path contains this text.'),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: User = Depends(require_staff),
    event_id: Optional[int] = Depends(event_filter),
):
    service = TelemetryService()
    filters = dict(
        start=start, end=end, event_id=event_id, category=category, user_id=user_id, path_contains=path_contains,
    )
    total = await service.count_events(actor, **filters)
    rows = await service.list_events(actor, **filters, limit=limit, offset=offset)
    return TelemetryEventPage(
        total=total, limit=limit, offset=offset,
        items=[
            TelemetryEventRow(
                id=r.id, created_at=r.created_at, user_id=r.user_id,
                user_name=r.user.preferred_name if r.user else None,
                category=r.category, event_type=r.event_type, path=r.path, details=r.details,
            )
            for r in rows
        ],
    )


@router.get(
    '/audit',
    response_model=AuditPageResponse,
    summary='The Audit Log as the report shows it',
    description=(
        'One page of audit entries newest first, each described in words (what, about whom, what '
        'changed) with the names of members, stages and tournaments it mentions resolved. Filter '
        'by local-date range, area, person or a search over the action and details. The raw rows '
        'are `GET /audit-logs`.\n\n**Who can call this:** Staff.'
    ),
)
async def audit(
    start: date = StartDate, end: date = EndDate,
    user_id: Optional[int] = Query(None, description="Only this person's actions."),
    area: Optional[str] = Query(None, description='Only this area, by key (see `areas`).'),
    search: Optional[str] = Query(None, description='Text to find in the action or its details.'),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: User = Depends(require_staff),
):
    page = await ReportsScreenService.audit_page(
        actor, start, end, user_id=user_id, area=area, search=search, limit=limit, offset=offset,
    )
    return AuditPageResponse(
        total=page.total, limit=limit, offset=offset,
        areas=[AuditAreaResponse(key=k, label=v) for k, v in page.areas],
        items=[
            AuditRowResponse(
                id=r.id, created_at=r.created_at, actor_id=r.actor_id, actor_name=r.actor_name,
                action=r.action, label=r.view.label, area=r.view.area, subject=r.view.subject,
                summary=r.view.summary,
                changes=[AuditChangeResponse(field=c.field, before=c.before, after=c.after) for c in r.view.changes],
                changed_fields=r.view.changed_fields,
                facts=[AuditFact(label=k, value=v) for k, v in r.view.facts],
                raw=r.view.raw, match_id=r.view.match_id,
            )
            for r in page.rows
        ],
    )
