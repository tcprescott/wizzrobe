"""Admin Service Health tab — tenant read-only subset (STAFF).

The full external-service board lives on the SUPER_ADMIN ``/platform`` surface;
this tab shows a tenant's STAFF only the dependencies *their* tenant relies on —
its authorized racetime bots and its own Challonge connection — with the same
status badges. Read-only: there is no probe-refresh button here (the platform
worker keeps the shared board warm); this view probes the tenant's own subset live
on load so a staffer can see, e.g., their Challonge token expiring before it breaks
a race day.
"""

from nicegui import app, ui

from application.services import ServiceHealthService, ServiceStatus, get_user_from_session
from application.tenant_context import get_current_tenant_id
from pages.admin_tabs.links import INTEGRATIONS, admin_url
from pages.service_health_view import build_refreshable_board
from theme.page_header import page_header


async def admin_service_health_page() -> None:
    with ui.column().classes('page-container-narrow'):
        page_header(
            'Service Health',
            'Health of the external services your tenant depends on — your '
            'authorized racetime bots and your Challonge connection. A credential '
            "warning (e.g. an expiring Challonge token) means reconnect before it "
            'becomes an outage.',
        )

        tenant_id = get_current_tenant_id()
        health = ServiceHealthService()

        viewer = await get_user_from_session(app.storage.user)

        async def _load():
            if tenant_id is None:
                return []
            return await health.tenant_subset(tenant_id, actor=viewer)

        build_refreshable_board(_load, action_for=_route_out)


def _route_out(result) -> tuple | None:
    """The page a staff member fixes this row on, when there is one.

    The blurb above tells them to "reconnect before it becomes an outage" and
    used to leave them to find the tab themselves. Only Challonge gets a link:
    a racetime bot is platform-managed and granted to the tenant by a
    super-admin, so there is nothing a community's staff can do about an
    unhealthy one — and a link that lands on a page which cannot fix it is
    worse than none.
    """
    if result.key == 'challonge' and result.status is not ServiceStatus.HEALTHY:
        return ('Reconnect Challonge under Settings → Integrations', admin_url(INTEGRATIONS))
    return None
