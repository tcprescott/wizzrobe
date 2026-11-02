"""Settings → Members: who can join this community, and what outsiders see."""

from nicegui import ui

from application.services import (
    SystemConfigService,
    TenantMembershipService,
    TenantService,
)
from application.services.join_mode_copy import join_mode_help, offered_join_modes
from application.services.tenant_membership_service import JoinMode
from application.tenant_context import require_tenant_id
from application.utils.app_links import USERS, admin_url
from pages.admin_tabs.settings._common import current_actor
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel


async def _joining_card(can_edit: bool) -> None:
    join_mode = await TenantMembershipService.get_join_mode()
    discord_invite_url = await SystemConfigService.get_discord_invite_url() or ''
    tenant = await TenantService.get_by_id(require_tenant_id())
    guild_linked = bool(tenant and tenant.discord_guild_id)

    card = await section_panel(
        'Joining this community', icon='meeting_room',
        subtitle='Who becomes a member, and what the join page shows everyone else.',
    )
    with card.body.classes('gap-3'):
        join_mode_input = ui.radio(
            {m.value: label for m, label in offered_join_modes(join_mode, guild_linked).items()},
            value=join_mode.value,
        ).props('dense')
        join_mode_caption = ui.label('').classes('wiz-field-hint')

        def describe_mode() -> None:
            join_mode_caption.text = join_mode_help(JoinMode(join_mode_input.value), guild_linked)

        describe_mode()
        join_mode_input.on_value_change(lambda _: describe_mode())

        ui.button(
            'Open the join queue', icon='manage_accounts',
            on_click=lambda: ui.navigate.to(admin_url(USERS)),
        ).props('flat dense no-caps color=primary').classes('self-start')

        ui.separator()
        discord_invite_input = ui.input(
            'Discord invite link', value=discord_invite_url,
            placeholder='https://discord.gg/abc123',
        ).classes('w-full').props('dense hint="Shown on the join page. Use one that doesn’t expire."')

    async def save_join() -> bool:
        try:
            actor = await current_actor()
            invite_raw = SystemConfigService.normalize_discord_invite_url(
                discord_invite_input.value,
            )
            await TenantMembershipService.set_join_mode(actor, JoinMode(join_mode_input.value))
            await SystemConfigService.set_discord_invite_url(invite_raw, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        discord_invite_input.value = invite_raw
        ui.notify('Join settings saved', color='positive')
        return True

    controls = (join_mode_input, discord_invite_input)
    with card.panel:
        SaveFooter(save_join, enabled=can_edit).watch(*controls)
    if not can_edit:
        for control in controls:
            control.disable()


async def members_settings_pane(can_edit: bool) -> None:
    await _joining_card(can_edit)
