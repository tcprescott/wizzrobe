"""Admin → Community Events: the review queue for member-submitted events.

Staff approve a submission, reject it with a reason the host is DM'd, or take a
listed event down the same way. ``open_event`` is the staff DM's deep link: the
tab opens on that event's review dialog.
"""

from typing import Any, Optional

from nicegui import ui

from application.content import parse_user_blocks
from application.services import CommunityEventService
from application.utils.timezone import format_local_display
from models import CommunityEvent, CommunityEventStatus
from theme.community_events import DISCLAIMER, STATUS_CHIPS, meta_row, status_chip, when_line
from theme.dialog._helpers import dialog_actions, dialog_more_menu, form_dialog
from theme.dialog.community_event_dialog import CommunityEventDialog
from theme.empty_state import no_data_slot
from theme.help import help_icon
from theme.help.render import render_user_blocks
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import current_actor, refresh_button
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys
from theme.url_state import consume_deep_link

_COLUMNS: list[dict[str, Any]] = [
    {'name': 'starts', 'label': 'Starts', 'field': 'starts', 'align': 'left', 'sortable': True},
    {'name': 'title', 'label': 'Event', 'field': 'title', 'align': 'left'},
    {'name': 'host', 'label': 'Host', 'field': 'host', 'align': 'left'},
    {'name': 'location', 'label': 'Where', 'field': 'location', 'align': 'left'},
    {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left'},
    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
]

_STATUS_SLOT = '''
    <span :class="'wiz-chip wiz-chip--' + props.row.tone">{{ props.row.status }}</span>
'''

_ROW_ACTIONS = '''
    <q-btn dense flat color="primary" label="Review" aria-label="Review this event"
           @click="$parent.$emit('review', props.row)" />
'''

_FILTERS = {
    'pending': 'Waiting for review',
    'approved': 'Listed',
    'rejected': 'Not listed',
    'cancelled': 'Cancelled',
    'all': 'All',
}


async def admin_community_events_page(open_event: Optional[int] = None) -> None:
    service = CommunityEventService()
    actor = await current_actor()
    pending = await service.pending_count(actor)
    state = {'filter': 'pending' if pending else 'all'}

    with ui.column().classes('page-container-narrow w-full'):
        with page_header(
            'Meetups',
            'Events attendees want to run during the event. Approve them to list them, '
            'or reject them with a reason the host will see.',
        ):
            await help_icon('community-events-review', user=actor)
            ui.space()
            refresh_button(lambda: _render_table.refresh(), tooltip='Refresh')

        def set_filter(e) -> None:
            state['filter'] = e.value
            _render_table.refresh()

        ui.select(_FILTERS, value=state['filter'], label='Show', on_change=set_filter).classes('w-56')

        @steady_refreshable
        async def _render_table() -> None:
            choice = state['filter']
            statuses = None if choice == 'all' else [CommunityEventStatus(choice)]
            events = await service.list_for_review(actor, statuses)
            rows = [
                {
                    'id': e.id,
                    'starts': format_local_display(e.starts_at),
                    'title': e.title,
                    'host': e.host.preferred_name,
                    'location': e.location or '',
                    'status': STATUS_CHIPS[e.status][0],
                    'tone': STATUS_CHIPS[e.status][1],
                }
                for e in events
            ]
            table = ui.table(columns=_COLUMNS, rows=rows, row_key='id').classes('w-full wiz-table')
            table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_SLOT}</q-td>')
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
            table.add_slot('no-data', no_data_slot('No meetups here.', icon='groups'))
            enable_mobile_grid(table, _COLUMNS, actions=_ROW_ACTIONS,
                               table_key=TableKeys.ADMIN_COMMUNITY_EVENTS,
                               field_slots={'status': _STATUS_SLOT})
            table.on('review', lambda e: open_review(e.args['id']))

        async def open_review(event_id: int) -> None:
            try:
                event = await service.get_event(actor, event_id)
            except ValueError:
                ui.notify('That event no longer exists.', color='warning')
                return
            _review_dialog(service, actor, event, _render_table.refresh)

        await _render_table()
        if open_event is not None:
            await open_review(open_event)
            consume_deep_link('community_event')


def _review_dialog(service: CommunityEventService, actor, event: CommunityEvent, on_change) -> None:
    with form_dialog('Review meetup') as dialog:
        with ui.column().classes('q-pa-md gap-2 full-width'):
            with ui.row().classes('items-center gap-2'):
                ui.label(event.title).classes('section-title')
                status_chip(event)
            meta_row('schedule', when_line(event, with_day=True))
            if event.location:
                meta_row('place', event.location)
            meta_row('person', f'Hosted by {event.host.preferred_name}')
            if event.review_note:
                meta_row('feedback', f'Last reason given: {event.review_note}')
            if event.description:
                with ui.column().classes('wiz-community-event__body gap-0 full-width'):
                    render_user_blocks(parse_user_blocks(event.description))
            ui.label(DISCLAIMER).classes('text-caption text-muted')
            reason = ui.textarea(
                'Reason (sent to the host if you reject it)',
                placeholder='What needs to change before it can be listed?',
            ).classes('full-width')
            if event.status is CommunityEventStatus.CANCELLED:
                reason.set_visibility(False)

        async def decide(approve: bool) -> None:
            with dialog:
                try:
                    if approve:
                        await service.approve(actor, event.id)
                        ui.notify('Approved. The host has been told.', color='positive')
                    else:
                        await service.reject(actor, event.id, reason.value or '')
                        ui.notify('Rejected. The host has been told why.', color='positive')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                dialog.close()
            await on_change()

        async def edited(_saved) -> None:
            dialog.close()
            await on_change()

        with dialog_actions():
            if event.status is not CommunityEventStatus.CANCELLED:
                with dialog_more_menu():
                    menu_action('edit', 'Edit event', lambda: CommunityEventDialog(
                        actor, event, is_staff=True, on_saved=edited,
                    ).open())
                ui.space()
                reject_label = 'Take down' if event.status is CommunityEventStatus.APPROVED else 'Reject'
                ui.button(reject_label, on_click=lambda: decide(False)).props('flat color=negative')
                if event.status is not CommunityEventStatus.APPROVED:
                    ui.button('Approve', icon='check', on_click=lambda: decide(True)).props('color=primary')
    dialog.open()
