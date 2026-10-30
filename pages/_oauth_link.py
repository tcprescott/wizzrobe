"""Shared scaffolding for OAuth identity-link pages.

racetime.gg and Twitch each expose the same single-flow link: a logged-in user
authorizes with the provider (read scope), we exchange the returned code once for
their verified identity and record it, then discard the token. The two page
modules were line-for-line identical except the service class, storage keys,
route paths, and user-facing strings; :func:`register_identity_link_pages`
parameterises that shape.

``challonge_oauth.py`` has its own two-flow (service + player) page registration
because its single callback is shared between the two flows, but it reuses the
:func:`read_callback_code` / :func:`returned_state` helpers here (and the player
link handoff below) so the callback-URL parsing lives in exactly one place.

Custom-domain handoff (``HOST_OAUTH_MODE=handoff``)
---------------------------------------------------
On a custom domain each provider callback still lands on the **platform** host
(one registered redirect URI per provider), whose session can't see the custom
domain's cookie — so the link would have no logged-in user to attach to. Today
those affordances are simply blocked on custom domains ("Main site only"). This
module mirrors the Discord-login handoff (:mod:`pages.auth`): the provider OAuth
runs on the platform host, then the **verified provider identity** (id + name —
public, exactly like the login handoff's Discord id/name) is handed back to the
custom domain through a single-use, host-bound, browser-bound signed token, where
the user's session lives and the link (with its tenant-scoped audit) is recorded.
Only public identity crosses the wire; the provider access token is exchanged and
discarded on the platform host, never handed across.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

from nicegui import Client, app, ui
from starlette.requests import Request
from starlette.responses import RedirectResponse

from application.services import oauth_handoff_service as handoff_service
from application.services.auth_service import get_user_from_session
from application.services.tenant_service import TenantService
from application.tenant_context import get_current_tenant_id, is_host_mode, tenant_scope
from application.utils.environment import (
    get_base_url,
    get_platform_host,
    host_oauth_handoff_enabled,
)
from application.utils.hostname import normalize_hostname, scheme_for_host
from application.utils.tenant_urls import safe_next
from models import User
from pages._browser_url import browser_url
from theme.notice import stash_notice

logger = logging.getLogger(__name__)

__all__ = [
    'IdentityLinkFlow',
    'LinkHandoffProvider',
    'handle_link_handoff_callback',
    'maybe_start_link_handoff',
    'platform_link_redirect',
    'read_callback_code',
    'register_identity_link_pages',
    'register_link_handoff_pages',
    'register_link_handoff_provider',
    'returned_state',
]


async def platform_link_redirect(local_return: str) -> Optional[str]:
    """Where to send a secondary OAuth link that was initiated on a custom domain.

    The secondary providers (Challonge, Twitch, racetime, Discord connect) all
    redirect to a single registered callback on the **platform** host, which
    can't see a custom domain's host-only session cookie — so the flow would
    silently fail there. In host mode, send the user to the equivalent path-mode
    URL on the platform host, where linking works. Returns ``None`` in path mode
    / platform surface (the flow runs in place).

    This is the **Design A** fallback: when ``HOST_OAUTH_MODE=handoff`` is on,
    :func:`maybe_start_link_handoff` runs the cleaner cross-host handoff instead,
    and callers only fall through to this detour with handoff off.
    """
    if not is_host_mode():
        return None
    tid = get_current_tenant_id()
    tenant = await TenantService.get_by_id(tid) if tid is not None else None
    base = get_base_url()
    if tenant is None:
        return base or '/'
    return f'{base}/t/{tenant.slug}{local_return}'


def returned_state(url: str) -> Optional[str]:
    """Extract the OAuth ``state`` parameter from a callback URL, if present."""
    try:
        params = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    return next(iter(params.get('state', [])), None)


def read_callback_code(
    url: str, expected_state: Optional[str], provider_label: str,
) -> Optional[str]:
    """Validate the OAuth callback URL and return the authorization code or None."""
    try:
        params = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    if 'error' in params:
        logger.warning('%s OAuth callback returned error: %s', provider_label, params.get('error'))
        return None
    state = next(iter(params.get('state', [])), None)
    if not expected_state or state != expected_state:
        logger.warning('%s OAuth state mismatch on callback.', provider_label)
        return None
    return next(iter(params.get('code', [])), None)


@dataclass(frozen=True)
class LinkHandoffProvider:
    """What the shared handoff routes need to complete a link for a provider.

    ``authorize_url`` runs on the platform host (start leg); ``exchange`` runs in
    the platform-host callback (code → ``{'user_id', 'name'}`` public identity);
    ``record`` runs on the custom domain (claim leg) where the user's session and
    tenant context live, so its audit row is stamped with the right tenant.

    ``is_mock`` / ``callback_route`` exist only so the start leg can reach the
    provider's own callback instead of an authorize URL that cannot answer under
    a provider mock. Everything downstream of that redirect — state match,
    exchange, mint, browser binding, claim — runs unmodified.
    """

    key: str
    label: str
    profile_return: str
    authorize_url: Callable[[str], str]
    exchange: Callable[[str], Awaitable[dict]]
    record: Callable[[User, dict], Awaitable[None]]
    is_mock: Callable[[], bool]
    callback_route: str


_HANDOFF_PROVIDERS: dict[str, LinkHandoffProvider] = {}

_pages_registered = False


def register_link_handoff_provider(provider: LinkHandoffProvider) -> None:
    """Register a provider so the shared ``/oauth/link/*`` routes can complete it."""
    _HANDOFF_PROVIDERS[provider.key] = provider


def _bind_commit(secret: str) -> str:
    """The commitment published through the platform hop for a browser secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _link_handoff_start_url(
    provider_key: str, target_host: str, next_path: str, bind_commit: str,
) -> str:
    """Platform-host ``/oauth/link/start`` URL that begins a handoff for a domain."""
    platform = get_platform_host()
    query = urlencode({'p': provider_key, 'host': target_host, 'next': next_path, 'b': bind_commit})
    return f'{scheme_for_host(platform)}://{platform}/oauth/link/start?{query}'


def _mock_callback_url(provider: LinkHandoffProvider, state: str) -> str:
    """Platform-host URL that stands in for a mocked provider's authorize page.

    Under ``MOCK_<PROVIDER>`` the provider's real authorize URL cannot answer, so
    the start leg redirects straight to the callback it would have returned to.
    The rest of the handoff is untouched: the callback matches the state the
    start leg stored, exchanges the code through the mock client, and mints a
    genuine host-bound single-use token the real claim code has to validate.
    """
    platform = get_platform_host()
    query = urlencode({'code': 'mock', 'state': state})
    return f'{scheme_for_host(platform)}://{platform}{provider.callback_route}?{query}'


def _link_claim_url(target_host: str, token: str) -> str:
    """Custom-domain ``/oauth/link/claim`` URL the platform callback hands off to."""
    return f'{scheme_for_host(target_host)}://{target_host}/oauth/link/claim?token={quote(token, safe="")}'


def _link_handback_url(
    target_host: str, provider_key: str, reason: str, next_path: str,
) -> str:
    """Custom-domain claim URL for a handoff the platform host could not finish.

    The platform host cannot stash a notice for the target domain — different
    origin, different cookie — so the failure crosses as an opaque reason code
    the claim route turns into a message. Routing it through the claim route
    keeps one door and one notice carrier for every handoff outcome.
    """
    query = urlencode({'r': reason, 'p': provider_key, 'next': next_path})
    return f'{scheme_for_host(target_host)}://{target_host}/oauth/link/claim?{query}'


def _default_link_return() -> str:
    """The return path shared by every registered provider, or ``/``.

    Derived rather than hardcoded: the expired-token branch of the claim route
    has no payload to read a return path from, and three literal
    ``'/home/profile'``\\ s is how the next provider's differing return silently
    stops working.
    """
    returns = {p.profile_return for p in _HANDOFF_PROVIDERS.values()}
    return returns.pop() if len(returns) == 1 else '/'


_HANDBACK_DENIED = 'denied'
_HANDBACK_FAILED = 'failed'


def _handback_message(reason: Optional[str], provider_key: Optional[str]) -> str:
    """Turn a hand-back reason code into the message the user sees."""
    provider = _HANDOFF_PROVIDERS.get(provider_key or '')
    if provider is None:
        return 'Could not complete the account link. Please try again.'
    if reason == _HANDBACK_DENIED:
        return f'{provider.label} linking was cancelled or failed.'
    return f'Could not link {provider.label}. Please try again.'


def _cross_host_url(host: str, path: str) -> str:
    """An absolute URL on ``host`` for a plain (token-less) return redirect."""
    return f'{scheme_for_host(host)}://{host}{path}'


async def maybe_start_link_handoff(
    provider_key: str, next_path: str,
) -> Optional[RedirectResponse]:
    """Begin a cross-host link handoff when on a custom domain in handoff mode.

    Returns a redirect into the platform-host ``/oauth/link/start`` (so the
    provider OAuth runs there and hands the verified identity back here), or
    ``None`` when the flow should run in place (path mode, or handoff disabled).
    Call it from a provider's ``/<provider>/link`` initiation page.
    """
    if not (is_host_mode() and host_oauth_handoff_enabled()):
        return None
    tid = get_current_tenant_id()
    tenant = await TenantService.get_by_id(tid) if tid is not None else None
    if tenant is None or not getattr(tenant, 'domain', None):
        return None
    bind = secrets.token_urlsafe(32)
    app.storage.user['oauth_link_bind'] = bind
    return RedirectResponse(
        _link_handoff_start_url(provider_key, tenant.domain, safe_next(next_path), _bind_commit(bind))
    )


def _pop_link_handoff_keys() -> dict:
    """Consume the platform-host handoff markers ``/oauth/link/start`` stored."""
    return {
        'provider': app.storage.user.pop('oauth_link_provider', None),
        'state': app.storage.user.pop('oauth_link_state', None),
        'host': app.storage.user.pop('oauth_link_host', None),
        'next': app.storage.user.pop('oauth_link_next', None),
        'bind_commit': app.storage.user.pop('oauth_link_bind_commit', None),
        'tenant': app.storage.user.pop('oauth_link_tenant', None),
    }


def _bind_matches(expected_commit: Optional[str], browser_secret) -> bool:
    """Whether ``browser_secret`` proves the committed hash the token carries.

    The link-CSRF / forced-link guard: the initiating browser stashed the raw
    secret in this domain's session and published only its ``sha256`` through the
    platform hop. Fails closed — a token minted with no/blank commitment, or
    presented by a browser without the matching secret, is rejected.
    """
    return bool(
        expected_commit and isinstance(browser_secret, str)
        and hmac.compare_digest(expected_commit, _bind_commit(browser_secret))
    )


async def handle_link_handoff_callback(url: str) -> bool:
    """Complete a custom-domain link on the platform host, then hand it back.

    If the current provider callback is a handoff leg (markers present in the
    platform-host session), exchange the code for the verified provider identity,
    mint a single-use host-bound token carrying that public identity, and redirect
    the browser to the target domain's ``/oauth/link/claim`` where the user's
    session lives. Returns ``True`` when it handled the callback (the caller must
    return), ``False`` for a normal in-place (path-mode / Design A) callback.
    """
    provider_key = app.storage.user.get('oauth_link_provider')
    if not provider_key:
        return False
    expected_state = app.storage.user.get('oauth_link_state')
    # A state mismatch means stale markers from an abandoned handoff: drop them, or they
    # hijack a later in-place link on this host.
    if not expected_state or returned_state(url) != expected_state:
        _pop_link_handoff_keys()
        return False
    keys = _pop_link_handoff_keys()
    host = keys['host']
    bind_commit = keys['bind_commit']
    provider = _HANDOFF_PROVIDERS.get(provider_key)
    next_path = safe_next(keys['next'] or (provider.profile_return if provider else '/'))
    if provider is None or not host:
        ui.navigate.to(_cross_host_url(host, next_path) if host else next_path)
        return True
    code = read_callback_code(url, expected_state, provider.label)
    if code is None:
        ui.navigate.to(_link_handback_url(host, provider_key, _HANDBACK_DENIED, next_path))
        return True
    # The platform host resolves no tenant: run under the start leg's tenant, or every
    # feature-gated provider hands back failed.
    try:
        with tenant_scope(keys['tenant']):
            data = await provider.exchange(code)
    except Exception:
        logger.exception('%s link handoff exchange failed', provider.label)
        ui.navigate.to(_link_handback_url(host, provider_key, _HANDBACK_FAILED, next_path))
        return True
    token = handoff_service.mint_data(
        data={'key': provider_key, **data},
        target_host=host,
        next_path=next_path,
        bind_commit=bind_commit or None,
    )
    ui.navigate.to(
        _link_claim_url(host, token) if token
        else _link_handback_url(host, provider_key, _HANDBACK_FAILED, next_path)
    )
    return True


def register_link_handoff_pages() -> None:
    """Register the shared platform-host ``/oauth/link/start`` + custom-domain
    ``/oauth/link/claim`` handoff routes. Idempotent; call once at startup."""
    global _pages_registered
    if _pages_registered:
        return
    _pages_registered = True

    @ui.page('/oauth/link/start')
    async def link_start(request: Request, client: Client) -> Optional[RedirectResponse]:
        """Platform-host entry point: begin a provider link for a custom domain.

        Records the provider, target host, return path, and browser-binding
        commitment in the platform-host session (the custom domain's cookie isn't
        visible here, so they arrive in the URL) and runs the provider's normal
        OAuth to its single platform callback. The target host is allow-listed
        against known active tenant domains (open-redirect / fixation guard).
        """
        if not host_oauth_handoff_enabled():
            return RedirectResponse('/login')
        params = request.query_params
        provider = _HANDOFF_PROVIDERS.get(params.get('p') or '')
        target = normalize_hostname(params.get('host'))
        next_path = safe_next(params.get('next') or '/')
        bind_commit = (params.get('b') or '')[:64]
        if provider is None:
            return RedirectResponse('/')
        tenant = await TenantService.get_by_domain(target) if target else None
        if tenant is None or not tenant.is_active:
            return RedirectResponse('/')
        state = secrets.token_urlsafe(32)
        app.storage.user['oauth_link_state'] = state
        app.storage.user['oauth_link_provider'] = provider.key
        app.storage.user['oauth_link_host'] = target
        app.storage.user['oauth_link_next'] = next_path
        app.storage.user['oauth_link_bind_commit'] = bind_commit
        app.storage.user['oauth_link_tenant'] = tenant.id
        if provider.is_mock():
            return RedirectResponse(_mock_callback_url(provider, state))
        return RedirectResponse(provider.authorize_url(state))

    @ui.page('/oauth/link/claim')
    async def link_claim(client: Client) -> Optional[RedirectResponse]:
        """Custom-domain claim: validate the handoff and record the link here.

        Also the single door for a handoff the **platform** host gave up on: it
        cannot stash a notice in this domain's session (different origin), so it
        hands back a ``r=<reason>`` this route turns into a message.
        """
        # Path mode never mints a claim token; without this guard the route redirects to the community picker.
        if not host_oauth_handoff_enabled():
            return RedirectResponse('/')
        url = await browser_url(client)
        if url is None:
            return None
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        token = next(iter(params.get('token', [])), None)
        request_host = normalize_hostname(parsed.netloc)
        # Crosses a host boundary as a bare query parameter: untrusted, keep it behind safe_next.
        handed_back = safe_next(next(iter(params.get('next', [])), None) or _default_link_return())
        bind = app.storage.user.pop('oauth_link_bind', None)
        if not token:
            stash_notice(_handback_message(
                next(iter(params.get('r', [])), None), next(iter(params.get('p', [])), None),
            ), color='warning')
            ui.navigate.to(handed_back)
            return None
        payload = handoff_service.claim(token, request_host) if request_host else None
        if payload is None:
            stash_notice('Link session expired or already used. Please try again.', color='warning')
            ui.navigate.to(handed_back)
            return None
        # Link-CSRF guard: rejects a token delivered to a browser that didn't start the link.
        if not _bind_matches(payload.get('bind_commit'), bind):
            logger.warning('OAuth link handoff browser-binding mismatch on %r', request_host)
            stash_notice('Link is not valid for this browser. Please try again.', color='warning')
            ui.navigate.to(safe_next(payload.get('next') or handed_back))
            return None
        data = payload.get('data') or {}
        provider = _HANDOFF_PROVIDERS.get(data.get('key'))
        next_path = safe_next(payload.get('next') or (provider.profile_return if provider else '/'))
        user = await get_user_from_session(app.storage.user)
        if user is None:
            stash_notice('Please log in and try linking again.', color='warning')
            ui.navigate.to('/login')
            return None
        if provider is None:
            ui.navigate.to(next_path)
            return None
        try:
            await provider.record(user, data)
            stash_notice(f'{provider.label} account linked.', color='positive')
        except ValueError as e:
            stash_notice(str(e), color='warning')
        except Exception:
            logger.exception('%s link handoff claim failed', provider.label)
            stash_notice(f'Could not link {provider.label}. Please try again.', color='negative')
        ui.navigate.to(next_path)
        return None


@dataclass(frozen=True)
class IdentityLinkFlow:
    """Provider-specific configuration for a single-flow identity link.

    ``provider_label`` is woven verbatim into log lines and toasts (``'racetime'``
    / ``'Twitch'``), so it doubles as the display name in messages.
    ``provider_key`` is the stable lowercase slug routed through the handoff.
    ``other_callback`` lets a second flow share the one registered redirect URI:
    it gets the callback URL first and answers True when it completed it.
    """

    provider_key: str
    provider_label: str
    link_route: str
    callback_route: str
    state_key: str
    return_key: str
    profile_return: str
    service_factory: Callable[[], object]
    authorize_url: Callable[[str], str]
    is_mock: Callable[[], bool]
    display_name: Callable[[dict], Optional[str]]
    other_callback: Optional[Callable[[str], Awaitable[bool]]] = None


async def _flow_exchange(flow: IdentityLinkFlow, code: str) -> dict:
    """Exchange the code and return the public identity to hand across the wire."""
    service = flow.service_factory()
    me = await service.exchange_player_code(code)
    return {'user_id': me['user_id'], 'name': flow.display_name(me)}


async def _flow_record(flow: IdentityLinkFlow, user: User, data: dict) -> None:
    service = flow.service_factory()
    await service.record_player_link(user, data['user_id'], data.get('name'), actor=user)


def register_identity_link_pages(flow: IdentityLinkFlow) -> None:
    """Register the ``/<provider>/link`` initiation + callback pages for ``flow``."""
    register_link_handoff_provider(LinkHandoffProvider(
        key=flow.provider_key,
        label=flow.provider_label,
        profile_return=flow.profile_return,
        authorize_url=flow.authorize_url,
        exchange=lambda code, f=flow: _flow_exchange(f, code),
        record=lambda user, data, f=flow: _flow_record(f, user, data),
        is_mock=flow.is_mock,
        callback_route=flow.callback_route,
    ))

    @ui.page(flow.link_route)
    async def link(request: Request) -> RedirectResponse:
        # The callback lands on the bare platform host: pin the tenant-qualified return here.
        root_path = request.scope.get('root_path', '') or ''
        user = await get_user_from_session(app.storage.user)
        if user is None:
            return RedirectResponse(f'{root_path}/login')
        # Must run before the mock short-circuit, or the handoff is unreachable in the only env that drives it.
        handoff = await maybe_start_link_handoff(flow.provider_key, f'{root_path}{flow.profile_return}')
        if handoff is not None:
            return handoff
        if flow.is_mock():
            # Mock mode links on the spot, so this page owns the outcome (a bare call 500s on an already-linked id).
            service = flow.service_factory()
            me = await service.exchange_player_code('mock')
            try:
                await service.record_player_link(
                    user, me['user_id'], flow.display_name(me), actor=user,
                )
                stash_notice(f'{flow.provider_label} account linked.', color='positive')
            except ValueError as e:
                stash_notice(str(e), color='warning')
            except Exception:
                logger.exception('%s mock linking failed', flow.provider_label)
                stash_notice(
                    f'Could not link {flow.provider_label}. Please try again.', color='negative',
                )
            return RedirectResponse(f'{root_path}{flow.profile_return}')
        detour = await platform_link_redirect(flow.profile_return)
        if detour:
            return RedirectResponse(detour)
        state = secrets.token_urlsafe(32)
        app.storage.user[flow.state_key] = state
        app.storage.user[flow.return_key] = f'{root_path}{flow.profile_return}'
        return RedirectResponse(flow.authorize_url(state))

    @ui.page(flow.callback_route)
    async def callback(client: Client) -> None:
        url = await browser_url(client)
        if url is None:
            return
        if await handle_link_handoff_callback(url):
            return
        if flow.other_callback is not None and await flow.other_callback(url):
            return
        user = await get_user_from_session(app.storage.user)
        expected_state = app.storage.user.pop(flow.state_key, None)
        return_path = app.storage.user.pop(flow.return_key, None) or flow.profile_return
        if user is None:
            ui.navigate.to(return_path)
            return
        code = read_callback_code(url, expected_state, flow.provider_label)
        await _finish_link(flow, user, code, return_path)


async def _finish_link(
    flow: IdentityLinkFlow, user: User, code: Optional[str], return_path: str,
) -> None:
    if code is None:
        stash_notice(f'{flow.provider_label} linking was cancelled or failed.', color='warning')
    else:
        try:
            service = flow.service_factory()
            me = await service.exchange_player_code(code)
            await service.record_player_link(
                user, me['user_id'], flow.display_name(me), actor=user,
            )
            stash_notice(f'{flow.provider_label} account linked.', color='positive')
        except ValueError as e:
            stash_notice(str(e), color='warning')
        except Exception:
            logger.exception('%s linking failed', flow.provider_label)
            stash_notice(f'Could not link {flow.provider_label}. Please try again.', color='negative')
    ui.navigate.to(return_path)
