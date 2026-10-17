"""Admin Challonge integration page (STAFF).

Manages the shared Wizzrobe Challonge service-account connection and lists the
tournaments linked to a Challonge bracket with a per-tournament Sync action.
"""

from nicegui import app, ui

from application.services import AuthService, ChallongeService, get_user_from_session
from application.tenant_context import require_tenant_id
from application.utils.timezone import format_local_display
from models import Tournament
from theme.busy import busy_click
from theme.dialog.challonge_participants_dialog import ChallongeParticipantsDialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable


async def admin_challonge_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    is_staff = await AuthService.is_staff(actor)
    service = ChallongeService()

    with ui.column().classes('page-container-narrow') as page_root:
        with page_header('Challonge Integration'):
            await help_icon('admin-challonge-sync', user=actor)

        @steady_refreshable
        async def connection_card() -> None:
            status = await service.get_connection_status()
            with ui.card().classes('w-full'):
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
                    quota = status.get('request_quota')
                    used = status.get('request_usage', 0)
                    if quota:
                        warn = used >= quota * 0.8
                        ui.label(
                            f"API requests this month: {used} / {quota}"
                        ).classes('text-caption ' + ('text-error' if warn else 'text-muted'))
                    if is_staff:
                        connect_controls('Reconnect')
                        ui.button('Disconnect', icon='link_off', on_click=disconnect).props('flat color=negative')
                else:
                    ui.label('The Wizzrobe Challonge account is not connected.').classes('text-muted')
                    await communities_row()
                    if is_staff:
                        connect_controls('Connect Challonge')

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

        def connect_controls(label: str) -> None:
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

        ui.label('Linked tournaments').classes('section-title q-mt-md')

        @steady_refreshable
        async def linked_tournaments() -> None:
            tournaments = await Tournament.filter(challonge_tournament_id__isnull=False, tenant_id=require_tenant_id()).order_by('name')
            if not tournaments:
                empty_panel('No tournaments are linked to Challonge yet. Link one from its edit dialog '
                            'on the Tournaments tab.', icon='link_off')
                return
            can_change_stages = await service.can_change_stages()
            with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
                for t in tournaments:
                    with ui.card(), ui.row().classes('items-center w-full no-wrap'):
                        with ui.column().classes('gap-0 col'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label(t.name).classes('text-bold')
                                if t.challonge_tournament_url:
                                    ui.link('bracket', t.challonge_tournament_url,
                                            new_tab=True).classes('text-caption')
                                stage = service.stage_label_for(t)
                                if stage:
                                    ui.badge(stage).props('outline color=primary')
                            synced = (
                                format_local_display(t.challonge_last_synced_at)
                                if t.challonge_last_synced_at else 'never'
                            )
                            ui.label(f'Last synced: {synced}').classes('text-caption text-muted')
                        ui.button(
                            'Sync', icon='sync',
                            # Return the coroutine so NiceGUI awaits it in the button's
                            # slot; a bare background task has no slot, so the notify /
                            # refresh calls in sync_one would raise and be lost.
                            on_click=busy_click('Syncing…', lambda _=None, tid=t.id: sync_one(tid)),
                        ).props('flat color=primary')
                        with more_menu(t.name):
                            for action in service.stage_actions_for(t):
                                item = menu_action(
                                    'skip_next', action.label,
                                    lambda _=None, row=t, a=action: confirm_stage(row, a),
                                )
                                if not can_change_stages:
                                    item.disable()
                                    item.tooltip(
                                        'Reconnect Challonge above to let Wizzrobe change '
                                        'tournament stages'
                                    )
                            menu_action('group', 'Entrants',
                                        lambda _=None, row=t: ChallongeParticipantsDialog(row).open())
                            if is_staff:
                                ui.separator()
                                menu_action('link_off', 'Unlink from Challonge',
                                            lambda _=None, row=t: confirm_unlink(row), negative=True)

        def confirm_stage(tournament, action) -> None:
            async def do_advance() -> None:
                try:
                    await service.advance_stage(tournament.id, action.key, actor)
                    ui.notify(f'{tournament.name}: {action.label.lower()} done.', color='positive')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                await linked_tournaments.refresh()
                await connection_card.refresh()

            ConfirmationDialog(
                action.confirm, on_confirm=do_advance, confirm_text=action.label,
                tone='primary', title=action.label, busy_text='Updating Challonge…',
            ).open()

        def confirm_unlink(tournament) -> None:
            """The migration path off Challonge, which had no control at all.

            A tournament uses a native bracket or a Challonge link and never both,
            and nothing else in the product clears the link — so a community
            moving onto native brackets had to abandon the tournament and build a
            duplicate.
            """
            async def do_unlink() -> None:
                try:
                    await service.unlink_tournament(tournament.id, actor)
                    ui.notify(
                        f'{tournament.name} unlinked from Challonge.', color='positive',
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                await linked_tournaments.refresh()

            confirm = ConfirmationDialog(
                f'Unlink “{tournament.name}” from Challonge? Its mirrored '
                'participants and bracket matches are removed here — the bracket on '
                'Challonge itself is untouched, and re-linking re-imports it. '
                'Scheduled matches stay exactly as they are, and the tournament '
                'becomes eligible for a native bracket.',
                on_confirm=do_unlink,
                confirm_text='Unlink',
            )
            confirm.open()

        async def sync_one(tournament_id: int) -> None:
            try:
                result = await service.sync_bracket(tournament_id, actor, force=True)
                ui.notify(
                    f"Synced {result['participants']} participants, {result['matches']} matches.",
                    color='positive',
                )
            except ValueError as e:
                ui.notify(str(e), color='warning')
            except Exception as e:
                ui.notify(f"Couldn't sync with Challonge: {e}", color='negative')
            await linked_tournaments.refresh()
            await connection_card.refresh()

        await linked_tournaments()
