"""What the bot does with ``GUILD_MEMBER_*`` gateway events.

Two independent syncs hang off the same events — application roles (from the
member's Discord roles, provisioning an account for a mapped-role holder who
has none) and the cached avatar hash — so they live together here
rather than in :mod:`discord_service`, which owns the bot singleton and the DM
senders. ``get_discord_bot`` wires both listeners; nothing else calls them.

Both are best-effort by design: they log and swallow, because an exception
escaping a gateway listener takes the connection down with it. Services are
imported lazily inside each function to avoid a circular import back through
:mod:`discord_service`.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from application.services.discord.discord_guild_ops import GuildMember

logger = logging.getLogger(__name__)


async def sync_member_avatar(member: discord.Member) -> None:
    """Record a member's current global avatar hash.

    ``GUILD_MEMBER_UPDATE`` is the only avatar refresh most people ever get:
    someone who changes their avatar and never signs in again would otherwise
    keep whatever hash their last login captured. ``Member.avatar`` is the
    *global* user asset (not the guild-specific one), matching what the OAuth
    login stores, so the two paths cannot disagree.
    """
    try:
        from application.services.user_service import UserService

        asset = member.avatar
        await UserService().sync_discord_avatar(member.id, asset.key if asset else None)
    except Exception:
        logger.exception('Avatar sync failed for discord_id=%s', member.id)


async def sync_member_roles(
    guild_id: int, discord_user_id: int, member: Optional[GuildMember] = None,
) -> None:
    """Re-sync a member's app roles when their Discord roles change.

    Runs in the bot event loop on ``GUILD_MEMBER_UPDATE`` / member-remove events.
    ``member`` is passed only by the update event: someone with no account who
    now holds a mapped role gets one. A removal passes nothing, since leaving
    the server is never a reason to create an account.
    """
    try:
        from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService
        from application.services.tenant_service import TenantService
        from models import User

        tenants = await TenantService.list_tenants_for_guild(guild_id)
        if not tenants:
            return
        service = DiscordRoleMappingService()
        user = await User.get_or_none(discord_id=discord_user_id)
        if user is None and member is not None:
            user = await service.provision_member(tenants, member)
        if user is None:
            return
        for tenant in tenants:
            await service.sync_user_roles_for_tenant(user, tenant)
    except Exception:
        logger.exception('Live role sync failed for discord_id=%s', discord_user_id)
