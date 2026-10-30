"""Feature tiers on ``/platform``: the groups, and one tenant's overrides.

Split out of ``pages/platform.py`` the way ``platform_tenant_admins`` was: the
page stacks four independent sections and kept running at the file-length
guideline, so each section that can stand alone does.

Both halves of the two-tier model live here — the named groups a super-admin
assigns as a tier, and the per-tenant Inherit / Force-on / Force-off dialog the
tenants table opens, which is the exception to whatever the tier says. They are
one subject read from two ends, so a change to how availability resolves lands
in one file.
"""

from nicegui import ui

from application.feature_flags import all_specs, spec_for
from application.services import FeatureFlagService, TenantService
from models import FeatureFlag
from pages.platform_shared import current_actor, section_intro
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import SlotItem, slot_more_menu, slot_name_link
from theme.notify import notify_error
from theme.pickers import CHIP_PROPS
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys, preferences_button

_FLAG_CHIPS = '''
    <div style="display:flex;flex-wrap:wrap;gap:4px;white-space:normal">
        <span v-for="f in props.row.flags" :key="f" class="wiz-chip wiz-chip--neutral">{{ f }}</span>
        <span v-if="!props.row.flags.length">—</span>
    </div>
'''

_DEFAULT_CHIP = '''
    <span v-if="props.row.default" class="wiz-chip wiz-chip--ok">Default</span>
'''

_NAME = slot_name_link('name', 'edit_group')
_ROW_ACTIONS = slot_more_menu('props.row.name', [
    SlotItem('edit', 'Edit group', 'edit_group'),
    SlotItem('delete', 'Delete group', 'delete_group', negative=True),
])


async def render_feature_groups_section(user) -> None:
    """Build the groups table, wire its row actions, and load it."""
    actions = section_intro(
        'Named feature bundles (tiers). Assign a tenant to a group from its '
        'Features button; ungrouped tenants fall back to the default group. '
        'Editing a group updates every tenant on it, live.'
    )
    with actions:
        group_header_row = ui.row().classes('items-center')
        ui.button('New group', icon='add', on_click=lambda: _open_group_create_dialog(user, group_table)).props('color=primary')

    group_columns: list[dict] = [
        {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left', 'sortable': True},
        {'name': 'flags', 'label': 'Features', 'field': 'flags', 'align': 'left'},
        {'name': 'default', 'label': 'Default', 'field': 'default', 'align': 'left'},
        {'name': 'tenants', 'label': 'Tenants', 'field': 'tenants', 'align': 'left'},
        {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
    ]
    group_table = ui.table(columns=group_columns, rows=[], row_key='id').classes('w-full wiz-table')
    group_table.add_slot('body-cell-name', f'<q-td :props="props">{_NAME}</q-td>')
    group_table.add_slot('body-cell-flags', f'<q-td :props="props">{_FLAG_CHIPS}</q-td>')
    group_table.add_slot('body-cell-default', f'<q-td :props="props">{_DEFAULT_CHIP}</q-td>')
    group_table.add_slot('body-cell-actions',
                         f'<q-td :props="props" style="white-space:nowrap">{_ROW_ACTIONS}</q-td>')
    enable_mobile_grid(
        group_table, group_columns, actions=_ROW_ACTIONS,
        table_key=TableKeys.PLATFORM_FEATURE_GROUPS,
        field_slots={'name': _NAME, 'flags': _FLAG_CHIPS, 'default': _DEFAULT_CHIP},
    )
    with group_header_row:
        preferences_button(group_table)

    async def _on_edit_group(e) -> None:
        await _open_group_edit_dialog(user, group_table, e.args)

    async def _on_delete_group(e) -> None:
        await _delete_group(user, group_table, e.args)

    group_table.on('edit_group', _on_edit_group)
    group_table.on('delete_group', _on_delete_group)

    await _refresh_groups(group_table)


async def open_tenant_features_dialog(actor, row) -> None:
    """Super-admin: set a tenant's tier (group) and per-feature overrides.

    Availability normally derives from the assigned group (or the default group
    when ungrouped); a per-feature Inherit / Force-on / Force-off override is the
    exception. Effective state is shown per row. Reopen after assigning a group to
    see refreshed effective values.
    """
    tenant_id = row['id']
    service = FeatureFlagService()
    flags = await service.list_for_tenant(actor, tenant_id)
    groups = await service.list_groups(actor)
    tenant = await TenantService.get_by_id(tenant_id)
    current_group_id = tenant.feature_group_id if tenant is not None else None

    async def _assign(group_id) -> None:
        try:
            await service.assign_tenant_group(actor, tenant_id, group_id or None)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Tier assigned — reopen to see updated availability', color='positive')

    async def _override(flag_value: str, choice: str) -> None:
        mapped = {'inherit': None, 'on': True, 'off': False}[choice]
        try:
            await service.set_availability(actor, tenant_id, FeatureFlag(flag_value), mapped)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Updated', color='positive')

    with form_dialog(f"Features for {row['name']}", card_classes='dialog-card gap-2') as dialog:

        group_options = {0: '— None (default fallback) —'}
        for g in groups:
            group_options[g.id] = g.name + (' (default)' if g.is_default else '')
        ui.select(
            options=group_options, value=current_group_id or 0, label='Tier / group',
            on_change=lambda e: _assign(e.value),
        ).props('outlined dense').classes('w-full')

        ui.separator()
        ui.label('Per-feature overrides (exceptions to the tier)').classes('text-caption text-grey')
        for f in flags:
            tier = 'on' if f['group_available'] else 'off'
            effective = 'live' if f['live'] else ('available' if f['available'] else 'off')
            current = 'inherit' if f['override'] is None else ('on' if f['override'] else 'off')
            with ui.row().classes('items-center justify-between w-full no-wrap'):
                with ui.column().classes('gap-0'):
                    ui.label(f['label'])
                    ui.label(f"tier: {tier} · effective: {effective}").classes('text-caption text-grey')
                ui.select(
                    options={'inherit': f'Inherit ({tier})', 'on': 'Force on', 'off': 'Force off'},
                    value=current,
                    on_change=lambda e, fv=f['flag']: _override(fv, e.value),
                ).props('dense outlined').classes('w-44 shrink-0')
    dialog.open()


async def _refresh_groups(table) -> None:
    groups = await FeatureFlagService().list_groups_with_counts(await current_actor())
    rows = []
    for g in groups:
        labels = [spec_for(FeatureFlag(k)).label for k in g['flags']]
        rows.append({
            'id': g['id'], 'name': g['name'],
            'flags': labels,
            'default': 'yes' if g['is_default'] else '',
            'tenants': str(g['tenant_count']),
        })
    table.rows = rows
    table.update()


def _group_form(existing=None):
    """Render the shared group input widgets; returns them for the submit handler."""
    is_edit = existing is not None
    name = ui.input('Name', value=existing['name'] if is_edit else '').classes('w-full')
    description = ui.textarea(
        'Description', value=existing.get('description', '') if is_edit else '',
    ).classes('w-full')
    flag_options = {spec.flag.value: spec.label for spec in all_specs()}
    flags = ui.select(
        options=flag_options, multiple=True, label='Features in this group',
        value=list(existing['flags']) if is_edit else [],
    ).props(CHIP_PROPS).classes('w-full')
    is_default = ui.switch('Default group (fallback for ungrouped tenants)',
                           value=existing['is_default'] if is_edit else False)
    return name, description, flags, is_default


def _open_group_create_dialog(actor, table) -> None:
    with form_dialog('New feature group', card_classes='dialog-card dialog-card--sm gap-2') as dialog:
        name, description, flags, is_default = _group_form()

        async def submit():
            try:
                await FeatureFlagService().create_group(
                    actor, name=name.value, flags=flags.value or [],
                    description=description.value, is_default=is_default.value,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Group created', color='positive')
            dialog.close()
            await _refresh_groups(table)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Create', on_click=submit, color='primary')
    dialog.open()


async def _open_group_edit_dialog(actor, table, row) -> None:
    group = await FeatureFlagService().get_group(actor, row['id'])
    existing = {
        'name': group.name, 'description': group.description or '',
        'flags': list(group.flags or []), 'is_default': group.is_default,
    }
    with form_dialog(f"Edit group '{group.name}'", card_classes='dialog-card dialog-card--sm gap-2') as dialog:
        name, description, flags, is_default = _group_form(existing)

        async def submit():
            try:
                await FeatureFlagService().update_group(
                    actor, group.id, name=name.value, flags=flags.value or [],
                    description=description.value, is_default=is_default.value,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Group updated', color='positive')
            dialog.close()
            await _refresh_groups(table)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Save', on_click=submit, color='primary')
    dialog.open()


async def _delete_group(actor, table, row) -> None:
    async def do_delete() -> None:
        try:
            await FeatureFlagService().delete_group(actor, row['id'])
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Group deleted; its tenants fell back to the default', color='positive')
        await _refresh_groups(table)

    ConfirmationDialog(
        message=f"Delete the {row['name']} group? Its {row['tenants']} tenant(s) fall back "
                'to the default group straight away, which can switch features off for them.',
        on_confirm=do_delete, confirm_text='Delete', title='Delete feature group?',
    ).open()
