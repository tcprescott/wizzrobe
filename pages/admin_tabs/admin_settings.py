"""Admin Settings/Tournaments Management Page"""


from nicegui import app, background_tasks, context, ui

from application.event_context import event_scoped
from application.services import AuthService, StageService, get_user_from_session
from application.utils.safe_url import http_url_or_empty
from models import Tournament
from theme.dialog import TournamentDialog
from theme.dialog.stage_edit_dialog import StageEditDialog
from theme.empty_state import no_data_slot
from theme.help import help_icon
from theme.page_header import page_header
from theme.tables.admin_crud import refresh_button
from theme.tables.preferences import TableKeys, customize_table, preferences_button
from theme.tables.tournament import TournamentTableView, tournament_columns


async def admin_tournaments_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    can_create = await AuthService.is_staff(actor)
    # A tournament admin edits, rosters and syncs only the tournaments they run,
    # so their list is those; every other row would open controls that refuse.
    own_ids = None if can_create else (await AuthService.tournament_scope(actor))[0]

    with ui.column().classes('page-container'):
        with page_header(
            'Tournaments',
            'Click a tournament to edit it, or its player count to manage entrants.',
        ):
            await help_icon('admin-tournaments', user=actor)

        columns = tournament_columns()

        async def add_tournament():
            async def after_submit(_):
                await table_view.refresh()
            dialog = TournamentDialog(on_submit=after_submit)
            await dialog.open()

        def get_query():
            query = event_scoped(Tournament.all())
            return query if own_ids is None else query.filter(id__in=own_ids)
        
        table_view = TournamentTableView(
            columns=columns, get_query=get_query,
            submit_tournament_callback=add_tournament if can_create else None,
            table_key=TableKeys.ADMIN_TOURNAMENTS, searchable=True, can_delete=can_create,
            can_sync=True, can_link=can_create,
        )
        
        # Route through the view's _bg so the tab-switch refresh rebinds the
        # tenant (the selected_tab handler runs in a detached task that lost it).
        def on_tab_selected():
            table_view._bg(table_view.refresh())
        ui.on('selected_tab', lambda e: on_tab_selected() if e.args == 'Tournaments' else None)


async def admin_stages_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    can_manage = await AuthService.can_manage_stages(actor)

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Stage Management',
            'The stages matches can be assigned to. Each maps to a stream URL used '
            'across the schedule and On Air views. Click a name to edit it.',
        )

        columns: list[dict] = [
            # No id column: the primary key was this table's only edit affordance
            # ("click the 1 to rename Stage 1"), which is both a database value on
            # screen and an unguessable control. The name carries the link now.
            {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True},
            {'name': 'stream_url', 'label': 'Stream URL', 'field': 'stream_url'},
            {'name': 'is_active', 'label': 'Active', 'field': 'is_active'},
        ]

        table_container = ui.column().classes('w-full')

        async def load_stages():
            stages = await StageService().get_all_stages()
            rows = [
                {
                    'id': stage.id,
                    'name': stage.name,
                    'stream_url': http_url_or_empty(stage.stream_url),
                    'is_active': stage.is_active,
                }
                for stage in stages
            ]
            return rows

        async def refresh_table():
            rows = await load_stages()
            table.rows = rows
            table.update()

        async def add_stage():
            async def after_submit(_):
                await refresh_table()
            with table_container:
                dialog = StageEditDialog(on_submit=after_submit)
                await dialog.open()

        async def edit_stage(row, client):
            # Runs in a background task (table 'edit' event → background_tasks.create),
            # where the slot stack is empty; restore the captured client so ui.notify
            # and the dialog have a slot context.
            with client:
                stage = await StageService().get_stage_by_id(row['id'])
                if not stage:
                    ui.notify("Couldn't find that stage.", color='warning')
                    return
                async def after_submit(_):
                    await refresh_table()
                dialog = StageEditDialog(stage=stage, on_submit=after_submit)
                await dialog.open()

        with table_container:
            with ui.row().classes('full-width'):
                if can_manage:
                    ui.button('Add Stage', icon='add', on_click=add_stage).props('color=primary').classes('wiz-write')
                ui.space()
                # Filled after the table exists; the toolbar is drawn first.
                gear_slot = ui.row().classes('items-center')
                refresh_button(refresh_table)

            table = ui.table(
                columns=columns,
                rows=[],
                row_key='id',
            ).classes('w-full wiz-table')
            
            table.props(':grid="Quasar.Screen.lt.md"')
            
            table.add_slot('body-cell-stream_url', '''
                <q-td :props="props">
                    <a :href="props.row.stream_url" target="_blank" v-if="props.row.stream_url">
                        {{ props.row.stream_url }}
                    </a>
                    <span v-else>-</span>
                </q-td>
            ''')
            
            table.add_slot('body-cell-is_active', '''
                <q-td :props="props">
                    <q-icon :name="props.row.is_active ? 'check_circle' : 'cancel'" 
                            :color="props.row.is_active ? 'positive' : 'negative'" size="sm" />
                </q-td>
            ''')
            
            table.add_slot('body', '''
                <q-tr :props="props">
                    <q-td v-for="col in props.cols" :key="col.name" :props="props">
                        <template v-if="col.name === 'name'">
                            <a href="javascript:void(0)" @click="$parent.$emit('edit', props.row)"
                               style="color: var(--wiz-link); text-decoration: underline; cursor: pointer;">
                                {{ col.value }}
                            </a>
                        </template>
                        <template v-else-if="col.name === 'stream_url'">
                            <a :href="props.row.stream_url" target="_blank" v-if="props.row.stream_url">
                                {{ props.row.stream_url }}
                            </a>
                            <span v-else>-</span>
                        </template>
                        <template v-else-if="col.name === 'is_active'">
                            <q-icon :name="props.row.is_active ? 'check_circle' : 'cancel'" 
                                    :color="props.row.is_active ? 'positive' : 'negative'" size="sm" />
                        </template>
                        <template v-else>
                            {{ col.value }}
                        </template>
                    </q-td>
                </q-tr>
            ''')

            table.add_slot('no-data', no_data_slot(
                'No stages yet — add one for each stage or capture station '
                'you run matches on.', icon='tv'))
            
            table.add_slot('item', '''
                <div class="q-pa-xs col-xs-12 col-sm-6 col-md-4">
                    <q-card class="q-pa-sm">
                        <q-card-section class="q-pa-sm">
                            <div style="display: flex; align-items: center; justify-content: space-between;">
                                <div>
                                    <span class="subsection-title">{{ props.row.name }}</span>
                                    <q-badge :color="props.row.is_active ? 'positive' : 'negative'" class="q-ml-sm">
                                        {{ props.row.is_active ? 'Active' : 'Inactive' }}
                                    </q-badge>
                                </div>
                                <q-btn flat round dense icon="edit" color="primary"
                                       @click="$parent.$emit('edit', props.row)" aria-label="Edit">
                                    <q-tooltip>Edit</q-tooltip>
                                </q-btn>
                            </div>
                        </q-card-section>
                        <q-card-section class="q-pa-sm" v-if="props.row.stream_url">
                            <div class="text-caption text-grey-7">Stream URL:</div>
                            <a :href="props.row.stream_url" target="_blank"
                               style="color: var(--wiz-link); text-decoration: underline; word-break: break-all;">
                                {{ props.row.stream_url }}
                            </a>
                        </q-card-section>
                        <q-card-section class="q-pa-sm" v-else>
                            <div class="text-caption text-grey-7">No stream URL set</div>
                        </q-card-section>
                    </q-card>
                </div>
            ''')
            
            customize_table(table, columns, key=TableKeys.ADMIN_STAGES)
            with gear_slot:
                preferences_button(table)

            table.on('edit', lambda e: background_tasks.create(edit_stage(e.args, context.client)))

        background_tasks.create(refresh_table())
