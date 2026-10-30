"""The Racetime bots tab of ``/platform``.

Split out of ``pages/platform.py`` the way ``platform_tenant_admins`` was: the
page stacks four independent sections and kept running at the file-length
guideline, so each section that can stand alone does.

Bots are platform-managed and shared: one per racetime category, granted to
tenants whose sync admins then select them on a tournament. Client secrets are
write-only — the form takes one, nothing ever renders it back.
"""

from nicegui import ui

from application.services import RacetimeBotService, TenantService
from pages.platform_shared import ACTIVE_ICON_SLOT, current_actor, section_intro
from theme.dialog._helpers import dialog_actions, dialog_more_menu, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import SlotItem, menu_action, slot_more_menu, slot_name_link, slot_row_button
from theme.notify import notify_error
from theme.tables.preferences import TableKeys, customize_table, preferences_button

_bot_service = RacetimeBotService()

# Rows must carry `status`, `status_kind` and `status_message` (may be empty).
_BOT_STATUS_CHIP = '''
    <span class="wiz-chip" :class="{
        'wiz-chip--ok': props.row.status_kind === 'connected',
        'wiz-chip--cancelled': props.row.status_kind === 'error' || props.row.status_kind === 'disconnected',
        'wiz-chip--neutral': props.row.status_kind === 'unknown'
    }">{{ props.row.status }}</span>
    <div v-if="props.row.status_message" class="text-caption"
         style="white-space:normal;overflow-wrap:anywhere;max-width:22rem">{{ props.row.status_message }}</div>
'''

_NAME = slot_name_link('category', 'edit_bot')
_ROW_ACTIONS = slot_row_button('Tenants', 'groups', 'grant_bot') + slot_more_menu('props.row.category', [
    SlotItem('edit', 'Edit bot', 'edit_bot'),
    SlotItem('restart_alt', 'Restart connection', 'restart_bot', negative=True),
])


async def render_bots_section(user) -> None:
    """Build the bots table, wire its row actions, and load it."""
    actions = section_intro(
        'Shared, platform-managed bots, one per racetime category. Grant a bot to '
        'a tenant to let its sync admins select it on a tournament. Client secrets '
        'are write-only and never shown.'
    )
    with actions:
        bot_header_row = ui.row().classes('items-center')
        ui.button('New bot', icon='add', on_click=lambda: _open_bot_create_dialog(user, bot_table)).props('color=primary')

    bot_columns: list[dict] = [
        {'name': 'id', 'label': 'ID', 'field': 'id', 'align': 'left'},
        {'name': 'category', 'label': 'Category', 'field': 'category', 'align': 'left', 'sortable': True},
        {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left'},
        {'name': 'client_id', 'label': 'Client ID', 'field': 'client_id', 'align': 'left'},
        {'name': 'active', 'label': 'Active', 'field': 'active', 'align': 'left'},
        {'name': 'status', 'label': 'Health', 'field': 'status', 'align': 'left'},
        {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
    ]
    bot_table = ui.table(columns=bot_columns, rows=[], row_key='id').classes(
        'w-full wiz-table').props(':grid="Quasar.Screen.lt.md"')
    bot_table.add_slot('body-cell-category', f'<q-td :props="props">{_NAME}</q-td>')
    bot_table.add_slot('body-cell-active', ACTIVE_ICON_SLOT)
    bot_table.add_slot('body-cell-status', f'<q-td :props="props">{_BOT_STATUS_CHIP}</q-td>')
    bot_table.add_slot('body-cell-actions',
                       f'<q-td :props="props" style="white-space:nowrap">{_ROW_ACTIONS}</q-td>')
    bot_table.add_slot('item', '''
        <div class="q-pa-xs col-xs-12 col-sm-6">
            <q-card bordered flat class="q-pa-sm wiz-grid-card">
                <div class="row items-center justify-between no-wrap q-mb-xs">
                    <div class="text-weight-bold">''' + _NAME + '''</div>
                    <q-icon :name="props.row.active_bool ? 'check_circle' : 'cancel'"
                            :color="props.row.active_bool ? 'positive' : 'negative'" size="sm">
                        <q-tooltip>{{ props.row.active_bool ? 'Active' : 'Inactive' }}</q-tooltip>
                    </q-icon>
                </div>
                <div class="text-caption text-grey-7 q-mb-xs">#{{ props.row.id }} · {{ props.row.name }}</div>
                <div class="row q-mb-xs">
                    <div class="col-4 text-grey-7 text-caption">Client ID</div>
                    <div class="col-8" style="overflow-wrap:anywhere">{{ props.row.client_id }}</div>
                </div>
                <div class="row items-start q-mb-xs">
                    <div class="col-4 text-grey-7 text-caption">Health</div>
                    <div class="col-8">''' + _BOT_STATUS_CHIP + '''</div>
                </div>
                <div class="row justify-end items-center q-gutter-x-xs q-mt-xs">''' + _ROW_ACTIONS + '''</div>
            </q-card>
        </div>
    ''')

    customize_table(bot_table, bot_columns, key=TableKeys.PLATFORM_RACETIME_BOTS)
    with bot_header_row:
        preferences_button(bot_table)

    async def _on_edit_bot(e) -> None:
        await _open_bot_edit_dialog(user, bot_table, e.args)

    async def _on_grant_bot(e) -> None:
        await _open_bot_tenants_dialog(user, e.args)

    async def _on_restart_bot(e) -> None:
        await _restart_bot(user, bot_table, e.args)

    bot_table.on('edit_bot', _on_edit_bot)
    bot_table.on('grant_bot', _on_grant_bot)
    bot_table.on('restart_bot', _on_restart_bot)

    await _refresh_bots(bot_table)


async def _refresh_bots(table) -> None:
    bots = await _bot_service.list_bots(await current_actor())
    table.rows = [
        {
            'id': b.id, 'category': b.category, 'name': b.name,
            'client_id': b.client_id,
            'active': 'yes' if b.is_active else 'no',
            'active_bool': b.is_active,
            'status': b.status.value.title(),
            'status_message': b.status_message or '',
            'status_kind': b.status.value,
        }
        for b in bots
    ]
    table.update()


async def _restart_bot(actor, table, row) -> None:
    from racetimebot import get_racetime_manager

    async def do_restart() -> None:
        try:
            await get_racetime_manager().restart(actor, row['id'])
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Bot connection restarting', color='positive')
        await _refresh_bots(table)

    ConfirmationDialog(
        message=f"Restart the {row['category']} bot? Its racetime connection drops and "
                'reconnects for every community using it.',
        on_confirm=do_restart, confirm_text='Restart', title='Restart bot?', tone='warning',
        busy_text='Restarting…',
    ).open()



def _bot_form(existing=None):
    """Render the shared bot input widgets; returns them for the submit handler."""
    is_edit = existing is not None
    category = ui.input('Category', value=existing['category'] if is_edit else '').classes('w-full')
    name = ui.input('Name', value=existing['name'] if is_edit else '').classes('w-full')
    client_id = ui.input('Client ID', value=existing['client_id'] if is_edit else '').classes('w-full')
    secret = ui.input('Client Secret').props('type=password').classes('w-full')
    if is_edit:
        secret.props('hint="Leave blank to keep the current secret"')
    description = ui.textarea('Description', value=existing.get('description', '') if is_edit else '').classes('w-full')
    handler = ui.input('Handler class', value=existing.get('handler_class', '') if is_edit else '').classes('w-full')
    active = ui.switch('Active', value=existing['active'] == 'yes' if is_edit else True)
    return category, name, client_id, secret, description, handler, active


def _open_bot_create_dialog(actor, table) -> None:
    with form_dialog('New racetime bot', card_classes='dialog-card gap-2') as dialog:
        category, name, client_id, secret, description, handler, active = _bot_form()

        async def submit():
            try:
                await _bot_service.create_bot(
                    actor, category=category.value, name=name.value,
                    client_id=client_id.value, client_secret=secret.value,
                    description=description.value, handler_class=handler.value,
                    is_active=active.value,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Bot created', color='positive')
            dialog.close()
            await _refresh_bots(table)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Create', on_click=submit, color='primary')
    dialog.open()


async def _open_bot_edit_dialog(actor, table, row) -> None:
    bot = await _bot_service.get_bot(actor, row['id'])
    existing = _bot_service.serialize(bot)
    with form_dialog(f"Edit bot #{bot.id}", card_classes='dialog-card gap-2') as dialog:
        existing['active'] = 'yes' if bot.is_active else 'no'
        category, name, client_id, secret, description, handler, active = _bot_form(existing)

        async def submit():
            try:
                await _bot_service.update_bot(
                    actor, bot.id, category=category.value, name=name.value,
                    client_id=client_id.value, client_secret=secret.value,
                    description=description.value, handler_class=handler.value,
                    is_active=active.value,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Bot updated', color='positive')
            dialog.close()
            await _refresh_bots(table)

        async def do_delete_bot():
            try:
                await _bot_service.delete_bot(actor, bot.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Bot deleted', color='positive')
            dialog.close()
            await _refresh_bots(table)

        def delete_bot():
            ConfirmationDialog(
                message=f'Delete the {bot.category} bot? Every community it’s granted to '
                        'loses it.',
                on_confirm=do_delete_bot, confirm_text='Delete', title='Delete bot?',
            ).open()

        with dialog_actions():
            with dialog_more_menu():
                menu_action('delete', 'Delete bot…', delete_bot, negative=True)
            ui.space()
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Save', on_click=submit, color='primary')
    dialog.open()


async def _open_bot_tenants_dialog(actor, row) -> None:
    bot_id = row['id']
    tenants = await TenantService.list_tenants()
    grants = {g.tenant_id: g for g in await _bot_service.list_grants(actor, bot_id)}
    with form_dialog(f"Tenants for {row['category']}", card_classes='dialog-card dialog-card--sm gap-2') as dialog:
        ui.label('Toggle which tenants may select this bot.').classes('text-caption text-grey')

        async def _toggle(tenant_id: int, enabled: bool):
            try:
                if enabled:
                    await _bot_service.grant_tenant(actor, bot_id, tenant_id)
                else:
                    await _bot_service.revoke_tenant(actor, bot_id, tenant_id)  # style-guide: exempt — a grant switch, flipped back to undo
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Updated', color='positive')

        for t in tenants:
            grant = grants.get(t.id)
            ui.switch(
                t.name, value=bool(grant and grant.is_active),
                on_change=lambda e, tid=t.id: _toggle(tid, e.value),
            )

    dialog.open()
