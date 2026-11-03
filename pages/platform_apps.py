"""The Apps tab of ``/platform``: every third-party app, and the everywhere switch.

A super admin sees each app's owner, kind, how many people connected it and in
how many communities, and can disable it with a reason (abuse). Disabling stops
every grant at once but keeps them, so enabling again restores them. A
community's own block lives in its Settings → Connected apps.
"""

from nicegui import ui

from application.services import OAuthAppService
from models import OAuthClientType
from pages.platform_shared import current_actor, section_intro
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.menu import SlotItem, slot_more_menu
from theme.notify import notify_error
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

_service = OAuthAppService()

_STATUS = '''
    <span class="wiz-chip" :class="props.row.disabled ? 'wiz-chip--cancelled' : 'wiz-chip--ok'">
        {{ props.row.disabled ? 'Disabled' : 'Active' }}
    </span>
    <div v-if="props.row.reason" class="text-caption" style="white-space:normal;overflow-wrap:anywhere;max-width:22rem">{{ props.row.reason }}</div>
'''

_ACTIONS = slot_more_menu('props.row.name', [
    SlotItem('check_circle', 'Enable', 'enable_app', v_if='props.row.disabled'),
    SlotItem('block', 'Disable…', 'disable_app', negative=True, v_if='!props.row.disabled'),
])


async def render_apps_section(user) -> None:
    section_intro(
        'Apps people registered to sign in with Wizzrobe. Disabling one stops it in every '
        'community at once; enabling it again brings its connections back.'
    )
    columns: list[dict] = [
        {'name': 'name', 'label': 'App', 'field': 'name', 'align': 'left', 'sortable': True},
        {'name': 'owner', 'label': 'Owner', 'field': 'owner', 'align': 'left', 'sortable': True},
        {'name': 'kind', 'label': 'Kind', 'field': 'kind', 'align': 'left'},
        {'name': 'people', 'label': 'People', 'field': 'people', 'align': 'right', 'sortable': True},
        {'name': 'communities', 'label': 'Communities', 'field': 'communities', 'align': 'right', 'sortable': True},
        {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left'},
        {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
    ]
    table = ui.table(columns=columns, rows=[], row_key='id').classes('w-full wiz-table')
    table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS}</q-td>')
    table.add_slot('body-cell-actions', f'<q-td :props="props" style="white-space:nowrap">{_ACTIONS}</q-td>')
    enable_mobile_grid(
        table, columns, actions=_ACTIONS, field_slots={'status': _STATUS},
        table_key=TableKeys.PLATFORM_APPS,
    )

    async def refresh() -> None:
        actor = await current_actor()
        apps = await _service.list_all_apps(actor)
        reach = await _service.platform_reach(actor)
        table.rows = [
            {
                'id': a.id,
                'name': a.name,
                'owner': a.owner.preferred_name,
                'kind': 'Website' if a.client_type == OAuthClientType.CONFIDENTIAL else 'Device app',
                'people': reach.get(a.id, (0, 0))[0],
                'communities': reach.get(a.id, (0, 0))[1],
                'status': 'Disabled' if a.disabled_at else 'Active',
                'disabled': a.disabled_at is not None,
                'reason': a.disabled_reason or '',
            }
            for a in apps
        ]
        table.update()

    async def enable(e) -> None:
        try:
            await _service.enable_app(await current_actor(), int(e.args['id']))
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        ui.notify(f"{e.args['name']} enabled", color='positive')
        await refresh()

    def disable(e) -> None:
        row = e.args

        async def submit() -> None:
            try:
                await _service.disable_app(await current_actor(), int(row['id']), reason.value or '')
            except (ValueError, PermissionError) as err:
                notify_error(err)
                return
            dialog.close()
            ui.notify(f"{row['name']} disabled everywhere", color='positive')
            await refresh()

        with form_dialog(f"Disable {row['name']}?", card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('w-full gap-2'):
                ui.label(
                    f"It stops working in every community at once for the {row['people']} people "
                    'who connected it. The owner sees the reason.'
                ).classes('text-caption text-muted')
                reason = ui.input('Reason', placeholder='e.g. Phishing').props(
                    'outlined stack-label maxlength=300'
                ).classes('input-full-width')
            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Disable', on_click=submit).props('color=negative')
        dialog.open()

    table.on('enable_app', enable)
    table.on('disable_app', disable)
    await refresh()
