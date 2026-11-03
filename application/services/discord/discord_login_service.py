"""Log in from Discord: one-time sign-in links handed out by the bot.

The bot answers ``/login`` and presses on a staff-posted login panel with an
ephemeral link to ``/login/discord?token=…`` on the community's site. Discord
has already told us who pressed the button (interactions are signed by
Discord), so the link signs that person in without the OAuth round trip.

Unlike the Design B handoff (``oauth_handoff_service``), this token cannot be
bound to a browser: the person opens it wherever Discord sends them. What keeps
it safe instead:

- **Single-use.** The nonce is popped from an in-process store on the first
  claim, so a second presentation finds nothing. (In-memory, matching the app's
  single-worker deployment, like the handoff store.)
- **Short TTL.** ``LINK_TTL_SECONDS``; the ephemeral reply deletes itself at the
  same moment.
- **Host- and community-bound.** The host the link points at and the tenant it
  was minted for are both checked at claim time, so a link for one community
  can't be redeemed on another (path-mode communities share a host).
- **Only the recipient sees it.** The reply is ephemeral.
- **Claimed after the page's socket connects**, so a link-preview fetcher or a
  mail scanner that only downloads the HTML can't burn it.
"""

import logging
import secrets
import time
from typing import Optional
from urllib.parse import quote, urlparse

from itsdangerous import BadData, URLSafeTimedSerializer

from application.errors import NotFoundError
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService, get_user_from_discord_id
from application.services.tenant_service import TenantService
from application.services.user_service import UserService
from application.tenant_context import tenant_scope
from application.utils.hostname import normalize_hostname
from application.utils.tenant_urls import tenant_url
from models import Tenant, User

logger = logging.getLogger(__name__)

LINK_TTL_SECONDS = 120
CLAIM_PATH = '/login/discord'

_SALT = 'wizzrobe-discord-login-link'
_STORE_MAX = 4096

_pending: dict[str, dict] = {}


def _serializer() -> URLSafeTimedSerializer:
    import os
    return URLSafeTimedSerializer(os.environ.get('STORAGE_SECRET') or '', salt=_SALT)


def _prune(now: float) -> None:
    for nonce in [n for n, p in _pending.items() if p['expiry'] <= now]:
        _pending.pop(nonce, None)


def reset() -> None:
    """Clear the pending store (test isolation)."""
    _pending.clear()


class DiscordLoginService:
    """Mint and redeem the bot's one-time login links; gate the login panel."""

    @staticmethod
    async def communities_for_guild(guild_id: Optional[int]) -> list[Tenant]:
        """Active communities linked to a Discord server (a server may back several)."""
        if guild_id is None:
            return []
        return [t for t in await TenantService.list_tenants_for_guild(guild_id) if t.is_active]

    @staticmethod
    async def community_for_panel(guild_id: Optional[int], tenant_id: int) -> Tenant:
        """The community a panel button names, if it's still linked to this server.

        A server can be unlinked (or relinked to another community) after the
        panel went up; the stale button then says so instead of signing anyone in.
        """
        for tenant in await DiscordLoginService.communities_for_guild(guild_id):
            if tenant.id == tenant_id:
                return tenant
        raise NotFoundError("This login button is for a community that's no longer linked to this server.")

    @staticmethod
    async def mint_link(
        tenant: Tenant,
        *,
        discord_id: int,
        username: str,
        avatar_hash: str,
        avatar_url: Optional[str],
    ) -> str:
        """A one-time sign-in URL on ``tenant``'s site for this Discord user.

        Provisions the account the same way an OAuth login does, so someone who
        has never visited the site can still use the button. Raises
        ``ValueError`` for a deactivated account.
        """
        with tenant_scope(tenant.id):
            user, _created = await UserService().provision_from_discord_login(
                discord_id, username, avatar=avatar_hash,
            )
        if not user.is_active:
            raise ValueError("This account is inactive. Contact staff if this seems wrong.")

        url_prefix = tenant_url(tenant, CLAIM_PATH)
        host = normalize_hostname(urlparse(url_prefix).netloc)
        if host is None:
            raise ValueError("This community's address isn't set up, so a login link can't be made.")

        now = time.time()
        _prune(now)
        nonce = secrets.token_urlsafe(24)
        if len(_pending) >= _STORE_MAX:
            _pending.pop(next(iter(_pending)), None)
        _pending[nonce] = {
            'discord_id': discord_id,
            'username': username,
            'avatar': avatar_url,
            'host': host,
            'tenant_id': tenant.id,
            'expiry': now + LINK_TTL_SECONDS,
        }
        token = _serializer().dumps({'n': nonce, 'h': host})
        return f'{url_prefix}?token={quote(token, safe="")}'

    @staticmethod
    def claim(token: str, request_host: str, tenant_id: Optional[int]) -> Optional[dict]:
        """Consume a login token presented on ``request_host`` for ``tenant_id``.

        Returns the stored identity (``discord_id``/``username``/``avatar``), or
        ``None`` for an invalid, expired, already-used, wrong-host or
        wrong-community token. The nonce is consumed whatever the outcome.
        """
        host = normalize_hostname(request_host)
        if host is None or not token:
            return None
        try:
            data = _serializer().loads(token, max_age=LINK_TTL_SECONDS)
        except BadData:
            return None
        if not isinstance(data, dict) or not data.get('n'):
            return None
        payload = _pending.pop(data['n'], None)
        if payload is None or payload['expiry'] <= time.time():
            return None
        if data.get('h') != host or payload['host'] != host or payload['tenant_id'] != tenant_id:
            logger.warning('Discord login link presented on the wrong host or community (%r)', host)
            return None
        return payload

    @staticmethod
    async def authorize_panel(discord_id: int, tenant: Tenant) -> User:
        """The staff member allowed to post ``tenant``'s login panel.

        Discord already limits the command to members with Manage Server; this
        adds the community's own STAFF gate. Raises ``PermissionError``.
        """
        user = await get_user_from_discord_id(str(discord_id))
        with tenant_scope(tenant.id):
            allowed = user is not None and await AuthService.is_staff(user)
        if user is None or not allowed:
            raise PermissionError(f'Only {tenant.name} staff can post the login panel.')
        return user

    @staticmethod
    async def record_panel_posted(actor: User, tenant: Tenant, channel_id: int) -> None:
        """Audit a login panel going up in ``channel_id``."""
        with tenant_scope(tenant.id):
            await AuditService().write_log(
                actor, AuditActions.DISCORD_LOGIN_PANEL_POSTED,
                {'guild_id': tenant.discord_guild_id, 'channel_id': channel_id},
            )
