"""The start.gg card of Admin → Settings → Integrations (STAFF).

The community's start.gg sign-in, and for each linked tournament whether that
account can report results there yet. Linking, syncing and entrants happen per
tournament from Edit tournament → Integrations.
"""

from nicegui import app, ui

from application.services import AuthService, StartggConnectionService, get_user_from_session
from application.utils.timezone import format_local_display
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable

_ACCESS_CHIPS = {
    True: ('Can report', 'wiz-chip--ok'),
    False: ('Read only', 'wiz-chip--pending'),
    None: ("Couldn't check", 'wiz-chip--neutral'),
}


async def admin_startgg_connection_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    is_staff = await AuthService.is_staff(actor)
    service = StartggConnectionService()

    with ui.column().classes('page-container-narrow'):
        page_header(
            'start.gg',
            'Wizzrobe signs in to start.gg as this account to read linked events and '
            'report confirmed results.',
        )

        @steady_refreshable
        async def connection_card() -> None:
            status = await service.get_status()
            with ui.column().classes('w-full gap-2'):
                if not status['connected']:
                    not_connected(status)
                    return
                with ui.row().classes('items-center gap-2'):
                    ui.icon('link', color='positive')
                    ui.label(f"Connected as {status.get('username') or 'unknown'}").classes('text-bold')
                if status.get('expired'):
                    ui.label(
                        "The sign-in has lapsed and couldn't be renewed. Reconnect to keep "
                        'reporting results.'
                    ).classes('text-caption text-error')
                else:
                    ui.label(
                        f"Renews automatically. Last renewed {format_local_display(status['renewed_at'])}."
                    ).classes('text-caption text-muted')
                await access_list(status.get('username') or 'This account')
                if is_staff:
                    controls('Reconnect', can_disconnect=True)

        def not_connected(status: dict) -> None:
            if not status['configured']:
                ui.label("start.gg sign-in isn't set up on this server yet.").classes('text-muted')
                if status['platform_token']:
                    ui.label(
                        'Linked events still sync with the server’s start.gg token, but '
                        'results are reported only with a community sign-in.'
                    ).classes('text-caption text-muted')
                return
            ui.label('start.gg is not connected for this community.').classes('text-muted')
            ui.label(
                'Sign in with a start.gg account that is an admin (Reporter or higher) on '
                'your start.gg tournaments, so confirmed results can be reported there.'
            ).classes('text-caption text-muted')
            if is_staff:
                controls('Connect start.gg')

        async def access_list(account: str) -> None:
            rows = await service.access_for_linked(actor)
            ui.label('Linked tournaments').classes('text-caption text-bold q-mt-sm')
            if not rows:
                ui.label(
                    'No tournaments are linked to start.gg yet. Link one from Edit tournament → Integrations.'
                ).classes('text-caption text-muted')
                return
            with ui.column().classes('w-full gap-0'):
                for row in rows:
                    label, chip_class = _ACCESS_CHIPS[row.can_report]
                    with ui.column().classes('w-full gap-1 q-py-sm'):
                        with ui.row().classes('items-center gap-2'):
                            ui.link(row.name, row.event_url, new_tab=True).classes('text-bold')
                            ui.chip(label, color=None).props('dense').classes(f'wiz-chip {chip_class}')
                            ui.label(
                                'Reporting confirmed results: ' + ('on' if row.reports_results else 'off')
                            ).classes('text-caption text-muted')
                        if row.can_report is False:
                            ui.label(
                                f"{account} isn't an admin on this start.gg tournament. Ask one "
                                'of its admins to add them as Reporter under Settings → Admin '
                                'Permissions, then check again.'
                            ).classes('text-caption text-muted')
                        elif row.can_report is None:
                            ui.label(
                                "start.gg didn't answer, so this couldn't be checked. Try again shortly."
                            ).classes('text-caption text-muted')
            if any(r.can_report is not True for r in rows):
                ui.button('Check again', icon='refresh', on_click=connection_card.refresh).props('flat dense')

        def controls(label: str, can_disconnect: bool = False) -> None:
            with ui.row().classes('items-center w-full justify-end gap-2'):
                ui.button(label, icon='link', on_click=lambda: ui.navigate.to('/startgg/connect')).props('color=primary')
                if can_disconnect:
                    with more_menu('start.gg'):
                        menu_action('link_off', 'Disconnect…', disconnect, negative=True)

        def disconnect() -> None:
            async def do_disconnect() -> None:
                try:
                    await service.disconnect(actor)
                    ui.notify('start.gg disconnected.', color='positive')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                await connection_card.refresh()

            ConfirmationDialog(
                message='Disconnect start.gg? Results stop being reported to start.gg, and '
                        'syncs fall back to the server’s token if it has one.',
                on_confirm=do_disconnect, confirm_text='Disconnect', title='Disconnect start.gg?',
            ).open()

        await connection_card()
