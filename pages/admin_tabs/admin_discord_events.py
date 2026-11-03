"""Admin Discord Events Page — schedule → Discord Scheduled Events mirror (SYNC_ADMIN/STAFF).

Per-tournament opt-in for mirroring scheduled matches into the tenant guild's
Discord Scheduled Events, plus an on-demand "Sync now" and an observability table
of the events this tenant currently owns. The background reconciler runs the same
reconcile on a cadence as the system user; this surface is the human-driven half.
The mirror needs a **linked Discord server** — the page surfaces that up front.
"""

from nicegui import app, background_tasks, context, ui

from application.services import (
    DiscordEventSyncService,
    get_user_from_session,
)
from theme.banner import banner
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.notify import notify_error
from theme.page_header import page_header
from theme.tables.admin_crud import refresh_button, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

_STATE_BADGE = '''
    <q-badge :color="props.row.state_color">{{ props.row.state_label }}</q-badge>
'''

_ROW_ACTIONS = '''
    <q-btn flat round dense icon="edit" color="primary" :disable="props.row.locked"
           @click="$parent.$emit('edit', props.row)" aria-label="Edit">
        <q-tooltip>{{ props.row.locked ? 'Fix the problem above first' : 'Edit' }}</q-tooltip>
    </q-btn>
'''


def _state(enabled: bool, locked: bool) -> tuple[str, str]:
    if not enabled:
        return 'Off', 'grey-7'
    return ('Paused', 'negative') if locked else ('Synced', 'positive')


async def admin_discord_events_page() -> None:
    service = DiscordEventSyncService()

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Discord Events',
            'Mirror scheduled matches into your Discord server\'s Scheduled Events. '
            'Enable per tournament below; the reconciler creates, updates, and '
            'cancels events to match your schedule. It only ever touches events it '
            'created, so sharing a Discord server with another community is safe.',
        )

        status_area = ui.column().classes('w-full')
        state = {'problem': None}

        async def _current():
            return await get_user_from_session(app.storage.user)

        def render_status() -> None:
            status_area.clear()
            problem = state['problem']
            with status_area:
                if problem is None:
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('check_circle').classes('st-ok')
                        ui.label('The bot can create events in your Discord server.').classes('text-caption')
                    return
                with banner(
                    f'{problem} Settings are locked and syncing is paused until this is fixed.',
                    title="Discord events can't sync", tone='danger', icon='block',
                ):
                    ui.button(
                        'Check again', icon='refresh',
                        on_click=busy_click('Checking…', refresh_tables),
                    ).props('outline color=primary')

        ui.label('Tournaments').classes('section-title q-mt-md')
        tournament_columns = [
            {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
            {'name': 'name', 'label': 'Tournament', 'field': 'name', 'sortable': True},
            {'name': 'enabled', 'label': 'Events', 'field': 'enabled'},
            {'name': 'duration', 'label': 'Duration (min)', 'field': 'duration'},
            {'name': 'actions', 'label': '', 'field': 'actions'},
        ]
        tournaments_container = ui.column().classes('w-full')

        ui.label('Mirrored events').classes('section-title q-mt-md')
        event_columns = [
            {'name': 'title', 'label': 'Title', 'field': 'title', 'sortable': True},
            {'name': 'when', 'label': 'When', 'field': 'when'},
            {'name': 'discord_event_id', 'label': 'Discord Event', 'field': 'discord_event_id'},
            {'name': 'synced', 'label': 'Synced', 'field': 'synced'},
        ]
        events_container = ui.column().classes('w-full')

        async def refresh_tables():
            from application.utils.timezone import format_local_display
            user = await _current()
            state['problem'] = await service.setup_problem(user)
            locked = state['problem'] is not None
            render_status()
            sync_button.set_enabled(not locked)
            tournaments = await service.list_tournaments(user)
            tournament_table.rows = [
                {
                    'id': t.id,
                    'name': t.name,
                    'enabled': _state(t.discord_events_enabled, locked)[0],
                    'state_label': _state(t.discord_events_enabled, locked)[0],
                    'state_color': _state(t.discord_events_enabled, locked)[1],
                    'locked': locked,
                    'enabled_bool': t.discord_events_enabled,
                    'duration': t.discord_event_duration_minutes,
                    'title_template': t.discord_event_title_template or '',
                    'description_template': t.discord_event_description_template or '',
                }
                for t in tournaments
            ]
            tournament_table.update()

            events = await service.list_events(user)
            event_table.rows = [
                {
                    'title': e.title,
                    'when': format_local_display(e.scheduled_at) if e.scheduled_at else '—',
                    'discord_event_id': str(e.discord_event_id),
                    'synced': format_local_display(e.synced_at) if e.synced_at else '—',
                }
                for e in events
            ]
            event_table.update()

        async def sync_now(client):
            with client:
                try:
                    result = await service.reconcile_now(await _current())
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                if result.setup_error:
                    ui.notify(f"Couldn't sync: {result.setup_error}", color='warning', multi_line=True)
                    await refresh_tables()
                    return
                elif result.errors:
                    ui.notify(
                        f"Sync finished with {result.errors} error(s); "
                        f"{result.created} created, {result.updated} updated",
                        color='warning',
                    )
                else:
                    ui.notify(
                        f"Synced: {result.created} created, {result.updated} updated, "
                        f"{result.cancelled} cancelled", color='positive',
                    )
                await refresh_tables()

        async def open_settings_dialog(row, client) -> None:
            if row.get('locked'):
                return
            with tournaments_container:
                with form_dialog(f"Discord events — {row['name']}", card_classes='dialog-card dialog-card--sm') as dialog:
                    enabled_switch = ui.switch('Sync this tournament\'s matches', value=row['enabled_bool'])
                    duration_input = ui.number(
                        'Event duration (min)', value=row['duration'], min=1,
                    ).props('inputmode=numeric').classes('w-full')
                    title_input = ui.input(
                        'Title template (optional)', value=row['title_template'],
                    ).classes('w-full')
                    desc_input = ui.input(
                        'Description template (optional)', value=row['description_template'],
                    ).classes('w-full')
                    ui.label(
                        'Placeholders: {tournament}, {match}, {players}'
                    ).classes('text-caption text-grey')

                    async def submit():
                        try:
                            await service.update_settings(
                                await _current(), row['id'],
                                enabled=enabled_switch.value,
                                duration_minutes=int(duration_input.value or 60),
                                title_template=title_input.value,
                                description_template=desc_input.value,
                            )
                            ui.notify('Settings saved', color='positive')
                            dialog.close()
                            await refresh_tables()
                        except (ValueError, PermissionError) as e:
                            notify_error(e)

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button('Save', icon='save', on_click=submit).props('color=primary')
            dialog.open()

        with tournaments_container:
            with ui.row().classes('full-width'):
                sync_button = ui.button(
                    'Sync now', icon='sync',
                    on_click=busy_click('Syncing…', lambda: sync_now(context.client)),
                ).props('color=primary')
                ui.space()
                refresh_button(refresh_tables, tooltip='Refresh')

            tournament_table = ui.table(columns=tournament_columns, rows=[], row_key='id').classes('w-full wiz-table')
            tournament_table.add_slot('body-cell-enabled', f'<q-td :props="props">{_STATE_BADGE}</q-td>')
            tournament_table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
            tournament_table.on('edit', lambda e: background_tasks.create(open_settings_dialog(e.args, context.client)))
            enable_mobile_grid(tournament_table, tournament_columns, actions=_ROW_ACTIONS,
                               table_key=TableKeys.ADMIN_DISCORD_EVENT_TOURNAMENTS,
                               field_slots={'enabled': _STATE_BADGE})

        with events_container:
            event_table = ui.table(columns=event_columns, rows=[], row_key='discord_event_id').classes('w-full wiz-table')
            enable_mobile_grid(event_table, event_columns,
                               table_key=TableKeys.ADMIN_DISCORD_EVENTS)

        wire_tab_refresh('Discord Events', refresh_tables)
        background_tasks.create(refresh_tables())
