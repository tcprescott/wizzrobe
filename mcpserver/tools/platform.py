"""Platform reads: the ``/platform`` page's tabs, for a super admin.

Every tool here is ``Gate.PLATFORM``: it names no community through ``tenant``
(a platform tool reaches all of them), requires the global super-admin role,
and is not even listed to anyone else. Each mirrors a ``GET /platform/...`` or
super-admin endpoint, and the owning services check the role a second time.

Communities are named by slug in a ``community`` argument rather than
``tenant``, because ``tenant`` resolves active communities only and a super
admin needs to reach a switched-off one to switch it back on.
"""

from typing import Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.platform import LogLevelResponse
from api.schemas.service_health import ProbeResultResponse
from application.api_scopes import ApiScope
from application.services import (
    CommunityOverview,
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
    PlatformCommunity,
    PlatformCommunityDetail,
    PlatformFeature,
    PlatformFeatureGroup,
    PlatformRacetimeBot,
    PlatformUser,
    UserSummary,
)
from mcpserver.tools._args import clamp
from models import RacetimeBot, User

# --- shared shapes, also used by platform_writes ------------------------------


def community_row(overview: CommunityOverview) -> PlatformCommunity:
    tenant = overview.tenant
    return PlatformCommunity(
        id=tenant.id, slug=tenant.slug, name=tenant.name, is_active=tenant.is_active,
        domain=tenant.domain, discord_guild_id=tenant.discord_guild_id,
        feature_group=overview.feature_group,
        setup_ready=overview.setup_ready, setup_missing=overview.setup_missing,
    )


async def community_detail(actor: User, tenant_id: int) -> PlatformCommunityDetail:
    overview = await PlatformAdminService().get_community(actor, tenant_id)
    staff = await TenantService.list_staff(actor, tenant_id)
    features = await FeatureFlagService().list_for_tenant(actor, tenant_id)
    return PlatformCommunityDetail(
        **community_row(overview).model_dump(),
        staff=[UserSummary.model_validate(u) for u in staff],
        features=[PlatformFeature.model_validate(f) for f in features],
    )


async def account_row(actor: User, user_id: int) -> PlatformUser:
    return _account(await PlatformAdminService().get_account(actor, user_id))


def _account(overview) -> PlatformUser:
    user = overview.user
    return PlatformUser(
        id=user.id, username=user.username, display_name=user.display_name,
        discord_id=user.discord_id, login_name=user.login_name or None,
        is_active=user.is_active, is_super_admin=overview.is_super_admin,
        communities=overview.communities,
    )


async def bot_row(actor: User, bot: RacetimeBot, slugs: Optional[Dict[int, str]] = None) -> PlatformRacetimeBot:
    service = RacetimeBotService()
    if slugs is None:
        slugs = {t.id: t.slug for t in await TenantService.list_tenants()}
    grants = await service.list_grants(actor, bot.id)
    data = service.serialize(bot)
    data['status'] = getattr(bot.status, 'value', bot.status)
    return PlatformRacetimeBot(
        **data,
        communities=sorted(slugs[g.tenant_id] for g in grants if g.is_active and g.tenant_id in slugs),  # type: ignore[attr-defined]
    )


async def group_rows(actor: User) -> List[PlatformFeatureGroup]:
    return [
        PlatformFeatureGroup(**row, community_count=row['tenant_count'])
        for row in await FeatureFlagService().list_groups_with_counts(actor)
    ]


# --- tools --------------------------------------------------------------------


async def platform_list_communities() -> List[PlatformCommunity]:
    """Every community on the platform, switched-off ones included, with its
    feature group and what setup it's missing. Super admin only.

    Use a row's `slug` as `community` in the other platform tools.
    """
    actor = current_actor().user
    return [community_row(o) for o in await PlatformAdminService().list_communities(actor)]


async def platform_get_community(community: str) -> PlatformCommunityDetail:
    """One community in full: setup, its Staff, and every feature's availability
    (group, override, and whether it's live). Super admin only.

    `community` is a slug from `platform_list_communities`.
    """
    actor = current_actor().user
    tenant = await PlatformAdminService().find_community(actor, community)
    return await community_detail(actor, tenant.id)


async def platform_list_users(
    search: Optional[str] = None,
    has_password: Optional[bool] = None,
    is_active: Optional[bool] = None,
    limit: int = 50,
) -> List[PlatformUser]:
    """Every account on the platform, across all communities. Super admin only.

    `search` matches name, Discord handle, Discord id and sign-in name.
    `has_password` and `is_active` narrow the list when given. Returns at most
    `limit` rows (up to 500).
    """
    rows = await PlatformAdminService().list_accounts(
        current_actor().user, search=search, has_password=has_password, is_active=is_active,
    )
    return [_account(o) for o in rows[:clamp(limit, low=1, high=500)]]


async def platform_get_user(user_id: int) -> PlatformUser:
    """One account: its communities, sign-in methods and whether it's active. Super admin only."""
    return await account_row(current_actor().user, user_id)


async def platform_list_feature_groups() -> List[PlatformFeatureGroup]:
    """Feature groups (tiers): which features each makes available, which is the
    default, and how many communities are on each. Super admin only."""
    return await group_rows(current_actor().user)


async def platform_list_racetime_bots() -> List[PlatformRacetimeBot]:
    """The shared racetime.gg bots: connection status and which communities may
    use each. Client secrets are never returned. Super admin only."""
    actor = current_actor().user
    slugs = {t.id: t.slug for t in await TenantService.list_tenants()}
    return [await bot_row(actor, b, slugs) for b in await RacetimeBotService().list_bots(actor)]


async def platform_list_log_levels() -> List[LogLevelResponse]:
    """Server logger levels: the loggers worth tuning plus any set by hand, with
    the level each runs at now. An empty `logger` is the root. Super admin only."""
    return [LogLevelResponse.from_row(r) for r in await LogLevelService().list_rows(current_actor().user)]


async def platform_service_health() -> List[ProbeResultResponse]:
    """The platform-wide health board: the latest check of every outside service
    (database, Discord, racetime.gg, SpeedGaming, Challonge, randomizers …).
    A service not checked yet shows `unknown`. Super admin only.

    This reads the last results; `platform_refresh_service_health` re-checks now.
    """
    return [ProbeResultResponse.model_validate(r.as_dict()) for r in ServiceHealthService().snapshot()]


def register_tools(mcp: MCPServer) -> None:
    for fn, title in (
        (platform_list_communities, 'List all communities'),
        (platform_get_community, 'Get a community (platform)'),
        (platform_list_users, 'List all accounts'),
        (platform_get_user, 'Get an account (platform)'),
        (platform_list_feature_groups, 'List feature groups'),
        (platform_list_racetime_bots, 'List racetime bots'),
        (platform_list_log_levels, 'List log levels'),
        (platform_service_health, 'Platform health board'),
    ):
        register(mcp, fn, gate=Gate.PLATFORM, scope=ApiScope.STAFF_READ, title=title)
