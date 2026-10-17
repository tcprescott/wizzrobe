"""Admin tab for moderating triforce text submissions."""

from nicegui import app, background_tasks, ui

from application.services import AuthService, TriforceTextService, get_user_from_session
from application.tenant_context import require_tenant_id, tenant_scope
from models import Tournament
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, more_menu
from theme.page_header import page_header

_STATUS_OPTIONS = {
    'pending': 'Pending',
    'approved': 'Approved',
    'rejected': 'Rejected',
    'all': 'All',
}


def _to_status(option: str):
    return None if option == 'all' else option


async def admin_triforce_texts_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    if actor is None:
        ui.label('User not found.').classes('text-error')
        return

    is_staff = await AuthService.is_staff(actor)
    if is_staff:
        tournaments = await Tournament.filter(
            is_active=True, tenant_id=require_tenant_id()
        ).order_by('name')
    else:
        tournaments = await Tournament.filter(
            is_active=True, admins__id=actor.id, tenant_id=require_tenant_id()
        ).distinct().order_by('name')

    if not tournaments:
        with ui.column().classes('page-container-narrow'):
            page_header('Triforce Texts')
            empty_panel('You are not an admin of any active tournament.', icon='emoji_events')
        return

    state = {
        'tournament_id': tournaments[0].id,
        'status': 'pending',
    }
    service = TriforceTextService()
    tenant_id = require_tenant_id()

    with ui.column().classes('page-container-narrow'):
        page_header('Triforce Texts')

        filters_open = {'value': False}

        def toggle_filters() -> None:
            filters_open['value'] = not filters_open['value']
            if filters_open['value']:
                filters_card.classes(add='wiz-filters-open')
            else:
                filters_card.classes(remove='wiz-filters-open')

        def update_filter_badge() -> None:
            count = (
                (1 if state['tournament_id'] != tournaments[0].id else 0)
                + (1 if state['status'] != 'pending' else 0)
            )
            filter_badge.set_text(str(count))
            filter_badge.set_visibility(count > 0)

        # Below 1024px the CSS collapses .match-filters-card until it carries
        # .wiz-filters-open, so phones need this toggle (hidden on desktop) or
        # the filters are unreachable.
        with ui.row().classes('wiz-filter-toggle full-width items-center'):
            ui.button('Filters', icon='filter_list', on_click=toggle_filters).props('flat color=primary')
            filter_badge = ui.badge('0').props('color=primary')
            filter_badge.set_visibility(False)

        with ui.card().classes('match-filters-card match-filters-card--compact') as filters_card:
            with ui.row().classes('match-filter-row'):
                tournament_select = ui.select(
                    options={t.id: t.name for t in tournaments},
                    label='Tournament',
                    value=state['tournament_id'],
                ).classes('match-filter-field').props('outlined dense options-dense')
                status_select = ui.select(
                    options=_STATUS_OPTIONS,
                    label='Status',
                    value=state['status'],
                ).classes('match-filter-field').props('outlined dense options-dense')

        table_container = ui.column().classes('w-full')

        @ui.refreshable
        def submissions_table() -> None:
            async def render():
                table_container.clear()
                # A background task started from a filter or moderation handler
                # has no slot context, so the client's tenant stash is out of
                # reach and the feature gate would read the tenant as missing.
                with tenant_scope(tenant_id):
                    rows = await service.list_for_moderation(
                        state['tournament_id'],
                        status=_to_status(state['status']),
                        actor=actor,
                    )
                with table_container:
                    if not rows:
                        empty_panel('No submissions match the current filter.', icon='change_history')
                        return
                    with ui.element('div').classes('wiz-tile-grid'):
                        for entry in rows:
                            with ui.card():
                                with ui.row().classes('items-start justify-between w-full'):
                                    with ui.column().classes('q-gutter-xs'):
                                        ui.label(entry.text).style(
                                            'white-space: pre-line; font-family: monospace;'
                                        )
                                        author = entry.author or (
                                            entry.user.preferred_name if entry.user else 'unknown'
                                        )
                                        ui.label(f'by {author}').classes('text-caption text-grey-7')
                                    with ui.column().classes('q-gutter-xs items-end'):
                                        status = (
                                            'Pending' if entry.approved is None
                                            else 'Approved' if entry.approved
                                            else 'Rejected'
                                        )
                                        status_color = (
                                            'orange' if entry.approved is None
                                            else 'positive' if entry.approved
                                            else 'negative'
                                        )
                                        ui.badge(status, color=status_color)
                                        with ui.row().classes('items-center no-wrap gap-1'):
                                            if entry.approved is None:
                                                ui.button(
                                                    'Reject', icon='close',
                                                    on_click=lambda _, eid=entry.id: _moderate(eid, False),
                                                ).props('flat dense no-caps color=negative')
                                                ui.button(
                                                    'Approve', icon='check',
                                                    on_click=lambda _, eid=entry.id: _moderate(eid, True),
                                                ).props('flat dense no-caps color=primary')
                                            with more_menu(f'text by {author}'):
                                                if entry.approved is True:
                                                    menu_action('close', 'Reject',
                                                                lambda _, eid=entry.id: _moderate(eid, False))
                                                    ui.separator()
                                                elif entry.approved is False:
                                                    menu_action('check', 'Approve',
                                                                lambda _, eid=entry.id: _moderate(eid, True))
                                                    ui.separator()
                                                menu_action('delete', 'Delete',
                                                            lambda _, eid=entry.id: _confirm_delete(eid),
                                                            negative=True)
            background_tasks.create(render())

        async def _moderate(text_id: int, approved: bool) -> None:
            try:
                await service.moderate(text_id, approved, actor)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            ui.notify(
                'Approved.' if approved else 'Rejected.',
                color='positive' if approved else 'warning',
            )
            submissions_table.refresh()

        async def _delete(text_id: int) -> None:
            try:
                await service.delete(text_id, actor)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            ui.notify('Deleted that submission.', color='positive')
            submissions_table.refresh()

        def _confirm_delete(text_id: int) -> None:
            async def on_confirm():
                await _delete(text_id)
            ConfirmationDialog(
                message='Are you sure you want to delete this triforce text submission? This action cannot be undone.',
                on_confirm=on_confirm,
                confirm_text='Delete',
                cancel_text='Cancel',
            ).open()

        def on_tournament_change(e):
            state['tournament_id'] = int(e.value)
            update_filter_badge()
            submissions_table.refresh()

        def on_status_change(e):
            state['status'] = e.value
            update_filter_badge()
            submissions_table.refresh()

        tournament_select.on_value_change(on_tournament_change)
        status_select.on_value_change(on_status_change)

        submissions_table()
