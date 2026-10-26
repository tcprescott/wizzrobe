"""Volunteer-position role mappings: the second half of the Role Mappings tab."""

from nicegui import app, background_tasks, context, ui

from application.services import (
    VolunteerPositionService,
    VolunteerRoleMappingService,
    get_user_from_session,
)
from models import Role
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.pickers import FilterMultiSelect, bulk_add_summary
from theme.tables.admin_crud import refresh_button, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys, search_input

TAB_LABEL = 'Role Mappings'

_ROLE_OPTIONS = {
    r.value: r.label for r in Role.volunteer_mappable()
}

_ROW_ACTIONS = '''
    <q-btn flat round dense icon="delete" color="negative"
           @click="$parent.$emit('delete', props.row)" aria-label="Remove mapping">
        <q-tooltip>Remove mapping</q-tooltip>
    </q-btn>
'''


async def volunteer_role_mappings_section(can_manage: bool) -> None:
    service = VolunteerRoleMappingService()

    ui.label('Volunteer positions').classes('subsection-title')
    ui.label(
        "Anyone with a published assignment to a shift of the position holds the "
        "role, and keeps it while any such assignment exists, finished shifts "
        "included. Unpublished drafts don't count. Unassigning, releasing, or "
        "deleting the shift or position takes it back; manually-granted roles "
        "are never touched."
    ).classes('text-caption text-grey')
    ui.label(
        "Coordinators decide who's on a shift, so only operational roles can be "
        "mapped: never Staff or a role that runs a subsystem."
    ).classes('text-caption text-grey')

    columns: list[dict] = [
        {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
        {'name': 'position', 'label': 'Position', 'field': 'position', 'sortable': True},
        {'name': 'app_role', 'label': 'Grants', 'field': 'app_role', 'sortable': True},
        {'name': 'actions', 'label': '', 'field': 'actions'},
    ]
    container = ui.column().classes('w-full')
    mappings: list = []

    async def refresh_table() -> None:
        try:
            mappings[:] = await service.list_mappings()
        except ValueError as e:
            notify_error(e)
            return
        table.rows = [
            {
                'id': m.id,
                'position': m.position.name,
                'app_role': _ROLE_OPTIONS.get(m.app_role.value, m.app_role.value),
            }
            for m in mappings
        ]
        table.selected = []
        table.update()
        sync_remove_button()

    async def open_add_dialog() -> None:
        positions = await VolunteerPositionService().list_all()
        if not positions:
            ui.notify(
                'No volunteer positions yet. Add one under Vol. Schedule → Manage positions.',
                color='warning',
            )
            return
        position_options = {p.id: p.name for p in sorted(positions, key=lambda p: p.name.casefold())}
        existing = {(m.position.id, m.app_role.value) for m in mappings}
        granted: dict[int, list[str]] = {}
        for m in mappings:
            granted.setdefault(m.position.id, []).append(
                _ROLE_OPTIONS.get(m.app_role.value, m.app_role.value)
            )
        position_extras = {
            pid: {'caption': 'Grants ' + ', '.join(roles)} for pid, roles in granted.items()
        }

        def planned() -> tuple[int, int]:
            new = skipped = 0
            for pid in position_select.value or []:
                for role in role_select.value or []:
                    if (pid, role) in existing:
                        skipped += 1
                    else:
                        new += 1
            return new, skipped

        def update_summary(_=None) -> None:
            new, skipped = planned()
            summary.set_text(bulk_add_summary(
                new, skipped, empty_hint='Pick one or more positions and the roles they grant.',
            ))
            add_button.set_enabled(bool(new))

        with container, form_dialog('Add volunteer role mappings', card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md gap-2 full-width'):
                ui.label('Every position you pick grants every role you pick.') \
                    .classes('text-caption text-grey')
                position_select = FilterMultiSelect(
                    position_options, label='Positions', extras=position_extras,
                ).classes('w-full')
                role_select = FilterMultiSelect(_ROLE_OPTIONS, label='Grants').classes('w-full')
                summary = ui.label().classes('text-caption')

            async def submit() -> None:
                if not planned()[0]:
                    ui.notify('Nothing new to add.', color='warning')
                    return
                try:
                    actor = await get_user_from_session(app.storage.user)
                    created, skipped = await service.add_mappings(
                        actor,
                        [int(pid) for pid in position_select.value],
                        [Role(v) for v in role_select.value],
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                dialog.close()
                message = f"Added {created} mapping{'' if created == 1 else 's'}"
                if skipped:
                    message += f' ({skipped} already existed)'
                ui.notify(message, color='positive')
                await refresh_table()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                add_button = ui.button('Add', icon='add', on_click=submit).props('color=primary')

        position_select.on_value_change(update_summary)
        role_select.on_value_change(update_summary)
        update_summary()
        dialog.open()

    async def delete_mapping(row: dict, client) -> None:
        async def do_remove() -> None:
            try:
                actor = await get_user_from_session(app.storage.user)
                await service.remove_mapping(actor, row['id'])
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Mapping removed', color='positive')
            await refresh_table()

        with client:
            ConfirmationDialog(
                f"Stop giving {row['position']} volunteers the {row['app_role']} role? "
                'Anyone who only held it through this mapping loses it now.',
                on_confirm=do_remove, confirm_text='Remove', title='Remove mapping?',
            ).open()

    async def remove_selected() -> None:
        try:
            actor = await get_user_from_session(app.storage.user)
            removed = await service.remove_mappings(actor, [row['id'] for row in table.selected])
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(f"Removed {removed} mapping{'' if removed == 1 else 's'}", color='positive')
        await refresh_table()

    def confirm_remove_selected() -> None:
        count = len(table.selected)
        if not count:
            return
        ConfirmationDialog(
            f"Remove {count} mapping{'' if count == 1 else 's'}? Anyone who only held a "
            'role through them loses it now.',
            on_confirm=remove_selected, confirm_text='Remove', title='Remove mappings',
        ).open()

    remove_button = None

    def sync_remove_button(_=None) -> None:
        if remove_button is None:
            return
        count = len(table.selected)
        remove_button.set_visibility(bool(count))
        remove_button.set_text(f'Remove {count} selected')

    with container:
        with ui.row().classes('full-width items-center'):
            if can_manage:
                ui.button('Add Mappings', icon='add', on_click=open_add_dialog).props('color=primary').classes('wiz-write')
                remove_button = ui.button(
                    icon='delete', on_click=confirm_remove_selected,
                ).props('outline color=negative')
            ui.space()
            refresh_button(refresh_table)
        search_row = ui.row().classes('full-width')

        table = ui.table(
            columns=columns, rows=[], row_key='id',
            selection='multiple' if can_manage else None,
            on_select=sync_remove_button,
        ).classes('w-full wiz-table')
        with search_row:
            search_input(table, placeholder='Search mappings…')
        if can_manage:
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
        table.on('delete', lambda e: background_tasks.create(delete_mapping(e.args, context.client)))
        enable_mobile_grid(
            table, columns, actions=_ROW_ACTIONS if can_manage else '',
            table_key=TableKeys.ADMIN_VOLUNTEER_ROLE_MAPPINGS, selectable=can_manage,
        )
        sync_remove_button()

    wire_tab_refresh(TAB_LABEL, refresh_table)
    background_tasks.create(refresh_table())
