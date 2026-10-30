"""Settings → Members: who can join this community, and what outsiders see."""

from nicegui import ui

from application.services import (
    SystemConfigService,
    TenantMembershipService,
    TenantService,
)
from application.services.tenant_membership_service import JoinMode
from application.tenant_context import require_tenant_id
from application.utils.app_links import USERS, admin_url
from pages.admin_tabs.settings._common import current_actor
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel

JOIN_MODE_LABELS = {
    JoinMode.OPEN: 'Anyone who signs in',
    JoinMode.DISCORD_OR_REQUEST: 'Discord server members, and others can ask',
    JoinMode.DISCORD: 'Discord server members only',
    JoinMode.REQUEST: 'Everyone asks, and staff approve',
    JoinMode.STAFF_ONLY: 'Only people staff add',
}

JOIN_MODE_HELP = {
    JoinMode.OPEN: (
        'Signing in with Discord makes someone a member on the spot. Nobody gets '
        'a role from it. Removing a member doesn’t keep them out: they’re back '
        'the next time they open the community.'
    ),
    JoinMode.DISCORD_OR_REQUEST: (
        'Members of the linked Discord server get in the first time they open the '
        'community. Anyone else can ask from the join page, and Staff decide on '
        'the Users tab.'
    ),
    JoinMode.DISCORD: (
        'Members of the linked Discord server get in the first time they open the '
        'community. Nobody else can ask.'
    ),
    JoinMode.REQUEST: (
        'People ask from the join page and Staff approve or decline them on the '
        'Users tab. Requests already waiting stay there whichever option you pick.'
    ),
    JoinMode.STAFF_ONLY: (
        'The join page takes no requests. Staff add people from the Users tab.'
    ),
}


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
        modes = {
            mode: label for mode, label in JOIN_MODE_LABELS.items()
            if guild_linked or not mode.uses_discord or mode is join_mode
        }
        join_mode_input = ui.radio(
            {m.value: label for m, label in modes.items()}, value=join_mode.value,
        ).props('dense')
        join_mode_caption = ui.label('').classes('wiz-field-hint')

        def describe_mode() -> None:
            mode = JoinMode(join_mode_input.value)
            caption = JOIN_MODE_HELP[mode]
            if mode.uses_discord and not guild_linked:
                caption = 'No Discord server is linked, so nobody gets in this way until one is.'
            elif not guild_linked and mode is not JoinMode.OPEN:
                caption += ' Link a Discord server under Settings → Discord to let its members in.'
            join_mode_caption.text = caption

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
