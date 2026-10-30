"""Admin Racetime Page — reusable race-room profiles (SYNC_ADMIN/STAFF).

Tenant-scoped CRUD for :class:`~models.RaceRoomProfile`: the reusable racetime
``startrace`` settings a tournament points at. The bots themselves (and their
per-tenant authorization) are platform-level and managed on ``/platform``.
"""

from nicegui import app, background_tasks, context, ui

from application.services import RaceRoomProfileService, get_user_from_session
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import SlotItem, slot_more_menu, slot_name_link
from theme.notify import notify_error
from theme.page_header import page_header
from theme.tables.admin_crud import refresh_button, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

_ROW_ACTIONS = slot_more_menu('props.row.name', [
    SlotItem('edit', 'Edit profile', 'edit'),
    SlotItem('delete', 'Delete profile', 'delete', negative=True),
])
_NAME = slot_name_link('name', 'edit')
_AUTO_START_ICON = '''
    <q-icon :name="props.row.auto_start_bool ? 'check_circle' : 'cancel'"
            :color="props.row.auto_start_bool ? 'positive' : 'negative'" size="sm" />
'''

_BOOL_FIELDS = (
    ('invitational', 'Invitational'),
    ('unlisted', 'Unlisted'),
    ('auto_start', 'Auto-start'),
    ('allow_comments', 'Allow comments'),
    ('allow_midrace_chat', 'Allow mid-race chat'),
    ('allow_non_entrant_chat', 'Allow non-entrant chat'),
    ('streaming_required', 'Streaming required'),
)
_INT_FIELDS = (
    ('chat_message_delay', 'Chat message delay (s)', 0),
    ('start_delay', 'Start delay (s)', 15),
    ('time_limit', 'Time limit (h)', 24),
)


async def admin_racetime_page() -> None:
    service = RaceRoomProfileService()

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Race Room Profiles',
            'Reusable racetime.gg room settings a tournament can point at — goal, '
            'chat and streaming rules, and timers. A tournament selects one under '
            'its Racetime section; the room-creation flow applies these when it '
            'opens a race room.',
        )

        columns = [
            {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
            {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True},
            {'name': 'goal', 'label': 'Goal', 'field': 'goal'},
            {'name': 'auto_start', 'label': 'Auto-start', 'field': 'auto_start'},
            {'name': 'actions', 'label': '', 'field': 'actions'},
        ]

        table_container = ui.column().classes('w-full')

        async def _current():
            return await get_user_from_session(app.storage.user)

        async def refresh_table():
            profiles = await service.list_profiles(await _current())
            table.rows = [
                {
                    'id': p.id,
                    'name': p.name,
                    'goal': p.goal or '—',
                    'auto_start': 'yes' if p.auto_start else 'no',
                    'auto_start_bool': p.auto_start,
                }
                for p in profiles
            ]
            table.update()

        async def delete_profile(row, client):
            async def do_delete() -> None:
                try:
                    await service.delete_profile(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Profile deleted', color='positive')
                await refresh_table()

            with client:
                ConfirmationDialog(
                    message=f"Delete the {row['name']} race room profile?",
                    on_confirm=do_delete, confirm_text='Delete', title='Delete profile?',
                ).open()

        async def edit_profile(row, client):
            with client:
                try:
                    profile = await service.get_profile(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                open_profile_dialog(profile)

        def open_profile_dialog(p=None) -> None:
            is_edit = p is not None
            with table_container:
                with form_dialog('Edit Race Room Profile' if is_edit else 'Add Race Room Profile', card_classes='dialog-card dialog-card--sm') as dialog:
                    name_input = ui.input('Name', value=p.name if p else '').classes('w-full')
                    goal_input = ui.input('Goal', value=(p.goal or '') if p else '').classes('w-full')
                    switches = {}
                    with ui.row().classes('gap-4 flex-wrap'):
                        for attr, label in _BOOL_FIELDS:
                            switches[attr] = ui.switch(
                                label, value=getattr(p, attr) if p else _default_bool(attr),
                            )
                    numbers = {}
                    with ui.row().classes('form-pair gap-2'):
                        for attr, label, default in _INT_FIELDS:
                            numbers[attr] = ui.number(
                                label, value=getattr(p, attr) if p else default, min=0,
                            ).props('inputmode=numeric')

                    async def submit():
                        payload = {attr: sw.value for attr, sw in switches.items()}
                        payload.update({attr: int(n.value or 0) for attr, n in numbers.items()})
                        payload['goal'] = goal_input.value
                        try:
                            current = await _current()
                            if is_edit:
                                await service.update_profile(current, p.id, name=name_input.value, **payload)
                                ui.notify('Profile updated', color='positive')
                            else:
                                await service.create_profile(current, name=name_input.value, **payload)
                                ui.notify('Profile created', color='positive')
                            dialog.close()
                            await refresh_table()
                        except (ValueError, PermissionError) as e:
                            notify_error(e)

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button(
                            'Save' if is_edit else 'Add', icon='save' if is_edit else 'add',
                            on_click=submit,
                        ).props('color=primary')
            dialog.open()

        with table_container:
            with ui.row().classes('full-width'):
                ui.button('Add Profile', icon='add', on_click=lambda: open_profile_dialog()).props('color=primary')
                ui.space()
                refresh_button(refresh_table)

            table = ui.table(columns=columns, rows=[], row_key='id').classes('w-full wiz-table')

            table.add_slot('body-cell-name', f'<q-td :props="props">{_NAME}</q-td>')
            table.add_slot('body-cell-auto_start', f'<q-td :props="props">{_AUTO_START_ICON}</q-td>')
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
            enable_mobile_grid(table, columns, actions=_ROW_ACTIONS,
                               table_key=TableKeys.ADMIN_RACETIME,
                               field_slots={'name': _NAME, 'auto_start': _AUTO_START_ICON})

            table.on('edit', lambda e: background_tasks.create(edit_profile(e.args, context.client)))
            table.on('delete', lambda e: background_tasks.create(delete_profile(e.args, context.client)))

        wire_tab_refresh('Racetime', refresh_table)
        background_tasks.create(refresh_table())


def _default_bool(attr: str) -> bool:
    return attr in ('auto_start', 'allow_comments', 'allow_midrace_chat', 'allow_non_entrant_chat')
