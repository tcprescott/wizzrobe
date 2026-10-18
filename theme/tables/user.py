from nicegui import ui

from application.tenant_context import get_current_tenant_id
from application.utils.timezone import format_local_display
from theme.empty_state import no_data_slot
from theme.tables.admin_crud import capture_render_context, scoped_background
from theme.tables.plain_rows import set_plain_rows
from theme.tables.preferences import customize_table, row_count_label, sticky_header


class UserTableView:
    """Encapsulates the user table UI and logic for admin/player dashboards."""

    def __init__(self, columns, get_query, extra_slots=None, submit_user_callback=None,
                 show_toolbar=True, row_actions=None, empty_message='No users match this filter.',
                 table_key=None, before_refresh=None, decorate_row=None):
        self.columns = columns
        # When set, this view's desktop columns follow the viewer's saved layout.
        self.table_key = table_key
        self._plan = None
        self.get_query = get_query
        self.extra_slots = extra_slots
        self.submit_user_callback = submit_user_callback
        self.show_toolbar = show_toolbar
        # Vue template for per-row buttons, rendered in both the desktop actions
        # cell and the mobile card so the two never drift.
        self.row_actions = row_actions
        self.empty_message = empty_message
        # Optional hooks for a caller that layers its own data on the rows: an
        # async ``before_refresh()`` that loads it (``get_query`` is sync), and a
        # ``decorate_row(row)`` that writes it onto each formatted row.
        self.before_refresh = before_refresh
        self.decorate_row = decorate_row
        self.table = None
        # Capture the tenant while the request context is live; background tasks
        # (initial load, pagination refresh) run detached from the client slot, so
        # scoped reads would otherwise raise require_tenant_id(). _bg rebinds it.
        self._tenant_id = get_current_tenant_id()
        self._render_context = capture_render_context()
        self._setup_ui()

    def _bg(self, coro) -> None:
        """Schedule ``coro`` with this view's tenant *and* display zone rebound."""
        scoped_background(self._render_context, coro)

    def _setup_ui(self):
        # Toolbar with actions (skipped when caller renders it externally)
        if self.show_toolbar:
            with ui.row().classes('full-width'):
                if self.submit_user_callback:
                    ui.button('Add User', icon='add', on_click=self.submit_user_callback).props('color=primary')
                ui.space()
                ui.button(icon='refresh', on_click=self.refresh).props('flat color=primary').tooltip('Refresh table')

        with ui.column().classes('full-width'):
            self.table = ui.table(
                columns=self.columns,
                rows=[],
                row_key='id',
                # Client-side paging over the rows already loaded — see the note
                # in theme/tables/match.py; there is no pagination handler.
                pagination={'rowsPerPage': 25, 'page': 1},
            ).classes('user-table user-table-container').props(':grid="Quasar.Screen.lt.md"')
        self.table.add_slot('no-data', no_data_slot(self.empty_message, icon='group'))
        if self.row_actions:
            self.table.add_slot(
                'body-cell-actions', f'<q-td :props="props">{self.row_actions}</q-td>')
        self.table.add_slot('body-cell-username', '''<q-td :props="props">
            <a href="#" @click="$parent.$emit('edit_user', props)" class="table-link user-table__name">{{ props.value }}</a>
        </q-td>''')
        self.table.add_slot('body-cell-preferred_name', '''<q-td :props="props">
            <span class="user-table__name">{{ props.value }}</span>
        </q-td>''')
        self.table.add_slot('body-cell-is_active', '''<q-td :props="props">
            <q-icon :name="props.value ? 'check_circle' : 'cancel'" :color="props.value ? 'positive' : 'negative'" size="sm" />
        </q-td>''')
        self.table.add_slot('body-cell-discord_id', '''<q-td :props="props">
            <span v-if="props.value" class="wrap" :title="props.value">{{ props.value.toString().length > 24 ? props.value.toString().substring(0, 21) + '...' : props.value }}</span>
            <span v-else>-</span>
        </q-td>''')
        self.table.add_slot('body-cell-ada', '''<q-td :props="props">
            <a v-if="props.value" href="#" class="table-link row items-center no-wrap q-gutter-x-xs"
               @click.prevent="$parent.$emit('open_ada', props.row)">
                <q-icon name="accessible" color="primary" size="sm" />
                <span>{{ props.value }}</span>
                <q-tooltip>Open this ADA request</q-tooltip>
            </a>
        </q-td>''')
        self.table.add_slot('body-cell-challonge', '''<q-td :props="props">
            <span v-if="props.value">{{ props.value }}</span>
            <span v-else class="text-grey-7">-</span>
        </q-td>''')
        # props.value is a comma-separated string.
        self.table.add_slot('body-cell-roles', '''<q-td :props="props">
            <div v-if="props.value" class="user-table__roles">
                <span v-for="r in props.value.split(',')" :key="r" class="wiz-chip wiz-chip--neutral">{{ r.trim() }}</span>
            </div>
            <span v-else class="text-grey-7">-</span>
        </q-td>''')
        self.render_grid_slot()
        if self.extra_slots:
            for slot_name, slot_template in self.extra_slots.items():
                self.table.add_slot(slot_name, slot_template)
        if self.table_key:
            # **After** render_grid_slot(), never before: the card's item slot is
            # generated from these columns at build time, so applying a saved
            # layout here cannot reach it. Pinned by
            # tests/theme/test_table_preferences.py.
            self._plan = customize_table(self.table, self.columns, key=self.table_key)
            with self.table.parent_slot:
                row_count_label(self.table, 'people')
            sticky_header(self.table)

        async def handle_edit_user(event):
            row = event.args['row'] if 'row' in event.args else event.args
            user_id = row['id']
            user_query = self.get_query()
            u = await user_query.filter(id=user_id).first()
            if not u:
                ui.notify("We couldn't find that user.", color='warning')
                return
            from theme.dialog import AdminUserDialog
            dialog = AdminUserDialog(u)
            await dialog.open()
        self.table.on('edit_user', handle_edit_user)
        # Initial load at build: correctness must not depend on the selected_tab
        # broadcast (which only fires on a *subsequent* tab switch) or on Quasar's
        # incidental pagination event.
        self._bg(self.refresh())

    def render_grid_slot(self):
        grid_fields = []
        for col in self.columns:
            # An unlabelled column (the row actions) has its own row below the
            # fields; as a field it rendered as a lone ":".
            if not col.get('label'):
                continue
            field = { 'label': col.get('label', col.get('name', '')), 'key': col.get('name', '') }
            if field['key'] == 'username':
                field['event'] = 'edit_user'
            if field['key'] == 'ada':
                field['event'] = 'open_ada'
            if field['key'] == 'is_active':
                field['bool'] = True
            grid_fields.append(field)
        js_field_array = ',\n    '.join([
            f"{{ label: '{f['label']}', key: '{f['key']}'" +
            (f", event: '{f['event']}'" if 'event' in f else '') +
            (", bool: true" if f.get('bool') else '') + " }" for f in grid_fields
        ])
        self.table.add_slot('item', f'''
        <div class="q-mb-sm user-grid-card" style="width: 100%; box-sizing: border-box;">
            <template v-for="field in [
                {js_field_array}
            ].filter(f => f.bool || f.event === 'edit_user' || props.row[f.key])" :key="field.key">
                <div class="text-grey-7">{{{{ field.label }}}}</div>
                <div style="overflow-wrap: anywhere; min-width: 0">
                <template v-if="field.event && props.row[field.key]">
                    <a href="#" @click.prevent="$parent.$emit(field.event, {{ row: props.row }})" class="table-link">{{{{ props.row[field.key] }}}}</a>
                </template>
                <div v-else-if="field.key === 'roles'" class="user-table__roles">
                    <span v-for="r in props.row.roles.split(',')" :key="r" class="wiz-chip wiz-chip--neutral">{{{{ r.trim() }}}}</span>
                </div>
                <template v-else-if="field.bool">
                    {{{{ props.row[field.key] ? 'Yes' : 'No' }}}}
                </template>
                <template v-else>
                    {{{{ props.row[field.key] || '-' }}}}
                </template>
                </div>
            </template>
            ''' + (f'<div class="user-grid-card__actions row justify-end q-gutter-x-sm">{self.row_actions}</div>'
                   if self.row_actions else '') + '''
            </div>
            ''')

    async def refresh(self, *_, **__):
        if self.before_refresh:
            await self.before_refresh()
        user_query = self.get_query()
        all_users = await user_query.order_by('username').prefetch_related(
            'roles', 'admin_tournaments', 'crew_coordinated_tournaments'
        )
        # Use the tenant captured at build, NOT a live get_current_tenant_id():
        # refresh() is also driven by the filter/toolbar/tab-switch handlers as
        # bare background tasks that have lost the tenant, where a live resolve
        # returns None and would re-leak cross-tenant role grants.
        rows = [self._format_user_row(u, self._tenant_id) for u in all_users]
        if self.decorate_row:
            for row in rows:
                self.decorate_row(row)
        set_plain_rows(self.table, rows)

    @staticmethod
    def _format_user_row(u, tid=None):
        # Show only this tenant's role grants. UserRole rows are per-tenant (the
        # same identity may hold STAFF in several communities and SUPER_ADMIN with
        # tenant=NULL), so an unscoped render both duplicates chips and leaks other
        # communities' grants into this admin — the display counterpart of the
        # scoping AuthService.get_roles already applies. Dedupe within the tenant.
        seen = set()
        role_labels = []
        for r in u.roles:
            if tid is not None and r.tenant_id != tid:
                continue
            label = r.role.label
            if label not in seen:
                seen.add(label)
                role_labels.append(label)
        # Tournaments are tenant-scoped too; count only those in this tenant.
        ta_count = sum(1 for t in u.admin_tournaments if tid is None or t.tenant_id == tid)
        cc_count = sum(1 for t in u.crew_coordinated_tournaments if tid is None or t.tenant_id == tid)
        if ta_count:
            role_labels.append(f'TA({ta_count})')
        if cc_count:
            role_labels.append(f'CC({cc_count})')
        return {
            'id': u.id,
            'username': u.username,
            'display_name': u.display_name or '',
            'preferred_name': u.preferred_name or '',
            'pronouns': u.pronouns or '',
            'discord_id': u.discord_id,
            'challonge': u.challonge_username or u.challonge_user_id or '',
            'is_active': u.is_active,
            'roles': ', '.join(role_labels),
            'created_at': format_local_display(u.created_at),
            'updated_at': format_local_display(u.updated_at),
        }

    async def update_row_by_id(self, user_id):
        """
        Update a single row in the table by its user ID, only if the row is currently visible.
        """
        idx = next((i for i, row in enumerate(self.table.rows)
                   if row.get('id') == user_id), None)
        if idx is None:
            return  # Row not visible, do nothing
        user_query = self.get_query()
        u = await user_query.filter(id=user_id).prefetch_related(
            'roles', 'admin_tournaments', 'crew_coordinated_tournaments'
        ).first()
        if not u:
            return  # User not found
        self.table.rows[idx] = self._format_user_row(u, self._tenant_id)
        self.table.update()
