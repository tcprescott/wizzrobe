"""
MCP OAuth Service - Business Logic Layer

The authorization server behind ``/mcp``: dynamic client registration,
authorization codes, and the access/refresh tokens they exchange into.

Deliberately free of any MCP SDK types — it deals in this app's models and plain
values, and ``mcpserver/oauth/provider.py`` translates at the boundary. That
keeps the protocol shape in the entry surface where it belongs, and means a SDK
upgrade cannot reach into the service layer.

Everything here is **global**: an OAuth grant authenticates a user across the
platform, and the community a tool call acts in is named by the call. Nothing
in this module reads or writes tenant context.

Only hashes are persisted, for codes and for both halves of a token pair, so a
database disclosure yields nothing replayable.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from application.api_scopes import (
    ALL_SCOPES,
    READ_SCOPES,
    STAFF_SCOPES,
    WRITE_SCOPES,
    ApiScope,
    format_scope,
    parse_scope,
)
from application.errors import NotFoundError
from application.repositories.api_token_repository import ApiTokenRepository
from application.repositories.mcp_auth_repository import McpAuthRepository
from application.services.api_token_service import (
    MCP_REFRESH_PREFIX,
    MCP_TOKEN_PREFIX,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_service import TenantService
from application.tenant_context import tenant_scope
from application.utils.parked_requests import prune_parked
from models import ApiToken, ApiTokenOrigin, McpAuthorizationCode, McpOAuthClient, User

logger = logging.getLogger(__name__)

# The consent page sends the browser to the redirect URI from the app's own
# origin, so a script-bearing scheme there runs as the signed-in person.
_REFUSED_REDIRECT_SCHEMES = frozenset({
    'javascript', 'data', 'vbscript', 'file', 'blob', 'about', 'filesystem',
})
_LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


def is_allowed_redirect_uri(uri: str) -> bool:
    """https anywhere, http only to loopback, or a native app's own scheme (RFC 8252)."""
    try:
        parts = urlsplit((uri or '').strip())
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not scheme or scheme in _REFUSED_REDIRECT_SCHEMES or parts.fragment:
        return False
    if scheme == 'https':
        return bool(parts.hostname)
    if scheme == 'http':
        return (parts.hostname or '').lower() in _LOOPBACK_HOSTS
    return True

# Short enough that a code left in a browser history or a proxy log is dead by
# the time anyone finds it; long enough for a human to finish a consent screen.
CODE_TTL = timedelta(minutes=5)
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)
# A consent screen the user never completes should not pin memory forever.
PENDING_TTL = timedelta(minutes=15)

READ_SCOPE = 'wizzrobe:read'
WRITE_SCOPE = 'wizzrobe:write'
# Kept under the old name because the SDK provider falls back to it wherever a
# scope is absent, and read is the floor every grant carries.
DEFAULT_SCOPE = READ_SCOPE
# What a dynamically-registered client is allowed to *ask* for. Asking is not
# getting: the granted scope is decided on the consent screen (see
# :func:`granted_scope`), so a client that requests write still gets a read-only
# token unless the person approving it ticks the box.
CLIENT_SCOPE = f'{READ_SCOPE} {WRITE_SCOPE}'

# Typed by the person approving before a grant may carry write. A checkbox alone
# is one click a hurried user makes without reading; typing a sentence is not.
WRITE_CONFIRMATION_PHRASE = 'this is dangerous'


def write_confirmation_matches(text: Optional[str]) -> bool:
    """Case and spacing are forgiven; the words are not."""
    return ' '.join((text or '').split()).lower() == WRITE_CONFIRMATION_PHRASE


def granted_scope(*, allow_write: bool) -> str:
    """Every read area, plus every write area when ``allow_write``: the grant a
    connection gets when nobody narrows it. Scripts and tests mint with it."""
    return format_scope(ALL_SCOPES if allow_write else READ_SCOPES)


def grant_scopes(scope: Optional[str]) -> FrozenSet[ApiScope]:
    """The area scopes a connection's stored scope string reaches.

    Grants made before connections were scoped carry only ``wizzrobe:read``
    and/or ``wizzrobe:write``. Either one reaches every area: what the old
    read-only grant could not do was write, and ``ApiToken.read_only`` still
    refuses that, so nobody has to reconnect and nothing they could call goes
    away.
    """
    names = (scope or '').split()
    if READ_SCOPE in names or WRITE_SCOPE in names:
        return ALL_SCOPES
    return parse_scope(scope)


def scope_allows_write(scope: Optional[str]) -> bool:
    names = (scope or '').split()
    if READ_SCOPE in names or WRITE_SCOPE in names:
        return WRITE_SCOPE in names
    return bool(parse_scope(scope) & WRITE_SCOPES)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class PendingAuthorization:
    """An authorization request parked while the user signs in and approves."""

    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    # What the client asked for. Recorded rather than honoured: the granted
    # scope comes from the consent screen, so this is only ever context for the
    # person deciding.
    scopes: List[str]
    state: Optional[str]
    resource: Optional[str]
    created_at: datetime


# In-process, like the existing cross-host OAuth handoff nonce store, and for
# the same reason: these live for seconds, are meaningless after a restart (the
# user simply reconnects), and the deployment is single-worker by requirement.
# Persisting them would buy nothing and cost a table.
_pending: Dict[str, PendingAuthorization] = {}


class McpAuthService:
    """Authorization-server operations for the MCP surface."""

    def __init__(self) -> None:
        self.repository = McpAuthRepository()
        self.token_repository = ApiTokenRepository()
        self.audit_service = AuditService()

    # --- Clients ---------------------------------------------------------

    async def register_client(
        self,
        client_name: str,
        redirect_uris: List[str],
        grant_types: List[str],
        response_types: List[str],
        token_endpoint_auth_method: str = 'none',
        scope: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> McpOAuthClient:
        """Register a client (RFC 7591 dynamic client registration).

        Open by design — that is what lets a user paste the server URL into
        Claude and have it configure itself. Registering grants nothing: every
        code still requires an interactive, authenticated approval.
        """
        if not redirect_uris:
            raise ValueError('At least one redirect URI is required')
        for uri in redirect_uris:
            if not is_allowed_redirect_uri(uri):
                raise ValueError(
                    'Redirect URIs must be https, http to localhost, or an app scheme.'
                )
        return await self.repository.create_client(
            client_id=client_id or secrets.token_urlsafe(24),
            client_name=client_name or 'MCP client',
            redirect_uris=redirect_uris,
            grant_types=grant_types or ['authorization_code', 'refresh_token'],
            response_types=response_types or ['code'],
            token_endpoint_auth_method=token_endpoint_auth_method,
            scope=scope or DEFAULT_SCOPE,
        )

    async def get_client(self, client_id: str) -> Optional[McpOAuthClient]:
        return await self.repository.get_client(client_id)

    # --- Authorization request -> consent --------------------------------

    def begin_authorization(self, pending: PendingAuthorization) -> str:
        """Park an authorization request and return its transaction id.

        The id is a capability: whoever holds it can complete *this* request.
        It is not a credential — approving still requires an authenticated
        session, and the code it produces is bound to the client's PKCE
        challenge — so a leaked id cannot be exchanged for anything.
        """
        self._sweep_pending(room_for=1)
        txn_id = secrets.token_urlsafe(24)
        _pending[txn_id] = pending
        return txn_id

    def get_pending(self, txn_id: str) -> PendingAuthorization:
        self._sweep_pending()
        pending = _pending.get(txn_id)
        if pending is None:
            raise NotFoundError(
                'This authorization request has expired. Start again from your MCP client.'
            )
        return pending

    def discard_pending(self, txn_id: str) -> None:
        _pending.pop(txn_id, None)

    @staticmethod
    def _sweep_pending(room_for: int = 0) -> None:
        prune_parked(_pending, PENDING_TTL, room_for=room_for)

    async def offered_scopes(self, user: User) -> List[ApiScope]:
        """The areas the consent screen offers ``user``, in display order.

        An MCP grant is platform-wide, so the staff areas are offered to anyone
        with an admin-side role in at least one community they belong to. Each
        call still checks their role in the community it names.
        """
        if await self._may_use_staff_scopes_somewhere(user):
            return list(ApiScope)
        return [scope for scope in ApiScope if scope not in STAFF_SCOPES]

    @staticmethod
    async def _may_use_staff_scopes_somewhere(user: User) -> bool:
        if await AuthService.is_super_admin(user):
            return True
        for tenant in await TenantService.list_tenants():
            if not tenant.is_active or not await TenantService.is_member(user.id, tenant.id):
                continue
            with tenant_scope(tenant.id):
                if await AuthService.can_use_staff_scopes(user):
                    return True
        return False

    async def approve(
        self, txn_id: str, user: User, *, scopes: Iterable[ApiScope],
        write_confirmation: Optional[str] = None,
    ) -> Tuple[str, str, Optional[str]]:
        """Consume a pending request and mint its authorization code.

        Returns ``(raw_code, redirect_uri, state)``. Popping the pending entry
        makes approval single-use, so a re-submitted consent form cannot mint a
        second code for the same request.

        ``scopes`` are the areas the person ticked, and are the only thing that
        decides what the connection may do: what the client asked for is never
        read. They must sit inside :meth:`offered_scopes`. A grant with any
        write area also needs ``write_confirmation`` to match
        :data:`WRITE_CONFIRMATION_PHRASE`. Both are checked before the pending
        request is consumed, so a mistake leaves the consent screen usable.
        """
        pending = self.get_pending(txn_id)
        chosen = frozenset(scopes)
        if not chosen:
            raise ValueError('Tick at least one thing it may do.')
        if chosen - set(await self.offered_scopes(user)):
            raise ValueError('You can only allow what your roles let you do.')
        if chosen & WRITE_SCOPES and not write_confirmation_matches(write_confirmation):
            raise ValueError(
                f'Type "{WRITE_CONFIRMATION_PHRASE}" to let it make changes, '
                'or untick the changes to connect read-only.'
            )
        self.discard_pending(txn_id)
        if not is_allowed_redirect_uri(pending.redirect_uri):
            raise ValueError('This app registered a redirect address Wizzrobe will not open.')

        client = await self.repository.get_client(pending.client_id)
        if client is None:
            raise NotFoundError('This client is no longer registered.')

        raw_code = secrets.token_urlsafe(32)
        await self.repository.create_code(
            code_hash=_hash(raw_code),
            client=client,
            user=user,
            redirect_uri=pending.redirect_uri,
            redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            code_challenge=pending.code_challenge,
            expires_at=datetime.now(timezone.utc) + CODE_TTL,
            scope=format_scope(chosen),
            resource=pending.resource,
        )
        logger.info(
            'MCP authorization approved: user=%s client=%s scope=%s',
            user.id, client.client_id, format_scope(chosen),
        )
        return raw_code, pending.redirect_uri, pending.state

    # --- Code / token exchange -------------------------------------------

    async def load_code(self, client_id: str, raw_code: str) -> Optional[McpAuthorizationCode]:
        """Fetch a live, unconsumed code belonging to ``client_id``.

        Expiry is re-checked by the SDK's token handler, but a consumed code is
        ours to reject: without that check a stolen code could be replayed
        inside its five-minute window.
        """
        code = await self.repository.get_code(_hash(raw_code))
        if code is None or code.consumed_at is not None:
            return None
        if code.client.client_id != client_id:
            return None
        return code

    async def exchange_code(
        self, code: McpAuthorizationCode
    ) -> Tuple[str, str, int]:
        """Consume a code and issue a token pair.

        Returns ``(access_token, refresh_token, expires_in_seconds)``.
        """
        now = datetime.now(timezone.utc)
        if code.consumed_at is not None or not await self.repository.consume_code(code, now):
            raise ValueError('This authorization code has already been used')

        access_raw = MCP_TOKEN_PREFIX + secrets.token_urlsafe(32)
        refresh_raw = MCP_REFRESH_PREFIX + secrets.token_urlsafe(32)
        # The scope on the code is the consent decision, carried forward. Writing
        # it onto the token as ``read_only`` is what the MCP tool gate actually
        # reads, so the two can never disagree.
        write_allowed = scope_allows_write(code.scope)
        await self.token_repository.create_oauth_token(
            user=code.user,
            client=code.client,
            name=code.client.client_name,
            token_hash=_hash(access_raw),
            token_prefix=access_raw[:17],
            expires_at=now + ACCESS_TOKEN_TTL,
            refresh_token_hash=_hash(refresh_raw),
            refresh_expires_at=now + REFRESH_TOKEN_TTL,
            scope=code.scope,
            read_only=not write_allowed,
        )
        await self.audit_service.write_log(
            code.user,
            AuditActions.APITOKEN_CREATED,
            {
                'client': code.client.client_name,
                'origin': ApiTokenOrigin.OAUTH.value,
                'write': write_allowed,
                'scope': code.scope,
            },
        )
        return access_raw, refresh_raw, int(ACCESS_TOKEN_TTL.total_seconds())

    async def load_refresh_token(self, client_id: str, raw_refresh: str) -> Optional[ApiToken]:
        token = await self.token_repository.get_by_refresh_hash(_hash(raw_refresh))
        if token is None or token.revoked_at is not None:
            return None
        if token.oauth_client is None or token.oauth_client.client_id != client_id:
            return None
        now = datetime.now(timezone.utc)
        if token.refresh_expires_at is not None and token.refresh_expires_at <= now:
            return None
        return token

    async def exchange_refresh(self, token: ApiToken) -> Tuple[str, str, int]:
        """Rotate both halves of a token pair.

        Rotating the refresh token too — not just the access token — is what
        makes a leaked refresh token single-use: the old hash stops matching the
        moment a legitimate client refreshes, so the theft surfaces as the
        attacker's next call failing rather than as silent shared access.

        Re-keys the same row, so ``scope`` and ``read_only`` survive untouched.
        A refresh can therefore never widen a grant: raising a read-only
        connection to a writing one takes a fresh trip through consent.
        """
        now = datetime.now(timezone.utc)
        access_raw = MCP_TOKEN_PREFIX + secrets.token_urlsafe(32)
        refresh_raw = MCP_REFRESH_PREFIX + secrets.token_urlsafe(32)
        rotated = await self.token_repository.rotate_refresh(
            token,
            token_hash=_hash(access_raw),
            token_prefix=access_raw[:17],
            expires_at=now + ACCESS_TOKEN_TTL,
            refresh_token_hash=_hash(refresh_raw),
            refresh_expires_at=now + REFRESH_TOKEN_TTL,
        )
        if not rotated:
            raise ValueError('This refresh token has already been used')
        return access_raw, refresh_raw, int(ACCESS_TOKEN_TTL.total_seconds())

    async def load_access_token(self, raw_token: str) -> Optional[ApiToken]:
        result = await ApiTokenRepository.get_by_hash(_hash(raw_token))
        if result is None or result.revoked_at is not None:
            return None
        if result.origin != ApiTokenOrigin.OAUTH.value:
            return None
        now = datetime.now(timezone.utc)
        if result.expires_at is not None and result.expires_at <= now:
            return None
        return result

    async def revoke(self, token: ApiToken) -> None:
        await self.token_repository.revoke(token, datetime.now(timezone.utc))
