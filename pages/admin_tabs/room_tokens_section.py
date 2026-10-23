"""Room token management, for Settings → Venue.

Staff issue one token per shared machine, tape the URL to it (or bookmark it),
and read back which machines are still using theirs. The URL is shown once, at
issue: only the hash is stored, so a lost one is replaced rather than recovered.

Presentation only — every mutation goes through :class:`RoomTokenService`.
"""


from nicegui import app, ui

from application.services import RoomTokenService, TenantService, get_user_from_session
from application.services.room_token_service import room_seeds_path
from application.tenant_context import require_tenant_id
from application.utils.tenant_urls import tenant_url
from application.utils.timezone import format_local_display
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.section import section_panel
from theme.steady import steady_refreshable


async def render_room_tokens_section(can_edit: bool) -> None:
    service = RoomTokenService()

    async def _actor():
        return await get_user_from_session(app.storage.user)

    async def _url_for(raw_token: str) -> str:
        # Absolute and tenant-qualified: the URL is pasted into a browser on a
        # different machine, where a bare path resolves against nothing.
        tenant = await TenantService.get_by_id(require_tenant_id())
        return tenant_url(tenant, room_seeds_path(raw_token))

    @steady_refreshable
    async def token_list() -> None:
        try:
            tokens = await service.list_tokens(await _actor())
        except PermissionError:
            return
        if not tokens:
            ui.label(
                'No room tokens yet. Issue one to open the seeds board on a '
                'machine nobody signs in on.'
            ).classes('text-caption text-grey')
            return

        for token in tokens:
            revoked = token.revoked_at is not None
            with ui.row().classes('items-center gap-3 q-mb-xs full-width no-wrap'):
                with ui.column().classes('gap-0 col'):
                    with ui.row().classes('items-center gap-2'):
                        ui.label(token.label).classes('text-weight-medium')
                        if revoked:
                            ui.badge('revoked').props('color=grey')
                    used = (
                        f'Last used {format_local_display(token.last_used_at)}'
                        if token.last_used_at else 'Never used'
                    )
                    issuer = token.created_by.preferred_name if token.created_by else 'someone since removed'
                    ui.label(f'{used} · issued by {issuer}').classes('text-caption text-grey')
                if can_edit and not revoked:
                    ui.button(
                        icon='delete',
                        on_click=lambda _, tid=token.id, lbl=token.label: confirm_revoke(tid, lbl),
                    ).props('flat dense color=negative').tooltip('Revoke')

    def confirm_revoke(token_id: int, label: str) -> None:
        async def do_revoke() -> None:
            try:
                await service.revoke(await _actor(), token_id)
                ui.notify('Room token revoked.', color='positive', icon='check_circle')
            except (ValueError, PermissionError) as e:
                notify_error(e)
            token_list.refresh()

        ConfirmationDialog(
            message=(
                f'Revoke “{label}”? The machine using it will get a 404 on its '
                'next refresh, and the URL cannot be brought back.'
            ),
            on_confirm=do_revoke, confirm_text='Revoke',
        ).open()

    async def show_url_dialog(raw_token: str) -> None:
        url = await _url_for(raw_token)
        with form_dialog('The room PC’s URL', card_classes='dialog-card dialog-card--sm') as dialog:
            ui.label(
                'Copy it now — only the hash is stored, so this is the last time '
                'you can see it. Anyone with this URL can read the seeds board, '
                'so bookmark it on the machine rather than posting it anywhere.'
            ).classes('text-warning')
            ui.input(value=url).classes('input-full-width').props('outlined readonly dense')
            with dialog_actions().classes('justify-end'):
                ui.button('Copy', icon='content_copy',
                          on_click=lambda: copy_to_clipboard(url, 'Room URL')).props('flat')
                ui.button('Done', on_click=dialog.close).props('color=primary')
        dialog.open()

    async def issue() -> None:
        try:
            _, raw_token = await service.issue(await _actor(), label_input.value or '')
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        label_input.value = ''
        token_list.refresh()
        await show_url_dialog(raw_token)

    card = await section_panel(
        'Room screens', icon='desktop_windows',
        subtitle=(
            'A read-only board of matches whose seed is rolled and not finished, '
            'for a machine in the room that nobody signs in on. Each token is an '
            'unlisted URL that opens that one page and nothing else, so bookmark '
            'it on the machine it belongs to.'
        ),
    )
    with card.body:
        with ui.column().classes('gap-1 full-width'):
            await token_list()

        if not can_edit:
            return

        with ui.row().classes('items-center gap-3'):
            label_input = ui.input('Label', placeholder='e.g. Room A desk PC') \
                .props('outlined dense maxlength=100').classes('w-64')
            ui.button('Issue token', icon='add', on_click=issue).props('color=primary unelevated no-caps')
