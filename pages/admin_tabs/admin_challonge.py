"""The Challonge card of Admin → Settings → Integrations (STAFF).

Manages the shared Wizzrobe Challonge service-account connection. Linking,
syncing, stage changes, entrants and unlinking happen per tournament from the
row ⋮ on Admin → Tournaments.
"""

from nicegui import app, ui

from application.services import AuthService, ChallongeService, get_user_from_session
from application.tenant_context import require_tenant_id
from application.utils.timezone import format_local_display
from models import Tournament
from theme.banner import banner
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help import help_icon
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable

_AUTO_SYNC_LABELS = {
    0: 'Off', 15: 'Every 15 minutes', 30: 'Every 30 minutes',
    60: 'Every hour', 120: 'Every 2 hours', 360: 'Every 6 hours',
}


async def admin_challonge_connection_page(tournaments_url: str = '/admin/tournaments') -> None:
    actor = await get_user_from_session(app.storage.user)
    is_staff = await AuthService.is_staff(actor)
    service = ChallongeService()

    with ui.column().classes('page-container-narrow') as page_root:
        with page_header(
            'Challonge',
            'Wizzrobe signs in to Challonge as this account to read brackets, add '
            'players who sign up, and start or finalize stages.',
        ):
            await help_icon('admin-challonge-sync', user=actor)

        @steady_refreshable
        async def connection_card() -> None:
            status = await service.get_connection_status()
            with ui.column().classes('w-full gap-2'):
                if not status.get('configured'):
                    ui.label('Challonge is not configured.').classes('text-error')
                    ui.label(
                        'The Challonge integration hasn’t been set up for this '
                        'deployment yet. Contact your platform administrator to '
                        'enable it.'
                    ).classes('text-muted')
                    return

                if status.get('connected'):
                    with ui.row().classes('items-center'):
                        ui.icon('link', color='positive')
                        ui.label(f"Connected as {status.get('challonge_username') or 'unknown'}").classes('text-bold')
                    await communities_row()
                    if status.get('scopes'):
                        ui.label(f"Scopes: {status['scopes']}").classes('text-caption text-muted')
                    if not await service.can_add_participants():
                        ui.label(
                            "Players can't sign up for Challonge brackets from the "
                            'Tournaments tab until you reconnect Challonge below.'
                        ).classes('text-caption text-error')
                    if status.get('token_expires_at'):
                        ui.label(
                            f"Token expires: {format_local_display(status['token_expires_at'])}"
                        ).classes('text-caption text-muted')
                    ui.label(
                        f"API requests this month: {status.get('request_usage', 0):,}"
                    ).classes('text-caption text-muted')
                    await linked_row()
                    await auto_sync_row()
                    if is_staff:
                        connect_controls('Reconnect', can_disconnect=True)
                else:
                    ui.label('The Wizzrobe Challonge account is not connected.').classes('text-muted')
                    await communities_row()
                    if is_staff:
                        connect_controls('Connect Challonge')

        async def linked_row() -> None:
            count = await Tournament.filter(
                challonge_tournament_id__isnull=False, tenant_id=require_tenant_id(),
            ).count()
            with ui.row().classes('items-baseline gap-1'):
                if count:
                    noun = 'tournament is' if count == 1 else 'tournaments are'
                    ui.label(f'{count} {noun} linked to Challonge.').classes('text-caption text-muted')
                    ui.link('Manage them on Tournaments', tournaments_url).classes('text-caption')
                else:
                    ui.label('No tournaments are linked yet.').classes('text-caption text-muted')
                    ui.link('Link one from its ⋮ on Tournaments', tournaments_url).classes('text-caption')

        async def auto_sync_row() -> None:
            minutes = await service.auto_sync_minutes()

            async def change(e) -> None:
                try:
                    await service.set_auto_sync_minutes(int(e.value), actor)
                except (ValueError, PermissionError) as err:
                    notify_error(err)
                await connection_card.refresh()

            with ui.column().classes('w-full gap-1 q-mt-sm'):
                ui.select(
                    options=_AUTO_SYNC_LABELS, value=minutes,
                    label='Sync automatically', on_change=change,
                ).props('outlined dense').classes('w-56').set_enabled(is_staff)
                ui.label(
                    'Picks up results reported on Challonge without anyone pressing '
                    'Sync. Finished brackets are skipped.'
                ).classes('text-caption text-muted')
                if minutes:
                    estimate = await service.auto_sync_estimate(minutes)
                    noun = 'bracket' if estimate.tournaments == 1 else 'brackets'
                    with banner(
                        f'Each sync uses about 2 API requests per bracket. '
                        f'{_AUTO_SYNC_LABELS[minutes]} with {estimate.tournaments} '
                        f'unfinished {noun} is about {estimate.requests_per_day:,} '
                        f'requests a day.',
                        tone='warning',
                    ):
                        pass

        async def communities_row() -> None:
            communities = await service.remembered_communities()
            if not communities:
                return
            with ui.row().classes('items-center gap-1'):
                ui.label('Communities').classes('text-caption text-muted q-mr-xs')
                for c in communities:
                    count = f'{c.tournament_count} tournament' + ('' if c.tournament_count == 1 else 's')
                    chip = ui.chip(
                        f'{c.slug} · {count}' if c.tournament_count else c.slug,
                        removable=is_staff and c.removable, color=None,
                    ).props('dense').classes('wiz-chip wiz-chip--neutral')
                    if is_staff and not c.removable:
                        chip.tooltip('Linked tournaments use this community, so it stays.')
                    chip.on('remove', lambda _, slug=c.slug: remove_community(slug))

        def remove_community(slug: str) -> None:
            async def do_remove() -> None:
                try:
                    await service.remove_community(actor, slug)
                    ui.notify(f'Removed {slug}.', color='info')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                await connection_card.refresh()

            # Opened outside the card: the refresh below would delete a dialog
            # built inside it. The chip hides itself client-side on tap, so the
            # refresh puts it back for a cancelled dialog.
            with page_root:
                ConfirmationDialog(
                    message=f'Stop asking Challonge for {slug} when you reconnect? '
                            'You can add it again by typing it in.',
                    on_confirm=do_remove, confirm_text='Remove', title=f'Remove {slug}?',
                ).open()
            connection_card.refresh()

        def connect_controls(label: str, can_disconnect: bool = False) -> None:
            with ui.row().classes('items-center w-full'):
                community = ui.input(
                    'Add a community (optional)',
                    placeholder='speedgaming',
                ).props('dense outlined clearable').classes('grow').tooltip(
                    'The subdomain of an organization Challonge page, e.g. '
                    'speedgaming for speedgaming.challonge.com. Needed to link '
                    'its tournaments; sign in as one of its admins. Communities '
                    'you have connected before are asked for again automatically.'
                )

                def go() -> None:
                    typed = (community.value or '').strip()
                    slug = ChallongeService.normalize_community(typed)
                    if typed and slug is None:
                        ui.notify(
                            "That doesn't look like a Challonge community subdomain.",
                            color='warning',
                        )
                        return
                    ui.navigate.to(
                        f'/challonge/connect?community={slug}' if slug else '/challonge/connect'
                    )

                ui.button(label, icon='link', on_click=go).props('color=primary')
                if can_disconnect:
                    with more_menu('Challonge'):
                        menu_action('link_off', 'Disconnect…', disconnect, negative=True)

        def disconnect() -> None:
            async def do_disconnect() -> None:
                try:
                    await service.disconnect(actor)
                    ui.notify('Challonge disconnected.', color='positive')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                await connection_card.refresh()

            ConfirmationDialog(
                message='Disconnect Challonge? Every linked tournament stops syncing '
                        'until someone reconnects.',
                on_confirm=do_disconnect, confirm_text='Disconnect', title='Disconnect Challonge?',
            ).open()

        await connection_card()
