"""Admin Feedback Review Page"""

from typing import Any

from nicegui import app, ui

from application.services import FeedbackService, get_user_from_session
from application.utils.tenant_urls import safe_local_path
from application.utils.timezone import format_local_display
from theme.dialog.feedback_dialog import CATEGORY_ICONS
from theme.empty_state import no_data_slot
from theme.help import help_icon
from theme.notify import notify_error
from theme.page_header import page_header
from theme.pickers import ColorSelect
from theme.tables.admin_crud import refresh_button
from theme.tables.preferences import TableKeys, customize_table, search_input
from theme.url_state import bind_url

_CATEGORY_LABELS = {
    'bug': 'Bug',
    'suggestion': 'Suggestion',
    'praise': 'Praise',
    'other': 'Other',
}
_ANY_CATEGORY = 'any'

_COLUMNS = [
    {'name': 'created_at', 'label': 'Submitted', 'field': 'created_ts', 'align': 'left',
     'sortable': True, 'style': 'width: 11rem'},
    {'name': 'user', 'label': 'From', 'field': 'user', 'align': 'left', 'style': 'width: 11rem'},
    {'name': 'message', 'label': 'Message', 'field': 'message', 'align': 'left'},
    {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left', 'style': 'width: 7rem'},
    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right', 'style': 'width: 1%'},
]

_STATUS_BADGE = '''
    <span :class="'wiz-chip ' + (props.row.status === 'reviewed' ? 'wiz-chip--ok' : 'wiz-chip--pending')">
        {{ props.row.status === 'reviewed' ? 'Reviewed' : 'New' }}
    </span>
'''

_CATEGORY = '''
    <span class="wiz-feedback-category">
        <q-icon :name="props.row.category_icon" size="16px" />{{ props.row.category }}
    </span>
'''

# Interpolated, never v-html: the message and page are the sender's own text.
_MESSAGE = '''
    <div class="wiz-feedback-message">{{ props.row.message }}</div>
    <a v-if="props.row.page_href" :href="props.row.page_href" target="_blank" rel="noopener"
       class="wiz-feedback-page" :aria-label="'Open ' + props.row.page_url + ' in a new tab'">
        <q-icon name="open_in_new" size="14px" /><span>{{ props.row.page_url }}</span>
    </a>
    <span v-else-if="props.row.page_url" class="wiz-feedback-page wiz-feedback-page--plain">
        {{ props.row.page_url }}
    </span>
'''

# Reversible: marking reviewed used to be one-way, so a mis-click dropped a
# submission out of the only queue anyone looks at with no way back.
_ROW_ACTIONS = '''
    <div class="row no-wrap items-center justify-end q-gutter-x-xs">
        <q-btn v-if="props.row.status !== 'reviewed'" unelevated dense no-caps color="primary"
               no-wrap icon="done" label="Mark reviewed" class="q-px-sm"
               @click="$parent.$emit('set_reviewed', props.row)" />
        <q-btn v-else outline dense no-caps color="primary" no-wrap icon="undo" label="Reopen" class="q-px-sm"
               @click="$parent.$emit('reopen', props.row)">
            <q-tooltip>Put this back in the New queue</q-tooltip>
        </q-btn>
        <q-btn flat round dense icon="more_vert" color="grey-7"
               :aria-label="'More for feedback from ' + props.row.user">
            <q-menu auto-close anchor="bottom right" self="top right">
                <q-list style="min-width: 180px">
                    <q-item clickable @click="$parent.$emit('copy_message', props.row)">
                        <q-item-section avatar><q-icon name="content_copy"/></q-item-section>
                        <q-item-section>Copy message</q-item-section>
                    </q-item>
                    <q-item v-if="props.row.page_url" clickable
                            @click="$parent.$emit('copy_page', props.row)">
                        <q-item-section avatar><q-icon name="link"/></q-item-section>
                        <q-item-section>Copy page path</q-item-section>
                    </q-item>
                </q-list>
            </q-menu>
        </q-btn>
    </div>
'''

_ITEM = '''
    <div class="q-pa-xs col-12">
        <q-card flat bordered class="q-pa-md wiz-grid-card wiz-feedback-card">
            <div class="row items-center no-wrap q-gutter-x-sm">
                <div class="col text-weight-bold">{{ props.row.user }}</div>
                ''' + _CATEGORY + '''
            </div>
            <div class="text-caption text-grey-7 q-mb-xs">
                {{ props.row.created_at }}
                <template v-if="props.row.status === 'reviewed'"> · Reviewed</template>
            </div>
            ''' + _MESSAGE + '''
            <div class="q-mt-sm">''' + _ROW_ACTIONS + '''</div>
        </q-card>
    </div>
'''


def _row(fb: Any) -> dict:
    category = fb.category.value
    return {
        'id': fb.id,
        'created_at': format_local_display(fb.created_at),
        'created_ts': fb.created_at.timestamp(),
        'user': fb.user.preferred_name if fb.user else '-',
        'category': _CATEGORY_LABELS.get(category, category),
        'category_key': category,
        'category_icon': CATEGORY_ICONS.get(category, {}).get('icon', 'o_chat_bubble_outline'),
        'message': fb.message,
        'page_url': fb.page_url or '',
        # Only a same-origin path is drawn as a link: the sender controls this
        # string, and an href is where javascript: or //evil would run.
        'page_href': safe_local_path(fb.page_url, auth_routes=()) or '',
        'status': fb.status.value,
    }


def _status_options(rows: list[dict]) -> dict[str, str]:
    new = sum(1 for r in rows if r['status'] != 'reviewed')
    return {'new': f'New · {new}', 'reviewed': 'Reviewed', 'all': 'All'}


async def admin_feedback_page() -> None:
    service = FeedbackService()
    state: dict[str, Any] = {'rows': [], 'status': 'new', 'category': _ANY_CATEGORY}

    with ui.column().classes('page-container-wide w-full'):
        with page_header(
            'Feedback',
            "What members sent with the Send Feedback button. Mark each one reviewed "
            "once it's handled.",
        ):
            ui.space()
            # Lambda, not a direct reference: reload is defined below the header.
            refresh_button(lambda: reload(), tooltip='Refresh')
            await help_icon('feedback-review', user=await get_user_from_session(app.storage.user))

        filter_row = ui.row().classes('w-full items-center gap-2 flex-wrap wiz-feedback-filters')

        table = ui.table(columns=_COLUMNS, rows=[], row_key='id', pagination=25) \
            .classes('w-full wiz-table wiz-feedback-table') \
            .props(':grid="Quasar.Screen.lt.md" wrap-cells')
        table.add_slot('body-cell-created_at', '''
            <q-td :props="props" class="text-grey-7">{{ props.row.created_at }}</q-td>''')
        table.add_slot('body-cell-user', f'''
            <q-td :props="props"><div class="text-weight-bold">{{{{ props.row.user }}}}</div>{_CATEGORY}</q-td>''')
        table.add_slot('body-cell-message', f'<q-td :props="props">{_MESSAGE}</q-td>')
        table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_BADGE}</q-td>')
        table.add_slot('body-cell-actions',
                       f'<q-td :props="props" style="white-space: nowrap">{_ROW_ACTIONS}</q-td>')
        table.add_slot('item', _ITEM)
        # Terminal on purpose — staff cannot manufacture feedback, so there
        # is nothing to offer here beyond saying the queue is empty.
        table.add_slot('no-data', no_data_slot('Nothing here. New feedback shows up in this list.',
                                               icon='feedback'))
        customize_table(table, _COLUMNS, key=TableKeys.ADMIN_FEEDBACK)

        def show() -> None:
            rows = state['rows']
            if state['status'] != 'all':
                want_reviewed = state['status'] == 'reviewed'
                rows = [r for r in rows if (r['status'] == 'reviewed') == want_reviewed]
            if state['category'] != _ANY_CATEGORY:
                rows = [r for r in rows if r['category_key'] == state['category']]
            table.rows = rows
            table.update()

        async def reload() -> None:
            actor = await get_user_from_session(app.storage.user)
            try:
                submissions = await service.list_recent(actor)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            state['rows'] = [_row(fb) for fb in submissions]
            status_toggle.set_options(_status_options(state['rows']), value=state['status'])
            show()

        def on_status(e) -> None:
            if e.value:
                state['status'] = e.value
                show()

        def on_category(e) -> None:
            state['category'] = e.value or _ANY_CATEGORY
            show()

        with filter_row:
            status_toggle = ui.toggle(_status_options([]), value='new', on_change=on_status) \
                .props('no-caps dense unelevated no-wrap').classes('wiz-segmented wiz-feedback-status')
            bind_url('status', status_toggle, default='new', history='replace')
            ColorSelect(
                {_ANY_CATEGORY: 'Any category', **_CATEGORY_LABELS},
                label='Category', value=_ANY_CATEGORY,
                extras={_ANY_CATEGORY: {'icon': 'o_category'}, **CATEGORY_ICONS},
            ).props('dense outlined').classes('wiz-feedback-category-select').on_value_change(on_category)
            search_input(table, placeholder='Search', width='flex-1 sm:flex-none sm:w-64')

        async def set_reviewed(event, reviewed: bool) -> None:
            row = event.args
            actor = await get_user_from_session(app.storage.user)
            try:
                await service.set_reviewed(actor, row['id'], reviewed=reviewed)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(
                'Marked as reviewed.' if reviewed else 'Back in the New queue.',
                color='positive',
            )
            await reload()

        def copy(text: str, what: str) -> None:
            ui.clipboard.write(text)
            ui.notify(f'{what} copied.', color='positive')

        table.on('set_reviewed', lambda e: set_reviewed(e, True))
        table.on('reopen', lambda e: set_reviewed(e, False))
        table.on('copy_message', lambda e: copy(e.args['message'], 'Message'))
        table.on('copy_page', lambda e: copy(e.args['page_url'], 'Page path'))

        await reload()
