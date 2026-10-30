"""
OAuth App Service - Business Logic Layer

Registration and management of third-party apps for the ``/oauth``
authorization server: create, edit, rotate the secret, delete, and the
platform admin's disable switch.

Apps are **global**. Anyone signed in may register up to
:data:`MAX_APPS_PER_OWNER`; registering grants nothing, because every grant
still needs a person to approve it on the consent screen, in one community that
has the ``third_party_apps`` flag on. That is why registration itself is not
behind the flag: the flag decides where an app may be *used*.

Everything an owner types (name, description, homepage) is untrusted player
input and is kept as plain text with tight length caps.
"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple, Union
from urllib.parse import urlsplit

from application.api_scopes import ApiScope, format_scope
from application.errors import NotFoundError
from application.repositories.api_token_repository import ApiTokenRepository
from application.repositories.oauth_app_repository import OAuthAppRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.mcp_auth_service import is_allowed_redirect_uri
from application.utils.slot_limit import SlotLimiter
from models import OAuthApp, OAuthClientType, User

logger = logging.getLogger(__name__)

CLIENT_ID_PREFIX = 'wza_'
CLIENT_SECRET_PREFIX = 'wzs_'
MAX_APPS_PER_OWNER = 10
APPS_CREATED_PER_DAY = 5
MAX_REDIRECT_URIS = 10
NAME_MAX = 60
DESCRIPTION_MAX = 300
SECRET_GRACE = timedelta(hours=24)

_CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]')
_LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})
_creations = SlotLimiter(APPS_CREATED_PER_DAY, 24 * 3600)


def reset_registration_limits() -> None:
    _creations.reset()


def browser_origin(uri: str) -> Optional[str]:
    """The web origin a redirect URI's page runs on, or None for an app scheme.

    https anywhere, or http on loopback for an app in development; the scheme
    and host are lower-cased and a default port is dropped, as browsers send it.
    """
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or '').lower()
    if not host or not (scheme == 'https' or (scheme == 'http' and host in _LOOPBACK_HOSTS)):
        return None
    if ':' in host:
        host = f'[{host}]'
    default = 443 if scheme == 'https' else 80
    return f'{scheme}://{host}' + (f':{port}' if port and port != default else '')


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _plain_text(value: Optional[str], *, field: str, max_len: int, required: bool) -> str:
    """One line of plain text: control and bidi characters stripped, spaces collapsed."""
    text = ' '.join(_CONTROL_CHARS.sub(' ', value or '').split())
    if required and not text:
        raise ValueError(f'{field} is required.')
    if len(text) > max_len:
        raise ValueError(f'{field} can be at most {max_len} characters.')
    return text


def _homepage(value: Optional[str]) -> Optional[str]:
    url = (value or '').strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValueError('Homepage must be an https:// address.') from None
    if parts.scheme.lower() != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Homepage must be an https:// address.')
    if len(url) > 512:
        raise ValueError('Homepage address is too long.')
    return url


def _redirect_uris(values: Iterable[str], client_type: OAuthClientType) -> List[str]:
    """Exact-match redirect URIs, under the MCP server's rules.

    A confidential app runs on a server, so it gets https or http to loopback
    only; a native app scheme (``com.example.app:/cb``) is for public apps,
    the RFC 8252 case it exists for.
    """
    uris: List[str] = []
    for raw in values or []:
        uri = (raw or '').strip()
        if not uri or uri in uris:
            continue
        if len(uri) > 2048 or not is_allowed_redirect_uri(uri):
            raise ValueError(
                'Redirect addresses must be https, http to localhost, or an app scheme, '
                'with no #fragment.'
            )
        parts = urlsplit(uri)
        if parts.username or parts.password:
            raise ValueError('Redirect addresses cannot carry a username or password.')
        if client_type == OAuthClientType.CONFIDENTIAL:
            scheme = parts.scheme.lower()
            loopback = scheme == 'http' and (parts.hostname or '').lower() in _LOOPBACK_HOSTS
            if scheme != 'https' and not loopback:
                raise ValueError('An app with a secret must redirect to an https:// address.')
        uris.append(uri)
    if not uris:
        raise ValueError('Add at least one redirect address.')
    if len(uris) > MAX_REDIRECT_URIS:
        raise ValueError(f'An app can have at most {MAX_REDIRECT_URIS} redirect addresses.')
    return uris


def strict_scopes(values: Union[str, Iterable[Union[ApiScope, str]], None]) -> FrozenSet[ApiScope]:
    """The scopes an app or a person named, refusing any name we don't know.

    ``api_scopes.parse_scope`` drops unknown names so stored grants survive a
    retired scope; a fresh request must hear about its typo instead.
    """
    names = (values or '').split() if isinstance(values, str) or values is None else list(values)
    known = {scope.value: scope for scope in ApiScope}
    unknown = sorted({str(getattr(n, 'value', n)) for n in names} - set(known))
    if unknown:
        raise ValueError(f"Unknown scope: {', '.join(unknown)}.")
    return frozenset(known[getattr(n, 'value', n)] for n in names)


def _scopes(values: Iterable[str]) -> List[str]:
    scopes = strict_scopes(values)
    if not scopes:
        raise ValueError('Pick at least one thing the app may ask for.')
    return format_scope(scopes).split()


def _new_secret() -> Tuple[str, str]:
    raw = CLIENT_SECRET_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:10]


def secret_matches(app: OAuthApp, presented: str, now: Optional[datetime] = None) -> bool:
    """Whether ``presented`` is the app's secret, or its previous one inside the grace period."""
    if not presented or not app.client_secret_hash:
        return False
    digest = hash_secret(presented)
    if hmac.compare_digest(digest, app.client_secret_hash):
        return True
    if app.previous_secret_hash and app.secret_rotated_at is not None:
        now = now or datetime.now(timezone.utc)
        if now - app.secret_rotated_at <= SECRET_GRACE:
            return hmac.compare_digest(digest, app.previous_secret_hash)
    return False


class OAuthAppService:
    """Owner and platform-admin operations on third-party apps."""

    def __init__(self) -> None:
        self.repository = OAuthAppRepository()
        self.token_repository = ApiTokenRepository()
        self.audit_service = AuditService()

    async def create_app(
        self,
        actor: User,
        *,
        name: str,
        client_type: OAuthClientType | str,
        redirect_uris: Iterable[str],
        allowed_scopes: Iterable[str],
        description: Optional[str] = None,
        homepage_url: Optional[str] = None,
    ) -> Tuple[OAuthApp, Optional[str]]:
        """Register an app for ``actor``. Returns ``(app, raw_secret)``.

        The secret is ``None`` for a public app, and is shown only here for a
        confidential one: only its hash is kept.
        """
        try:
            kind = OAuthClientType(client_type)
        except ValueError:
            raise ValueError('Choose whether the app can keep a secret.') from None
        if await self.repository.count_for_owner(actor) >= MAX_APPS_PER_OWNER:
            raise ValueError(f'You can register at most {MAX_APPS_PER_OWNER} apps.')
        name = _plain_text(name, field='Name', max_len=NAME_MAX, required=True)
        description = _plain_text(description, field='Description', max_len=DESCRIPTION_MAX, required=False)
        homepage_url = _homepage(homepage_url)
        uris = _redirect_uris(redirect_uris, kind)
        scopes = _scopes(allowed_scopes)
        if not _creations.take(actor.id):
            raise ValueError(
                f'You can register {APPS_CREATED_PER_DAY} apps a day. Edit one you have, or try again tomorrow.'
            )

        secret, prefix = _new_secret() if kind == OAuthClientType.CONFIDENTIAL else (None, None)
        app = await self.repository.create_app(
            owner=actor,
            name=name,
            description=description,
            homepage_url=homepage_url,
            client_type=kind,
            client_id=CLIENT_ID_PREFIX + secrets.token_urlsafe(18),
            redirect_uris=uris,
            allowed_scopes=scopes,
            client_secret_hash=hash_secret(secret) if secret else None,
            secret_prefix=prefix,
        )
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_CREATED,
            {'app_id': app.id, 'name': app.name, 'client_type': kind.value},
        )
        return app, secret

    async def browser_origins(self) -> FrozenSet[str]:
        """Web origins allowed to call ``/api`` and ``/oauth/token`` from a browser.

        Taken from live public apps' redirect URIs: a public app is the only
        kind that runs in a browser, and its page is served from the origin it
        redirects to. A confidential app talks from its server, which needs no
        CORS. Not behind the flag: a community with apps off still refuses the
        call itself, and the list only says who may ask.
        """
        origins = set()
        for uris in await self.repository.public_redirect_uris():
            for uri in uris or []:
                origin = browser_origin(uri)
                if origin:
                    origins.add(origin)
        return frozenset(origins)

    async def list_my_apps(self, actor: User) -> List[OAuthApp]:
        return await self.repository.list_for_owner(actor)

    async def get_my_app(self, actor: User, app_id: int) -> OAuthApp:
        """The actor's own app; someone else's reads as missing, never as forbidden."""
        app = await self.repository.get_by_id(app_id)
        if app is None or app.owner_id != actor.id:
            raise NotFoundError('App not found.')
        return app

    async def update_app(
        self,
        actor: User,
        app_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        homepage_url: Optional[str] = None,
        redirect_uris: Optional[Iterable[str]] = None,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> OAuthApp:
        """Edit an app. Omitted fields stay; ``homepage_url=''`` clears it.

        Narrowing ``allowed_scopes`` changes what the app may ask for next time;
        grants people already approved keep what they approved until revoked.
        """
        app = await self.get_my_app(actor, app_id)
        changes: dict = {}
        if name is not None:
            changes['name'] = _plain_text(name, field='Name', max_len=NAME_MAX, required=True)
        if description is not None:
            changes['description'] = _plain_text(
                description, field='Description', max_len=DESCRIPTION_MAX, required=False,
            )
        if homepage_url is not None:
            changes['homepage_url'] = _homepage(homepage_url)
        if redirect_uris is not None:
            changes['redirect_uris'] = _redirect_uris(redirect_uris, app.client_type)
        if allowed_scopes is not None:
            changes['allowed_scopes'] = _scopes(allowed_scopes)
        if not changes:
            return app
        await self.repository.update(app, **changes)
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_UPDATED,
            {'app_id': app.id, 'changed': sorted(changes)},
        )
        return app

    async def rotate_secret(self, actor: User, app_id: int) -> str:
        """Issue a new secret; the old one keeps working for :data:`SECRET_GRACE`."""
        app = await self.get_my_app(actor, app_id)
        if app.client_type != OAuthClientType.CONFIDENTIAL:
            raise ValueError('This app has no secret to replace.')
        secret, prefix = _new_secret()
        await self.repository.update(
            app,
            previous_secret_hash=app.client_secret_hash,
            client_secret_hash=hash_secret(secret),
            secret_prefix=prefix,
            secret_rotated_at=datetime.now(timezone.utc),
        )
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_SECRET_ROTATED, {'app_id': app.id, 'name': app.name},
        )
        return secret

    async def delete_app(self, actor: User, app_id: int) -> int:
        """Delete an app and end every grant it holds. Returns how many grants ended."""
        app = await self.get_my_app(actor, app_id)
        revoked = await self.token_repository.revoke_app_grants_for_app(app.id, datetime.now(timezone.utc))
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_DELETED,
            {'app_id': app.id, 'name': app.name, 'grants_revoked': revoked},
        )
        await self.repository.delete(app)
        return revoked

    async def connected_user_count(self, actor: User, app_id: int) -> int:
        """How many people have this app connected. The owner sees a count, never who."""
        app = await self.get_my_app(actor, app_id)
        return await self.token_repository.count_app_grant_users(app.id)

    async def list_all_apps(self, actor: User) -> List[OAuthApp]:
        await AuthService.ensure_super_admin(actor)
        return await self.repository.list_all()

    async def platform_reach(self, actor: User) -> Dict[int, Tuple[int, int]]:
        """Per app id: how many people connected it, in how many communities."""
        await AuthService.ensure_super_admin(actor)
        return await self.token_repository.app_grant_reach()

    async def disable_app(self, actor: User, app_id: int, reason: str) -> OAuthApp:
        """Stop an app everywhere. Its grants stop working at once but are kept,
        so re-enabling restores them."""
        await AuthService.ensure_super_admin(actor)
        app = await self._get_any(app_id)
        reason = _plain_text(reason, field='Reason', max_len=300, required=True)
        await self.repository.update(app, disabled_at=datetime.now(timezone.utc), disabled_reason=reason)
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_DISABLED, {'app_id': app.id, 'name': app.name, 'reason': reason},
        )
        return app

    async def enable_app(self, actor: User, app_id: int) -> OAuthApp:
        await AuthService.ensure_super_admin(actor)
        app = await self._get_any(app_id)
        await self.repository.update(app, disabled_at=None, disabled_reason=None)
        await self.audit_service.write_log(
            actor, AuditActions.OAUTH_APP_ENABLED, {'app_id': app.id, 'name': app.name},
        )
        return app

    async def _get_any(self, app_id: int) -> OAuthApp:
        app = await self.repository.get_by_id(app_id)
        if app is None:
            raise NotFoundError('App not found.')
        return app
