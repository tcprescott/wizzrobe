"""The app's Admin › Reports screen: which reports a viewer sees, and the Overview numbers.

The report bodies stay in ``ReportsService`` / ``AnalyticsService``; this
module answers the two questions the NiceGUI dashboard answered inline: which
reports does this person get, and what are the four headline numbers for a
date range. It also carries the chart series the existing REST reshapes away
(capacity per interval, stage bookings), because the app draws them.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from application.services.audit_service import AuditService
from application.services.auth_service import AuthService
from application.services.exports.data_export_service import DataExportService
from application.services.feature_flag_service import FeatureFlagService
from application.services.reporting_shared import ensure_report_reader, ensure_volunteer_report_reader
from application.services.reports_service import ReportsService
from application.services.system_config_service import SystemConfigService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.audit_display import AREAS, AuditDescription, area_by_key, describe
from application.utils.serialization import decode_json_details
from application.utils.timezone import local_day_bounds
from models import FeatureFlag, User


@dataclass(frozen=True)
class ReportEntry:
    key: str
    title: str
    icon: str
    description: str


REPORTS: Tuple[ReportEntry, ...] = (
    ReportEntry('insights', 'Insights & Trends', 'insights',
                'Crew participation, volunteer hours, and tournament health trended over weeks or months.'),
    ReportEntry('capacity', 'Capacity Forecast', 'show_chart',
                'Concurrent players across the event window vs configured capacity.'),
    ReportEntry('match_ops', 'Match Operations', 'timer',
                'Start delays, durations, and on-time rate per tournament.'),
    ReportEntry('crew', 'Staff / Crew Activity', 'groups',
                'Commentator and tracker coverage per match and per person.'),
    ReportEntry('stages', 'Stage Utilization', 'tv',
                'Hours scheduled per stage, gaps, and unplaced candidates.'),
    ReportEntry('volunteers', 'Volunteer Coverage', 'volunteer_activism',
                'Per-shift filled vs needed counts, highlighting understaffed shifts.'),
    ReportEntry('audit', 'Audit Log', 'fact_check', 'Filterable history of admin actions across the app.'),
    ReportEntry('telemetry', 'Engagement Telemetry', 'query_stats',
                'How people use the tool: page views, interactions, and domain events.'),
    ReportEntry('messages', 'Sent messages', 'mail', 'Every DM sent in this community, and whether it got there.'),
    ReportEntry('exports', 'Exports', 'download',
                'Download tournament participants and matches as CSVs or JSON, and anything you '
                'exported in the last 3 days.'),
)
_STAFF_ONLY = frozenset({'audit', 'telemetry', 'messages'})
_VOLUNTEER_COORDINATOR_REPORTS = frozenset({'volunteers', 'exports'})


@dataclass(frozen=True)
class RangeJump:
    label: str
    start: date
    end: date


@dataclass(frozen=True)
class ReportsMenu:
    overview: bool
    reports: Tuple[ReportEntry, ...]
    event_start: date
    event_end: date
    jumps: Tuple[RangeJump, ...]


@dataclass(frozen=True)
class OverviewKpi:
    key: str
    label: str
    value: str
    detail: str
    tone: str
    report: str


@dataclass(frozen=True)
class CapacitySeries:
    interval_minutes: int
    max_capacity: int
    times: List[datetime] = field(default_factory=list)
    players: List[int] = field(default_factory=list)
    on_stream: List[int] = field(default_factory=list)
    off_stream: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class AuditRow:
    id: int
    created_at: datetime
    actor_id: Optional[int]
    actor_name: str
    action: str
    view: AuditDescription


@dataclass(frozen=True)
class AuditPage:
    total: int
    rows: List[AuditRow]
    areas: Tuple[Tuple[str, str], ...] = tuple((a.key, a.label) for a in AREAS)


class ReportsScreenService:
    """Stateless; every method takes the viewer and checks it."""

    @staticmethod
    async def visible_reports(actor: Optional[User]) -> Tuple[bool, Tuple[ReportEntry, ...]]:
        """(sees the Overview, the reports this viewer gets), mirroring the Admin › Reports tab gate."""
        live = await FeatureFlagService().enabled_flags()
        volunteers_on = FeatureFlag.VOLUNTEERS in live
        if await AuthService.can_view_admin(actor):
            is_staff = await AuthService.is_staff(actor)
            can_export = await DataExportService.can_export(actor)
            shown = tuple(
                r for r in REPORTS
                if (r.key not in _STAFF_ONLY or is_staff)
                and (r.key != 'exports' or can_export)
                and (r.key != 'volunteers' or volunteers_on)
            )
            return True, shown
        if volunteers_on and await AuthService.is_volunteer_coordinator(actor):
            return False, tuple(r for r in REPORTS if r.key in _VOLUNTEER_COORDINATOR_REPORTS)
        raise PermissionError('Only staff, tournament admins and crew coordinators can read reports.')

    @classmethod
    async def menu(cls, actor: Optional[User]) -> ReportsMenu:
        overview, reports = await cls.visible_reports(actor)
        start, end = await SystemConfigService.get_event_window()
        jumps = [RangeJump('Whole event', start, end)]
        days = (end - start).days + 1
        if 1 < days <= 7:
            for offset in range(days):
                day = start + timedelta(days=offset)
                jumps.append(RangeJump(day.strftime('%a %m-%d'), day, day))
        return ReportsMenu(overview, reports, start, end, tuple(jumps))

    @staticmethod
    def _bounds(start: date, end: date) -> Tuple[datetime, datetime]:
        if end < start:
            raise ValueError('The end date must not be before the start date.')
        return local_day_bounds(start, end)

    @classmethod
    async def overview(cls, actor: Optional[User], start: date, end: date) -> List[OverviewKpi]:
        """The four headline numbers the Overview shows for a local-date range."""
        await ensure_report_reader(actor)
        lo, hi = cls._bounds(start, end)
        reports = ReportsService()
        forecast = await reports.generate_capacity_forecast(lo, hi, actor=actor)
        ops = await reports.match_operations(lo, hi, actor=actor)
        coverage = await reports.crew_coverage(lo, hi, actor=actor)
        utilization = await reports.stage_utilization(lo, hi, actor=actor)
        max_stages = await SystemConfigService.get_max_concurrent_stages()

        counts: List[int] = forecast['player_counts']
        peak_players = max(counts) if counts else 0
        peak_at = forecast['intervals'][counts.index(peak_players)] if peak_players else None
        peak_stages = max(
            (
                sum(1 for s in utilization['stages'] if any(m['start'] <= t <= m['end'] for m in s['matches']))
                for t in forecast['intervals']
            ),
            default=0,
        )
        rows = ops['rows']
        in_flight = sum(1 for r in rows if r['state'] in ('Checked In', 'In Progress'))
        finished = sum(1 for r in rows if r['state'] == 'Finished')
        candidates = [r for r in coverage['coverage_rows'] if r['is_stream_candidate']]
        covered = sum(1 for r in candidates if r['commentators_approved'] > 0 and r['trackers_approved'] > 0)
        pct = covered / len(candidates) * 100 if candidates else None
        if pct is None:
            coverage_tone = 'neutral'
        elif pct >= 80:
            coverage_tone = 'ok'
        elif pct >= 50:
            coverage_tone = 'pending'
        else:
            coverage_tone = 'bad'
        capacity = forecast['max_capacity']
        return [
            OverviewKpi(
                'peak_players', 'Peak players', f'{peak_players} / {capacity}',
                peak_at.isoformat() if peak_at else '',
                'bad' if peak_players > capacity else 'neutral', 'capacity',
            ),
            OverviewKpi(
                'peak_stages', 'Peak stages used', f'{peak_stages} / {max_stages}', 'across the window',
                'bad' if peak_stages > max_stages else 'neutral', 'stages',
            ),
            OverviewKpi(
                'matches', 'Matches', str(len(rows)), f'{in_flight} in flight · {finished} finished',
                'neutral', 'match_ops',
            ),
            OverviewKpi(
                'stream_coverage', 'Stream candidate coverage', f'{pct:.0f}%' if pct is not None else '—',
                f'{covered}/{len(candidates)} fully covered' if candidates else 'no stream candidates in window',
                coverage_tone, 'crew',
            ),
        ]

    @classmethod
    async def capacity_series(
        cls, actor: Optional[User], start: date, end: date, tournament_id: Optional[int] = None,
    ) -> CapacitySeries:
        """Players at once per interval, split on and off stream, for the Capacity chart."""
        lo, hi = cls._bounds(start, end)
        forecast = await ReportsService().generate_capacity_forecast(lo, hi, tournament_id, actor=actor)
        return CapacitySeries(
            interval_minutes=forecast['interval_minutes'], max_capacity=forecast['max_capacity'],
            times=list(forecast['intervals']), players=list(forecast['player_counts']),
            on_stream=list(forecast['on_stream_player_counts']),
            off_stream=list(forecast['off_stream_player_counts']),
        )

    @classmethod
    async def stage_bookings(
        cls, actor: Optional[User], start: date, end: date,
    ) -> Dict[int, List[Dict]]:
        """Each stage's bookings in the window (match, tournament, start, end), for the stage timeline."""
        lo, hi = cls._bounds(start, end)
        utilization = await ReportsService().stage_utilization(lo, hi, actor=actor)
        return {
            s['stage_id']: [
                {'match_id': m['match_id'], 'tournament_name': m['tournament_name'],
                 'start': m['start'], 'end': m['end']}
                for m in s['matches']
            ]
            for s in utilization['stages']
        }

    @classmethod
    async def volunteer_coverage(cls, actor: Optional[User], start: date, end: date) -> List[Dict]:
        """Per-shift filled vs needed across a local-date range: the Volunteer Coverage report."""
        await ensure_volunteer_report_reader(actor)
        lo, hi = cls._bounds(start, end)
        return await VolunteerScheduleService().coverage(lo, hi)

    @classmethod
    async def audit_page(
        cls, actor: Optional[User], start: date, end: date, *,
        user_id: Optional[int] = None, area: Optional[str] = None, search: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> AuditPage:
        """One page of the Audit Log as the report shows it: each row described in plain words."""
        lo, hi = cls._bounds(start, end)
        service = AuditService()
        query = dict(
            actor=actor, start=lo, end=hi, user_id=user_id,
            area=area if area_by_key(area) else None, search=(search or '').strip() or None,
        )
        total = await service.count_logs(**query)
        logs = await service.list_logs(**query, limit=limit, offset=offset)
        details = [decode_json_details(log.details) for log in logs]
        names = await service.reference_names(
            actor=actor, details=[d if isinstance(d, dict) else None for d in details],
        )
        return AuditPage(total, [
            AuditRow(
                id=log.id, created_at=log.created_at, actor_id=log.user_id,  # type: ignore[attr-defined]
                actor_name=log.user.preferred_name if log.user else f'Member #{log.user_id}',  # type: ignore[attr-defined]
                action=log.action,
                view=describe(log.action, d, names, actor_id=log.user_id),  # type: ignore[attr-defined]
            )
            for log, d in zip(logs, details, strict=True)
        ])
