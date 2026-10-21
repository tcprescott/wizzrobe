"""Reports summary dashboard.

KPI strip + report cards. All KPIs scoped to the configured event window.
"""

import asyncio
from typing import Any, Optional

from nicegui import app, ui

from application.services import (
    AuthService,
    DataExportService,
    ReportsService,
    SystemConfigService,
    get_user_from_session,
)
from application.timezone_context import pin_client_context
from application.utils.timezone import format_local_date, format_local_display, timezone_label
from models import FeatureFlag
from theme.page_header import page_header
from theme.section import section_panel

from .shared import (
    date_range_filter,
    default_date_range,
    display_bounds,
    event_jump_options,
    is_custom_range,
    kpi_card,
    kpi_grid,
    navigate_with_params,
    report_filter_bar,
    report_jumps,
    reports_url,
)

REPORT_CARDS: list[dict[str, Any]] = [
    {
        'key': 'insights',
        'title': 'Insights & Trends',
        'icon': 'insights',
        'description': 'Crew participation, volunteer hours, and tournament health trended over weeks or months.',
    },
    {
        'key': 'capacity',
        'title': 'Capacity Forecast',
        'icon': 'show_chart',
        'description': 'Concurrent players across the event window vs configured capacity.',
    },
    {
        'key': 'match_ops',
        'title': 'Match Operations',
        'icon': 'timer',
        'description': 'Start delays, durations, and on-time rate per tournament.',
    },
    {
        'key': 'crew',
        'title': 'Staff / Crew Activity',
        'icon': 'groups',
        'description': 'Commentator and tracker coverage per match and per person.',
    },
    {
        'key': 'stages',
        'title': 'Stage Utilization',
        'icon': 'tv',
        'description': 'Hours scheduled per stage, gaps, and unplaced candidates.',
    },
    {
        'key': 'volunteers',
        'title': 'Volunteer Coverage',
        'icon': 'volunteer_activism',
        'description': 'Per-shift filled vs needed counts, highlighting understaffed shifts.',
        'feature': FeatureFlag.VOLUNTEERS,
    },
    {
        'key': 'audit',
        'title': 'Audit Log',
        'icon': 'fact_check',
        'description': 'Filterable history of admin actions across the app.',
        'staff_only': True,
    },
    {
        'key': 'telemetry',
        'title': 'Engagement Telemetry',
        'icon': 'insights',
        'description': 'How people use the tool: page views, interactions, and '
                       'domain events.',
        # The card used to say "Staff only" in its own description while being
        # offered to every viewer of this tab — the code admitting the gate
        # belonged one level up. telemetry_page still refuses, this stops the
        # invitation.
        'staff_only': True,
    },
    {
        'key': 'exports',
        'title': 'Exports',
        'icon': 'download',
        'description': 'Download tournament participants and matches as CSVs or JSON, '
                       'and anything you exported in the last 3 days.',
        'exporters_only': True,
    },
    {
        'key': 'messages',
        'title': 'Sent messages',
        'icon': 'mail',
        'description': 'Every DM sent in this community, and whether it got there.',
        'staff_only': True,
    },
]


async def dashboard_page(
    live: Optional[set] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> None:
    reports_service = ReportsService()
    viewer = await get_user_from_session(app.storage.user)
    is_staff = await AuthService.is_staff(viewer)
    can_export = await DataExportService.can_export(viewer)
    live = live if live is not None else set()

    # An absent pair still means the event window, so the overview's subtitle
    # keeps telling the truth and an unfiltered dashboard is unchanged.
    start_d, end_d = await default_date_range(start, end)
    bounds_start, bounds_end = display_bounds(start_d, end_d)

    def nav(s, e) -> None:
        navigate_with_params(report=None, start=s, end=e)

    with ui.column().classes('page-container-wide wiz-report'):
        page_header('Reports', 'How the event is going, and the reports behind each number.')

        # `Peak players 14 / 12` is the loudest number here and the one most
        # likely to make someone ask "over what period?"; until now there was no
        # way to answer. No CSV: each KPI links to a report that already exports
        # the data it comes from, so a fifth export would be maintenance, not a
        # feature.
        with report_filter_bar(int(await is_custom_range(start_d, end_d))):
            date_range_filter(start_d, end_d, on_change=nav)
            report_jumps(await event_jump_options(), start_d, end_d, on_pick=nav)

        with pin_client_context():
            forecast, ops, coverage, utilization, max_stages = await asyncio.gather(
                reports_service.generate_capacity_forecast(bounds_start, bounds_end, actor=viewer),
                reports_service.match_operations(bounds_start, bounds_end, actor=viewer),
                reports_service.crew_coverage(bounds_start, bounds_end, actor=viewer),
                reports_service.stage_utilization(bounds_start, bounds_end, actor=viewer),
                SystemConfigService.get_max_concurrent_stages(),
            )

        peak_players = max(forecast['player_counts']) if forecast['player_counts'] else 0
        if peak_players > 0:
            peak_idx = forecast['player_counts'].index(peak_players)
            peak_time = forecast['intervals'][peak_idx]
        else:
            peak_time = None

        peak_stages = 0
        if forecast['intervals']:
            stage_counts = []
            for t in forecast['intervals']:
                used = sum(
                    1 for stage in utilization['stages']
                    if any(m['start'] <= t <= m['end'] for m in stage['matches'])
                )
                stage_counts.append(used)
            peak_stages = max(stage_counts) if stage_counts else 0

        total_matches = len(ops['rows'])
        in_progress = sum(1 for r in ops['rows'] if r['state'] in ('Checked In', 'In Progress'))
        finished = sum(1 for r in ops['rows'] if r['state'] == 'Finished')

        candidate_rows = [r for r in coverage['coverage_rows'] if r['is_stream_candidate']]
        covered = sum(1 for r in candidate_rows if r['commentators_approved'] > 0 and r['trackers_approved'] > 0)
        coverage_pct = (covered / len(candidate_rows) * 100) if candidate_rows else None

        overview = await section_panel(
            'Event overview', icon='insights',
            subtitle=f'{format_local_date(start_d)} to {format_local_date(end_d)} ({timezone_label()})',
        )
        with overview.body, kpi_grid():
            # Each KPI is computed from exactly one report's data, and every
            # link carries this page's window so the destination cannot
            # contradict the tile it came from.
            kpi_card(
                'Peak players',
                f'{peak_players} / {forecast["max_capacity"]}',
                _peak_subtitle(peak_time),
                color='primary' if peak_players <= forecast['max_capacity'] else 'negative',
                href=reports_url('capacity', start=start_d, end=end_d),
                href_label='Capacity forecast →',
            )
            kpi_card(
                'Peak stages used',
                f'{peak_stages} / {max_stages}',
                'across the window',
                color='primary' if peak_stages <= max_stages else 'negative',
                href=reports_url('stages', start=start_d, end=end_d),
                href_label='Stage utilization →',
            )
            kpi_card(
                'Matches',
                f'{total_matches}',
                f'{in_progress} in flight • {finished} finished',
                href=reports_url('match_ops', start=start_d, end=end_d),
                href_label='Match operations →',
            )
            # Three-tier so an ops-critical gap reads as a problem, not a mild
            # amber: green >= 80%, amber 50-79%, red < 50% (and red when there are
            # candidates but zero coverage).
            if coverage_pct is None:
                coverage_color = 'primary'
            elif coverage_pct >= 80:
                coverage_color = 'positive'
            elif coverage_pct >= 50:
                coverage_color = 'warning'
            else:
                coverage_color = 'negative'
            kpi_card(
                'Stream candidate coverage',
                f'{coverage_pct:.0f}%' if coverage_pct is not None else '—',
                f'{covered}/{len(candidate_rows)} fully covered'
                if candidate_rows else 'no stream candidates in window',
                color=coverage_color,
                href=reports_url('crew', start=start_d, end=end_d),
                href_label='Crew activity →',
            )

        reports = await section_panel('All reports', icon='bar_chart')
        with reports.body, ui.element('div').classes('wiz-tile-grid'):
            for card in REPORT_CARDS:
                if card.get('staff_only') and not is_staff:
                    continue
                if card.get('exporters_only') and not can_export:
                    continue
                feature = card.get('feature')
                if feature is not None and feature not in live:
                    continue
                _report_card(card)


def _report_card(card: dict) -> None:
    url = reports_url(report=card['key'])
    # cursor-pointer was on the card while only the link navigated. Make the
    # whole card do what it looks like it does; the link stays for keyboard and
    # middle-click.
    with ui.card().classes('q-pa-md cursor-pointer wiz-kpi') as card_el:
        with ui.row().classes('items-center no-wrap'):
            ui.icon(card['icon']).classes('text-h5 q-mr-sm')  # style-guide: exempt — icon size, not a title
            ui.label(card['title']).classes('section-title')
        ui.label(card['description']).classes('text-body2 q-mt-xs')
        ui.link('Open report →', url).classes('q-mt-sm')
    card_el.on('click', lambda: ui.navigate.to(url))


def _peak_subtitle(peak_time) -> str:
    if peak_time is None:
        return 'no scheduled matches in window'
    return f'at {format_local_display(peak_time)}'
