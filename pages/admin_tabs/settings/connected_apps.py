"""Settings → Connected apps: the outside apps members have connected here.

One row per app with how many members connected it, never who. The ⋮ blocks
an app in this community only: everyone here is disconnected from it and it
stops appearing for this community when someone signs in to it. A platform
admin's disable is the everywhere switch; this is the community's own.

The switch at the bottom is the Third-party Apps feature itself, the same one
as under Features, so staff can stop every app here without hunting for it.
"""

from nicegui import ui

from application.services import FeatureFlagService, OAuthGrantService
from application.services.oauth_grant_service import CommunityApp
from models import FeatureFlag
from pages.admin_tabs.settings._common import current_actor, field_hint
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.section import section_panel
from theme.steady import steady_refreshable


async def connected_apps_settings_pane(can_edit: bool) -> None:
    service = OAuthGrantService()
    actor = await current_actor()

    card = await section_panel(
        'Connected apps', icon='apps',
        subtitle=(
            'Apps members of this community have connected. Blocking one disconnects '
            'everyone here and stops new connections.'
        ),
        user=actor,
    )

    @steady_refreshable
    async def app_rows() -> None:
        try:
            apps = await service.list_community_apps(actor)
        except (ValueError, PermissionError) as e:
            ui.label(str(e)).classes('text-muted')
            return
        if not apps:
            ui.label('No member has connected an app yet.').classes('text-muted')
        for i, row in enumerate(apps):
            with ui.element('div').classes('wiz-pwebhook-row' + (' wiz-pwebhook-row--first' if i == 0 else '')):
                with ui.column().classes('gap-0 col min-w-0'):
                    with ui.row().classes('items-center gap-1'):
                        ui.label(row.name).classes('text-weight-medium')
                        ui.badge('Unverified').props('outline color=warning') \
                            .tooltip("Wizzrobe hasn't checked who runs this app")
                        if row.blocked:
                            ui.badge('Blocked here').props('color=negative')
                        if row.disabled:
                            ui.badge('Disabled by Wizzrobe').props('outline color=grey-8')
                    noun = 'member' if row.members == 1 else 'members'
                    host = f' · {row.homepage_host}' if row.homepage_host else ''
                    ui.label(f'by {row.owner_name} · {row.members} {noun}{host}') \
                        .classes('text-caption text-muted')
                if can_edit:
                    with more_menu(row.name):
                        if row.blocked:
                            menu_action('check_circle', 'Unblock', lambda r=row: unblock(r))
                        else:
                            menu_action('block', 'Block here…', lambda r=row: confirm_block(r), negative=True)

    def confirm_block(row: CommunityApp) -> None:
        async def do_block() -> None:
            try:
                ended = await service.block_app_here(await current_actor(), row.app_id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            noun = 'member' if ended == 1 else 'members'
            ui.notify(f'{row.name} blocked, {ended} {noun} disconnected', color='positive')
            app_rows.refresh()

        noun = 'member' if row.members == 1 else 'members'
        ConfirmationDialog(
            title=f'Block {row.name} here?',
            message=(
                f'{row.members} {noun} are disconnected from it now, and nobody here can connect '
                'it again until you unblock it. Other communities are not affected.'
            ),
            confirm_text='Block',
            on_confirm=do_block,
        ).open()

    async def unblock(row: CommunityApp) -> None:
        try:
            await service.unblock_app_here(await current_actor(), row.app_id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(f'{row.name} unblocked. Members can connect it again.', color='positive')
        app_rows.refresh()

    def confirm_off() -> None:
        async def do_off() -> None:
            try:
                await FeatureFlagService().set_tenant_enabled(await current_actor(), FeatureFlag.THIRD_PARTY_APPS, False)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Apps turned off for this community', color='positive')
            ui.navigate.reload()

        ConfirmationDialog(
            title='Turn off apps for this community?',
            message=(
                'Every connected app stops working here at once. Turn it back on under '
                'Settings → Features, and the connections that were working come back.'
            ),
            confirm_text='Turn off',
            on_confirm=do_off,
        ).open()

    def on_switch(value: bool) -> None:
        if not value:
            apps_switch.set_value(True)
            confirm_off()

    with card.body:
        await app_rows()
        ui.separator().classes('q-my-sm')
        with ui.column().classes('gap-1'):
            apps_switch = ui.switch('Let members connect apps', value=True).props('dense')
            apps_switch.on_value_change(lambda e: on_switch(bool(e.value)))
            field_hint('Off stops every app in this community.')
            if not can_edit:
                apps_switch.disable()
