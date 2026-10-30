"""Admin Reports package.

Dispatches to a specific report based on the ``report`` query-string param.
Falls back to the summary dashboard.
"""

from typing import Optional

from nicegui import app, background_tasks, ui

from application.services import FeatureFlagService, TelemetryService
from models import FeatureFlag
from theme.assets import asset_url
from theme.steady import steady_refreshable

from .audit import audit_page
from .capacity import capacity_page
from .crew import crew_page
from .dashboard import dashboard_page
from .exports import exports_page
from .insights import insights_page
from .match_ops import match_ops_page
from .messages import messages_page
from .shared import bind_report_refresh
from .stages import stages_page
from .telemetry import telemetry_page
from .volunteers import volunteers_page

_REPORT_HANDLERS = {
    'insights': insights_page,
    'capacity': capacity_page,
    'match_ops': match_ops_page,
    'crew': crew_page,
    'stages': stages_page,
    'volunteers': volunteers_page,
    'audit': audit_page,
    'telemetry': telemetry_page,
    'messages': messages_page,
    'exports': exports_page,
}

_REPORT_FEATURES = {
    'volunteers': FeatureFlag.VOLUNTEERS,
}

VOLUNTEER_REPORTS: frozenset[str] = frozenset({'volunteers', 'exports'})


def _track_report_view(report: str) -> None:
    """Fire-and-forget an interaction row when a specific report is opened.

    Only called for an explicit ``report`` (not the dashboard landing), so a
    plain ``/admin`` load — where every tab panel renders eagerly — does not
    manufacture a spurious report view.
    """
    try:
        background_tasks.create(
            TelemetryService().track_interaction(
                event_type='report.viewed',
                path=report,
                discord_id=app.storage.user.get('discord_id'),
                user_id=app.storage.user.get('user_id'),
                username=app.storage.user.get('username'),
                session_id=app.storage.browser.get('id'),
            )
        )
    except Exception:
        pass


async def _render_report(
    report: Optional[str], params: dict, live: set,
    only: Optional[frozenset[str]] = None,
) -> None:
    """Dispatch to one report body. Re-runs on every filter change.

    ``only`` narrows a viewer to those reports. The dashboard is not among them
    (its KPIs read every report's data), so anything else, the landing
    included, opens the first allowed report in ``_REPORT_HANDLERS`` order, with
    no "All reports" link to go back to.
    """
    if only is not None:
        if report not in only:
            report = next(key for key in _REPORT_HANDLERS if key in only)
        params = {**params, 'all_reports_link': False}
    feature = _REPORT_FEATURES.get(report)
    if feature is not None and feature not in live:
        report = None
    handler = _REPORT_HANDLERS.get(report)
    if handler is None and only is not None:
        return
    if handler is None:
        await dashboard_page(
            live=live, start=params.get('start'), end=params.get('end'),
        )
        return
    _track_report_view(report)
    await handler(**params)


async def reports_page(
    report: Optional[str] = None,
    only: Optional[frozenset[str]] = None,
    **params,
) -> None:
    """Top-level entry called from the admin tabs config.

    ``only`` limits the viewer to those report keys (see ``_render_report``).
    """
    # Installed here, not per report, so the dashboard gets it too.
    ui.add_head_html(f'<script src="{asset_url("js/report-nav.js")}"></script>')

    live = await FeatureFlagService().enabled_flags()

    # The filter strip is built inside each report body, so its handlers close over the new values.
    @steady_refreshable
    async def report_body(report: Optional[str], params: dict) -> None:
        await _render_report(report, params, live, only)

    bind_report_refresh(lambda r, p: report_body.refresh(r, p))
    await report_body(report, params)


__all__ = ['VOLUNTEER_REPORTS', 'reports_page']
