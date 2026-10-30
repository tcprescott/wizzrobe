"""Insights & Trends report.

Longitudinal view across events: crew participation trends, volunteer hours
over time, tournament health scorecards, and admin activity — all bucketed by
week or month. Complements the point-in-time snapshot reports.
"""

import asyncio
from datetime import date, timedelta
from typing import List, Optional

from nicegui import app, ui

from application.services import AnalyticsService, get_user_from_session
from application.timezone_context import pin_client_context
from application.utils.timezone import now_local
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    CHART_AMBER,
    CHART_NEUTRAL,
    CHART_RED,
    CHART_TEAL,
    ChartPalette,
    chart_palette,
    csv_export_button,
    date_range_filter,
    display_bounds,
    filter_field,
    kpi_card,
    kpi_grid,
    navigate_with_params,
    parse_date,
    report_filter_bar,
    report_jumps,
    report_page_shell,
    themed_chart_option,
    tournament_filter,
)

# Fallback trailing window for a community with no history at all: trends need
# history, so a single event weekend (the reports default) would collapse to one
# bucket. When there *is* history the window comes from the data's own extent.
DEFAULT_TREND_DAYS = 90

# Below this a single-day extent would render as one bucket with nothing either
# side of it — pad out to something that reads as a trend.
MIN_TREND_DAYS = 28
# And above it, three years of history should not open on 156 weekly buckets;
# keep the most recent slice.
MAX_TREND_DAYS = 400
# Weekly buckets past this many days become unreadable; switch to monthly.
WEEKLY_BUCKET_LIMIT_DAYS = 180


async def _default_range(
    analytics, viewer, start: Optional[str], end: Optional[str],
    tournament_id: Optional[int] = None,
) -> tuple[date, date]:
    """The window to open on. Explicit dates win; otherwise follow the data.

    Measured before this: the fixed 90-day window bucketed weekly gave 13 empty
    buckets and everything in the last one — eight charts that were each one
    spike at the right edge. The crew report's instinct (default to the event
    window) is right but not reusable here, since this page trends *across*
    events; the data's own extent is its equivalent.
    """
    s = parse_date(start)
    e = parse_date(end)
    if s and e:
        return s, e

    today = now_local().date()
    first, last = await analytics.activity_extent(tournament_id=tournament_id, actor=viewer)
    if first is None or last is None:
        return today - timedelta(days=DEFAULT_TREND_DAYS), today

    if (last - first).days > MAX_TREND_DAYS:
        first = last - timedelta(days=MAX_TREND_DAYS)
    if (last - first).days < MIN_TREND_DAYS:
        pad = (MIN_TREND_DAYS - (last - first).days) // 2 + 1
        first, last = first - timedelta(days=pad), last + timedelta(days=pad)
    return first, last


def _default_bucket(start_d: date, end_d: date) -> str:
    """Weekly for a window a week's granularity can render, monthly beyond."""
    return 'week' if (end_d - start_d).days <= WEEKLY_BUCKET_LIMIT_DAYS else 'month'


def _normalize_bucket(bucket: Optional[str]) -> str:
    return 'month' if bucket == 'month' else 'week'


async def insights_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    bucket: Optional[str] = None,
    tournament_id: Optional[int] = None,
    **_unused,
) -> None:
    analytics = AnalyticsService()
    viewer = await get_user_from_session(app.storage.user)
    start_d, end_d = await _default_range(analytics, viewer, start, end, tournament_id)
    # Only *default* the bucket — an explicit choice is the operator's.
    bucket = _normalize_bucket(bucket) if bucket else _default_bucket(start_d, end_d)
    bounds_start, bounds_end = display_bounds(start_d, end_d)

    def nav(**overrides) -> None:
        params = {
            'report': 'insights',
            'start': start_d,
            'end': end_d,
            'bucket': bucket,
            'tournament_id': tournament_id,
        }
        params.update(overrides)
        navigate_with_params(**params)

    today = now_local().date()
    ranges = [
        (label, today - timedelta(days=days), today)
        for days, label in ((30, 'Last 30 days'), (90, 'Last 90 days'), (365, 'Last year'))
    ]
    # Explicit dates are the only thing that counts as narrowed: the default
    # window follows the data, so there is no fixed range to compare against.
    active = int(bool(start or end)) + int(tournament_id is not None)
    with report_page_shell(
        'Insights & Trends',
        'Crew, volunteer hours, tournament health and admin activity, week by week or month by month.',
    ):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e))
            bucket_select = filter_field(ui.select(
                options={'week': 'Weekly', 'month': 'Monthly'},
                value=bucket,
                label='Group by',
            ))
            bucket_select.on('update:model-value', lambda: nav(bucket=bucket_select.value))
            await tournament_filter(tournament_id, on_change=lambda t_id: nav(tournament_id=t_id))
            report_jumps(ranges, start_d, end_d, on_pick=lambda s, e: nav(start=s, end=e))

        crew, hours, health, activity = await _load(analytics, viewer, bounds_start, bounds_end, bucket, tournament_id)
        palette = await chart_palette()

        # Volunteer shifts and audit logs are not tournament-scoped in the data
        # model, so the tournament filter only narrows the crew and health sections.
        scoped = tournament_id is not None
        _kpi_strip(crew, hours, health)
        await _crew_section(crew, start_d, end_d, palette)
        await _volunteer_section(hours, start_d, end_d, palette, scoped)
        await _health_section(health, start_d, end_d)
        await _activity_section(activity, palette, scoped)


async def _load(analytics, viewer, start, end, bucket, tournament_id):
    with pin_client_context():
        return await asyncio.gather(
            analytics.crew_participation_trends(start, end, bucket, tournament_id=tournament_id, actor=viewer),
            analytics.volunteer_hour_trends(start, end, bucket, actor=viewer),
            analytics.tournament_health(start, end, tournament_id=tournament_id, actor=viewer),
            analytics.activity_trends(start, end, bucket, actor=viewer),
        )


def _kpi_strip(crew: dict, hours: dict, health: dict) -> None:
    scored = [r['health_score'] for r in health['rows'] if r['health_score'] is not None]
    avg_health = sum(scored) / len(scored) if scored else None
    approved = crew['totals']['commentator_approved'] + crew['totals']['tracker_approved']

    with kpi_grid():
        kpi_card('Approved crew slots', f'{approved}',
                  f"{crew['totals']['unique_people']} unique people")
        kpi_card('Volunteer hours', f"{hours['totals']['scheduled_hours']:g}",
                  f"{hours['totals']['checked_in_hours']:g}h checked in")
        kpi_card('Tournaments scored', f"{len(scored)}",
                  f"of {len(health['rows'])} in window")
        kpi_card('Avg health score',
                  f'{avg_health:.0f}' if avg_health is not None else '—',
                  'across scored tournaments',
                  color=_health_color(avg_health))


async def _crew_section(crew: dict, start_d: date, end_d: date, palette: ChartPalette) -> None:
    labels = crew['bucket_labels']
    chart = await section_panel(
        'Crew participation over time', icon='headset_mic',
        subtitle='Approved commentator and tracker slots, with how many people filled them.',
    )
    with chart.body:
        if not _has_signal(crew['commentator_approved'] + crew['tracker_approved'] + crew['unique_people']):
            ui.label('No crew activity in the selected window.').classes('italic-note q-mt-md')
        else:
            option = {
                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
                'legend': {'data': ['Commentators', 'Trackers', 'Unique people'], 'top': 0},
                'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                'xAxis': {'type': 'category', 'data': labels},
                'yAxis': [
                    {'type': 'value', 'name': 'Approved slots'},
                    {'type': 'value', 'name': 'People', 'splitLine': {'show': False}},
                ],
                'series': [
                    {'name': 'Commentators', 'type': 'bar', 'stack': 'slots',
                     'data': crew['commentator_approved'], 'itemStyle': {'color': palette.primary}},
                    {'name': 'Trackers', 'type': 'bar', 'stack': 'slots',
                     'data': crew['tracker_approved'], 'itemStyle': {'color': palette.secondary}},
                    {'name': 'Unique people', 'type': 'line', 'yAxisIndex': 1, 'smooth': True,
                     'data': crew['unique_people'], 'itemStyle': {'color': CHART_NEUTRAL},
                     'lineStyle': {'width': 2, 'color': CHART_NEUTRAL}},
                ],
            }
            ui.echart(themed_chart_option(option)).classes('chart-height')

    rows = [
        {
            'name': c['name'],
            'commentator_approved': c['commentator_approved'],
            'tracker_approved': c['tracker_approved'],
            'total_approved': c['total_approved'],
        }
        for c in crew['top_contributors']
    ]
    columns = [
        {'name': 'name', 'label': 'Contributor', 'field': 'name', 'sortable': True},
        {'name': 'commentator_approved', 'label': 'Commentary', 'field': 'commentator_approved', 'sortable': True},
        {'name': 'tracker_approved', 'label': 'Tracking', 'field': 'tracker_approved', 'sortable': True},
        {'name': 'total_approved', 'label': 'Total approved', 'field': 'total_approved', 'sortable': True},
    ]
    top = await section_panel('Top crew contributors', icon='groups')
    with top.aside:
        csv_export_button(f'crew-contributors-{start_d}-to-{end_d}', lambda: columns, lambda: rows)
    with top.body:
        if not rows:
            ui.label('No crew contributors in this window.').classes('italic-note')
        else:
            table = ui.table(columns=columns, rows=rows, pagination=10, row_key='name').classes('full-width')
            enable_mobile_grid(table, columns, table_key=TableKeys.REPORTS_INSIGHTS_CREW)


async def _volunteer_section(
    hours: dict, start_d: date, end_d: date, palette: ChartPalette, scoped: bool = False,
) -> None:
    labels = hours['bucket_labels']
    subtitle = 'Scheduled against checked-in volunteer hours, with the fill rate.'
    if scoped:
        subtitle += ' Event-wide: volunteer shifts are not tied to a tournament.'
    chart = await section_panel('Volunteer hours over time', icon='volunteer_activism', subtitle=subtitle)
    with chart.body:
        if not _has_signal(hours['scheduled_hours'] + hours['checked_in_hours']):
            ui.label('No volunteer hours in the selected window.').classes('italic-note q-mt-md')
        else:
            option = {
                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
                'legend': {'data': ['Scheduled', 'Checked in', 'Fill rate %'], 'top': 0},
                'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                'xAxis': {'type': 'category', 'data': labels},
                'yAxis': [
                    {'type': 'value', 'name': 'Hours'},
                    {'type': 'value', 'name': 'Fill %', 'max': 100, 'splitLine': {'show': False}},
                ],
                'series': [
                    {'name': 'Scheduled', 'type': 'bar',
                     'data': hours['scheduled_hours'], 'itemStyle': {'color': palette.primary}},
                    {'name': 'Checked in', 'type': 'bar',
                     'data': hours['checked_in_hours'], 'itemStyle': {'color': palette.secondary}},
                    {'name': 'Fill rate %', 'type': 'line', 'yAxisIndex': 1, 'smooth': True,
                     'data': hours['fill_rate'], 'itemStyle': {'color': CHART_NEUTRAL},
                     'lineStyle': {'width': 2, 'color': CHART_NEUTRAL}},
                ],
            }
            ui.echart(themed_chart_option(option)).classes('chart-height')

    rows = [
        {
            'name': v['name'],
            'scheduled_hours': v['scheduled_hours'],
            'checked_in_hours': v['checked_in_hours'],
            'shifts': v['shifts'],
        }
        for v in hours['top_volunteers']
    ]
    columns = [
        {'name': 'name', 'label': 'Volunteer', 'field': 'name', 'sortable': True},
        {'name': 'scheduled_hours', 'label': 'Scheduled h', 'field': 'scheduled_hours', 'sortable': True},
        {'name': 'checked_in_hours', 'label': 'Checked-in h', 'field': 'checked_in_hours', 'sortable': True},
        {'name': 'shifts', 'label': 'Shifts', 'field': 'shifts', 'sortable': True},
    ]
    top = await section_panel('Top volunteers by hours', icon='schedule')
    with top.aside:
        csv_export_button(f'volunteer-hours-{start_d}-to-{end_d}', lambda: columns, lambda: rows)
    with top.body:
        if not rows:
            ui.label('No volunteer assignments in this window.').classes('italic-note')
        else:
            table = ui.table(columns=columns, rows=rows, pagination=10, row_key='name').classes('full-width')
            enable_mobile_grid(table, columns, table_key=TableKeys.REPORTS_INSIGHTS_PLAYERS)


async def _health_section(health: dict, start_d: date, end_d: date) -> None:
    rows = health['rows']
    scored = [r for r in rows if r['health_score'] is not None]

    chart = await section_panel(
        'Tournament health', icon='monitor_heart',
        subtitle='A 0–100 score from completion, starting on time, crew coverage and match length.',
    )
    with chart.body:
        if not scored:
            ui.label('No scoreable tournaments in the selected window.').classes('italic-note q-mt-md')
        else:
            ranked = sorted(scored, key=lambda r: r['health_score'])
            names = [r['tournament_name'] for r in ranked]
            data = [
                {'value': r['health_score'], 'itemStyle': {'color': _health_hex(r['health_score'])}}
                for r in ranked
            ]
            option = {
                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
                'grid': {'left': '3%', 'right': '6%', 'bottom': '3%', 'containLabel': True},
                'xAxis': {'type': 'value', 'name': 'Health', 'max': 100},
                'yAxis': {'type': 'category', 'data': names},
                'series': [{'name': 'Health score', 'type': 'bar', 'data': data}],
            }
            ui.echart(themed_chart_option(option)).classes('chart-height')

    display_rows = health_display_rows(rows)
    columns = [
        {'name': 'tournament_name', 'label': 'Tournament', 'field': 'tournament_name', 'sortable': True},
        {'name': 'health_score', 'label': 'Health', 'field': 'health_score', 'sortable': True},
        {'name': 'matches', 'label': 'Finished/Past', 'field': 'matches'},
        {'name': 'completion_pct', 'label': 'Completion', 'field': 'completion_pct', 'sortable': True},
        {'name': 'on_time_pct', 'label': 'On-time', 'field': 'on_time_pct', 'sortable': True},
        {'name': 'coverage_pct', 'label': 'Crew coverage', 'field': 'coverage_pct', 'sortable': True},
        {'name': 'avg_duration_min', 'label': 'Avg dur (exp)', 'field': 'avg_duration_min'},
    ]
    csv_rows = [
        {
            'tournament_name': r['tournament_name'],
            'health_score': r['health_score'],
            'matches_total': r['matches_total'],
            'matches_past': r['matches_past'],
            'matches_finished': r['matches_finished'],
            'completion_pct': r['completion_pct'],
            'on_time_pct': r['on_time_pct'],
            'coverage_pct': r['coverage_pct'],
            'avg_start_delay_min': r['avg_start_delay_min'],
            'avg_duration_min': r['avg_duration_min'],
            'expected_avg_min': r['expected_avg_min'],
        }
        for r in rows
    ]
    csv_columns = [{'name': k, 'label': k, 'field': k} for k in (
        'tournament_name', 'health_score', 'matches_total', 'matches_past',
        'matches_finished', 'completion_pct', 'on_time_pct', 'coverage_pct',
        'avg_start_delay_min', 'avg_duration_min', 'expected_avg_min',
    )]
    detail = await section_panel('Health detail', icon='table_chart')
    with detail.aside:
        csv_export_button(f'tournament-health-{start_d}-to-{end_d}', lambda: csv_columns, lambda: csv_rows)
    with detail.body:
        if not rows:
            ui.label('No tournaments with matches in this window.').classes('italic-note')
        else:
            table = ui.table(columns=columns, rows=display_rows, pagination=25, row_key='tournament_name') \
                .classes('full-width')
            for name, snippet in HEALTH_DISPLAY_CELLS.items():
                table.add_slot(f'body-cell-{name}', f'<q-td :props="props">{snippet}</q-td>')
            enable_mobile_grid(table, columns, field_slots=HEALTH_DISPLAY_CELLS,
                               table_key=TableKeys.REPORTS_TOURNAMENT_HEALTH)


async def _activity_section(activity: dict, palette: ChartPalette, scoped: bool = False) -> None:
    labels = activity['bucket_labels']
    categories = activity['categories']
    subtitle = 'Audit-log actions, by the busiest kinds of action.'
    if scoped:
        subtitle += ' Event-wide: audit activity is not tied to a tournament.'
    chart = await section_panel('Admin activity over time', icon='fact_check', subtitle=subtitle)
    with chart.body:
        if activity['total'] == 0:
            ui.label('No audit activity in the selected window.').classes('italic-note q-mt-md')
        else:
            # Palette holds three distinct series hues; keep the top three
            # categories and fold the long tail into a neutral "Other" stack.
            top = categories[:3]
            colours = [palette.primary, palette.secondary, CHART_RED]
            series = []
            legend = []
            for i, cat in enumerate(top):
                series.append({
                    'name': cat['category'], 'type': 'bar', 'stack': 'actions',
                    'data': cat['counts'], 'itemStyle': {'color': colours[i]},
                })
                legend.append(cat['category'])
            if len(categories) > 3:
                other = [0] * len(labels)
                for cat in categories[3:]:
                    for i, v in enumerate(cat['counts']):
                        other[i] += v
                series.append({
                    'name': 'other', 'type': 'bar', 'stack': 'actions',
                    'data': other, 'itemStyle': {'color': CHART_NEUTRAL},
                })
                legend.append('other')
            option = {
                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
                'legend': {'data': legend, 'top': 0},
                'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                'xAxis': {'type': 'category', 'data': labels},
                'yAxis': {'type': 'value', 'name': 'Actions'},
                'series': series,
            }
            ui.echart(themed_chart_option(option)).classes('chart-height')


def _has_signal(values: List) -> bool:
    return any(v for v in values)


def _pct(value: Optional[float]) -> str:
    return f'{value:.0f}%' if value is not None else '—'


# The Tournament-health columns that are both **sortable** and **formatted**.
# Quasar sorts the raw field value, so a column holding '9%' / '10%' / '100%'
# sorts as text — '100%' first, '9%' last — which is silently wrong and reads as
# a broken report rather than a broken column. So each of these keeps the number
# in its own field (what Quasar sorts) and carries the formatted string beside it
# in ``<name>_display`` (what the cell paints).
HEALTH_SORTED_DISPLAY_FIELDS = (
    'health_score', 'completion_pct', 'on_time_pct', 'coverage_pct',
)

# One snippet per field, used for the desktop cell *and* the mobile card: both
# Quasar scopes expose ``props.row``, so the two cannot drift.
HEALTH_DISPLAY_CELLS = {
    name: '<span>{{ props.row.' + name + '_display }}</span>'
    for name in HEALTH_SORTED_DISPLAY_FIELDS
}


def health_display_rows(rows: List[dict]) -> List[dict]:
    """Table rows for the Tournament-health detail table.

    Pure, so the number-beside-its-rendering invariant above is unit-testable
    without a browser: every sortable column's field must stay numeric.
    """
    return [
        {
            'tournament_name': r['tournament_name'],
            'health_score': r['health_score'],
            'health_score_display': (
                r['health_score'] if r['health_score'] is not None else '—'),
            'matches': f"{r['matches_finished']}/{r['matches_past']}",
            'completion_pct': r['completion_pct'],
            'completion_pct_display': _pct(r['completion_pct']),
            'on_time_pct': r['on_time_pct'],
            'on_time_pct_display': _pct(r['on_time_pct']),
            'coverage_pct': r['coverage_pct'],
            'coverage_pct_display': _pct(r['coverage_pct']),
            'avg_duration_min': _dur(r['avg_duration_min'], r['expected_avg_min']),
        }
        for r in rows
    ]


def _dur(avg: Optional[float], expected: Optional[int]) -> str:
    if avg is None:
        return '—'
    if expected:
        return f'{avg:.0f} ({expected})'
    return f'{avg:.0f}'


def _health_hex(score: Optional[float]) -> str:
    if score is None:
        return CHART_NEUTRAL
    if score >= 75:
        return CHART_TEAL
    if score >= 50:
        return CHART_AMBER
    return CHART_RED


def _health_color(score: Optional[float]) -> str:
    if score is None:
        return 'primary'
    if score >= 75:
        return 'positive'
    if score >= 50:
        return 'warning'
    return 'negative'
