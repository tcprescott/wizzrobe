"""Admin → Check-in: set up the events the check-in desk runs (Staff).

Each event names a Matcherino venue whose badge sales become the desk's
roster. Opening an event puts it on the desk and starts the background sync;
the desk itself is Volunteer → Check-in Desk.

The Matcherino login card above the events (``theme.matcherino_login_card``)
holds the refresh token the sync signs in with.
"""

from nicegui import background_tasks, context, ui

from application.services import CheckInService
from application.utils.app_links import volunteer_check_in_url
from application.utils.timezone import format_local_display
from models import CheckInEventStatus
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog import ConfirmationDialog
from theme.dialog.check_in_dialogs import open_event_dialog
from theme.empty_state import no_data_slot
from theme.matcherino_login_card import matcherino_login_card
from theme.menu import SlotItem, slot_more_menu, slot_name_link, slot_row_button
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import current_actor, refresh_button
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

_STATUS_LABELS = {
    CheckInEventStatus.DRAFT.value: 'Draft',
    CheckInEventStatus.OPEN.value: 'Open',
    CheckInEventStatus.CLOSED.value: 'Closed',
}

_COLUMNS: list[dict] = [
    {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
    {'name': 'name', 'label': 'Event', 'field': 'name', 'align': 'left', 'sortable': True},
    {'name': 'status', 'label': 'Status', 'field': 'status_label', 'align': 'left', 'sortable': True},
    {'name': 'venue', 'label': 'Matcherino venue', 'field': 'venue', 'align': 'left'},
    {'name': 'synced', 'label': 'Last sync', 'field': 'synced', 'align': 'left'},
    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
]

_STATUS_BADGE = '''<q-badge :color="props.row.status === 'open' ? 'positive'
                                 : props.row.status === 'draft' ? 'grey-7' : 'blue-grey'">
    {{ props.row.status_label }}</q-badge>'''

_SYNCED = '''<span :class="props.row.sync_error ? 'text-warning' : ''">{{ props.row.synced }}</span>'''

_ACTIONS = slot_row_button('Open desk', 'how_to_reg', 'desk', extra_class=REQUIRES_SOCKET_CLASS) + slot_more_menu(
    'props.row.name', [
        SlotItem('edit', 'Edit event', 'edit'),
        SlotItem('delete', 'Delete event', 'remove', negative=True),
    ], extra_class=REQUIRES_SOCKET_CLASS,
)
_NAME = slot_name_link('name', 'edit')


def _synced_text(event) -> str:
    comps = bool(event.comp_roles) or event.comp_volunteers
    if event.matcherino_venue_id is None and not comps:
        return 'Walk-ups only'
    if event.matcherino_venue_id is None and event.last_synced_at is None:
        return 'Comps only · not synced yet'
    if event.matcherino_venue_id is None:
        return f'Comps only · {format_local_display(event.last_synced_at)}'
    if event.last_sync_error:
        return f'Failed: {event.last_sync_error}'
    if event.last_synced_at is None:
        return 'Not yet'
    return f'{format_local_display(event.last_synced_at)} · {event.last_sync_count or 0} with badges'


async def admin_check_in_page() -> None:
    service = CheckInService()

    with ui.column().classes('page-container-narrow w-full'):
        page_header(
            'Check-in Roster',
            "Each event pulls its roster from a Matcherino venue's badge sales. Open an "
            'event to put it on the check-in desk; the desk syncs on its own while it is open.',
        )
        ui.separator().classes('separator-spacing')

        await matcherino_login_card()

        with ui.row().classes('full-width items-center'):
            async def add_event() -> None:
                actor = await current_actor()
                if actor is None:
                    return
                await open_event_dialog(service, actor, on_done=_render_table.refresh)

            ui.button('New event', icon='add', on_click=add_event).props('color=primary') \
                .classes(f'{REQUIRES_SOCKET_CLASS} wiz-write')
            ui.space()
            refresh_button(lambda: _render_table.refresh(), tooltip='Refresh').classes(REQUIRES_SOCKET_CLASS)

        @steady_refreshable
        async def _render_table() -> None:
            events = await service.list_events()
            rows = [
                {
                    'id': e.id,
                    'name': e.name,
                    'status': e.status.value,
                    'status_label': _STATUS_LABELS.get(e.status.value, e.status.value),
                    'venue': str(e.matcherino_venue_id) if e.matcherino_venue_id else '—',
                    'synced': _synced_text(e),
                    'sync_error': bool(e.last_sync_error),
                }
                for e in events
            ]
            table = ui.table(columns=_COLUMNS, rows=rows, row_key='id').classes('w-full')
            table.add_slot('body-cell-name', f'<q-td :props="props">{_NAME}</q-td>')
            table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_BADGE}</q-td>')
            table.add_slot('body-cell-synced', f'<q-td :props="props">{_SYNCED}</q-td>')
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ACTIONS}</q-td>')
            table.add_slot('no-data', no_data_slot(
                'No check-in events yet. Create one for your next in-person event.',
                icon='how_to_reg'))
            enable_mobile_grid(
                table, _COLUMNS, actions=_ACTIONS,
                field_slots={'name': _NAME, 'status': _STATUS_BADGE, 'synced': _SYNCED},
                table_key=TableKeys.ADMIN_CHECK_IN_EVENTS,
            )

            async def handle_edit(row, client) -> None:
                with client:
                    actor = await current_actor()
                    event = await service.get_event(row['id'])
                    if actor is None:
                        return
                    if event is None:
                        ui.notify("Couldn't find that event. Try refreshing.", color='warning')
                        return
                    await open_event_dialog(service, actor, on_done=_render_table.refresh, event=event)

            async def handle_remove(row, client) -> None:
                with client:
                    actor = await current_actor()

                    async def do_delete() -> None:
                        try:
                            await service.delete_event(actor, row['id'])
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        ui.notify('Event deleted.', color='positive')
                        await _render_table.refresh()

                    ConfirmationDialog(
                        message=f"Delete {row['name']} and its whole roster, check-ins included?",
                        on_confirm=do_delete, confirm_text='Delete',
                    ).open()

            table.on('desk', lambda e: ui.navigate.to(volunteer_check_in_url(e.args['id'])))
            table.on('edit', lambda e: background_tasks.create(handle_edit(e.args, context.client)))
            table.on('remove', lambda e: background_tasks.create(handle_remove(e.args, context.client)))

        await _render_table()
