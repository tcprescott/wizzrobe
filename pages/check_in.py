"""The event check-in desk — the page volunteers work from on their phones.

It is a tab of the Volunteer hub (``/volunteer/check-in-desk``), which lists the
open check-in events (and goes straight to the one when there is only one);
``?event=<id>`` opens the desk itself: a search box
pinned to the top, filter chips with counts (one per badge type too), and one
card per person showing the badges they bought and a full-width Check in button.

Built phone-first. Below ``md`` the roster is a card grid; on a desktop it is a
table with the same actions. Search filters in the browser (Quasar's
``filter-method``), so typing a name is instant even on slow venue wifi.

Every desk open on the same event stays in step: the service publishes on
:mod:`application.events.check_in_live` and each desk re-reads its roster,
flashing the card that changed. A desk also re-reads after its socket comes back
from a blip, so a phone that was locked doesn't show stale check-ins.

Presentation only — :class:`CheckInService` decides who may do what. The page
hides what a viewer can't use (walk-ups and removal are staff-only) so nobody
taps a button the service will refuse.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from nicegui import background_tasks, context, ui
from starlette.requests import Request
from starlette.responses import RedirectResponse

from application.event_context import event_scope
from application.events import check_in_live
from application.services import (
    AuthService,
    CheckInDeskService,
    CheckInService,
)
from application.services.check_in_rules import (
    DESK_FILTERS,
    LANYARD_LABELS,
    LANYARDS,
    active_badges,
    comp_label,
    entrant_filters,
    lanyard_for,
)
from application.table_preferences_context import table_prefs_scope
from application.tenant_context import tenant_scope
from application.timezone_context import hour_cycle_scope, tz_scope
from application.utils.app_links import volunteer_check_in_url
from application.utils.timezone import format_local_display, format_local_time
from middleware.auth import protected_page
from models import (
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    CheckInLinkMethod,
    FeatureFlag,
    Role,
)
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog import ConfirmationDialog
from theme.dialog.check_in_dialogs import open_link_dialog, open_walk_up_dialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.notify import notify_error
from theme.page_header import page_header
from theme.realtime import refresh_on_reconnect, register_view
from theme.tables.admin_crud import capture_render_context
from theme.tables.export import csv_export_button
from theme.tables.preferences import TableKeys, customize_table, search_input
from theme.timer_teardown import PageTimer
from theme.url_state import bind_url

DESK_ROLES = [Role.STAFF, Role.CHECK_IN_DESK]

_LINK_LABELS = {
    'matcherino_id': 'seen before',
    'discord_id': 'via Discord',
    'twitch_id': 'via Twitch',
    'matcherino_handle': 'via profile handle',
    'manual': 'linked by staff',
    'walk_up': 'walk-up',
    'comp': 'comped',
}

_DM_TEXT = {
    'pending': 'Sending', 'sent': 'Reaches them', 'blocked': "Can't DM them",
    'opted_out': 'DMs off in Wizzrobe', 'failed': 'Not tested (Discord down)',
}

_DM_PENDING_GIVE_UP = timedelta(minutes=5)


def _dm_status(entrant) -> str:
    if not entrant.dm_status:
        return ''
    status = entrant.dm_status.value
    if (status == 'pending' and entrant.dm_checked_at is not None
            and datetime.now(timezone.utc) - entrant.dm_checked_at > _DM_PENDING_GIVE_UP):
        return 'failed'
    return status

_FILTER_LABELS = {
    'all': 'All',
    'not_yet': 'Not yet',
    'checked_in': 'Checked in',
    'comped': 'Comped',
    'walk_up': 'Walk-ups',
}
FILTERS = {key: _FILTER_LABELS[key] for key in DESK_FILTERS}

_LANYARD_COLORS = {
    'staff': 'red-8', 'volunteer': 'purple-7', 'vip': 'amber-8', 'base': 'blue-8', 'day_pass': 'teal-8',
}


# Desktop columns only: the bespoke mobile card doesn't read these.
_COLUMNS: list[dict] = [
    {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left', 'sortable': True},
    {'name': 'lanyard', 'label': 'Lanyard', 'field': 'lanyard_rank', 'align': 'left', 'sortable': True},
    {'name': 'account', 'label': 'Wizzrobe account', 'field': 'account', 'align': 'left', 'sortable': True},
    {'name': 'status', 'label': 'Status', 'field': 'status_label', 'align': 'left', 'sortable': True},
    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
]

_EXPORT_COLUMNS: list[dict] = [
    {'name': 'name', 'label': 'Name', 'field': 'name'},
    {'name': 'matcherino_id', 'label': 'Matcherino ID', 'field': 'matcherino_id'},
    {'name': 'lanyard', 'label': 'Lanyard', 'field': 'lanyard_label'},
    {'name': 'badges', 'label': 'Badges', 'field': 'badges_text'},
    {'name': 'codes', 'label': 'Badge codes', 'field': 'codes_text'},
    {'name': 'account', 'label': 'Wizzrobe account', 'field': 'account'},
    {'name': 'linked_via', 'label': 'Linked via', 'field': 'linked_via'},
    {'name': 'source', 'label': 'Source', 'field': 'source'},
    {'name': 'volunteer', 'label': 'Volunteer', 'field': 'volunteer_text'},
    {'name': 'comp', 'label': 'Comp', 'field': 'comp_text'},
    {'name': 'registered', 'label': 'Bought', 'field': 'registered'},
    {'name': 'status', 'label': 'Status', 'field': 'status_label'},
    {'name': 'checked_in', 'label': 'Checked in at', 'field': 'checked_in_when'},
    {'name': 'checked_in_by', 'label': 'Checked in by', 'field': 'checked_in_by'},
    {'name': 'discord_dm', 'label': 'Discord DM', 'field': 'dm_text'},
]

_FILTER_METHOD = (
    "(rows, terms) => { const t = String(terms || '').toLowerCase().trim();"
    " return t ? rows.filter(r => r.search.includes(t)) : rows; }"
)

_STATUS_BADGES = '''
    <q-badge v-if="props.row.checked_in" color="positive" class="q-mr-xs">
        <q-icon name="check" size="14px" class="q-mr-xs"/>{{ props.row.checked_in_label }}
    </q-badge>
    <q-badge v-if="props.row.withdrawn" :color="props.row.refunded ? 'negative' : 'grey-7'" class="q-mr-xs">
        {{ props.row.withdrawn_label }}</q-badge>
    <q-badge v-if="props.row.walk_up" color="info" class="q-mr-xs">Walk-up</q-badge>
'''

_MENU = f'''
    <q-btn flat round dense icon="more_vert" class="{REQUIRES_SOCKET_CLASS}"
           :aria-label="'More for ' + props.row.name">
        <q-menu auto-close>
            <q-list style="min-width: 200px">
                <q-item v-if="!props.row.comp_only" clickable @click="$parent.$emit('link', props.row)">
                    <q-item-section avatar><q-icon name="link"/></q-item-section>
                    <q-item-section>{{{{ props.row.account ? 'Change linked account' : 'Link an account' }}}}</q-item-section>
                </q-item>
                <q-item v-if="props.row.account && !props.row.comp_only" clickable
                        @click="$parent.$emit('unlink', props.row)">
                    <q-item-section avatar><q-icon name="link_off"/></q-item-section>
                    <q-item-section>Unlink account</q-item-section>
                </q-item>
                <q-item v-if="props.row.can_test_dm" clickable @click="$parent.$emit('test_dm', props.row)">
                    <q-item-section avatar><q-icon name="send"/></q-item-section>
                    <q-item-section>Send the test DM again</q-item-section>
                </q-item>
                <q-item v-if="props.row.checked_in" clickable @click="$parent.$emit('undo', props.row)">
                    <q-item-section avatar><q-icon name="undo"/></q-item-section>
                    <q-item-section>Undo check-in</q-item-section>
                </q-item>
                <q-separator v-if="props.row.can_remove"/>
                <q-item v-if="props.row.can_remove" clickable class="text-negative"
                        @click="$parent.$emit('remove', props.row)">
                    <q-item-section avatar><q-icon name="delete"/></q-item-section>
                    <q-item-section>Remove walk-up</q-item-section>
                </q-item>
            </q-list>
        </q-menu>
    </q-btn>
'''

_CHECK_IN_BUTTON = f'''
    <q-btn v-if="!props.row.checked_in && !props.row.withdrawn" unelevated no-caps no-wrap color="primary"
           icon="how_to_reg" label="Check in" class="{REQUIRES_SOCKET_CLASS} checkin-btn"
           @click="$parent.$emit('checkin', props.row)" />
    <q-btn v-if="props.row.can_check_in_anyway" outline no-caps no-wrap color="negative" icon="how_to_reg"
           label="Check in anyway" class="{REQUIRES_SOCKET_CLASS}"
           @click="$parent.$emit('checkin_anyway', props.row)" />
'''

_WITHDRAWN_NOTE = '''
    <div v-if="props.row.withdrawn && !props.row.checked_in && !props.row.can_check_in_anyway"
         class="text-caption text-negative q-mt-sm">
        No badge to check in with. Send them to staff.</div>
'''

_DM_NOTE = '''
    <div v-if="props.row.dm_status === 'pending'" class="text-caption text-grey-7 q-mt-xs">
        <q-spinner size="12px" class="q-mr-xs"/>Sending them a test DM…</div>
    <div v-else-if="props.row.dm_status === 'sent'" class="text-caption text-positive q-mt-xs">
        <q-icon name="mark_chat_read" size="14px" class="q-mr-xs"/>Discord DMs reach them</div>
    <div v-else-if="props.row.dm_status === 'blocked'" class="text-caption text-negative q-mt-xs">
        <q-icon name="speaker_notes_off" size="14px" class="q-mr-xs"/>Discord won't let us DM them.
        Ask them to join the server and allow DMs from server members, then send it again from ⋮.</div>
    <div v-else-if="props.row.dm_status === 'opted_out'" class="text-caption text-warning q-mt-xs">
        <q-icon name="notifications_off" size="14px" class="q-mr-xs"/>They've turned Discord DMs off
        in Wizzrobe, so they won't get match calls. Ask them to turn DMs on in their profile.</div>
    <div v-else-if="props.row.dm_status === 'failed'" class="text-caption text-grey-7 q-mt-xs">
        Couldn't test their DMs: Discord didn't answer. Send it again from ⋮.</div>
'''

_STATUS_CELL = f'''<q-td :props="props" class="checkin-status">
    <span v-if="!props.row.checked_in && !props.row.withdrawn" class="text-grey-7">Not yet</span>
    {_STATUS_BADGES}
    {_DM_NOTE}
</q-td>'''

_ACTIONS_CELL = f'''<q-td :props="props" class="checkin-actions">
<div class="row no-wrap items-center justify-end gap-1">
    <span v-if="props.row.withdrawn && !props.row.checked_in && !props.row.can_check_in_anyway"
          class="text-caption text-negative">Send to staff</span>
    {_CHECK_IN_BUTTON}
    {_MENU}
</div>
</q-td>'''

_LANYARD_CHIP = '''
    <q-chip v-if="props.row.lanyard" square :color="props.row.lanyard_color" :text-color="props.row.lanyard_text"
            icon="badge" class="q-ml-none text-weight-bold checkin-lanyard">{{ props.row.lanyard_label }}</q-chip>
    <div v-if="props.row.extra_badges" class="text-caption text-weight-medium text-warning">
        + {{ props.row.extra_badges }}</div>
'''

_LANYARD_CELL = f'''<q-td :props="props">
    {_LANYARD_CHIP}
    <span v-if="!props.row.lanyard" class="text-grey-7">—</span>
</q-td>'''

_NOT_LINKED = f'''
    <q-btn flat dense no-caps no-wrap align="left" color="warning" icon="link"
           :label="props.row.link_label" class="{REQUIRES_SOCKET_CLASS} checkin-link-btn"
           @click="$parent.$emit('link', props.row)" />
'''

_ACCOUNT_CELL = f'''<q-td :props="props">
    <span v-if="props.row.account">{{{{ props.row.account }}}}</span>
    <template v-else>{_NOT_LINKED}</template>
</q-td>'''

_GRID_CARD = f'''<div class="q-pa-xs col-12">
<div class="checkin-grid-card q-pa-sm" :class="[props.row.checked_in ? 'checkin-grid-card--in' : '',
                                             props.row._flash ? 'wiz-row-flash' : '']">
    <div class="row no-wrap items-center q-gutter-sm">
        <q-avatar size="40px" color="grey-4" text-color="grey-9">
            <img v-if="props.row.avatar" :src="props.row.avatar" referrerpolicy="no-referrer">
            <span v-else>{{{{ props.row.initial }}}}</span>
        </q-avatar>
        <div class="col" style="min-width: 0">
            <div class="text-subtitle1 text-weight-medium ellipsis">{{{{ props.row.name }}}}</div>
            <div v-if="props.row.account" class="text-caption text-grey-7 ellipsis">
                {{{{ props.row.account }}}} · {{{{ props.row.linked_via }}}}
            </div>
            <div v-else>{_NOT_LINKED}</div>
            <div v-if="props.row.lanyard" class="q-mt-xs">{_LANYARD_CHIP}</div>
            <div class="q-mt-xs">{_STATUS_BADGES}</div>
            {_DM_NOTE}
        </div>
        {_MENU}
    </div>
    <q-btn v-if="!props.row.checked_in && !props.row.withdrawn" unelevated no-caps color="primary"
           icon="how_to_reg" label="Check in" size="lg"
           class="full-width q-mt-sm {REQUIRES_SOCKET_CLASS} checkin-btn"
           @click="$parent.$emit('checkin', props.row)" />
    <q-btn v-if="props.row.can_check_in_anyway" outline no-caps color="negative" icon="how_to_reg"
           label="Check in anyway" class="full-width q-mt-sm {REQUIRES_SOCKET_CLASS}"
           @click="$parent.$emit('checkin_anyway', props.row)" />
    {_WITHDRAWN_NOTE}
</div>
</div>'''


def _link_label(suggestions: int) -> str:
    if not suggestions:
        return 'Not linked'
    return f"Not linked · {suggestions} suggestion{'' if suggestions == 1 else 's'}"


def _row(entrant: CheckInEntrant, can_manage: bool, volunteers: set[int], suggestions: int = 0) -> Dict:
    user = entrant.user
    checked_by = entrant.checked_in_by
    account = user.preferred_name if user else ''
    checked_in = entrant.checked_in_at is not None
    withdrawn = entrant.withdrawn_at is not None
    walk_up = entrant.source == CheckInEntrantSource.WALK_UP
    comp_only = entrant.source == CheckInEntrantSource.COMP
    comp_text = ', '.join(comp_label(reason) for reason in entrant.comp_reasons or [])
    volunteer = entrant.user_id is not None and entrant.user_id in volunteers
    all_badges = list(entrant.passes)
    held = active_badges(all_badges)
    lanyard = lanyard_for(entrant, held, volunteer)
    lanyard_label = LANYARD_LABELS[lanyard] if lanyard else ''
    badge_lanyards = sorted((b.tier.lanyard.value for b in held), key=LANYARDS.index)
    extra_badges = ', '.join(f'{LANYARD_LABELS[value]} badge' for value in badge_lanyards[1:])
    refunded = not held and any(b.refunded_at is not None for b in all_badges)
    if comp_only:
        withdrawn_label = 'No longer comped'
    else:
        withdrawn_label = 'Refunded' if refunded else 'No badge'
    if checked_in:
        status_label = 'Checked in'
    elif withdrawn:
        status_label = withdrawn_label
    else:
        status_label = 'Not yet'
    checked_in_label = ''
    if checked_in:
        checked_in_label = format_local_time(entrant.checked_in_at)
        if checked_by is not None:
            checked_in_label += f' by {checked_by.preferred_name}'
    badges = [{'title': b.tier.title, 'code': b.code} for b in held]
    search = ' '.join(filter(None, [
        entrant.display_name, account, user.username if user else '',
        user.twitch_username if user else '', *(b.code for b in held),
    ])).lower()
    return {
        'id': entrant.id,
        'name': entrant.display_name,
        'initial': (entrant.display_name[:1] or '?').upper(),
        'avatar': entrant.avatar_url or '',
        'account': account,
        'linked_via': _LINK_LABELS.get(entrant.link_method.value, '') if entrant.link_method and user else '',
        'source': 'Walk-up' if walk_up else 'Comp' if comp_only else 'Matcherino',
        'comp_text': comp_text,
        'comp_only': comp_only,
        'matcherino_id': entrant.matcherino_user_id or '',
        'lanyard': lanyard or '',
        'lanyard_label': lanyard_label,
        'lanyard_color': _LANYARD_COLORS.get(lanyard or '', 'grey-7'),
        # Amber is too light for white text, so the VIP lanyard reads in black.
        'lanyard_text': 'black' if lanyard == 'vip' else 'white',
        'lanyard_rank': LANYARDS.index(lanyard) if lanyard else len(LANYARDS),
        'extra_badges': extra_badges,
        'badges_text': ', '.join(
            ([f'Comp ({comp_text})'] if comp_text else []) + [b['title'] for b in badges]
        ),
        'codes_text': ', '.join(b['code'] for b in badges),
        'registered': format_local_display(entrant.registered_at) if entrant.registered_at else '',
        'status_label': status_label,
        'checked_in': checked_in,
        'checked_in_label': checked_in_label,
        'checked_in_when': format_local_display(entrant.checked_in_at) if checked_in else '',
        'checked_in_by': checked_by.preferred_name if checked_by else '',
        'withdrawn': withdrawn,
        'withdrawn_label': withdrawn_label,
        'refunded': refunded,
        'walk_up': walk_up,
        'volunteer_text': 'Yes' if volunteer else '',
        'unlinked': user is None,
        'link_label': _link_label(suggestions) if user is None else '',
        'prompt_link': (
            user is None and entrant.source == CheckInEntrantSource.MATCHERINO
            and entrant.link_method != CheckInLinkMethod.MANUAL
        ),
        'can_remove': walk_up and can_manage,
        'dm_status': _dm_status(entrant),
        'dm_text': _DM_TEXT.get(_dm_status(entrant), ''),
        'can_test_dm': checked_in and user is not None and bool(user.discord_id),
        'can_check_in_anyway': withdrawn and not checked_in and can_manage,
        'search': search,
        'filters': sorted((entrant_filters(entrant) & set(DESK_FILTERS)) | {'all'}),
        '_flash': False,
    }


def _matches(row: Dict, key: str) -> bool:
    return key in row['filters']


def _can_sync(event: CheckInEvent) -> bool:
    return event.matcherino_venue_id is not None or bool(event.comp_roles) or event.comp_volunteers


def _sync_line(event: CheckInEvent) -> tuple[str, str]:
    """The desk's one-line sync status, and the class to show it in."""
    comps = bool(event.comp_roles) or event.comp_volunteers
    if event.matcherino_venue_id is None and not comps:
        return 'Walk-ups only: no Matcherino venue linked.', 'text-grey-7'
    if event.matcherino_venue_id is None:
        if event.last_synced_at is None:
            return 'Comps only: not synced yet.', 'text-grey-7'
        return f'Comps only · updated at {format_local_time(event.last_synced_at)}', 'text-grey-7'
    if event.last_sync_error:
        when = format_local_time(event.updated_at) if event.updated_at else ''
        return (
            f"Couldn't reach Matcherino at {when}, so the roster may be out of date. "
            f'{event.last_sync_error}', 'text-warning',
        )
    if event.last_synced_at is None:
        return 'Not synced with Matcherino yet.', 'text-grey-7'
    return (
        f'Synced with Matcherino at {format_local_time(event.last_synced_at)} · '
        f'{event.last_sync_count or 0} with badges', 'text-grey-7',
    )


async def check_in_desk_tab(user, event: Optional[str] = None) -> None:
    """Volunteer → Check-in Desk: the desk for one event, or a picker.

    ``event`` is a check-in event id from the URL (``?event=``). Without one the
    tab opens the only open event, or lists them when several are open.
    """
    service = CheckInService()
    if event is not None and event.isdecimal():
        current = await service.get_event(int(event))
        if current is not None:
            await _render_desk(service, user, current)
            return
        ui.notify("That check-in event doesn't exist any more.", color='warning')
    open_events = await service.list_open_events()
    if len(open_events) == 1:
        await _render_desk(service, user, open_events[0])
        return

    with ui.column().classes('page-container-narrow w-full q-pa-md gap-3'):
        with page_header('Check-in Desk'):
            await help_icon('check-in-desk', user=user)
        if not open_events:
            empty_panel('No event is open for check-in right now.', icon='how_to_reg')
            if await AuthService.can_manage_check_in(user):
                ui.label('Open one from Admin → Check-in Roster.').classes('text-caption text-grey-7')
            return
        ui.label('Pick the event you are checking people in for.').classes('text-caption text-grey-7')
        for item in open_events:
            ui.button(item.name, icon='how_to_reg',
                      on_click=lambda _, e=item: ui.navigate.to(volunteer_check_in_url(e.id))) \
                .props('unelevated no-caps color=primary size=lg align=left').classes('w-full')


def create() -> None:
    @protected_page('/checkin', roles=DESK_ROLES, feature=FeatureFlag.EVENT_CHECK_IN)
    async def check_in_index(request: Request):
        root_path = request.scope.get('root_path', '') or ''
        return RedirectResponse(f'{root_path}{volunteer_check_in_url()}')

    @protected_page('/checkin/{event_id}', roles=DESK_ROLES, feature=FeatureFlag.EVENT_CHECK_IN)
    async def check_in_desk(request: Request, event_id: str):
        root_path = request.scope.get('root_path', '') or ''
        return RedirectResponse(f'{root_path}{volunteer_check_in_url(event_id)}')


async def _render_desk(service: CheckInService, user, event: CheckInEvent) -> None:
    can_manage = await AuthService.can_manage_check_in(user)
    render_context = capture_render_context()
    client = context.client
    state: Dict = {'event': event, 'rows': [], 'filter': 'all', 'flash': set(), 'suggestions': (None, {}),
                   'undo_timer': None}

    with ui.column().classes('page-container-narrow w-full gap-2 q-px-sm'):
        with ui.row().classes('w-full items-center no-wrap gap-2 q-pt-sm'):
            title = ui.label(event.name).classes('page-title col q-ma-none')
            title.tag = 'h1'
            with ui.row().classes('no-wrap items-center gap-0 wiz-help-row'):
                await help_icon('check-in-desk', user=user)
                sync_btn = ui.button(icon='sync').props('flat round color=primary') \
                    .classes(REQUIRES_SOCKET_CLASS).tooltip('Sync badges and comps now')
                if not _can_sync(event):
                    sync_btn.set_visibility(False)
                if can_manage:
                    ui.button(icon='person_add', on_click=lambda: background_tasks.create(add_walk_up())) \
                        .props('flat round color=primary').classes(REQUIRES_SOCKET_CLASS) \
                        .tooltip('Add walk-up')
        closed_notice = ui.label('This event is closed. You can still record late check-ins.') \
            .classes('text-caption text-warning')
        with ui.column().classes('checkin-toolbar w-full gap-1 q-py-sm'):
            with ui.row().classes('w-full items-center no-wrap gap-2') as box_slot:
                pass
            with ui.row().classes('w-full items-center no-wrap gap-2'):
                progress = ui.linear_progress(value=0, show_value=False, size='8px').classes('col') \
                    .props('rounded color=positive aria-label="Checked in"')
                counts_label = ui.label().classes('text-caption text-weight-medium checkin-counts')
            sync_label = ui.label().classes('text-caption')

        # Phone cards below 1200px, not md: the five columns don't fit a 1024px laptop beside the drawer.
        table = ui.table(columns=_COLUMNS, rows=[], row_key='id', pagination=0) \
            .classes('w-full checkin-table').props(':grid="Quasar.Screen.width < 1200" flat hide-pagination')
        table._props[':filter-method'] = _FILTER_METHOD
        table.add_slot('body-cell-status', _STATUS_CELL)
        table.add_slot('body-cell-lanyard', _LANYARD_CELL)
        table.add_slot('body-cell-account', _ACCOUNT_CELL)
        table.add_slot('body-cell-actions', _ACTIONS_CELL)
        table.add_slot('item', _GRID_CARD)
        table.add_slot('no-data', '''<div class="full-width text-center text-grey-7 q-pa-lg">
            {{ props.filter ? 'Nobody matches that search.' : 'Nobody here yet.' }}</div>''')
        # After the card slot, never before: the card must not follow a saved layout.
        customize_table(table, _COLUMNS, key=TableKeys.CHECK_IN_DESK, pager=False)
        with box_slot:
            box = search_input(table, placeholder='Name or badge code', width='w-full')
            box.props('autofocus outlined').classes('col')
            chips = ui.select(FILTERS, value='all').props('dense outlined options-dense behavior=menu') \
                .props('aria-label="Show"').classes('checkin-filter')
            with chips.add_slot('prepend'):
                ui.icon('filter_list')

        with ui.row().classes('w-full items-center gap-2 q-mt-sm checkin-footer'):
            csv_export_button('check-in', lambda: _EXPORT_COLUMNS, lambda: state['rows'], label='Export CSV')

        undo_bar = ui.row().classes('checkin-undo items-center no-wrap gap-2 q-pa-sm')
        undo_bar.set_visibility(False)

    def paint() -> None:
        current = state['event']
        rows = state['rows']
        counts = {key: sum(1 for r in rows if _matches(r, key)) for key in FILTERS}
        chips.set_options({key: f'{label} {counts[key]}' for key, label in FILTERS.items()}, value=state['filter'])
        visible = [dict(r, _flash=r['id'] in state['flash']) for r in rows if _matches(r, state['filter'])]
        table.rows = visible
        table.update()
        total = counts['checked_in'] + counts['not_yet']
        progress.value = (counts['checked_in'] / total) if total else 0
        text, cls = _sync_line(current)
        stale = cls != 'text-grey-7' or current.last_synced_at is None
        synced = f' · synced {format_local_time(current.last_synced_at)}' \
            if current.last_synced_at is not None and not stale else ''
        counts_label.text = f"{counts['checked_in']} of {total} checked in{synced}"
        sync_label.text = text
        sync_label.classes(replace=f'text-caption {cls}')
        sync_label.set_visibility(stale)
        title.text = current.name
        closed_notice.set_visibility(current.status == CheckInEventStatus.CLOSED)
        sync_btn.set_visibility(_can_sync(current))

    async def suggestion_counts(current: CheckInEvent, entrants) -> Dict[int, int]:
        key = (
            frozenset((e.id, e.display_name) for e in entrants if e.user_id is None),
            frozenset(e.user_id for e in entrants if e.user_id is not None),
        )
        cached_key, counts = state['suggestions']
        if key != cached_key:
            counts = await service.suggestion_counts(current, entrants)
            state['suggestions'] = (key, counts)
        return counts

    async def reload(flash: Optional[int] = None) -> None:
        current = await service.get_event(state['event'].id)
        if current is None:
            ui.notify('This check-in event was deleted.', color='warning')
            ui.navigate.to('/checkin')
            return
        state['event'] = current
        volunteers = await service.volunteer_user_ids()
        entrants = await service.roster(current)
        suggestions = await suggestion_counts(current, entrants)
        state['rows'] = sorted(
            (_row(e, can_manage, volunteers, suggestions.get(e.id, 0)) for e in entrants),
            key=lambda r: r['name'].casefold(),
        )
        if flash is not None:
            state['flash'].add(flash)

            def clear() -> None:
                state['flash'].discard(flash)
                paint()
            PageTimer(1.6, clear, once=True)
        paint()

    def on_filter(e) -> None:
        state['filter'] = e.value or 'all'
        paint()

    chips.on_value_change(on_filter)
    bind_url('show', chips, default='all', choices=FILTERS, history='replace')

    def show_undo(entrant_id: int, name: str, lanyard: str, extra: str) -> None:
        undo_bar.clear()
        with undo_bar:
            with ui.column().classes('col gap-0'):
                ui.label(f'Checked in {name}').classes('ellipsis')
                if lanyard:
                    hand_over = f'Hand over: {lanyard} lanyard'
                    ui.label(f'{hand_over}, plus {extra}' if extra else hand_over) \
                        .classes('text-caption text-weight-bold ellipsis')

            async def undo() -> None:
                undo_bar.set_visibility(False)
                await run(service.undo_check_in(user, entrant_id), f'Undid check-in for {name}.')

            ui.button('Undo', on_click=undo).props('flat no-caps color=yellow-4') \
                .classes(REQUIRES_SOCKET_CLASS)
        undo_bar.set_visibility(True)
        if state['undo_timer'] is not None:
            state['undo_timer'].cancel()
        state['undo_timer'] = PageTimer(8.0, lambda: undo_bar.set_visibility(False), once=True)

    async def run(coro, success: Optional[str] = None) -> bool:
        try:
            await coro
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        if success:
            ui.notify(success, color='positive')
        await reload()
        return True

    async def check_in_entrant(entrant_id: int, despite_withdrawn: bool = False) -> None:
        try:
            outcome = await service.check_in(user, entrant_id, despite_withdrawn=despite_withdrawn)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        entrant = outcome.entrant
        await reload()
        if outcome.already:
            by = entrant.checked_in_by.preferred_name if entrant.checked_in_by else 'someone'
            ui.notify(
                f'{entrant.display_name} was already checked in by {by} at '
                f'{format_local_time(entrant.checked_in_at)}.',
                color='info',
            )
            return
        # Read the lanyard after the reload: linking just before can change it.
        row: Dict = next((r for r in state['rows'] if r['id'] == entrant.id), {})
        show_undo(entrant.id, entrant.display_name, row.get('lanyard_label', ''), row.get('extra_badges', ''))

    async def handle_checkin(row) -> None:
        if row.get('prompt_link'):
            entrant = await service.get_entrant(row['id'])
            if entrant is None:
                ui.notify('That person is no longer on the roster.', color='warning')
                await reload()
                return
            # Another desk may have linked or checked them in since this row was drawn.
            if entrant.user_id is None and entrant.checked_in_at is None:
                await open_link_dialog(
                    service, user, entrant, on_done=reload,
                    then_check_in=lambda: check_in_entrant(entrant.id),
                )
                return
        await check_in_entrant(row['id'])

    async def handle_checkin_anyway(row) -> None:
        async def confirm() -> None:
            await check_in_entrant(row['id'], despite_withdrawn=True)

        why = {
            'Refunded': f"{row['name']}'s badge was refunded.",
            'No longer comped': f"{row['name']} isn't comped any more.",
        }.get(row['withdrawn_label'], f"{row['name']} doesn't hold a badge any more.")
        ConfirmationDialog(
            message=f'{why} Check them in anyway?',
            on_confirm=confirm, confirm_text='Check in anyway',
        ).open()

    async def handle_link(row) -> None:
        entrant = await service.get_entrant(row['id'])
        if entrant is None:
            ui.notify('That person is no longer on the roster.', color='warning')
            await reload()
            return
        await open_link_dialog(service, user, entrant, on_done=reload)

    async def handle_unlink(row) -> None:
        await run(service.unlink(user, row['id']), f"Unlinked {row['name']}.")

    async def handle_test_dm(row) -> None:
        await run(CheckInDeskService().send_test_dm(user, row['id']), f"Sending {row['name']} a test DM.")

    async def handle_undo(row) -> None:
        await run(service.undo_check_in(user, row['id']), f"Undid check-in for {row['name']}.")

    async def handle_remove(row) -> None:
        async def confirm() -> None:
            await run(service.remove_entrant(user, row['id']), f"Removed {row['name']}.")

        ConfirmationDialog(
            message=f"Remove walk-up {row['name']}? Their check-in goes with them.",
            on_confirm=confirm, confirm_text='Remove',
        ).open()

    async def add_walk_up() -> None:
        with client, tenant_scope(render_context[0]), event_scope(render_context[4]), tz_scope(render_context[1]), hour_cycle_scope(render_context[3]), \
                table_prefs_scope(render_context[2]):
            await open_walk_up_dialog(service, user, state['event'], on_done=reload)

    async def sync_now() -> None:
        sync_btn.props('loading')
        try:
            result = await service.sync_event(user, state['event'].id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            await reload()
            return
        finally:
            sync_btn.props(remove='loading')
        parts = [f'{result.total} with badges']
        if result.added:
            parts.append(f'{result.added} new')
        if result.comped:
            parts.append(f'{result.comped} comped')
        if result.withdrawn:
            parts.append(f'{result.withdrawn} withdrawn')
        if result.auto_linked:
            parts.append(f'{result.auto_linked} linked')
        ui.notify('Synced: ' + ', '.join(parts) + '.', color='positive')
        await reload()

    def bind(event_name: str, handler) -> None:
        async def run_in_context(args) -> None:
            with client, tenant_scope(render_context[0]), event_scope(render_context[4]), tz_scope(render_context[1]), hour_cycle_scope(render_context[3]), \
                    table_prefs_scope(render_context[2]):
                await handler(args)
        table.on(event_name, lambda e: background_tasks.create(run_in_context(e.args)))

    bind('checkin', handle_checkin)
    bind('checkin_anyway', handle_checkin_anyway)
    bind('link', handle_link)
    bind('unlink', handle_unlink)
    bind('test_dm', handle_test_dm)
    bind('undo', handle_undo)
    bind('remove', handle_remove)
    sync_btn.on_click(sync_now)

    async def on_live_change(event_id: int, entrant_id: Optional[int], change_type: str) -> None:
        if event_id != state['event'].id:
            return
        with tenant_scope(render_context[0]), event_scope(render_context[4]), tz_scope(render_context[1]), hour_cycle_scope(render_context[3]), \
                table_prefs_scope(render_context[2]):
            await reload(flash=entrant_id if change_type == check_in_live.CHANGED else None)

    register_view(on_live_change, channel=check_in_live)

    async def reread() -> None:
        with tenant_scope(render_context[0]), event_scope(render_context[4]), tz_scope(render_context[1]), hour_cycle_scope(render_context[3]), \
                table_prefs_scope(render_context[2]):
            await reload()

    refresh_on_reconnect(reread)
    await reload()


__all__: List[str] = ['create']
