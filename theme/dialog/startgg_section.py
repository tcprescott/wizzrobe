"""The start.gg block of the tournament edit dialog's Integrations section.

Link an event, sync it, choose which phase players book from, opt in to
reporting results back, and open the entrant matching dialog.
"""

from nicegui import app, ui

from application.services import StartggService, get_user_from_session
from application.utils.timezone import format_local_display
from theme.busy import busy_click
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.startgg_entrants_dialog import StartggEntrantsDialog
from theme.notify import notify_error
from theme.steady import steady_refreshable

_LAST_PHASE = ''


async def render_startgg_section(tournament, dialog) -> None:
    service = StartggService()
    ui.separator()
    ui.label('start.gg').classes('text-bold')

    @steady_refreshable
    async def body() -> None:
        actor = await get_user_from_session(app.storage.user)
        if not tournament.startgg_event_slug:
            ui.label('Not linked to a start.gg event.').classes('text-caption text-muted')
            link_input = ui.input(
                'start.gg event link',
                placeholder='https://www.start.gg/tournament/…/event/…',
            ).classes('input-full-width')

            async def link() -> None:
                value = (link_input.value or '').strip()
                if not value:
                    with dialog:
                        ui.notify('Paste the link to the start.gg event.', color='warning')
                    return
                try:
                    result = await service.link_event(tournament.id, value, actor)
                except (ValueError, PermissionError) as e:
                    with dialog:
                        notify_error(e)
                    return
                await tournament.refresh_from_db()
                with dialog:
                    ui.notify(f"Linked and synced: {result['entrants']} entrants.", color='positive')
                body.refresh()

            ui.button('Link & Sync', icon='sync', on_click=busy_click('Syncing…', link)).props('flat color=primary')
            return

        synced = (
            format_local_display(tournament.startgg_last_synced_at)
            if tournament.startgg_last_synced_at else 'never'
        )
        with ui.row().classes('items-center gap-2'):
            ui.link(
                tournament.startgg_event_name or tournament.startgg_event_slug,
                service.event_url(tournament) or '', new_tab=True,
            )
        ui.label(f'Last synced: {synced}. Syncs on its own every 2 minutes.') \
            .classes('text-caption text-muted')

        phases = await service.list_phases(tournament.id, actor)
        options = {_LAST_PHASE: 'Last phase (default)'}
        options.update({p.phase_id: p.name for p in phases})

        async def set_phase(e) -> None:
            try:
                await service.set_bookable_phase(tournament.id, e.value or None, actor)
            except (ValueError, PermissionError) as err:
                with dialog:
                    notify_error(err)

        ui.select(
            options, label='Players book matches from',
            value=tournament.startgg_bookable_phase_id or _LAST_PHASE,
            on_change=set_phase,
        ).props('dense outlined').classes('input-full-width')

        async def set_reporting(e) -> None:
            try:
                await service.set_report_results(tournament.id, bool(e.value), actor)
            except (ValueError, PermissionError) as err:
                with dialog:
                    notify_error(err)

        ui.checkbox(
            'Report confirmed results to start.gg', value=tournament.startgg_report_results,
            on_change=set_reporting,
        )
        ui.label(
            "Only when start.gg has no result for that match yet, so an admin's result "
            "always stands. The start.gg account behind the API token needs to be a "
            "reporter on the tournament."
        ).classes('text-caption text-muted')

        async def sync() -> None:
            try:
                result = await service.sync_event(tournament.id, actor, force=True)
            except (ValueError, PermissionError) as e:
                with dialog:
                    notify_error(e)
                return
            await tournament.refresh_from_db()
            with dialog:
                ui.notify(f"Synced {result['entrants']} entrants.", color='positive')
            body.refresh()

        def unlink() -> None:
            async def do_unlink() -> None:
                try:
                    await service.unlink_event(tournament.id, actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                await tournament.refresh_from_db()
                ui.notify('Unlinked from start.gg.', color='positive')
                body.refresh()

            ConfirmationDialog(
                f'Unlink “{tournament.name}” from start.gg? Its pools and bracket are '
                'removed here and players can no longer book from it. The event on '
                'start.gg is untouched, and scheduled matches stay as they are.',
                on_confirm=do_unlink, confirm_text='Unlink',
            ).open()

        with ui.row().classes('items-center gap-2'):
            ui.button('Sync now', icon='sync', on_click=busy_click('Syncing…', sync)).props('flat color=primary')
            ui.button(
                'Entrants', icon='group',
                on_click=lambda: StartggEntrantsDialog(tournament).open(),
            ).props('flat color=primary')
            ui.button('Unlink', icon='link_off', on_click=unlink).props('flat color=negative')

    await body()
