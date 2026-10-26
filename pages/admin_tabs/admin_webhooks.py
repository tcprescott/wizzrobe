"""Admin Webhooks Page — staff-managed outbound webhooks."""

import json

from nicegui import app, context, ui

from application.events import EventType
from application.services import EventService, WebhookService, get_user_from_session
from application.services.event_service import EventRef
from application.tenant_context import require_tenant_id
from application.utils.timezone import format_local_display
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.secret_dialog import show_secret_dialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.pickers import CHIP_PROPS
from theme.steady import steady_refreshable
from theme.tables.admin_crud import capture_render_context, scoped_background, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

# Update options for the multiselect. '*' (every update) is offered first.
_EVENT_OPTIONS = {EventType.WILDCARD: 'All updates (*)'}
_EVENT_OPTIONS.update({name: name for name in sorted(EventType.ALL)})

# Receiver-side verification snippet shown in the payload-format reference.
_SIGNATURE_SNIPPET = '''import hmac, hashlib

signed = f"{request.headers['X-Wizzrobe-Timestamp']}.{raw_body}".encode()
expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Wizzrobe-Signature"])'''

def _health_cell(entry: dict | None) -> dict:
    """The row's delivery-health chip, from one webhook's 24h counts.

    Three states worth distinguishing, and no more: nothing sent (the common
    case for a quiet community — not a problem), everything landed, and some
    did not. The failure count leads when there are failures, because that is
    the number the operator is looking for.
    """
    if not entry or not (entry['ok'] or entry['failed']):
        return {'health': '', 'health_chip': ''}
    total = entry['ok'] + entry['failed']
    if entry['failed']:
        return {
            'health': f"{entry['failed']} of {total} failed",
            'health_chip': 'wiz-chip--cancelled',
        }
    return {
        'health': f'{total} delivered',
        'health_chip': 'wiz-chip--ok',
    }


def _open_format_reference() -> None:
    """A code-derived reference for what the app POSTs to a receiver, in a dialog."""
    ref = WebhookService.format_reference()
    with form_dialog('Payload & event reference', card_classes='dialog-card dialog-card--lg') as dialog:
        ui.label(
            'When a subscribed event occurs, the app sends an HTTP POST to your URL with '
            'this JSON body (keys are sorted for a stable signature):'
        ).classes('text-caption text-grey')
        ui.code(json.dumps(ref['example_payload'], indent=2), language='json').classes('w-full')

        ui.label('Headers').classes('text-subtitle2 q-mt-md')
        # A static 2-column reference, not a board anyone works from; it reads
        # fine on a phone as a table and has nothing worth customizing.
        # mobile-grid: exempt, table-prefs: exempt
        ui.table(
            columns=[
                {'name': 'name', 'label': 'Header', 'field': 'name', 'align': 'left'},
                {'name': 'description', 'label': 'Meaning', 'field': 'description', 'align': 'left'},
            ],
            rows=ref['headers'],
            row_key='name',
        ).props('flat dense wrap-cells').classes('w-full')

        ui.label('Verify the signature (receiver side)').classes('text-subtitle2 q-mt-md')
        ui.code(_SIGNATURE_SNIPPET, language='python').classes('w-full')

        c = ref['constants']
        ui.label(
            f"Delivery runs off the request path with a {c['timeout_seconds']:.0f}s timeout, "
            f"up to {c['max_attempts']} attempts with exponential backoff "
            f"({c['backoff_base']}**attempt seconds) on any non-2xx or transport error. "
            'Each attempt is recorded (see Recent deliveries).'
        ).classes('text-caption text-grey q-mt-md')

        ui.label('Updates').classes('text-subtitle2 q-mt-md')
        ui.label(
            f"Subscribe to specific updates, or '{ref['wildcard']}' for all:"
        ).classes('text-caption text-grey')
        with ui.column().classes('gap-0'):
            for group, names in ref['events'].items():
                with ui.row().classes('items-baseline gap-2'):
                    ui.label(f'{group}.').classes('text-weight-medium')
                    ui.label(', '.join(names)).classes('text-caption text-grey')

        ui.label(
            'Security: URLs must be https:// (SSRF-guarded in production); the signing secret '
            'is shown only once and is never returned by the API or written to logs.'
        ).classes('text-caption text-grey q-mt-md')
    dialog.open()


def _event_groups(event_types) -> list[tuple[str, str]]:
    """Subscribed events grouped by area, e.g. ``('match', 'rescheduled, stage assigned')``.

    Grouped and wrapped so a webhook on six events reads in two lines instead of
    pushing the list sideways.
    """
    if not event_types:
        return []
    if EventType.WILDCARD in event_types:
        return [('All updates', '')]
    groups: dict[str, list[str]] = {}
    for name in event_types:
        area, _, action = name.partition('.')
        groups.setdefault(area, []).append(action.replace('_', ' '))
    return [(area, ', '.join(actions)) for area, actions in groups.items()]


def _event_label(event: EventRef) -> str:
    if event.is_draft:
        return f'{event.name} (Draft)'
    if event.is_archived:
        return f'{event.name} (Archived)'
    return event.name


def _events_line(event_ids, events: dict[int, EventRef]) -> str:
    """Which community events a webhook sends from, for the line under its URL."""
    if event_ids is None:
        return 'All events'
    names = [_event_label(events[i]) for i in event_ids if i in events]
    return ', '.join(names) or 'No events'


async def admin_webhooks_page() -> None:
    service = WebhookService()

    async def _current():
        return await get_user_from_session(app.storage.user)

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Webhooks',
            'Wizzrobe sends a signed POST to each URL when one of its events happens. '
            'The signing secret shows once, when you create or rotate it.',
        )

        table_container = ui.column().classes('w-full')
        # The community's events, by id, refreshed with the list.
        events: dict[int, EventRef] = {}

        async def refresh_table():
            await webhook_list.refresh()

        async def delete_webhook(row, client):
            async def do_delete() -> None:
                try:
                    await service.delete_webhook(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Webhook deleted', color='positive')
                await refresh_table()

            with client:
                ConfirmationDialog(
                    message=f"Delete the {row['name']} webhook? Its receiver stops getting events.",
                    on_confirm=do_delete, confirm_text='Delete', title='Delete webhook?',
                ).open()

        async def regenerate_secret(row, client):
            async def do_regenerate() -> None:
                try:
                    secret = await service.regenerate_secret(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                show_secret_dialog('New signing secret', secret, noun='signing secret')

            with client:
                ConfirmationDialog(
                    message=f"Make a new signing secret for {row['name']}? The old one stops "
                            'working straight away, so the receiver rejects every delivery '
                            'until you give it the new one.',
                    on_confirm=do_regenerate, confirm_text='Regenerate',
                    title='Regenerate secret?',
                ).open()

        async def set_active(row, active: bool):
            try:
                await service.update_webhook(await _current(), row['id'], is_active=active)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f"{row['name']} {'resumed' if active else 'paused'}", color='positive')
            await refresh_table()

        async def view_deliveries(row, client):
            with client:
                try:
                    deliveries = await service.list_deliveries(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                with form_dialog(f"Recent deliveries — {row['name']}", card_classes='dialog-card dialog-card--lg') as dialog:
                    if not deliveries:
                        ui.label('No deliveries yet.').classes('text-grey')
                    else:
                        rows = [
                            {
                                'event': d.event_type,
                                'status': '✓' if d.success else '✗',
                                'code': d.response_status if d.response_status is not None else '—',
                                'attempts': d.attempt_count,
                                'when': format_local_display(d.created_at) if d.created_at else '',
                                'error': d.error or '',
                            }
                            for d in deliveries
                        ]
                        delivery_columns = [
                            {'name': 'when', 'label': 'When', 'field': 'when'},
                            {'name': 'event', 'label': 'Event', 'field': 'event'},
                            {'name': 'status', 'label': 'OK', 'field': 'status'},
                            {'name': 'code', 'label': 'HTTP', 'field': 'code'},
                            {'name': 'attempts', 'label': 'Tries', 'field': 'attempts'},
                            {'name': 'error', 'label': 'Error', 'field': 'error'},
                        ]
                        delivery_table = ui.table(
                            columns=delivery_columns,
                            rows=rows,
                            row_key='when',
                        ).classes('w-full wiz-table')
                        enable_mobile_grid(delivery_table, delivery_columns,
                                           table_key=TableKeys.ADMIN_WEBHOOK_DELIVERIES)
                dialog.open()

        def open_webhook_dialog(existing=None) -> None:
            is_edit = existing is not None
            with table_container:
                with form_dialog('Edit webhook' if is_edit else 'Add webhook', card_classes='dialog-card dialog-card--sm') as dialog:
                    name_input = ui.input(
                        'Name', value=existing['name'] if is_edit else '',
                    ).classes('w-full')
                    url_input = ui.input(
                        'URL', value=existing['url'] if is_edit else '',
                        placeholder='https://example.com/hook',
                    ).classes('w-full')
                    events_select = ui.select(
                        options=_EVENT_OPTIONS,
                        label='Updates',
                        multiple=True,
                        value=existing['event_types'] if is_edit else [],
                    ).props(CHIP_PROPS).classes('w-full')
                    picked = existing['event_ids'] if is_edit else None
                    # Archived events send nothing, so they're offered only to
                    # keep one this webhook already had.
                    choices = {
                        e.id: _event_label(e) for e in events.values()
                        if not e.is_archived or (picked and e.id in picked)
                    }
                    ui.label('From events').classes('text-subtitle2 q-mt-sm')
                    scope = ui.radio(
                        {'all': 'All events', 'some': 'Only these events'},
                        value='all' if picked is None else 'some',
                    ).props('dense')
                    ui.label('All events includes events added later.').classes('wiz-field-hint')
                    events_picker = ui.select(
                        options=choices, label='Events', multiple=True,
                        value=[i for i in (picked or []) if i in choices],
                    ).props(CHIP_PROPS).classes('w-full')
                    events_picker.bind_visibility_from(scope, 'value', backward=lambda v: v == 'some')
                    active_switch = ui.switch(
                        'Active', value=existing['is_active_raw'] if is_edit else True,
                    )

                    async def submit():
                        updates = list(events_select.value or [])
                        event_ids = list(events_picker.value or []) if scope.value == 'some' else None
                        try:
                            current = await _current()
                            if is_edit:
                                await service.update_webhook(
                                    current, existing['id'],
                                    name=name_input.value,
                                    url=url_input.value,
                                    event_types=updates,
                                    event_ids=event_ids,
                                    is_active=active_switch.value,
                                )
                                dialog.close()
                                ui.notify('Webhook updated', color='positive')
                                await refresh_table()
                            else:
                                webhook = await service.create_webhook(
                                    current,
                                    name=name_input.value,
                                    url=url_input.value,
                                    event_types=updates,
                                    event_ids=event_ids,
                                    is_active=active_switch.value,
                                )
                                dialog.close()
                                await refresh_table()
                                show_secret_dialog('Webhook created', webhook.secret, noun='signing secret')
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
            with ui.row().classes('full-width items-center no-wrap'):
                ui.button('Add webhook', icon='add', on_click=lambda: open_webhook_dialog()).props('color=primary no-wrap')
                refresh_context = capture_render_context()
                with ui.button('Actions').props('flat color=primary no-wrap icon-right=expand_more'):
                    with ui.menu():
                        menu_action('description', 'Payload & event reference', _open_format_reference)
                        # Refresh lives here rather than as a loose icon: three
                        # toolbar controls don't fit a phone inside the Settings card.
                        menu_action('refresh', 'Refresh list',
                                    lambda: scoped_background(refresh_context, refresh_table()))

            @steady_refreshable
            async def webhook_list() -> None:
                actor = await _current()
                try:
                    webhooks = await service.list_webhooks(actor)
                    health = await service.recent_health(actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                events.clear()
                events.update({e.id: e for e in await EventService.events_for_tenant(require_tenant_id())})
                if not webhooks:
                    empty_panel('No webhooks yet. Add one to send events to another app.', icon='webhook')
                    return
                client = context.client
                with ui.element('div').classes('wiz-webhook-list'):
                    for w in webhooks:
                        row = {
                            'id': w.id,
                            'name': w.name,
                            'url': w.url,
                            'event_types': w.event_types,
                            'event_ids': w.event_ids,
                            'is_active_raw': w.is_active,
                        }
                        _webhook_row(row, _health_cell(health.get(w.id)), client)

            def _webhook_row(row: dict, health: dict, client) -> None:
                with ui.element('div').classes('wiz-webhook-row'):
                    ui.element('span').classes(
                        'wiz-webhook-row__dot' + (' wiz-webhook-row__dot--on' if row['is_active_raw'] else '')
                    )
                    with ui.element('div').classes('wiz-webhook-row__id'):
                        with ui.row().classes('items-center no-wrap gap-2'):
                            ui.button(
                                row['name'], on_click=lambda r=row: open_webhook_dialog(r),
                            ).props('flat dense no-caps align=left padding="0"').classes('wiz-webhook-row__name')
                            if not row['is_active_raw']:
                                ui.label('Paused').classes('wiz-chip wiz-chip--neutral')
                        url_label = ui.label(row['url']).classes('wiz-webhook-row__url')
                        url_label.props['title'] = row['url']
                        with ui.row().classes('wiz-webhook-row__scope items-center no-wrap gap-1'):
                            ui.icon('event', size='14px').props('aria-hidden="true"')
                            ui.label(_events_line(row['event_ids'], events)).classes('wiz-webhook-row__scope-text')
                    with ui.element('div').classes('wiz-webhook-row__events'):
                        groups = _event_groups(row['event_types'])
                        if not groups:
                            ui.label('No events').classes('text-grey-7')
                        for area, actions in groups:
                            with ui.element('span').classes('wiz-webhook-row__group'):
                                ui.label(area).classes('wiz-webhook-row__area')
                                if actions:
                                    ui.label(actions).classes('wiz-webhook-row__actions')
                    with ui.element('div').classes('wiz-webhook-row__health'):
                        if health['health']:
                            ui.label(health['health']).classes(f"wiz-chip {health['health_chip']}")
                        else:
                            ui.label('Nothing sent').classes('text-grey-7')
                    ui.button(
                        'Deliveries', icon='history',
                        on_click=lambda r=row: view_deliveries(r, client),
                    ).props('flat dense no-caps no-wrap color=primary').classes('wiz-webhook-row__deliveries')
                    with ui.element('div').classes('wiz-webhook-row__more'):
                        with more_menu(row['name']):
                            menu_action('edit', 'Edit…', lambda r=row: open_webhook_dialog(r))
                            menu_action('vpn_key', 'Regenerate secret…',
                                        lambda r=row: regenerate_secret(r, client))
                            if row['is_active_raw']:
                                menu_action('pause_circle', 'Pause', lambda r=row: set_active(r, False))
                            else:
                                menu_action('play_circle', 'Resume', lambda r=row: set_active(r, True))
                            ui.separator()
                            menu_action('delete', 'Delete…', lambda r=row: delete_webhook(r, client),
                                        negative=True)

            await webhook_list()

        wire_tab_refresh('Webhooks', refresh_table)
