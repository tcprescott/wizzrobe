"""The MCP SDK's authorization-server provider, backed by :class:`McpAuthService`.

Pure translation: SDK protocol types in, service calls out, SDK types back. All
the policy — TTLs, rotation, single-use codes, what a registration may claim —
lives in the service. Keeping the SDK's vocabulary confined to this file means a
SDK upgrade touches one module.

The SDK's own handlers already enforce PKCE (S256 verifier vs. stored
challenge), redirect-URI consistency between authorize and token, and code
expiry, so none of that is re-implemented here. What the SDK cannot know — that
a code is single-use, that a refresh token belongs to the client presenting it —
is enforced in the service.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from application.services import McpAuthService
from application.services.mcp_auth_service import (
    CLIENT_SCOPE,
    DEFAULT_SCOPE,
    PendingAuthorization,
)
from application.utils.environment import get_base_url

logger = logging.getLogger(__name__)

CONSENT_PATH = '/oauth/mcp/consent'


def _epoch(value: Optional[datetime]) -> Optional[int]:
    """The SDK compares expiries against ``time.time()``, so hand it epochs.

    Integer seconds, not a float: ``RefreshToken.expires_at`` is typed ``int``
    and pydantic refuses a fractional value outright. Truncating moves expiry
    at most a second *earlier*, which is the safe direction.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


class WizzrobeOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Authorization-code + refresh-token provider for MCP clients."""

    def __init__(self) -> None:
        self.service = McpAuthService()

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        client = await self.service.get_client(client_id)
        if client is None:
            return None
        return OAuthClientInformationFull(
            client_id=client.client_id,
            client_name=client.client_name,
            redirect_uris=list(client.redirect_uris or []),
            grant_types=list(client.grant_types or []),
            response_types=list(client.response_types or []),
            # Every client is public: no secret is stored, PKCE binds the code.
            # mcp 2.x refuses a secret-based client that has no stored secret,
            # so reporting the registered method would 401 every /token call
            # from a client (Claude's connectors) that registered with
            # client_secret_post. Rows from before this fix still carry it.
            token_endpoint_auth_method='none',
            scope=client.scope,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        try:
            await self._register_client(client_info)
        except ValueError as exc:
            raise RegistrationError(
                error='invalid_redirect_uri', error_description=str(exc),
            ) from exc

    async def _register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self.service.register_client(
            client_id=client_info.client_id,
            client_name=client_info.client_name or 'MCP client',
            redirect_uris=[str(uri) for uri in client_info.redirect_uris],
            grant_types=list(client_info.grant_types or []),
            response_types=list(client_info.response_types or []),
            # The SDK has already minted a secret and will echo it to the
            # client, which may keep sending it; it is never checked.
            token_endpoint_auth_method='none',
            # Both scopes, so a client that explicitly requests write is not
            # rejected at /authorize by the SDK's registered-scope check. It
            # decides nothing: the consent screen grants the scope, and a client
            # that asked for write still gets a read-only token unless the user
            # said otherwise.
            scope=client_info.scope or CLIENT_SCOPE,
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to our consent screen.

        No code is minted here. The SDK has validated the client and its
        redirect URI, but nobody has proven who the *user* is yet — that happens
        at the consent page, which requires a signed-in session.
        """
        pending = PendingAuthorization(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            scopes=list(params.scopes or [DEFAULT_SCOPE]),
            state=params.state,
            resource=params.resource,
            created_at=datetime.now(timezone.utc),
        )
        txn_id = self.service.begin_authorization(pending)
        return f'{get_base_url()}{CONSENT_PATH}?{urlencode({"txn": txn_id})}'

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        code = await self.service.load_code(client.client_id, authorization_code)
        if code is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=(code.scope or DEFAULT_SCOPE).split(),
            expires_at=_epoch(code.expires_at),
            client_id=client.client_id,
            code_challenge=code.code_challenge,
            redirect_uri=code.redirect_uri,
            redirect_uri_provided_explicitly=code.redirect_uri_provided_explicitly,
            resource=code.resource,
            subject=str(code.user_id),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Re-load rather than trusting the object handed back: between the SDK's
        # load and this call the code may have been consumed by a concurrent
        # exchange, and the service's consumed check is what makes it single-use.
        code = await self.service.load_code(client.client_id, authorization_code.code)
        if code is None:
            raise TokenError(
                error='invalid_grant', error_description='Invalid or already-used authorization code',
            )
        try:
            access, refresh, expires_in = await self.service.exchange_code(code)
        except ValueError as exc:
            raise TokenError(error='invalid_grant', error_description=str(exc)) from exc
        return OAuthToken(
            access_token=access,
            token_type='Bearer',
            expires_in=expires_in,
            scope=code.scope or DEFAULT_SCOPE,
            refresh_token=refresh,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        token = await self.service.load_refresh_token(client.client_id, refresh_token)
        if token is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=(token.scope or DEFAULT_SCOPE).split(),
            expires_at=_epoch(token.refresh_expires_at),
            subject=str(token.user_id),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        token = await self.service.load_refresh_token(client.client_id, refresh_token.token)
        if token is None:
            raise TokenError(
                error='invalid_grant', error_description='Invalid or expired refresh token',
            )
        try:
            access, refresh, expires_in = await self.service.exchange_refresh(token)
        except ValueError as exc:
            raise TokenError(error='invalid_grant', error_description=str(exc)) from exc
        return OAuthToken(
            access_token=access,
            token_type='Bearer',
            expires_in=expires_in,
            scope=token.scope or DEFAULT_SCOPE,
            refresh_token=refresh,
        )

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        record = await self.service.load_access_token(token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record.oauth_client.client_id if record.oauth_client else '',
            scopes=(record.scope or DEFAULT_SCOPE).split(),
            expires_at=_epoch(record.expires_at),
            subject=str(record.user_id),
        )

    async def revoke_token(self, token) -> None:
        """Revoke the whole grant, whichever half was presented.

        RFC 7009 lets a server revoke the pair when given either one, and that
        is the safer reading here: a user revoking access expects access to
        stop, not for a live refresh token to mint a replacement.
        """
        raw = getattr(token, 'token', None)
        if not raw:
            return
        record = await self.service.load_access_token(raw)
        if record is None:
            record = await self.service.load_refresh_token(token.client_id, raw)
        if record is not None:
            await self.service.revoke(record)
