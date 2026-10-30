"""Platform writes: changing what the ``/platform`` page changes, for a super admin.

Kept apart from ``platform`` for the same reason ``match_writes`` is kept apart
from ``matches``: whether a tool writes decides its annotation and whether a
read-only connection is shown it. Every tool is ``Gate.PLATFORM`` with
``write=True``, mirroring ``require_super_admin_write``, and each mirrors one
REST endpoint under ``/platform``, ``/racetime-bots`` or ``/service-health``.

Three things the page does are left out on purpose: setting or removing a
password, creating an account, and anything that takes a racetime bot's client
secret (creating a bot). A credential typed into a chat ends up in a transcript.
Deleting a bot is left out too, since only the page can make it again.
"""

from typing import List, Literal, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.platform import LogLevelResponse
from api.schemas.service_health import ProbeResultResponse
from application.api_scopes import ApiScope
from application.errors import require_found
from application.services import (
    FeatureFlagService,
    LogLevelService,
    PlatformAdminService,
    RacetimeBotService,
    ServiceHealthService,
    TenantService,
)
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    OperationResult,
    PlatformCommunityDetail,
    PlatformFeatureGroup,
    PlatformRacetimeBot,
    PlatformUser,
    UserSummary,
)
from mcpserver.tools.platform import account_row, bot_row, community_detail, group_rows
from models import FeatureFlag


async def platform_create_community(
    name: str,
    slug: str,
    domain: Optional[str] = None,
    discord_guild_id: Optional[str] = None,
) -> PlatformCommunityDetail:
    """Create a community, switched on and with no Staff yet. Super admin only.

    Follow with `platform_grant_community_staff` so someone can run it.
    `slug` is its URL (`/t/<slug>`): lowercase letters, digits and hyphens.
    `discord_guild_id` is the Discord server id, as a string.
    """
    actor = current_actor().user
    tenant = await TenantService.create_tenant(
        actor, name=name, slug=slug, domain=domain, discord_guild_id=_snowflake(discord_guild_id),
    )
    return await community_detail(actor, tenant.id)


async def platform_update_community(
    community: str,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    domain: Optional[str] = None,
    discord_guild_id: Optional[str] = None,
    clear_domain: bool = False,
    clear_discord_guild: bool = False,
) -> PlatformCommunityDetail:
    """Edit a community's name, slug, custom domain or Discord server. Super admin only.

    Omitted fields stay as they are; `clear_domain` / `clear_discord_guild`
    remove those. Changing `slug` breaks every saved link to the community, so
    confirm with the user first. To switch a community off, use
    `platform_set_community_active`.
    """
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    fields: dict = {}
    if name is not None:
        fields['name'] = name
    if slug is not None:
        fields['slug'] = slug
    if clear_domain:
        fields['domain'] = None
    elif domain is not None:
        fields['domain'] = domain
    if clear_discord_guild:
        fields['discord_guild_id'] = None
    elif discord_guild_id is not None:
        fields['discord_guild_id'] = _snowflake(discord_guild_id)
    if fields:
        await TenantService.update_tenant(actor, tenant, **fields)
    return await community_detail(actor, tenant.id)


async def platform_set_community_active(community: str, active: bool) -> PlatformCommunityDetail:
    """Switch a community off (`active=false`) or back on. Super admin only.

    A switched-off community is hidden from everyone, its own Staff included,
    until it's switched back on. Confirm with the user before switching one off.
    """
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    if tenant.is_active != active:
        await TenantService.update_tenant(actor, tenant, is_active=active)
    return await community_detail(actor, tenant.id)


async def platform_grant_community_staff(community: str, user_id: int) -> List[UserSummary]:
    """Make an account a member and Staff of a community, the way a community
    gets its first admin. Returns its Staff afterwards. Super admin only.

    `user_id` comes from `platform_list_users`.
    """
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    staff = await PlatformAdminService().grant_community_staff(actor, tenant.id, user_id)
    return [UserSummary.model_validate(u) for u in staff]


async def platform_set_community_feature_group(
    community: str, group_id: Optional[int] = None,
) -> PlatformCommunityDetail:
    """Put a community on a feature group (tier), or back on the default group
    when `group_id` is omitted. Features can switch on or off for it at once.
    Super admin only. Group ids come from `platform_list_feature_groups`.
    """
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    await FeatureFlagService().assign_tenant_group(actor, tenant.id, group_id)
    return await community_detail(actor, tenant.id)


async def platform_set_community_feature(
    community: str,
    feature: str,
    availability: Literal['inherit', 'on', 'off'],
) -> PlatformCommunityDetail:
    """Override one feature for one community: `on` forces it available, `off`
    forces it off, `inherit` follows its feature group again. Super admin only.

    The community's own on/off choice (Admin → Features) is left alone, so a
    feature forced `on` is live only if its staff haven't switched it off.
    `feature` is a key from `platform_get_community`'s `features`.
    """
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    try:
        flag = FeatureFlag(feature)
    except ValueError as exc:
        raise ValueError(
            f"Unknown feature '{feature}'. Valid: {', '.join(f.value for f in FeatureFlag)}"
        ) from exc
    available = {'inherit': None, 'on': True, 'off': False}[availability]
    await FeatureFlagService().set_availability(actor, tenant.id, flag, available)
    return await community_detail(actor, tenant.id)


async def platform_create_feature_group(
    name: str,
    flags: List[str],
    description: Optional[str] = None,
    is_default: bool = False,
) -> PlatformFeatureGroup:
    """Create a feature group (tier) making `flags` available. Super admin only.

    `is_default` makes it the group every community without one falls back to,
    replacing the old default. Unknown feature keys are dropped.
    """
    actor = current_actor().user
    group = await FeatureFlagService().create_group(
        actor, name=name, flags=flags, description=description, is_default=is_default,
    )
    return await _group(group.id)


async def platform_update_feature_group(
    group_id: int,
    name: Optional[str] = None,
    flags: Optional[List[str]] = None,
    description: Optional[str] = None,
    is_default: Optional[bool] = None,
) -> PlatformFeatureGroup:
    """Edit a feature group. Omitted fields stay as they are; `flags` replaces the
    whole list. Every community on the group gains or loses features at once.
    Super admin only.
    """
    await FeatureFlagService().update_group(
        current_actor().user, group_id,
        name=name, flags=flags, description=description, is_default=is_default,
    )
    return await _group(group_id)


async def platform_delete_feature_group(group_id: int) -> OperationResult:
    """Delete a feature group. Its communities fall back to the default group at
    once, which can switch features off for them. The only default group can't
    be deleted. Super admin only; confirm with the user first.
    """
    await FeatureFlagService().delete_group(current_actor().user, group_id)
    return OperationResult(detail=f'Feature group {group_id} deleted; its communities are on the default group.')


async def _group(group_id: int) -> PlatformFeatureGroup:
    rows = await group_rows(current_actor().user)
    return require_found(next((g for g in rows if g.id == group_id), None), 'Feature group')


async def platform_set_user_active(user_id: int, active: bool) -> PlatformUser:
    """Deactivate (`active=false`) or reactivate an account, platform-wide.
    Super admin only.

    Deactivating signs them out everywhere and stops them signing in to any
    community until reactivated. You can't deactivate yourself. Confirm with
    the user before deactivating anyone.
    """
    actor = current_actor().user
    await PlatformAdminService().set_account_active(actor, user_id, active)
    return await account_row(actor, user_id)


async def platform_update_racetime_bot(
    bot_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    client_id: Optional[str] = None,
    handler_class: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> PlatformRacetimeBot:
    """Edit a racetime bot. Omitted fields stay as they are. Super admin only.

    The client secret can only be changed on the /platform page. Switching a bot
    off (`is_active=false`) stops race rooms opening for every community using it.
    """
    actor = current_actor().user
    bot = await RacetimeBotService().update_bot(
        actor, bot_id, name=name, description=description, category=category,
        client_id=client_id, handler_class=handler_class, is_active=is_active,
    )
    return await bot_row(actor, bot)


async def platform_grant_racetime_bot(bot_id: int, community: str) -> PlatformRacetimeBot:
    """Let a community use a racetime bot for its race rooms. Super admin only."""
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    service = RacetimeBotService()
    await service.grant_tenant(actor, bot_id, tenant.id)
    return await bot_row(actor, await service.get_bot(actor, bot_id))


async def platform_revoke_racetime_bot(bot_id: int, community: str) -> PlatformRacetimeBot:
    """Stop a community using a racetime bot. Its tournaments can't open rooms
    with it any more. Super admin only; confirm with the user first."""
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    service = RacetimeBotService()
    await service.revoke_tenant(actor, bot_id, tenant.id)
    return await bot_row(actor, await service.get_bot(actor, bot_id))


async def platform_restart_racetime_bot(bot_id: int) -> PlatformRacetimeBot:
    """Drop a racetime bot's connection and reconnect it, for every community
    using it. Use when its status is `error` or `disconnected`, or rooms stop
    responding. An inactive bot stays off. Super admin only.
    """
    actor = current_actor().user
    return await bot_row(actor, await RacetimeBotService().restart_connection(actor, bot_id))


async def platform_set_log_level(logger: str, level: str) -> List[LogLevelResponse]:
    """Set how much one server logger writes, straight away and across restarts
    until reset. Super admin only.

    `logger` is a dotted module name (`application.services.match`) or `root`;
    `level` is TRACE, DEBUG, INFO, WARNING, ERROR or CRITICAL. Turn it back with
    `platform_reset_log_level` when done: DEBUG is noisy.
    """
    actor = current_actor().user
    service = LogLevelService()
    await service.set_level(actor, logger, level)
    return [LogLevelResponse.from_row(r) for r in await service.list_rows(actor)]


async def platform_reset_log_level(logger: str) -> List[LogLevelResponse]:
    """Drop the level set on a logger so it inherits its parent's again. Super admin only."""
    actor = current_actor().user
    service = LogLevelService()
    await service.clear(actor, logger)
    return [LogLevelResponse.from_row(r) for r in await service.list_rows(actor)]


async def platform_refresh_service_health() -> List[ProbeResultResponse]:
    """Re-check every outside service now and return the fresh board. Sends no
    alerts. Super admin only."""
    return [ProbeResultResponse.model_validate(r.as_dict()) for r in await ServiceHealthService().refresh(alert=False)]


def _snowflake(value: Optional[str]) -> Optional[int]:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError('discord_guild_id must be a numeric Discord server id') from exc


def register_tools(mcp: MCPServer) -> None:
    def write_tool(fn, title: str, *, destructive: bool = False) -> None:
        register(mcp, fn, gate=Gate.PLATFORM, scope=ApiScope.STAFF_WRITE, write=True, title=title, destructive=destructive)

    write_tool(platform_create_community, 'Create a community')
    write_tool(platform_update_community, 'Edit a community')
    write_tool(platform_set_community_active, 'Switch a community on or off', destructive=True)
    write_tool(platform_grant_community_staff, 'Make someone Staff of a community')
    write_tool(platform_set_community_feature_group, "Set a community's feature group")
    write_tool(platform_set_community_feature, 'Override a feature for a community')
    write_tool(platform_create_feature_group, 'Create a feature group')
    write_tool(platform_update_feature_group, 'Edit a feature group')
    write_tool(platform_delete_feature_group, 'Delete a feature group', destructive=True)
    write_tool(platform_set_user_active, 'Deactivate or reactivate an account', destructive=True)
    write_tool(platform_update_racetime_bot, 'Edit a racetime bot')
    write_tool(platform_grant_racetime_bot, 'Grant a racetime bot to a community')
    write_tool(platform_revoke_racetime_bot, 'Revoke a racetime bot from a community', destructive=True)
    write_tool(platform_restart_racetime_bot, 'Restart a racetime bot')
    write_tool(platform_set_log_level, 'Set a log level')
    write_tool(platform_reset_log_level, 'Reset a log level')
    write_tool(platform_refresh_service_health, 'Refresh the health board')
