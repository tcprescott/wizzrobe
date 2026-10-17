"""Orientation tools: who am I, and which communities can I ask about.

These are the only tools that take no ``tenant`` argument, because they are how
a caller learns which slugs exist. Front-loading identity, roles, and enabled
features into one call is what lets the model skip tools that would be refused
instead of discovering each refusal one round-trip at a time.
"""

from typing import List

from mcp.server.mcpserver import MCPServer

from application.services import AuthService, FeatureFlagService
from application.services.tenant_service import TenantService
from application.tenant_context import tenant_scope
from application.utils.build_info import build_info
from mcpserver.auth import Gate, belongs_to_tenant, current_actor
from mcpserver.registry import register
from mcpserver.schemas import ServerVersion, TenantInfo, WhoAmI


async def _tenants_for_actor() -> List[TenantInfo]:
    """Communities the caller can meaningfully address.

    A super admin sees every active community — that role is global and bypasses
    the per-tenant gate everywhere else, so hiding communities here would only
    make the tool lie. Everyone else sees the communities they belong to;
    listing the rest would leak the platform's customer list to any authenticated
    user, and would offer slugs whose tools all refuse them anyway.
    """
    actor = current_actor()
    is_super = await AuthService.is_super_admin(actor.user)
    out: List[TenantInfo] = []
    for tenant in await TenantService.list_tenants():
        if not tenant.is_active:
            continue
        # Roles and flags are both per-tenant, so they have to be read inside
        # each community's scope — the same predicate returns different answers
        # per binding.
        with tenant_scope(tenant.id):
            if not is_super and not await belongs_to_tenant(actor.user):
                continue
            roles = await AuthService.get_roles(actor.user)
            # One query per community, and the reason a caller can skip a tool
            # instead of discovering its `not_found` a round-trip later. A flag
            # is not a permission: it is reported to everyone who can see the
            # community, exactly as the feature's absence would be.
            features = await FeatureFlagService().enabled_flags()
        out.append(TenantInfo(
            slug=tenant.slug,
            name=tenant.name,
            roles=sorted(role.value for role in roles),
            features=sorted(flag.value for flag in features),
        ))
    return out


async def list_tenants() -> List[TenantInfo]:
    """List the Wizzrobe communities you can query, with your roles in each.

    Call this first to learn the `tenant` slugs every other tool needs.
    """
    return await _tenants_for_actor()


async def whoami() -> WhoAmI:
    """Report who you are signed in as, whether you may write, and where.

    The cheapest way to orient: it tells you your roles per community and
    whether this connection can change anything, so you can tell in advance
    which tools will be permitted.
    """
    actor = current_actor()
    return WhoAmI(
        user_id=actor.user.id,
        username=actor.user.username,
        display_name=actor.user.display_name,
        is_super_admin=await AuthService.is_super_admin(actor.user),
        # A property of the connection, not the person: the same user can hold
        # a writing grant in one client and a reading one in another.
        can_write=not actor.token.read_only,
        tenants=await _tenants_for_actor(),
    )


async def server_version() -> ServerVersion:
    """Report which build of Wizzrobe is running: version, commit and its date.

    Quote it when reporting a bug, or to check whether a fix has been deployed.
    """
    info = build_info()
    return ServerVersion(
        version=info.version,
        commit=info.commit,
        commit_short=info.commit_short,
        commit_date=info.commit_date,
        source_url=info.source_url,
    )


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_tenants, gate=Gate.GLOBAL, title='List communities')
    register(mcp, whoami, gate=Gate.GLOBAL, title='Who am I')
    register(mcp, server_version, gate=Gate.GLOBAL, title='Server version')
