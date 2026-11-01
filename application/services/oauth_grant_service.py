"""
OAuth Grant Service - Business Logic Layer

The authorization server behind ``/oauth`` for third-party apps: the
authorization request, the consent decision, the code and refresh exchanges,
revocation, and the per-request check that an app grant may still be used.

The flow is authorization code + PKCE (S256) for every app; confidential apps
also authenticate with their secret. A grant is an :class:`~models.ApiToken`
with ``origin='app'``, bound to the one community the person picked on the
consent screen and to the scopes they approved, so the REST API resolves it
exactly like a personal access token: tenant from the token, membership
re-checked on every request.

The ``third_party_apps`` flag is checked wherever a community comes into play:
offering it on consent, approving, both exchanges, and every API request. A
community that turns the flag off stops its grants at once without revoking
them.

Free of FastAPI and HTTP: protocol failures are :class:`OAuthError` carrying
the RFC 6749 error code, and ``api/oauth_server.py`` turns them into responses.
Only hashes are stored, for codes and both halves of a token pair.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Iterable, List, Optional, Set
from urllib.parse import urlencode, urlsplit, urlunsplit

from application.api_scopes import STAFF_SCOPES, WRITE_SCOPES, ApiScope, format_scope, parse_scope
from application.errors import FeatureDisabledError, NotFoundError
from application.events import EventType
from application.repositories.api_token_repository import ApiTokenRepository
from application.repositories.oauth_app_repository import OAuthAppRepository
from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.services.api_token_service import APP_REFRESH_PREFIX, APP_TOKEN_PREFIX
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.mcp_auth_service import is_allowed_redirect_uri
from application.services.oauth_app_service import secret_matches, strict_scopes
from application.services.tenant_service import TenantService
from application.tenant_context import tenant_scope
from application.utils.environment import get_base_url
from application.utils.parked_requests import prune_parked
from models import ApiToken, FeatureFlag, OAuthApp, OAuthClientType, User

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=5)
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)
PENDING_TTL = timedelta(minutes=15)
CONSENT_PATH = '/oauth/consent'

_VERIFIER_RE = re.compile(r'^[A-Za-z0-9\-._~]{43,128}$')
_CHALLENGE_RE = re.compile(r'^[A-Za-z0-9\-_]{43}$')
_STATE_MAX = 1024


def issuer() -> str:
    """The authorization server's issuer identifier (RFC 8414)."""
    return f'{get_base_url()}/oauth'


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def _with_query(uri: str, params: Dict[str, Optional[str]]) -> str:
    """Append parameters to a registered redirect URI, keeping any query it has."""
    parts = urlsplit(uri)
    extra = urlencode({k: v for k, v in params.items() if v is not None})
    query = f'{parts.query}&{extra}' if parts.query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ''))


class OAuthError(ValueError):
    """A protocol failure with its RFC 6749 error code.

    ``redirect_uri`` is set only once the client and redirect URI have been
    verified; until then an error must be shown to the person, never sent to
    an address the request merely claimed (RFC 6749 §4.1.2.1).
    """

    def __init__(
        self,
        error: str,
        description: str,
        *,
        status: int = 400,
        redirect_uri: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status
        self.redirect_uri = redirect_uri
        self.state = state

    def redirect_url(self) -> Optional[str]:
        if self.redirect_uri is None:
            return None
        return _with_query(self.redirect_uri, {
            'error': self.error, 'error_description': self.description,
            'state': self.state, 'iss': issuer(),
        })


@dataclass
class PendingAppAuthorization:
    """An authorization request parked while the person signs in and decides."""

    app_id: int
    redirect_uri: str
    code_challenge: str
    requested_scopes: FrozenSet[ApiScope]
    state: Optional[str]
    created_at: datetime


@dataclass
class ConsentCommunity:
    tenant_id: int
    name: str
    slug: str
    scopes: List[ApiScope]


@dataclass
class ConsentView:
    """Everything the consent screen shows. Plain values; the page renders them as text."""

    app_name: str
    app_description: str
    homepage_host: Optional[str]
    owner_name: str
    redirect_host: str
    verified: bool
    requested_scopes: List[ApiScope]
    communities: List[ConsentCommunity] = field(default_factory=list)


@dataclass
class CommunityApp:
    """One app as this community's staff see it. Plain values; rendered as text."""

    app_id: int
    name: str
    owner_name: str
    homepage_host: Optional[str]
    members: int
    blocked: bool
    disabled: bool


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    token_type: str = 'Bearer'

    def as_dict(self) -> dict:
        return {
            'access_token': self.access_token,
            'token_type': self.token_type,
            'expires_in': self.expires_in,
            'refresh_token': self.refresh_token,
            'scope': self.scope,
        }


_pending: Dict[str, PendingAppAuthorization] = {}


def _app_usable(app: Optional[OAuthApp]) -> bool:
    if app is None or app.disabled_at is not None:
        return False
    owner = getattr(app, 'owner', None)
    return owner is None or bool(getattr(owner, 'is_active', True))


class OAuthGrantService:
    """Authorization-server operations for third-party apps."""

    def __init__(self) -> None:
        self.repository = OAuthAppRepository()
        self.token_repository = ApiTokenRepository()
        self.audit_service = AuditService()

    async def begin_authorization(
        self,
        *,
        client_id: Optional[str],
        redirect_uri: Optional[str],
        response_type: Optional[str],
        code_challenge: Optional[str],
        code_challenge_method: Optional[str],
        scope: Optional[str],
        state: Optional[str],
    ) -> str:
        """Validate an authorization request and park it. Returns its id.

        The client and redirect URI are checked first and their failures are
        never redirectable. Everything after that is reported to the app at
        its verified redirect URI.
        """
        app = await self.repository.get_by_client_id(client_id or '')
        if not _app_usable(app):
            raise OAuthError('invalid_client', 'This app is not registered with Wizzrobe, or has been disabled.')
        assert app is not None
        if not redirect_uri or redirect_uri not in (app.redirect_uris or []) or not is_allowed_redirect_uri(redirect_uri):
            raise OAuthError('invalid_request', "This app sent a redirect address it hasn't registered.")

        def fail(error: str, description: str) -> OAuthError:
            return OAuthError(error, description, redirect_uri=redirect_uri, state=state)

        if state is not None and len(state) > _STATE_MAX:
            raise fail('invalid_request', 'state is too long.')
        if response_type != 'code':
            raise fail('unsupported_response_type', 'Only response_type=code is supported.')
        if not code_challenge or code_challenge_method != 'S256':
            raise fail('invalid_request', 'PKCE is required: send code_challenge with code_challenge_method=S256.')
        if not _CHALLENGE_RE.match(code_challenge):
            raise fail('invalid_request', 'code_challenge is not a valid S256 challenge.')
        try:
            requested = strict_scopes(scope)
        except ValueError as exc:
            raise fail('invalid_scope', str(exc)) from None
        allowed = parse_scope(' '.join(app.allowed_scopes or []))
        if not requested:
            requested = allowed
        if not requested <= allowed:
            raise fail('invalid_scope', 'This app asked for more than it registered for.')

        self._sweep_pending(room_for=1)
        txn_id = secrets.token_urlsafe(24)
        _pending[txn_id] = PendingAppAuthorization(
            app_id=app.id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            requested_scopes=frozenset(requested),
            state=state,
            created_at=datetime.now(timezone.utc),
        )
        return txn_id

    @staticmethod
    def consent_url(txn_id: str) -> str:
        return f'{CONSENT_PATH}?{urlencode({"request": txn_id})}'

    def get_pending(self, txn_id: str) -> PendingAppAuthorization:
        self._sweep_pending()
        pending = _pending.get(txn_id or '')
        if pending is None:
            raise NotFoundError('This sign-in request has expired. Start again from the app.')
        return pending

    @staticmethod
    def _sweep_pending(room_for: int = 0) -> None:
        prune_parked(_pending, PENDING_TTL, room_for=room_for)

    async def consent_view(self, txn_id: str, user: User) -> ConsentView:
        """What the consent screen offers ``user`` for this request.

        Only communities they belong to that are active and have the flag on,
        and in each, only the scopes they could actually use there.
        """
        pending = self.get_pending(txn_id)
        app = await self._usable_app(pending.app_id)
        communities: List[ConsentCommunity] = []
        for membership in await TenantMembershipRepository.list_for_user(user):
            offer = await self._offer(user, membership.tenant_id, pending.app_id, pending.requested_scopes)
            if offer is not None:
                communities.append(offer)
        homepage_host = urlsplit(app.homepage_url).hostname if app.homepage_url else None
        return ConsentView(
            app_name=app.name,
            app_description=app.description or '',
            homepage_host=homepage_host,
            owner_name=app.owner.preferred_name,
            redirect_host=self._redirect_label(pending.redirect_uri),
            verified=False,
            requested_scopes=[s for s in ApiScope if s in pending.requested_scopes],
            communities=communities,
        )

    async def approve(
        self, txn_id: str, user: User, tenant_id: int, scopes: Iterable[ApiScope | str],
    ) -> str:
        """Record the person's approval and return the redirect back to the app.

        The grant carries exactly what they ticked, which must be inside what
        this community offers them. Validation runs before the request is
        consumed, so a mistake leaves the consent screen usable.
        """
        pending = self.get_pending(txn_id)
        offer = await self._offer(user, tenant_id, pending.app_id, pending.requested_scopes)
        if offer is None:
            raise ValueError("You can't connect this app to that community.")
        chosen = strict_scopes(scopes)
        if not chosen:
            raise ValueError('Tick at least one thing the app may do, or deny it.')
        if not chosen <= set(offer.scopes):
            raise ValueError("That includes something you can't grant in this community.")

        _pending.pop(txn_id, None)
        app = await self._usable_app(pending.app_id)
        now = datetime.now(timezone.utc)
        await self.repository.purge_expired_codes(now)
        raw_code = secrets.token_urlsafe(32)
        await self.repository.create_code(
            code_hash=_hash(raw_code),
            app=app,
            user=user,
            tenant_id=tenant_id,
            redirect_uri=pending.redirect_uri,
            code_challenge=pending.code_challenge,
            scope=format_scope(chosen),
            expires_at=now + CODE_TTL,
        )
        logger.info('App authorization approved: user=%s app=%s tenant=%s', user.id, app.id, tenant_id)
        return _with_query(pending.redirect_uri, {'code': raw_code, 'state': pending.state, 'iss': issuer()})

    async def approve_first_party(self, txn_id: str, user: User) -> Optional[str]:
        """Skip consent for Wizzrobe's own app and return the redirect back to it.

        Returns None for any other app, which goes through the consent screen.
        The code names no community: the app picks one on each call.
        """
        pending = self.get_pending(txn_id)
        app = await self._usable_app(pending.app_id)
        if not app.first_party:
            return None
        if not user.is_active:
            raise PermissionError('This account is inactive.')
        _pending.pop(txn_id, None)
        now = datetime.now(timezone.utc)
        await self.repository.purge_expired_codes(now)
        raw_code = secrets.token_urlsafe(32)
        await self.repository.create_code(
            code_hash=_hash(raw_code),
            app=app,
            user=user,
            tenant_id=None,
            redirect_uri=pending.redirect_uri,
            code_challenge=pending.code_challenge,
            scope=format_scope(pending.requested_scopes),
            expires_at=now + CODE_TTL,
        )
        logger.info('First-party sign-in: user=%s app=%s', user.id, app.id)
        return _with_query(pending.redirect_uri, {'code': raw_code, 'state': pending.state, 'iss': issuer()})

    def deny(self, txn_id: str) -> str:
        """Drop the request and return the redirect telling the app it was refused."""
        pending = self.get_pending(txn_id)
        _pending.pop(txn_id, None)
        return OAuthError(
            'access_denied', 'The person declined.',
            redirect_uri=pending.redirect_uri, state=pending.state,
        ).redirect_url() or pending.redirect_uri

    async def _offer(
        self, user: User, tenant_id: int, app_id: int, requested: FrozenSet[ApiScope],
    ) -> Optional[ConsentCommunity]:
        """What ``user`` may grant in ``tenant_id``, or None if nothing at all."""
        tenant = await TenantService.get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            return None
        if not await TenantService.is_member(user.id, tenant_id):
            return None
        if await self.repository.is_blocked(app_id, tenant_id):
            return None
        with tenant_scope(tenant_id):
            if not await FeatureFlagService().is_enabled(FeatureFlag.THIRD_PARTY_APPS):
                return None
            scopes: Set[ApiScope] = set(requested)
            if not await AuthService.can_use_staff_scopes(user):
                scopes -= STAFF_SCOPES
        if not scopes:
            return None
        return ConsentCommunity(
            tenant_id=tenant.id, name=tenant.name, slug=tenant.slug,
            scopes=[s for s in ApiScope if s in scopes],
        )

    @staticmethod
    def _redirect_label(uri: str) -> str:
        """The part of the redirect a person can trust: its host, or the app scheme."""
        parts = urlsplit(uri)
        return parts.netloc or f'{parts.scheme}:'

    async def _usable_app(self, app_id: int) -> OAuthApp:
        app = await self.repository.get_by_id(app_id)
        if not _app_usable(app):
            raise NotFoundError('This app is no longer available.')
        assert app is not None
        return app

    async def authenticate_client(self, client_id: Optional[str], client_secret: Optional[str]) -> OAuthApp:
        """Who is calling the token or revocation endpoint.

        A confidential app must present its secret; a public app must not
        present one, since a "secret" baked into a desktop or browser app is
        public and accepting it would suggest otherwise.
        """
        app = await self.repository.get_by_client_id(client_id or '')
        if app is None:
            raise OAuthError('invalid_client', 'Unknown client.', status=401)
        if app.client_type == OAuthClientType.CONFIDENTIAL:
            if not secret_matches(app, client_secret or ''):
                raise OAuthError('invalid_client', 'Client authentication failed.', status=401)
        elif client_secret:
            raise OAuthError('invalid_client', 'A public client has no secret; send client_id only.', status=401)
        if not _app_usable(app):
            raise OAuthError('invalid_client', 'This app has been disabled.', status=401)
        return app

    async def exchange_code(
        self, app: OAuthApp, *, code: Optional[str], redirect_uri: Optional[str], code_verifier: Optional[str],
    ) -> TokenResponse:
        if not code or not code_verifier:
            raise OAuthError('invalid_request', 'code and code_verifier are required.')
        record = await self.repository.get_code(_hash(code))
        if record is None or record.app_id != app.id:
            raise OAuthError('invalid_grant', 'Invalid authorization code.')
        now = datetime.now(timezone.utc)
        if record.consumed_at is not None:
            await self._revoke_replayed(record)
            raise OAuthError('invalid_grant', 'This authorization code has already been used.')
        if record.expires_at <= now:
            raise OAuthError('invalid_grant', 'This authorization code has expired.')
        if redirect_uri != record.redirect_uri:
            raise OAuthError('invalid_grant', 'redirect_uri does not match the authorization request.')
        if not _VERIFIER_RE.match(code_verifier) or not hmac.compare_digest(_s256(code_verifier), record.code_challenge):
            raise OAuthError('invalid_grant', 'PKCE verification failed.')
        if not await self.repository.consume_code(record, now):
            raise OAuthError('invalid_grant', 'This authorization code has already been used.')

        user = record.user
        if app.first_party:
            return await self._first_party_grant(app, user, record, now)
        assert record.tenant_id is not None
        with tenant_scope(record.tenant_id):
            await self._ensure_still_allowed(user, record.tenant_id, app.id)
            scopes = parse_scope(record.scope)
            replaced = await self.token_repository.live_app_grants(user.id, app.id, record.tenant_id)
            for old in replaced:
                await self.token_repository.revoke(old, now)
            access_raw, refresh_raw = self._new_pair()
            grant = await self.token_repository.create_app_grant(
                user=user,
                app=app,
                tenant_id=record.tenant_id,
                token_hash=_hash(access_raw),
                token_prefix=access_raw[:17],
                expires_at=now + ACCESS_TOKEN_TTL,
                refresh_token_hash=_hash(refresh_raw),
                refresh_expires_at=now + REFRESH_TOKEN_TTL,
                scope=record.scope,
                read_only=not (scopes & WRITE_SCOPES),
            )
            await self.repository.link_code_to_grant(record, grant)
            details = {'app_id': app.id, 'app': app.name, 'scope': record.scope, 'replaced': len(replaced)}
            await self.audit_service.write_and_publish(
                user, AuditActions.OAUTH_GRANT_CREATED, details, EventType.OAUTH_GRANT_CREATED,
                event_extra={'user_id': user.id},
            )
        return TokenResponse(access_raw, refresh_raw, int(ACCESS_TOKEN_TTL.total_seconds()), record.scope)

    async def exchange_refresh(
        self, app: OAuthApp, *, refresh_token: Optional[str], scope: Optional[str] = None,
    ) -> TokenResponse:
        """Rotate both halves of a grant's token pair.

        A refresh can narrow a grant (``scope``) but never widen it. Presenting
        a refresh token that has already been rotated away is treated as theft:
        whoever holds the live one and whoever holds the spent one can't both
        be the app, so the grant is revoked for both.
        """
        if not refresh_token:
            raise OAuthError('invalid_request', 'refresh_token is required.')
        digest = _hash(refresh_token)
        grant = await self.token_repository.get_app_grant_by_refresh_hash(digest)
        if grant is None:
            await self._handle_possible_reuse(app, digest)
            raise OAuthError('invalid_grant', 'Invalid refresh token.')
        now = datetime.now(timezone.utc)
        if grant.oauth_app_id != app.id or grant.revoked_at is not None:
            raise OAuthError('invalid_grant', 'Invalid refresh token.')
        if grant.refresh_expires_at is not None and grant.refresh_expires_at <= now:
            raise OAuthError('invalid_grant', 'This refresh token has expired. Sign in again.')

        current = parse_scope(grant.scope)
        narrowed: Optional[Set[ApiScope]] = None
        if scope is not None:
            try:
                narrowed = strict_scopes(scope)
            except ValueError as exc:
                raise OAuthError('invalid_scope', str(exc)) from None
            if not narrowed or not narrowed <= current:
                raise OAuthError('invalid_scope', 'A refresh can only keep or narrow what was approved.')

        if app.first_party:
            if grant.tenant_id is not None or not grant.user.is_active:
                raise OAuthError('invalid_grant', 'This account is inactive.')
        else:
            assert grant.tenant_id is not None
            with tenant_scope(grant.tenant_id):
                await self._ensure_still_allowed(grant.user, grant.tenant_id, app.id)
        access_raw, refresh_raw = self._new_pair()
        final = narrowed if narrowed is not None else current
        rotated = await self.token_repository.rotate_refresh(
            grant,
            token_hash=_hash(access_raw),
            token_prefix=access_raw[:17],
            expires_at=now + ACCESS_TOKEN_TTL,
            refresh_token_hash=_hash(refresh_raw),
            refresh_expires_at=now + REFRESH_TOKEN_TTL,
            remember_previous=True,
            scope=format_scope(final) if narrowed is not None else None,
            read_only=(not (final & WRITE_SCOPES)) if narrowed is not None else None,
        )
        if not rotated:
            raise OAuthError('invalid_grant', 'This refresh token has already been used.')
        return TokenResponse(access_raw, refresh_raw, int(ACCESS_TOKEN_TTL.total_seconds()), format_scope(final))

    async def revoke_token(self, app: OAuthApp, token: Optional[str]) -> None:
        """RFC 7009: either half ends the whole grant. Unknown tokens are not an error."""
        if not token:
            return
        digest = _hash(token)
        grant = (
            await self.token_repository.get_app_grant_by_hash(digest)
            or await self.token_repository.get_app_grant_by_refresh_hash(digest)
        )
        if grant is None or grant.oauth_app_id != app.id or grant.revoked_at is not None:
            return
        await self._revoke_grant(grant, app, AuditActions.OAUTH_GRANT_REVOKED, 'app')

    async def ensure_grant_usable(self, token: ApiToken) -> None:
        """Refuse an app grant whose app is disabled or whose community has the flag off.

        Called by the REST API after it has bound the grant's community, so the
        flag is read for that community. Raises ``PermissionError`` for a dead
        app and :class:`~application.errors.FeatureDisabledError` for the flag.
        """
        if not _app_usable(token.oauth_app):
            raise PermissionError('This app has been disabled.')
        await FeatureFlagService().ensure_enabled(FeatureFlag.THIRD_PARTY_APPS)
        if (
            token.tenant_id is not None and token.oauth_app_id is not None
            and await self.repository.is_blocked(token.oauth_app_id, token.tenant_id)
        ):
            raise PermissionError('This community has blocked this app.')

    async def list_community_apps(self, actor: User) -> List[CommunityApp]:
        """Apps members of this community have connected, plus any it blocks.

        Staff only. Counts people, never names them, and never shows another
        community's grants.
        """
        await self._ensure_staff_here(actor)
        users = await self.token_repository.app_grant_users_here()
        blocked = set(await self.repository.blocked_app_ids())
        apps = await self.repository.list_by_ids(sorted(set(users) | blocked))
        return [
            CommunityApp(
                app_id=app.id,
                name=app.name,
                owner_name=app.owner.preferred_name,
                homepage_host=urlsplit(app.homepage_url).hostname if app.homepage_url else None,
                members=users.get(app.id, 0),
                blocked=app.id in blocked,
                disabled=app.disabled_at is not None,
            )
            for app in apps
        ]

    async def block_app_here(self, actor: User, app_id: int) -> int:
        """Block an app in this community and end its grants here. Returns how many ended."""
        await self._ensure_staff_here(actor)
        app = await self.repository.get_by_id(app_id)
        if app is None:
            raise NotFoundError('App not found.')
        if not await self.repository.block(app, actor):
            return 0
        revoked = await self.token_repository.revoke_app_grants_here(app.id, datetime.now(timezone.utc))
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_BLOCKED,
            {'app_id': app.id, 'name': app.name, 'grants_revoked': revoked},
        )
        return revoked

    async def unblock_app_here(self, actor: User, app_id: int) -> None:
        """Let members connect an app again. Grants ended by the block stay ended."""
        await self._ensure_staff_here(actor)
        app = await self.repository.get_by_id(app_id)
        if app is None or not await self.repository.unblock(app.id):
            raise NotFoundError('That app is not blocked here.')
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_UNBLOCKED, {'app_id': app.id, 'name': app.name},
        )

    @staticmethod
    async def _ensure_staff_here(actor: User) -> None:
        """Staff of this community, with apps turned on here."""
        await FeatureFlagService().ensure_enabled(FeatureFlag.THIRD_PARTY_APPS)
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only staff can manage connected apps.')

    @staticmethod
    def _new_pair() -> tuple[str, str]:
        return APP_TOKEN_PREFIX + secrets.token_urlsafe(32), APP_REFRESH_PREFIX + secrets.token_urlsafe(32)

    async def _first_party_grant(self, app: OAuthApp, user: User, record, now: datetime) -> TokenResponse:
        """A grant for Wizzrobe's own app: no community, nothing replaced.

        Earlier grants stay live so signing in on a second device doesn't sign
        the first one out. Each REST call names its community and is checked
        for membership there.
        """
        if record.tenant_id is not None:
            raise OAuthError('invalid_grant', 'Invalid authorization code.')
        if not user.is_active:
            raise OAuthError('invalid_grant', 'This account is inactive.')
        access_raw, refresh_raw = self._new_pair()
        grant = await self.token_repository.create_app_grant(
            user=user,
            app=app,
            tenant_id=None,
            token_hash=_hash(access_raw),
            token_prefix=access_raw[:17],
            expires_at=now + ACCESS_TOKEN_TTL,
            refresh_token_hash=_hash(refresh_raw),
            refresh_expires_at=now + REFRESH_TOKEN_TTL,
            scope=record.scope,
            read_only=False,
        )
        await self.repository.link_code_to_grant(record, grant)
        await self.audit_service.write_log(
            user, AuditActions.OAUTH_GRANT_CREATED, {'app_id': app.id, 'app': app.name, 'first_party': True},
        )
        return TokenResponse(access_raw, refresh_raw, int(ACCESS_TOKEN_TTL.total_seconds()), record.scope)

    async def _ensure_still_allowed(self, user: User, tenant_id: int, app_id: int) -> None:
        """Everything that may have changed since consent: the flag, the
        community, a block on the app there, the account, the membership.
        Runs inside ``tenant_scope``."""
        try:
            await FeatureFlagService().ensure_enabled(FeatureFlag.THIRD_PARTY_APPS)
        except FeatureDisabledError:
            raise OAuthError('invalid_grant', 'Third-party apps are turned off in this community.') from None
        tenant = await TenantService.get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            raise OAuthError('invalid_grant', 'This community is inactive.')
        if not user.is_active:
            raise OAuthError('invalid_grant', 'This account is inactive.')
        if await self.repository.is_blocked(app_id, tenant_id):
            raise OAuthError('invalid_grant', 'This community has blocked this app.')
        if not await TenantService.is_member(user.id, tenant_id):
            raise OAuthError('invalid_grant', 'You are no longer a member of this community.')

    async def _revoke_replayed(self, record) -> None:
        """A used code came back: revoke what it was exchanged for (RFC 6749 §4.1.2)."""
        if record.grant_id is None:
            return
        grant = await self.token_repository.get_app_grant_by_id(record.grant_id)
        if grant is not None and grant.revoked_at is None:
            await self._revoke_grant(grant, record.app, AuditActions.OAUTH_GRANT_REVOKED, 'code_replayed')

    async def _handle_possible_reuse(self, app: OAuthApp, digest: str) -> None:
        grant = await self.token_repository.get_app_grant_by_previous_refresh_hash(digest)
        if grant is None or grant.oauth_app_id != app.id or grant.revoked_at is not None:
            return
        logger.warning('Refresh token reuse: grant=%s app=%s; revoking', grant.id, app.id)
        await self._revoke_grant(grant, app, AuditActions.OAUTH_GRANT_REFRESH_REUSED, 'refresh_reused')

    async def _revoke_grant(self, grant: ApiToken, app: OAuthApp, action: str, reason: str) -> None:
        await self.token_repository.revoke(grant, datetime.now(timezone.utc))
        details = {'app_id': app.id, 'app': app.name, 'token_id': grant.id, 'reason': reason}
        with tenant_scope(grant.tenant_id):
            await self.audit_service.write_and_publish(
                grant.user, action, details, EventType.OAUTH_GRANT_REVOKED,
                event_extra={'user_id': grant.user_id},
            )
