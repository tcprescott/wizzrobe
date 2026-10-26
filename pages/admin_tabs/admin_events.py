"""Admin → Events: every event of the community, and their lifecycle.

One row per event. New event opens a draft, copying whichever setup groups are
ticked from another event. A row's ⋮ has Event settings, Rename…, New event
from this…, then Go live… (a draft), Make default (a live one), Archive… or
Unarchive…, and Delete… last (a draft with no matches).
"""

import json
from datetime import date
from typing import Any, Optional

from nicegui import ui

from application.services import EventService
from application.services.event_service import EventRef
from application.services.event_setup_copy import COPY_GROUPS
from application.tenant_context import require_tenant_id
from middleware.event_routing import event_href, page_request
from models import EventStatus, User
from theme.archived_banner import confirm_unarchive
from theme.dialog._helpers import dialog_actions, form_dialog, native_date_input, submit_on_enter
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import no_data_slot
from theme.event_switcher import STATUS_CHIPS, event_dates_label
from theme.help import help_icon
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import current_actor
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

#: What each copy checkbox brings, under its label in the New event dialog.
COPY_HINTS = {
    'tournaments': 'Names, formats, presets, Discord role mappings. No players, brackets, '
                   'round schedules or Challonge/start.gg links.',
    'positions': 'With who is qualified for each, and the hour reward tiers.',
    'stages': 'With how stations are numbered.',
    'check_in': 'Badge tiers and comp rules. No attendees.',
    'discord_roles': 'The rules, not the roles already given.',
}

_COLUMNS: list[dict[str, Any]] = [
    {'name': 'name', 'label': 'Event', 'field': 'name', 'align': 'left'},
    {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left'},
    {'name': 'dates', 'label': 'Dates', 'field': 'dates', 'align': 'left'},
    {'name': 'size', 'label': 'Size', 'field': 'size', 'align': 'left'},
    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
]

_NAME_SLOT = '''
    <span class="text-weight-bold">{{ props.row.name }}</span>
    <span v-if="props.row.is_default" class="wiz-chip wiz-chip--neutral q-ml-sm">Default</span>
'''

_STATUS_SLOT = '''
    <span :class="'wiz-chip wiz-chip--' + props.row.tone">{{ props.row.status }}</span>
'''


def _menu_item(event: str, icon: str, label: str, condition: str = '') -> str:
    v_if = f' v-if="{condition}"' if condition else ''
    return f'''
                    <q-item clickable{v_if} @click="$parent.$emit('{event}', props.row)">
                        <q-item-section avatar><q-icon name="{icon}"/></q-item-section>
                        <q-item-section>{label}</q-item-section>
                    </q-item>'''


_ROW_ACTIONS = '''
    <q-btn flat round dense icon="more_vert" color="grey-7" :aria-label="'More for ' + props.row.name">
        <q-menu auto-close anchor="bottom right" self="top right">
            <q-list style="min-width: 200px">''' + ''.join([
    _menu_item('settings', 'settings', 'Event settings'),
    _menu_item('rename', 'edit', 'Rename…'),
    _menu_item('copy', 'content_copy', 'New event from this…'),
    '<q-separator />',
    _menu_item('go_live', 'play_circle', 'Go live…', "props.row.status_key === 'draft'"),
    _menu_item('make_default', 'star', 'Make default', "props.row.status_key === 'live' && !props.row.is_default"),
    _menu_item('archive', 'inventory_2', 'Archive…', "props.row.status_key === 'live'"),
    _menu_item('unarchive', 'unarchive', 'Unarchive…', "props.row.status_key === 'archived'"),
    _menu_item('delete', 'delete', 'Delete…', "props.row.status_key === 'draft' && !props.row.matches"),
]) + '''
            </q-list>
        </q-menu>
    </q-btn>
'''


def _size(tournaments: int, matches: int) -> str:
    if not tournaments and not matches:
        return '—'
    return f'{tournaments:,} tournament{"" if tournaments == 1 else "s"} · {matches:,} match{"" if matches == 1 else "es"}'


async def admin_events_page() -> None:
    actor = await current_actor()
    if actor is None:
        return

    with ui.column().classes('page-container-narrow w-full'):
        with page_header(
            'Events',
            'Several events can be live. Bare links open the Default one. Archived events stay '
            'visible to everyone.',
        ):
            await help_icon('staff-events-lifecycle', 'staff-events-archive', user=actor)
            ui.space()
            ui.button('New event', icon='add', on_click=lambda: _new_event_dialog(actor, None, render.refresh)) \
                .props('color=primary')

        @steady_refreshable
        async def render() -> None:
            refs = await EventService.events_for_tenant(require_tenant_id())
            sizes = await EventService.sizes()
            rows = []
            for ref in refs:
                tournaments, matches = sizes.get(ref.id, (0, 0))
                text, tone = STATUS_CHIPS[ref.status]
                rows.append({
                    'id': ref.id, 'name': ref.name, 'slug': ref.slug, 'is_default': ref.is_default,
                    'status': text, 'tone': tone, 'status_key': ref.status.value,
                    'dates': event_dates_label(ref.starts_on, ref.ends_on) or '—',
                    'size': _size(tournaments, matches), 'matches': matches,
                })
            table = ui.table(columns=_COLUMNS, rows=rows, row_key='id').classes('w-full wiz-table')
            table.add_slot('body-cell-name', f'<q-td :props="props">{_NAME_SLOT}</q-td>')
            table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_SLOT}</q-td>')
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
            table.add_slot('no-data', no_data_slot('No events yet.', icon='event'))
            enable_mobile_grid(
                table, _COLUMNS, actions=_ROW_ACTIONS, table_key=TableKeys.ADMIN_EVENTS,
                field_slots={'name': _NAME_SLOT, 'status': _STATUS_SLOT},
            )
            by_id = {ref.id: ref for ref in refs}

            def handler(action):
                return lambda e: action(by_id[e.args['id']])

            table.on('settings', handler(_open_settings))
            table.on('rename', handler(lambda ref: _rename_dialog(actor, ref, render.refresh)))
            table.on('copy', handler(lambda ref: _new_event_dialog(actor, ref, render.refresh)))
            table.on('go_live', handler(lambda ref: _go_live(actor, ref, render.refresh)))
            table.on('make_default', handler(lambda ref: _make_default(actor, ref, render.refresh)))
            table.on('archive', handler(lambda ref: _archive_dialog(actor, ref, refs, render.refresh)))
            table.on('unarchive', handler(lambda ref: confirm_unarchive(actor, ref, render.refresh)))
            table.on('delete', handler(lambda ref: _delete(actor, ref, render.refresh)))

        await render()


def _open_settings(ref: EventRef) -> None:
    request = page_request()
    if request is None:
        return
    ui.run_javascript(f'location.assign({json.dumps(event_href(request, ref.slug, "/admin/event-settings"))})')


def _parse_date(raw: Optional[str], label: str) -> Optional[date]:
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f'The {label} date must be in YYYY-MM-DD format.') from None


async def _new_event_dialog(actor: User, source: Optional[EventRef], on_change) -> None:
    refs = await EventService.events_for_tenant(require_tenant_id())
    options = {ref.id: ref.name for ref in refs}
    default = next((r for r in refs if r.is_default), None)
    default_source = source.id if source else next(iter(options), None)
    with form_dialog('New event') as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            name = ui.input('Name').classes('full-width').props('autofocus')
            with ui.row().classes('gap-3 full-width no-wrap'):
                starts = native_date_input('Starts', clearable=True).classes('col')
                ends = native_date_input('Ends', clearable=True).classes('col')
            copy_from = ui.select(
                {0: 'Nothing, start empty', **options}, value=default_source or 0, label='Copy setup from',
            ).classes('full-width')
            boxes: dict[str, ui.checkbox] = {}
            with ui.column().classes('gap-1 full-width') as copy_list:
                for key, label in COPY_GROUPS.items():
                    with ui.column().classes('gap-0'):
                        boxes[key] = ui.checkbox(label, value=True)
                        ui.label(COPY_HINTS[key]).classes('wiz-field-hint q-ml-xl')
            copy_list.bind_visibility_from(copy_from, 'value', backward=bool)
            keeps = f'; {default.name} keeps running until you archive it' if default else ''
            ui.label(
                f'Starts as a Draft only staff can see. Go live when you’re ready{keeps}.'
            ).classes('text-caption text-muted')

        async def submit() -> None:
            try:
                groups = frozenset(k for k, box in boxes.items() if box.value) if copy_from.value else frozenset()
                event, copied = await EventService.create_event(
                    actor, name=name.value or '',
                    starts_on=_parse_date(starts.value, 'start'), ends_on=_parse_date(ends.value, 'end'),
                    copy_from=copy_from.value or None, copy=groups,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            skipped = copied.get('skipped') or []
            note = f' {len(skipped)} rule{"" if len(skipped) == 1 else "s"} not copied: ' + ' '.join(skipped) \
                if skipped else ''
            ui.notify(f'{event.name} is a draft. Only staff can see it.{note}', color='positive')
            await on_change()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Create draft', on_click=submit).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()


def _rename_dialog(actor: User, ref: EventRef, on_change) -> None:
    with form_dialog(f'Rename {ref.name}', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            name = ui.input('Name', value=ref.name).classes('full-width').props('autofocus')
            slug = ui.input('Link name', value=ref.slug).classes('full-width')
            if ref.status is EventStatus.DRAFT:
                ui.label('Appears in links as /e/<link name>. It can’t change once the event is live.') \
                    .classes('wiz-field-hint')
            else:
                slug.disable()
                ui.label('Links already point at this event, so its link name stays.').classes('wiz-field-hint')

        async def submit() -> None:
            try:
                await EventService.update_event(
                    actor, ref.id, name=name.value or '',
                    slug=slug.value if ref.status is EventStatus.DRAFT else None,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            ui.notify('Event renamed', color='positive')
            await on_change()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Save', on_click=submit).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()


async def _run(action, success: str, on_change) -> None:
    try:
        await action()
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return
    ui.notify(success, color='positive')
    await on_change()


def _go_live(actor: User, ref: EventRef, on_change) -> None:
    ConfirmationDialog(
        f'Everyone in the community can see {ref.name} once it’s live. The default event stays the '
        f'default until you make {ref.name} the default.',
        title=f'Go live with {ref.name}?', confirm_text='Go live', tone='primary',
        on_confirm=lambda: _run(lambda: EventService.go_live(actor, ref.id), f'{ref.name} is live', on_change),
    ).open()


async def _make_default(actor: User, ref: EventRef, on_change) -> None:
    await _run(lambda: EventService.make_default(actor, ref.id),
               f'Bare links now open {ref.name}', on_change)


async def _archive_dialog(actor: User, ref: EventRef, refs: tuple[EventRef, ...], on_change) -> None:
    check = await EventService.archive_check(ref.id)
    others = {r.id: r.name for r in refs if r.is_live and r.id != ref.id}
    with form_dialog(f'Archive {ref.name}?', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            ui.label(
                f'{ref.name} stays visible to everyone, and you can unarchive it later. '
                'Workers stop running for it.'
            )
            open_items = [
                _count(check.open_matches, 'match', 'matches', 'not finished'),
                _count(check.future_shifts, 'volunteer shift', 'volunteer shifts', 'still to come'),
                _count(check.open_race_rooms, 'race room', 'race rooms', 'open'),
            ]
            open_items = [line for line in open_items if line]
            if open_items:
                ui.label('Still open: ' + ', '.join(open_items) + '.').classes('text-warning')
            new_default = None
            if ref.is_default and others:
                new_default = ui.select(others, value=next(iter(others)), label='New default event') \
                    .classes('full-width')

        async def submit() -> None:
            dialog.close()
            await _run(
                lambda: EventService.archive(
                    actor, ref.id, new_default_id=new_default.value if new_default is not None else None,
                ),
                f'{ref.name} is archived', on_change,
            )

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Archive', on_click=submit).props('color=negative')
    dialog.open()


def _count(n: int, one: str, many: str, state: str) -> str:
    return f'{n} {one if n == 1 else many} {state}' if n else ''


def _delete(actor: User, ref: EventRef, on_change) -> None:
    ConfirmationDialog(
        f'{ref.name} and the setup copied into it (tournaments, positions, stages) are deleted. '
        'This can’t be undone.',
        title=f'Delete {ref.name}?', confirm_text='Delete',
        on_confirm=lambda: _run(lambda: EventService.delete_draft(actor, ref.id), f'{ref.name} deleted', on_change),
    ).open()
