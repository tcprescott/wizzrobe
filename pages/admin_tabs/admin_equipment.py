"""Admin Equipment Management Page (Equipment Manager / Staff)."""

from nicegui import app, background_tasks, context, ui

from application.services import EquipmentService, TenantService, get_user_from_session
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog import ConfirmationDialog, EquipmentDialog, QrLabelDialog, open_checkout, quick_checkin
from theme.help import help_icon
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import capture_render_context, scoped_background
from theme.tables.preferences import (
    TableKeys,
    customize_table,
    preferences_button,
    search_input,
)

_STATUS_LABELS = {
    'available': 'Available',
    'checked_out': 'Checked out',
    'retired': 'Retired',
}

_COLUMNS: list[dict] = [
    {'name': 'asset_number', 'label': '#', 'field': 'asset_number', 'align': 'left', 'sortable': True},
    {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left', 'sortable': True},
    {'name': 'owner', 'label': 'Owner', 'field': 'owner', 'align': 'left'},
    {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left', 'sortable': True},
    {'name': 'holder', 'label': 'Checked out to', 'field': 'holder', 'align': 'left'},
    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
]

_STATUS_CELL = '''<q-td :props="props">
    <q-badge :color="props.row.status_value === 'available' ? 'positive'
                     : props.row.status_value === 'checked_out' ? 'warning' : 'grey'">
        {{ props.value }}
    </q-badge>
</q-td>'''

_STATUS_BADGE = '''<q-badge :color="props.row.status_value === 'available' ? 'positive'
                                 : props.row.status_value === 'checked_out' ? 'warning' : 'grey'">
                    {{ props.row.status }}
                </q-badge>'''

# Every emitting control needs the offline guard's class; a test counts them against the emits.
_ROW_ACTIONS = f'''<q-btn v-if="props.row.status_value === 'available'" outline dense no-caps
           icon="logout" color="primary" label="Check out"
           class="equipment-state-btn {REQUIRES_SOCKET_CLASS}"
           @click="$parent.$emit('checkout', props.row)" />
    <q-btn v-if="props.row.status_value === 'checked_out'" unelevated dense no-caps
           icon="login" color="secondary" label="Check in"
           class="equipment-state-btn {REQUIRES_SOCKET_CLASS}"
           @click="$parent.$emit('checkin', props.row)" />
    <q-btn flat round icon="more_vert" color="grey-7" class="equipment-more-btn"
           :aria-label="'More for ' + props.row.name">
        <q-menu auto-close anchor="bottom right" self="top right">
            <q-list style="min-width: 200px">
                <q-item clickable class="{REQUIRES_SOCKET_CLASS}"
                        @click="$parent.$emit('view', props.row)">
                    <q-item-section avatar><q-icon name="qr_code_2"/></q-item-section>
                    <q-item-section>Open asset page</q-item-section>
                </q-item>
                <q-item clickable class="{REQUIRES_SOCKET_CLASS}"
                        @click="$parent.$emit('edit', props.row)">
                    <q-item-section avatar><q-icon name="edit"/></q-item-section>
                    <q-item-section>Edit</q-item-section>
                </q-item>
                <q-separator />
                <q-item clickable class="text-negative {REQUIRES_SOCKET_CLASS}"
                        @click="$parent.$emit('remove', props.row)">
                    <q-item-section avatar><q-icon name="delete" color="negative"/></q-item-section>
                    <q-item-section>Delete</q-item-section>
                </q-item>
            </q-list>
        </q-menu>
    </q-btn>'''

_ACTIONS_CELL = f'''<q-td :props="props">
    <div class="row no-wrap items-center justify-end q-gutter-x-xs">
    {_ROW_ACTIONS}
    </div>
</q-td>'''

_GRID_CARD = f'''<div class="q-mb-sm equipment-grid-card" style="width: 100%; box-sizing: border-box;">
    <div class="equipment-grid-card__head">
        <div style="min-width: 0">
            <div class="equipment-grid-card__name">{{{{ props.row.name }}}}</div>
            <div class="equipment-grid-card__meta">
                #{{{{ props.row.asset_number }}}} · {{{{ props.row.owner }}}}<template
                    v-if="props.row.holder_name"> · with <strong>{{{{ props.row.holder_name }}}}</strong></template>
            </div>
        </div>
        {_STATUS_BADGE}
    </div>
    <div class="equipment-grid-card__actions">
    {_ROW_ACTIONS}
    </div>
</div>'''


def _menu_action(icon: str, label: str, on_click) -> None:
    with ui.menu_item(on_click=on_click):
        with ui.item_section().props('avatar'):
            ui.icon(icon)
        ui.item_section(label)


async def admin_equipment_page() -> None:
    service = EquipmentService()

    with ui.column().classes('page-container-narrow w-full'):
        with page_header('Equipment'):
            await help_icon('equipment-desk')

        with ui.row().classes('full-width items-center equipment-toolbar'):
            async def add_asset():
                actor = await get_user_from_session(app.storage.user)
                await EquipmentDialog(actor, on_saved=_render_table.refresh).open()

            async def print_labels():
                actor = await get_user_from_session(app.storage.user)
                await QrLabelDialog(actor).open()

            refresh_context = capture_render_context()

            async def reload():
                await _render_table.refresh()

            ui.button('Add asset', icon='add', on_click=add_asset).props(
                'color=primary').classes(REQUIRES_SOCKET_CLASS)
            with ui.button('Actions').props('flat color=primary icon-right=expand_more') \
                    .classes(REQUIRES_SOCKET_CLASS):
                with ui.menu():
                    _menu_action('qr_code_2', 'Print QR labels', print_labels)
                    # scoped_background, not a bare task: a click's task has no tenant and fails silently.
                    _menu_action('refresh', 'Refresh list', lambda: scoped_background(
                        refresh_context, reload()))
            ui.space()
            gear_slot = ui.row().classes('items-center no-wrap equipment-search')

        @steady_refreshable
        async def _render_table() -> None:
            assets = await service.list_assets()
            open_loans = await service.open_loans_by_equipment_id()
            community = await TenantService.current_community_name()
            rows = []
            for a in assets:
                holder = open_loans[a.id].borrower.preferred_name if a.id in open_loans else None
                rows.append({
                    'id': a.id,
                    'asset_number': a.asset_number,
                    'name': a.name,
                    'owner': a.owner_label(community),
                    'status': _STATUS_LABELS.get(a.status.value, a.status.value),
                    'status_value': a.status.value,
                    'holder': holder or '-',
                    'holder_name': holder,
                })

            table = ui.table(columns=_COLUMNS, rows=rows, row_key='id').classes(
                'equipment-table equipment-table-container w-full'
            ).props(':grid="Quasar.Screen.lt.md"')
            table.add_slot('body-cell-status', _STATUS_CELL)
            table.add_slot('body-cell-actions', _ACTIONS_CELL)
            table.add_slot('item', _GRID_CARD)
            # After the card slot, never before: the card must not follow a saved layout.
            customize_table(table, _COLUMNS, key=TableKeys.ADMIN_EQUIPMENT)
            # Clear first: this block re-runs on every refresh and the gear would stack up.
            gear_slot.clear()
            with gear_slot:
                search_input(table, placeholder='Search equipment…')
                preferences_button(table)

            def handle_view(event):
                ui.navigate.to(f"/equipment/{event.args['id']}")

            async def handle_checkout(row, client):
                with client:
                    actor = await get_user_from_session(app.storage.user)
                    await open_checkout(actor, row['id'], can_manage=True, on_done=_render_table.refresh)

            async def handle_checkin(row, client):
                with client:
                    actor = await get_user_from_session(app.storage.user)
                    await quick_checkin(actor, row['id'], on_done=_render_table.refresh)

            async def handle_edit(row, client):
                with client:
                    actor = await get_user_from_session(app.storage.user)
                    asset = await service.get_asset(row['id'])
                    if asset is None:
                        ui.notify("Couldn't find that asset. Try refreshing.", color='warning')
                        return
                    await EquipmentDialog(actor, equipment=asset, on_saved=_render_table.refresh).open()

            async def handle_remove(row, client):
                with client:
                    actor = await get_user_from_session(app.storage.user)

                    async def do_delete():
                        try:
                            await service.delete_asset(actor, row['id'])
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        ui.notify('Asset deleted.', color='positive')
                        await _render_table.refresh()

                    confirm = ConfirmationDialog(
                        message=f"Delete asset #{row['asset_number']} ({row['name']})?",
                        on_confirm=do_delete,
                        confirm_text='Delete',
                    )
                    confirm.open()

            table.on('view', handle_view)
            table.on('checkout', lambda e: background_tasks.create(handle_checkout(e.args, context.client)))
            table.on('checkin', lambda e: background_tasks.create(handle_checkin(e.args, context.client)))
            table.on('edit', lambda e: background_tasks.create(handle_edit(e.args, context.client)))
            table.on('remove', lambda e: background_tasks.create(handle_remove(e.args, context.client)))

        await _render_table()
