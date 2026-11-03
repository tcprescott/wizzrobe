"""Discord Link Service — verified tenant ↔ Discord-server connection.

Links a tenant to a Discord guild only when the acting user proves they
administer that guild. The proof is Discord's own bot-authorization flow — which
only a member with *Manage Server* can complete — followed by a server-side
re-check that the acting user (not merely *someone* who clicked through consent)
has Manage Server / Administrator / is the owner of the guild Discord bound the
bot to. No client-supplied guild id is ever trusted.

Runs on the platform surface as well as inside a tenant, so it takes the target
``Tenant`` explicitly and does its own STAFF/super-admin gate rather than relying
on the ambient tenant for authorization.
"""

import logging
import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord.discord_service import DiscordService
from application.services.tenant_service import TenantService
from application.utils.environment import get_base_url
from models import Tenant, User

logger = logging.getLogger(__name__)

_AUTHORIZE_URL = 'https://discord.com/api/oauth2/authorize'
_EXCHANGE_URL = 'https://discord.com/api/oauth2/token'
# Manage Roles (0x10000000), the minimum to apply mapped roles; DISCORD_BOT_PERMISSIONS overrides it.
_DEFAULT_BOT_PERMISSIONS = '268435456'


def _client_id() -> str:
    return os.getenv('DISCORD_CLIENT_ID') or ''


def _client_secret() -> str:
    return os.getenv('DISCORD_CLIENT_SECRET') or ''


def _bot_permissions() -> str:
    return os.getenv('DISCORD_BOT_PERMISSIONS') or _DEFAULT_BOT_PERMISSIONS


def connect_redirect_uri() -> str:
    """Callback for the bot-authorization flow, resolved at request time.

    A single URI on the platform host serves every tenant; the target tenant and
    CSRF state are carried in the session, not the URL (mirrors the login flow).
    """
    return os.getenv('DISCORD_CONNECT_REDIRECT_URL') or f'{get_base_url()}/oauth/discord/connect/callback'


class DiscordLinkService:
    """Verified linking of a tenant to a Discord guild + the bot-invite URL."""

    @staticmethod
    async def can_manage_link(actor: Optional[User]) -> bool:
        """STAFF (per current tenant) or a global super-admin may link/unlink."""
        if actor is None:
            return False
        if await AuthService.is_super_admin(actor):
            return True
        return await AuthService.can_grant_roles(actor)

    @staticmethod
    def authorize_url(state: str) -> str:
        """Discord bot-authorization URL for this attempt.

        ``scope=bot`` makes Discord show a server picker limited to guilds where
        the user has Manage Server and add the bot on consent; ``response_type=code``
        yields an authoritative ``guild`` object on token exchange.
        """
        params = urlencode({
            'client_id': _client_id(),
            'permissions': _bot_permissions(),
            'scope': 'bot applications.commands',
            'response_type': 'code',
            'redirect_uri': connect_redirect_uri(),
            'state': state,
        })
        return f'{_AUTHORIZE_URL}?{params}'

    @staticmethod
    async def _exchange_code(code: str) -> Optional[int]:
        """Exchange the auth code; return the guild id Discord bound the bot to.

        The bot-scope token response carries an authoritative ``guild`` object —
        unforgeable proof of which server was authorized. Returns ``None`` on any
        failure so the caller refuses the link rather than trusting a guess.
        """
        data = {
            'client_id': _client_id(),
            'client_secret': _client_secret(),
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': connect_redirect_uri(),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    _EXCHANGE_URL, data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                )
            if resp.status_code != 200:
                logger.warning('Discord bot token exchange failed: %s %s', resp.status_code, resp.text[:200])
                return None
            guild = (resp.json() or {}).get('guild') or {}
            guild_id = guild.get('id')
            return int(guild_id) if guild_id is not None else None
        except Exception:
            logger.exception('Discord bot token exchange errored')
            return None

    @staticmethod
    async def complete_link(actor: User, tenant: Tenant, code: str) -> Tenant:
        """Exchange the bot-auth ``code`` for its guild, then verify and bind it.

        Raises :class:`ValueError` (user-facing) when the actor lacks the app
        role, the code exchange fails, or the actor cannot administer the guild.
        """
        await AuthService.ensure(
            await DiscordLinkService.can_manage_link(actor),
            'You need the Staff role to connect a Discord server.',
        )
        guild_id = await DiscordLinkService._exchange_code(code)
        if guild_id is None:
            raise ValueError('Could not confirm the Discord authorization. Please try again.')
        return await DiscordLinkService.link_guild(actor, tenant, guild_id)

    @staticmethod
    async def link_guild(actor: User, tenant: Tenant, guild_id: int) -> Tenant:
        """Verify the actor administers ``guild_id`` and bind ``tenant`` to it.

        ``guild_id`` must come from a trusted source — the bot-auth token exchange
        (:meth:`complete_link`), never raw client input. Regardless, this re-checks
        that the acting user has Manage Server / Administrator / is owner of the
        guild, and fails closed if the bot cannot determine it (not ready / not in
        the guild / API error). Raises :class:`ValueError` on any failure.
        """
        await AuthService.ensure(
            await DiscordLinkService.can_manage_link(actor),
            'You need the Staff role to connect a Discord server.',
        )
        ok, can_manage = await DiscordService().member_can_manage_guild(guild_id, int(actor.discord_id))
        if not ok:
            raise ValueError(f'Could not verify your permissions on that server: {can_manage}')
        if not can_manage:
            raise ValueError('You must have "Manage Server" on that Discord server to connect it.')

        await TenantService.set_discord_guild_id(tenant, guild_id)
        await AuditService().write_log(
            actor, AuditActions.DISCORD_SERVER_LINKED,
            {'tenant_id': tenant.id, 'discord_guild_id': guild_id},
        )
        return tenant

    @staticmethod
    async def disconnect(actor: User, tenant: Tenant) -> Tenant:
        """Clear a tenant's Discord link. Leaves the bot in the guild (it may be
        shared by other tenants)."""
        await AuthService.ensure(
            await DiscordLinkService.can_manage_link(actor),
            'You need the Staff role to disconnect a Discord server.',
        )
        previous = tenant.discord_guild_id
        await TenantService.set_discord_guild_id(tenant, None)
        await AuditService().write_log(
            actor, AuditActions.DISCORD_SERVER_UNLINKED,
            {'tenant_id': tenant.id, 'previous_guild_id': previous},
        )
        return tenant
