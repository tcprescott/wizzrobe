"""
API Token Service - Business Logic Layer

Issues, lists, revokes, and authenticates personal API access tokens.

Only the SHA-256 hash of a token is persisted; the plaintext is returned
exactly once, at creation. A token authenticates as its owning ``User`` and
inherits that user's permissions; a ``read_only`` token may only be used for
read (GET) endpoints.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from application.errors import NotFoundError
from application.repositories.api_token_repository import ApiTokenRepository
from application.services.audit_service import AuditActions, AuditService
from models import ApiToken, User

logger = logging.getLogger(__name__)

TOKEN_PREFIX = 'wizzrobe_pat_'
MCP_TOKEN_PREFIX = 'wizzrobe_mcp_'
MCP_REFRESH_PREFIX = 'wizzrobe_mcpref_'
APP_TOKEN_PREFIX = 'wizzrobe_app_'
APP_REFRESH_PREFIX = 'wizzrobe_appref_'

# Public by design: the prefixes identify a credential's surface, they never protect it.
BEARER_PREFIXES = (TOKEN_PREFIX, MCP_TOKEN_PREFIX, APP_TOKEN_PREFIX)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class ApiTokenService:
    """Service for personal API access token operations."""

    def __init__(self) -> None:
        self.repository = ApiTokenRepository()
        self.audit_service = AuditService()

    async def create_token(
        self,
        actor: User,
        name: str,
        read_only: bool = False,
        expires_at: Optional[datetime] = None,
        not_after: Optional[datetime] = None,
    ) -> Tuple[ApiToken, str]:
        """Create a token for ``actor``. Returns (token_record, raw_token).

        The raw token is shown only here and never recoverable afterwards.
        ``not_after`` caps the expiry (the minting token's own, over REST).
        """
        if not name or not name.strip():
            raise ValueError("Token name is required")
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            raise ValueError("Expiry must be in the future")
        if not_after is not None and (expires_at is None or expires_at > not_after):
            expires_at = not_after

        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = await self.repository.create(
            user=actor,
            name=name.strip(),
            token_hash=_hash_token(raw_token),
            token_prefix=raw_token[:17],
            read_only=read_only,
            expires_at=expires_at,
        )

        await self.audit_service.write_log(
            actor,
            AuditActions.APITOKEN_CREATED,
            {'token_id': token.id, 'name': token.name, 'read_only': read_only},
        )
        return token, raw_token

    async def list_tokens(self, actor: User) -> List[ApiToken]:
        """List the actor's own active (non-revoked) tokens."""
        return await self.repository.list_for_user(actor)

    async def revoke_token(self, actor: User, token_id: int) -> None:
        token = await self.repository.get_by_id(token_id)
        if token is None or token.revoked_at is not None:
            raise NotFoundError("Token not found")
        if token.user_id != actor.id:
            raise PermissionError("You can only revoke your own tokens")

        await self.repository.revoke(token, datetime.now(timezone.utc))
        await self.audit_service.write_log(
            actor,
            AuditActions.APITOKEN_REVOKED,
            {'token_id': token.id, 'name': token.name},
        )

    async def authenticate(self, raw_token: str) -> Optional[Tuple[User, ApiToken]]:
        """Resolve a raw bearer token to its (user, token), or None if invalid.

        Rejects unknown, revoked, and expired tokens. Updates ``last_used_at``
        on success.
        """
        if not raw_token:
            return None
        token = await self.repository.get_by_hash(_hash_token(raw_token))
        if token is None:
            logger.warning('API token auth failed: unknown token (prefix=%s)', raw_token[:17])
            return None
        if token.revoked_at is not None:
            logger.warning('API token auth failed: revoked token id=%s', token.id)
            return None
        now = datetime.now(timezone.utc)
        if token.expires_at is not None and token.expires_at <= now:
            logger.warning('API token auth failed: expired token id=%s', token.id)
            return None

        await self.repository.touch_last_used(token, now)
        logger.debug('API token auth succeeded: token id=%s', token.id)
        return token.user, token

    async def resolve_actor(self, raw_token: str) -> Optional[Tuple[User, ApiToken]]:
        """Authenticate a bearer token and return its ``(user, token)``.

        The shared front half of every bearer-authenticated entry surface: the
        REST API and the MCP server both call this so their notion of "who is
        this token" cannot drift. Returns ``None`` for an unknown, revoked, or
        expired token, and raises ``PermissionError`` when the token itself is
        valid but its owner has been deactivated — the two cases map to
        different statuses (401 vs 403) at every caller.

        GLOBAL: deliberately touches no tenant context. Which community a request
        acts in is the caller's decision — the REST API takes it from the token's
        own ``tenant``, the MCP server from each tool call — so binding it here
        would force one surface's model onto the other.
        """
        result = await self.authenticate(raw_token)
        if result is None:
            return None
        user, token = result
        if not user.is_active:
            raise PermissionError("This account is inactive")
        return user, token
