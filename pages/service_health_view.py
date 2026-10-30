"""Shared service-health board rendering (PR 5).

One ``@ui.refreshable`` table of dependency statuses, reused by the platform
board (full, refreshable) and the tenant admin tab (read-only subset). Colored
status badges make an outage or credential warning scannable at a glance.
"""

from contextlib import nullcontext
from typing import Callable, List, Optional

from nicegui import background_tasks, context, ui

from application.services import ProbeResult, ServiceStatus
from application.utils.timezone import format_local_display
from theme.busy import busy_click
from theme.empty_state import empty_panel
from theme.tables.preferences import TableKeys, customize_table, preferences_button

_STATUS_COLOR = {
    ServiceStatus.HEALTHY: 'positive',
    ServiceStatus.MOCK: 'info',
    ServiceStatus.DEGRADED: 'orange',
    ServiceStatus.CREDENTIAL_WARNING: 'amber-8',
    ServiceStatus.DOWN: 'negative',
    ServiceStatus.UNKNOWN: 'grey',
}

_STATUS_LABEL = {
    ServiceStatus.HEALTHY: 'Healthy',
    ServiceStatus.MOCK: 'Mock',
    ServiceStatus.DEGRADED: 'Degraded',
    ServiceStatus.CREDENTIAL_WARNING: 'Credential warning',
    ServiceStatus.DOWN: 'Down',
    ServiceStatus.UNKNOWN: 'Unknown',
}

# The probe's own ``category`` is a machine slug ('racetime', 'integrations');
# unknown values fall through title-cased rather than being dropped.
_CATEGORY_LABEL = {
    'racetime': 'Racetime',
    'integrations': 'Integrations',
    'infrastructure': 'Infrastructure',
    'discord': 'Discord',
}

_COLUMNS: list[dict] = [
    {'name': 'label', 'label': 'Dependency', 'field': 'label', 'align': 'left', 'sortable': True},
    {'name': 'category', 'label': 'Category', 'field': 'category', 'align': 'left', 'sortable': True},
    {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left'},
    {'name': 'message', 'label': 'Detail', 'field': 'message', 'align': 'left'},
    {'name': 'checked_at', 'label': 'Checked', 'field': 'checked_at', 'align': 'left'},
    {'name': 'action', 'label': '', 'field': 'action', 'align': 'right'},
]

ActionFor = Callable[[ProbeResult], Optional[tuple]]

_ACTION_SLOT = '''
    <q-td :props="props">
        <a v-if="props.row.action_url" :href="props.row.action_url" class="text-link">
            {{ props.row.action }}
        </a>
    </q-td>
'''


def _rows(results: List[ProbeResult], action_for: Optional[ActionFor] = None) -> List[dict]:
    rows = []
    for r in results:
        action = action_for(r) if action_for else None
        rows.append({
            'label': r.label,
            'category': _CATEGORY_LABEL.get(r.category, r.category.replace('_', ' ').title()),
            'status': _STATUS_LABEL[r.status],
            'status_color': _STATUS_COLOR[r.status],
            'message': r.message,
            'checked_at': format_local_display(r.checked_at),
            'action': action[0] if action else '',
            'action_url': action[1] if action else '',
        })
    return rows


def render_health_table(
    results: List[ProbeResult], action_for: Optional[ActionFor] = None,
) -> None:
    """Render the status table for a set of probe results (no controls)."""
    if not results:
        empty_panel('Nothing to report. No dependencies configured yet.', icon='monitor_heart')
        return
    gear_slot = ui.row().classes('w-full justify-end')
    table = ui.table(
        columns=_COLUMNS, rows=_rows(results, action_for), row_key='label',
    ).classes('w-full wiz-table').props(':grid="Quasar.Screen.lt.md"')
    table.add_slot('body-cell-status', '''
        <q-td :props="props">
            <q-badge :color="props.row.status_color" :label="props.value" />
        </q-td>
    ''')
    table.add_slot('body-cell-action', _ACTION_SLOT)
    # Mobile card (below Quasar's lt.md breakpoint) — the desktop columns overflow
    # a phone viewport, so each dependency renders as a stacked card instead.
    table.add_slot('item', '''
        <div class="q-pa-xs col-xs-12 col-sm-6">
            <q-card bordered flat class="q-pa-sm wiz-grid-card">
                <div class="row items-center justify-between no-wrap q-mb-xs">
                    <div class="text-weight-bold">{{ props.row.label }}</div>
                    <q-badge :color="props.row.status_color" :label="props.row.status" />
                </div>
                <div class="text-caption text-grey-7 q-mb-xs">{{ props.row.category }}</div>
                <div v-if="props.row.message" class="q-mb-xs" style="overflow-wrap:anywhere">{{ props.row.message }}</div>
                <div class="text-caption text-grey-7">Checked {{ props.row.checked_at }}</div>
                <a v-if="props.row.action_url" :href="props.row.action_url"
                   class="text-link">{{ props.row.action }}</a>
            </q-card>
        </div>
    ''')
    customize_table(table, _COLUMNS, key=TableKeys.PLATFORM_SERVICE_HEALTH)
    with gear_slot:
        preferences_button(table)


def build_refreshable_board(
    initial_loader: Callable[[], 'object'],
    *,
    refresh_loader: Optional[Callable[[], 'object']] = None,
    action_for: Optional[ActionFor] = None,
    refresh_slot: Optional[ui.element] = None,
) -> None:
    """Render a health board that loads its rows in the background.

    ``initial_loader`` (a coroutine → list[ProbeResult]) supplies the rows shown on
    mount — typically the cached snapshot, so the page paints instantly. When
    ``refresh_loader`` is given, a "Refresh now" button re-runs it (a live re-probe)
    and repaints. Both are called off the render path so a slow probe never blocks
    the page.

    ``action_for`` is the caller's map from a result to the page that fixes it —
    per board, because the platform's route out of a problem is not a tenant's.

    ``refresh_slot`` is where "Refresh now" goes when the caller has a toolbar
    for it; without one the button sits above the board.
    """
    state: dict = {'results': []}
    # Captured at render time (valid slot context); restored inside the background
    # tasks below, where the slot stack is otherwise empty.
    client = context.client

    @ui.refreshable
    def board() -> None:
        render_health_table(state['results'], action_for)

    async def _load(loader: Callable[[], 'object'], *, notify: bool) -> None:
        results = list(await loader())
        state['results'] = results
        with client:
            board.refresh()
            if notify:
                ui.notify('Health refreshed', color='positive')

    if refresh_loader is not None:
        # refresh-context: exempt — this board is the platform's, not a tenant's.
        # Its loader probes every dependency across the install and takes no
        # tenant, so the rebind theme.tables.admin_crud.refresh_button exists to
        # do would bind nothing. The admin tab passes no refresh_loader at all.
        with refresh_slot if refresh_slot is not None else nullcontext():
            ui.button('Refresh now', icon='refresh',
                      on_click=busy_click('Checking…', lambda: _load(refresh_loader, notify=True)))

    board()
    background_tasks.create(_load(initial_loader, notify=False))
