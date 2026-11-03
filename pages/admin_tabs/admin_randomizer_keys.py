"""Admin → Settings → Integrations, Randomizer Keys: this community's own randomizer API credentials.

The keyed randomizer backends (OoT Randomizer, Map Rando, DK64 Randomizer) roll
against upstreams that require a credential. Those are per community, not per
deployment: STAFF/``PRESET_MANAGER`` enter the key their community was issued and
is bound by, and a randomizer stays hidden from the Presets and Tournament
selectors until its key is set.

Values are **write-only** — the stored secret is never rendered back into the
form; a blank submit is rejected and clearing is an explicit action.
"""

from nicegui import app, background_tasks, context, ui

from application.services import RandomizerCredentialService, get_user_from_session
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import wire_tab_refresh


async def admin_randomizer_keys_page() -> None:
    service = RandomizerCredentialService()

    async def _current():
        return await get_user_from_session(app.storage.user)

    async def save(randomizer: str, key: str, value_input, client) -> None:
        with client:
            try:
                await service.set_credential(await _current(), randomizer, key, value_input.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            # Never leave the typed secret sitting in client state.
            value_input.value = ''
            ui.notify('Credential saved', color='positive')
            credentials_section.refresh()

    async def clear(randomizer: str, key: str, client) -> None:
        with client:
            try:
                await service.clear_credential(await _current(), randomizer, key)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Credential cleared', color='positive')
            credentials_section.refresh()

    def confirm_clear(row: dict) -> None:
        client = context.client
        ConfirmationDialog(
            f"Clear the {row['label']} for {row['randomizer']}? Seeds for it can’t be rolled, "
            'and it disappears from the Presets and Tournament pickers, until someone '
            'enters a key again.',
            on_confirm=lambda: background_tasks.create(
                clear(row['randomizer'], row['key'], client)
            ),
            confirm_text='Clear', title='Clear credential?',
        ).open()

    @steady_refreshable
    async def credentials_section() -> None:
        try:
            rows = await service.list_status(await _current())
        except PermissionError as e:
            ui.notify(str(e), color='warning')
            return
        for row in rows:
            with ui.card().classes('w-full'):
                with ui.row().classes('items-center justify-between w-full no-wrap'):
                    with ui.column().classes('gap-0'):
                        ui.label(f"{row['label']} ({row['randomizer']})").classes(
                            'text-subtitle2 text-bold'
                        )
                        ui.label(row['help_text']).classes('text-caption text-grey')
                    with ui.column().classes('items-end gap-1'):
                        if row['configured']:
                            ui.label('Configured').classes('text-caption text-positive')
                        else:
                            ui.label('Not set').classes('text-caption text-grey')
                value_input = ui.input(row['label']).props(
                    'type=password autocomplete=off dense outlined'
                ).classes('w-full')
                if row['configured']:
                    value_input.props('hint="Entering a new value replaces the stored one"')
                with ui.element('div').classes('wiz-pair-actions'):
                    if row['configured']:
                        ui.button(
                            'Clear', icon='delete',
                            on_click=lambda r=row: confirm_clear(r),
                        ).props('flat color=negative')
                    ui.button(
                        'Save', icon='save',
                        on_click=lambda r=row, i=value_input: background_tasks.create(
                            save(r['randomizer'], r['key'], i, context.client)
                        ),
                    ).props('color=primary')

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Randomizer Keys',
            "Credentials for the randomizers whose upstream requires one. These are "
            "your community's own keys, issued to you and used only for your seeds — "
            "a randomizer stays out of the Presets and Tournament seed-generator "
            "selectors until its key is set. Stored values are never shown again.",
        )

        await credentials_section()

    wire_tab_refresh('Randomizer Keys', credentials_section.refresh)
