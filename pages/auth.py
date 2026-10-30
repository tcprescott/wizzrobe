"""Authentication page routes: Discord OAuth ``/login``, ``/logout`` and
``/oauth/callback``.

This is the presentation half of authentication — it only registers the
``@ui.page`` routes. The actual middleware (``AuthMiddleware``, the
``protected_page`` decorator and the protected-route registry) lives in
``middleware/auth.py``.

When ``MOCK_DISCORD`` is enabled the three routes are replaced by a local
user-picker so developers can impersonate any user (or mint a new one) without
performing real Discord OAuth. User provisioning writes go through
``UserService`` to keep the presentation layer free of direct ORM writes.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import random
import secrets
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

from nicegui import Client, app, ui
from starlette.requests import Request
from starlette.responses import RedirectResponse
from zenora import APIClient

from application.services import (
    DiscordLinkService,
    DiscordLoginService,
    TenantService,
    UserService,
    get_user_from_discord_id,
    get_user_from_session,
)
from application.services import oauth_handoff_service as handoff_service
from application.services.discord.discord_login_service import CLAIM_PATH as DISCORD_LOGIN_PATH
from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService
from application.tenant_context import get_current_tenant_id, is_host_mode, tenant_scope
from application.utils.environment import get_platform_host, host_oauth_handoff_enabled
from application.utils.hostname import normalize_hostname, scheme_for_host
from application.utils.mocks.mock_discord import is_mock_discord
from application.utils.tenant_urls import (
    AUTH_ROUTES,
    return_path_for_login,
    safe_next,
    strip_root_path,
    tenant_home,
)
from models import Role, Tenant, User
from pages._browser_url import browser_url
from pages._session import start_session
from theme.notice import drain_notice, stash_notice
from theme.pickers import CHIP_PROPS
from theme.tables.mobile_grid import enable_mobile_grid
from theme.waiting import waiting_screen

logger = logging.getLogger(__name__)

_client_id = os.getenv("DISCORD_CLIENT_ID")

_LOGIN_ACTION = '''
    <q-btn color="primary" dense label="Log in as"
           @click="$parent.$emit('login_as', props.row)" />
'''

config = {
    "DISCORD_TOKEN": os.getenv("DISCORD_TOKEN"),
    "DISCORD_CLIENT_SECRET": os.getenv("DISCORD_CLIENT_SECRET"),
    "DISCORD_CLIENT_ID": _client_id,
    "STORAGE_SECRET": os.getenv("STORAGE_SECRET"),
}

# validate_token=False: validating is a blocking, timeout-less GET at import, so a Discord outage stops boot.
discordClient = (
    APIClient(config["DISCORD_TOKEN"], validate_token=False,
              client_secret=config["DISCORD_CLIENT_SECRET"])
    if not is_mock_discord() else None
)

# zenora's requests calls have no timeout: its own pool keeps a stalled Discord from starving the
# default pool that password hashing and web push share.
_OAUTH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='discord-oauth')
_OAUTH_TIMEOUT_SECONDS = 20
_ROLE_SYNC_TIMEOUT_SECONDS = 10

def _platform_redirect_uri() -> str:
    """Discord OAuth callback on the platform host (path mode + platform surface).

    Built from the **platform host** — the host that actually serves this callback
    — never from ``BASE_URL``. The two can diverge (``PLATFORM_HOST`` set
    independently, or a legacy single-tenant domain still lingering in
    ``BASE_URL``); deriving from ``BASE_URL`` then points the callback at the wrong
    host, so Discord round-trips a platform-host login back to that stale host.
    ``https`` is forced for a real host; only a ``*.localhost`` dev host keeps
    ``http`` (via :func:`scheme_for_host`). ``REDIRECT_URL`` stays an explicit
    escape hatch for non-standard setups. Read lazily so a per-deploy
    ``REDIRECT_URL`` / ``PLATFORM_HOST`` change applies without reimporting.
    """
    explicit = os.getenv("REDIRECT_URL")
    if explicit:
        return explicit
    host = get_platform_host()
    return f'{scheme_for_host(host)}://{host}/oauth/callback'


def _redirect_uri_for_tenant(tenant: Optional[Tenant]) -> str:
    """The canonical Discord callback URI for a resolved tenant, or the platform.

    Built from the tenant's **stored** ``domain`` (never a reflected ``Host``
    header), so the ``/login`` authorize leg and the callback exchange leg
    produce a byte-identical string — which Discord requires — and there is
    nothing attacker-controlled to inject. ``https`` is forced for a real domain;
    only a ``*.localhost`` dev host keeps ``http``. Off a custom domain (platform
    host, or an unresolved tenant) it is the shared platform callback.
    """
    if tenant is not None and tenant.domain:
        return f'{scheme_for_host(tenant.domain)}://{tenant.domain}/oauth/callback'
    return _platform_redirect_uri()


async def _host_mode_tenant() -> Optional[Tenant]:
    """The tenant when this request is on its own custom domain, else None."""
    if not is_host_mode():
        return None
    tid = get_current_tenant_id()
    return await TenantService.get_by_id(tid) if tid is not None else None


async def _callback_tenant(url: str) -> Optional[Tenant]:
    """Resolve the tenant a callback landed on, from its browser URL host.

    ``window.location.href`` is the real address the browser is on (not a
    spoofable header), so its host names the custom domain when the callback ran
    in host mode; resolving that back to the tenant reproduces the exact
    ``redirect_uri`` the ``/login`` leg sent.
    """
    host = normalize_hostname(urlparse(url).netloc)
    if not host:
        return None
    tenant = await TenantService.get_by_domain(host)
    return tenant if (tenant is not None and tenant.is_active) else None


def _safe_next(path) -> str:
    """A safe in-app return path, or ``/`` — see :func:`tenant_urls.safe_next`."""
    return safe_next(path)


def _bind_commit(secret: str) -> str:
    """The commitment published through the platform hop for a ``/login`` secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _handoff_start_url(target_host: str, next_path: str, bind_commit: str = '') -> str:
    """Platform-host ``/oauth/start`` URL that begins a Design B login for a domain."""
    platform = get_platform_host()
    query = urlencode({'host': target_host, 'next': next_path, 'b': bind_commit})
    return f'{scheme_for_host(platform)}://{platform}/oauth/start?{query}'


def _claim_url(target_host: str, token: str) -> str:
    """Custom-domain ``/session/claim`` URL the platform callback hands off to."""
    return f'{scheme_for_host(target_host)}://{target_host}/session/claim?token={quote(token, safe="")}'


def _oauth_url(state: str, redirect_uri: str) -> str:
    """Discord authorize URL for this login attempt, built at request time."""
    explicit = os.getenv("OAUTH_URL")
    if explicit:
        sep = '&' if '?' in explicit else '?'
        return f'{explicit}{sep}state={quote(state, safe="")}'
    params = urlencode({
        'client_id': _client_id or '',
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'identify',
        'state': state,
        'prompt': 'none',
    })
    return f'https://discord.com/api/oauth2/authorize?{params}'


def _sanitized_return(root_path: str, request: Request) -> str:
    """Post-login return target for this tenant.

    A sign-in button on a page anyone can open (the join door, a bracket, the
    help) carries the page it was pressed on as ``?next=``, tenant-local; it is
    accepted only as a plain same-origin path. Otherwise the target is the
    ``referrer_path`` ``AuthMiddleware`` wrote when a protected page bounced the
    reader here, accepted only when it belongs to this tenant. Either way the
    fallback is the community home — see :func:`return_path_for_login`.
    """
    return return_path_for_login(
        root_path,
        request.query_params.get('next'),
        app.storage.user.get('referrer_path'),
    )


def _register_discord_connect_callback() -> None:
    """Register the bot-authorization callback, shared by real and mock modes.

    Runs on the bare platform host (no tenant in scope), so the target tenant,
    CSRF state, and return path are carried in the session — set by the tenant
    admin page before redirecting to Discord. DB writes are wrapped in
    ``tenant_scope`` so the STAFF gate and guild stamp land on the right tenant.
    """
    @ui.page('/oauth/discord/connect/callback')
    async def discord_connect_callback(client: Client):
        url = await browser_url(client)
        if url is None:
            return
        expected_state = app.storage.user.pop('discord_connect_state', None)
        tenant_id = app.storage.user.pop('discord_connect_tenant_id', None)
        return_path = app.storage.user.pop('discord_connect_return', None) or '/'
        if not return_path.startswith('/'):
            return_path = '/'

        params = parse_qs(urlparse(url).query)
        if 'error' in params:
            stash_notice("That Discord authorization didn't go through. Try again when you're ready.", color='warning')
            ui.navigate.to(return_path)
            return
        returned_state = next(iter(params.get('state', [])), None)
        if not expected_state or returned_state != expected_state or not tenant_id:
            stash_notice('That connection link expired. Try again.', color='warning')
            ui.navigate.to(return_path)
            return

        actor = await get_user_from_session(app.storage.user)
        tenant = await TenantService.get_by_id(int(tenant_id))
        if actor is None or tenant is None:
            stash_notice('Your session expired. Log in again to continue.', color='warning')
            ui.navigate.to(return_path)
            return

        try:
            with tenant_scope(int(tenant_id)):
                if is_mock_discord():
                    guild_raw = (params.get('guild_id') or ['1'])[0]
                    await DiscordLinkService.link_guild(actor, tenant, int(guild_raw))
                else:
                    code = next(iter(params.get('code', [])), None)
                    if not code:
                        stash_notice('The Discord authorization was cancelled.', color='warning')
                        ui.navigate.to(return_path)
                        return
                    await DiscordLinkService.complete_link(actor, tenant, code)
        except (ValueError, PermissionError) as e:
            stash_notice(str(e), color='warning')
            ui.navigate.to(return_path)
            return
        except Exception:
            logger.exception('Discord connect callback failed')
            stash_notice('Something went wrong connecting to Discord. Give it another try.', color='negative')
            ui.navigate.to(return_path)
            return

        stash_notice('Discord server connected.', color='positive')
        ui.navigate.to(return_path)


def _register_discord_login_claim() -> None:
    """Redeem a one-time login link the bot handed out (``/login`` in Discord).

    Registered in real and mock modes alike: the link carries an identity
    Discord already vouched for, so nothing here talks to Discord. The token is
    read only once the socket connects, so a preview fetcher that downloads the
    HTML never consumes it.
    """
    @ui.page(DISCORD_LOGIN_PATH)
    async def discord_login_claim(client: Client) -> None:
        tenant_id = get_current_tenant_id()
        wait = await waiting_screen('Signing you in…')
        url = await browser_url(client)
        if url is None:
            return
        parsed = urlparse(url)
        token = (parse_qs(parsed.query).get('token') or [''])[0]
        payload = DiscordLoginService.claim(token, parsed.netloc, tenant_id)
        if payload is None:
            stash_notice(
                "That login link has expired or was already used. Run /login in Discord for a new one.",
                color='warning',
            )
            await wait.navigate_to('/')
            return
        user = await get_user_from_discord_id(str(payload['discord_id']))
        if user is None:
            app.storage.user.clear()
            stash_notice('This account is inactive. Contact staff if this seems wrong.', color='negative')
            await wait.navigate_to('/')
            return
        # Someone else was signed in here: drop their whole session, not just the identity.
        if str(app.storage.user.get('discord_id')) != str(payload['discord_id']) or \
                app.storage.user.get('user_id') not in (None, user.id):
            app.storage.user.clear()
        start_session(user, {
            'username': payload['username'],
            'avatar': payload['avatar'],
            'discord_id': payload['discord_id'],
        })
        await DiscordRoleMappingService().sync_user_roles(user)
        await wait.navigate_to('/')


def create() -> None:
    _register_discord_connect_callback()
    _register_discord_login_claim()
    if is_mock_discord():
        _create_mock()
        return

    @ui.page('/login')
    async def login(request: Request, client: Client) -> Optional[RedirectResponse]:
        root_path = request.scope.get('root_path', '') or ''
        if app.storage.user.get('authenticated', False):
            return RedirectResponse(
                return_path_for_login(root_path, request.query_params.get('next'), None)
            )
        return_path = _sanitized_return(root_path, request)
        if host_oauth_handoff_enabled() and is_host_mode():
            tenant = await _host_mode_tenant()
            if tenant is not None and tenant.domain:
                # Login-CSRF guard: bind the handoff to this browser; only the secret's hash crosses the platform hop.
                bind = secrets.token_urlsafe(32)
                app.storage.user['handoff_bind'] = bind
                return RedirectResponse(
                    _handoff_start_url(tenant.domain, _safe_next(return_path), _bind_commit(bind))
                )
        # Pin the post-login return to this tenant before leaving for Discord.
        app.storage.user['referrer_path'] = return_path
        state = secrets.token_urlsafe(32)
        app.storage.user['oauth_state'] = state
        tenant = await _host_mode_tenant()
        return RedirectResponse(_oauth_url(state, _redirect_uri_for_tenant(tenant)))

    @ui.page('/logout')
    def logout(request: Request, client: Client) -> Optional[RedirectResponse]:
        root_path = request.scope.get('root_path', '') or ''
        app.storage.user.clear()
        return RedirectResponse(tenant_home(root_path))

    @ui.page('/oauth/callback')
    async def oauth_callback(client: Client):
        # Built before the socket connects, so the spinner paints while the work runs.
        wait = await waiting_screen('Signing you in…')
        url = await browser_url(client)
        if url is None:
            return
        expected_state = app.storage.user.pop('oauth_state', None)
        try:
            parsed_url = urlparse(url)
            params = parse_qs(parsed_url.query)

            if 'error' in params:
                logger.warning('OAuth callback returned error: %s', params.get('error'))
                stash_notice("That Discord login didn't go through. Try again when you're ready.", color='warning')
                await wait.navigate_to('/login')
                return

            returned_state = next(iter(params.get('state', [])), None)
            if not expected_state or returned_state != expected_state:
                logger.warning('OAuth state mismatch on callback.')
                stash_notice('That login link expired. Log in again to continue.', color='warning')
                await wait.navigate_to('/login')
                return

            code = next(iter(params.get('code', [])), None)
            if not code:
                logger.warning('OAuth callback missing authorization code.')
                stash_notice("That login didn't go through. Try again.", color='warning')
                await wait.navigate_to('/login')
                return

            # Rebuild the exact redirect_uri the /login leg sent: Discord requires the exchange to match.
            callback_tenant = await _callback_tenant(url)
            redirect_uri = _redirect_uri_for_tenant(callback_tenant)

            # zenora is synchronous: offload to threads or it blocks the shared event loop.
            def _exchange_and_fetch():
                access_token = discordClient.oauth.get_access_token(
                    code, redirect_uri,
                ).access_token
                bearer_client = APIClient(access_token, validate_token=False, bearer=True)
                return bearer_client.users.get_current_user()

            try:
                current_user = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(_OAUTH_POOL, _exchange_and_fetch),
                    _OAUTH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning('Discord did not answer the OAuth exchange within %ss',
                               _OAUTH_TIMEOUT_SECONDS)
                stash_notice("Discord isn't answering right now. Give it a minute and try again.",
                             color='negative')
                await wait.navigate_to('/login')
                return

            user, _created = await UserService().provision_from_discord_login(
                current_user.id, current_user.username,
                # '' (not None), so the login clears a stale avatar hash.
                avatar=current_user.avatar_hash or '',
            )

            if not user.is_active:
                logger.warning('Inactive account %s attempted web login', current_user.id)
                app.storage.user.clear()
                stash_notice(
                    'This account is inactive. Contact staff if this seems wrong.',
                    color='negative',
                )
                await wait.navigate_to('/login')
                return

            # Do NOT authenticate the platform-host session: hand it over to the target domain's /session/claim.
            handoff_host = app.storage.user.pop('handoff_target_host', None)
            handoff_next = app.storage.user.pop('handoff_next', None)
            handoff_bind_commit = app.storage.user.pop('handoff_bind_commit', None)
            if handoff_host and host_oauth_handoff_enabled():
                token = handoff_service.mint(
                    discord_id=current_user.id,
                    username=current_user.username,
                    avatar=current_user.avatar_url,
                    target_host=handoff_host,
                    next_path=_safe_next(handoff_next or '/'),
                    bind_commit=handoff_bind_commit or None,
                )
                if token:
                    ui.navigate.to(_claim_url(handoff_host, token))
                    return

            start_session(user, {
                'username': current_user.username,
                'avatar': current_user.avatar_url,
                'discord_id': current_user.id,
            })

            try:
                await asyncio.wait_for(
                    DiscordRoleMappingService().sync_user_roles(user), _ROLE_SYNC_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning('Role sync for user %s timed out; keeping current roles', user.id)

            # Deliberately not through strip_root_path: served off the bare platform host or the tenant's
            # own domain, so only the fully qualified path lands in the right community.
            referrer = app.storage.user.get('referrer_path', '/')
            if referrer.split('?', 1)[0] in AUTH_ROUTES:
                referrer = '/'
            await wait.navigate_to(referrer)
            app.storage.user.pop('referrer_path', None)
        except Exception:
            logger.exception('Unexpected error during OAuth callback')
            stash_notice('Something went wrong logging you in. Give it another try.', color='negative')
            await wait.navigate_to('/login')

    @ui.page('/oauth/start')
    async def oauth_start(request: Request, client: Client) -> Optional[RedirectResponse]:
        """Design B entry point (platform host): begin OAuth for a custom domain.

        Records the target host + return path in the platform-host session (the
        custom domain's cookie isn't visible here, so they arrive in the URL) and
        runs the normal Discord flow to the single platform callback. The target
        host is allow-listed against known active tenant domains.
        """
        if not host_oauth_handoff_enabled():
            return RedirectResponse('/login')
        params = request.query_params
        target = normalize_hostname(params.get('host'))
        next_path = _safe_next(params.get('next') or '/')
        bind_commit = (params.get('b') or '')[:64]
        tenant = await TenantService.get_by_domain(target) if target else None
        if tenant is None or not tenant.is_active:
            # Refuse an unknown host rather than hand it a session (open-redirect / session-fixation guard).
            return RedirectResponse('/')
        state = secrets.token_urlsafe(32)
        app.storage.user['oauth_state'] = state
        app.storage.user['handoff_target_host'] = target
        app.storage.user['handoff_next'] = next_path
        app.storage.user['handoff_bind_commit'] = bind_commit
        return RedirectResponse(_oauth_url(state, _platform_redirect_uri()))

    @ui.page('/session/claim')
    async def session_claim(client: Client) -> None:
        """Design B claim (custom domain): validate the handoff and set the session."""
        wait = await waiting_screen('Finishing your login…')
        url = await browser_url(client)
        if url is None:
            return
        parsed = urlparse(url)
        token = next(iter(parse_qs(parsed.query).get('token', [])), None)
        request_host = normalize_hostname(parsed.netloc)
        bind = app.storage.user.pop('handoff_bind', None)
        payload = handoff_service.claim(token, request_host) if (token and request_host) else None
        if payload is None:
            stash_notice("That login link has expired or already been used. Log in again to continue.", color='warning')
            await wait.navigate_to('/login')
            return
        # Login-CSRF guard: rejects a token delivered to a browser that lacks the committed secret.
        expected = payload.get('bind_commit')
        if not (expected and isinstance(bind, str)
                and hmac.compare_digest(expected, _bind_commit(bind))):
            logger.warning('OAuth handoff browser-binding mismatch on %r', request_host)
            stash_notice("That login link isn't valid for this browser. Log in again from here.", color='warning')
            await wait.navigate_to('/login')
            return
        # Re-check is_active: the account may have been deactivated inside the token's TTL.
        user = await get_user_from_discord_id(payload['discord_id'])
        if user is None:
            app.storage.user.clear()
            stash_notice('This account is inactive. Contact staff if this is a mistake.', color='negative')
            await wait.navigate_to('/login')
            return
        start_session(user, {
            'username': payload.get('username'),
            'avatar': payload.get('avatar'),
            'discord_id': payload['discord_id'],
        })
        await DiscordRoleMappingService().sync_user_roles(user)
        # Host mode: root_path is empty here, so the path travels unstripped, exactly as minted.
        await wait.navigate_to(_safe_next(payload.get('next') or '/'))


def _login_as(user: User, root_path: str = '') -> None:
    """Populate app.storage.user the same way the real OAuth callback does."""
    start_session(user, {
        'username': user.username,
        'avatar': None,
        'discord_id': user.discord_id,
    })
    referrer = app.storage.user.get('referrer_path', '/')
    if referrer.split('?', 1)[0] in AUTH_ROUTES:
        referrer = '/'
    app.storage.user.pop('referrer_path', None)
    # referrer_path is stored tenant-qualified but nicegui.js re-adds the /t/<slug> prefix: strip it.
    ui.navigate.to(strip_root_path(root_path, referrer))


def _create_mock() -> None:
    """Register the MOCK_DISCORD replacements for the three auth routes.

    Turns ``/login`` into a public user-picker that can impersonate any user or
    mint a new one. Never active in production (``is_mock_discord`` refuses it).
    """
    @ui.page('/login')
    async def mock_login(request: Request, client: Client):
        root_path = request.scope.get('root_path', '') or ''
        if app.storage.user.get('authenticated', False):
            # Tenant-local: the client prepends root_path, so a qualified path would double it.
            ui.navigate.to(strip_root_path(
                root_path,
                return_path_for_login(root_path, request.query_params.get('next'), None),
            ))
            return
        app.storage.user['referrer_path'] = _sanitized_return(root_path, request)

        drain_notice()

        ui.page_title('Mock Discord Login')

        with ui.column().classes('w-full max-w-4xl mx-auto p-4 gap-4'):
            ui.label('Mock Discord Login').classes('text-2xl font-bold')
            ui.label(
                'MOCK_DISCORD is enabled. Pick an existing user to impersonate, '
                'or create a new one. No real Discord OAuth is performed.'
            ).classes('text-sm text-gray-600')

            with ui.card().classes('w-full'):
                ui.label('Existing users').classes('text-lg font-semibold')

                filter_input = ui.input(label='Filter by username or discord_id').classes('w-full')

                users = await User.all().order_by('username').prefetch_related(
                    'roles', 'admin_tournaments', 'crew_coordinated_tournaments',
                )

                def format_roles(u: User) -> str:
                    seen = set()
                    labels = []
                    for r in u.roles:
                        label = r.role.label
                        if label not in seen:
                            seen.add(label)
                            labels.append(label)
                    if len(u.admin_tournaments):
                        labels.append(f'TA({len(u.admin_tournaments)})')
                    if len(u.crew_coordinated_tournaments):
                        labels.append(f'CC({len(u.crew_coordinated_tournaments)})')
                    return ', '.join(labels) or '-'

                rows = [
                    {
                        'id': u.id,
                        'username': u.username,
                        'display_name': u.display_name or '',
                        'discord_id': str(u.discord_id),
                        'roles': format_roles(u),
                    }
                    for u in users
                ]
                user_by_id = {u.id: u for u in users}

                columns = [
                    {'name': 'username', 'label': 'Username', 'field': 'username', 'align': 'left', 'sortable': True},
                    {'name': 'display_name', 'label': 'Display Name', 'field': 'display_name', 'align': 'left'},
                    {'name': 'discord_id', 'label': 'Discord ID', 'field': 'discord_id', 'align': 'left'},
                    {'name': 'roles', 'label': 'Roles', 'field': 'roles', 'align': 'left'},
                    {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
                ]

                # table-prefs: exempt — development-only mock-Discord user picker
                table = ui.table(columns=columns, rows=rows, row_key='id').classes('w-full')
                table.add_slot('body-cell-actions', f'<q-td :props="props">{_LOGIN_ACTION}</q-td>')
                enable_mobile_grid(table, columns, actions=_LOGIN_ACTION)

                def on_login_as(e):
                    user_id = e.args.get('id')
                    user = user_by_id.get(user_id)
                    if user is None:
                        ui.notify('User no longer exists', color='negative')
                        return
                    _login_as(user, root_path)

                table.on('login_as', on_login_as)

                def apply_filter(_=None):
                    needle = (filter_input.value or '').lower().strip()
                    if not needle:
                        table.rows = rows
                    else:
                        table.rows = [
                            r for r in rows
                            if needle in r['username'].lower()
                            or needle in r['display_name'].lower()
                            or needle in r['discord_id']
                        ]
                    table.update()

                filter_input.on('update:model-value', apply_filter)

            with ui.card().classes('w-full'):
                ui.label('Create new user').classes('text-lg font-semibold')

                username_input = ui.input(label='Username').classes('w-full')
                display_name_input = ui.input(label='Display name (optional)').classes('w-full')
                discord_id_input = ui.number(
                    label='Discord ID',
                    value=random.randint(10_000_000_000_000_000, 99_999_999_999_999_999),
                    format='%.0f',
                ).classes('w-full')
                role_options = {r.value: r.label for r in Role}
                role_select = ui.select(
                    options=role_options,
                    value=[],
                    label='Roles',
                    multiple=True,
                ).props(CHIP_PROPS).classes('w-full')

                async def create_user():
                    username = (username_input.value or '').strip()
                    if not username:
                        ui.notify('Username is required', color='warning')
                        return
                    try:
                        discord_id = int(discord_id_input.value)
                    except (TypeError, ValueError):
                        ui.notify('Discord ID must be numeric', color='warning')
                        return
                    if await User.exists(discord_id=discord_id):
                        ui.notify('A user with that discord_id already exists', color='warning')
                        return
                    display_name = (display_name_input.value or '').strip() or None
                    user = await UserService().create_mock_login_user(
                        discord_id=discord_id,
                        username=username,
                        display_name=display_name,
                        role_values=role_select.value or [],
                    )
                    ui.notify(f'Created user {user.username} (#{user.discord_id})', color='positive')
                    _login_as(user, root_path)

                ui.button('Create and log in', color='green', on_click=create_user)

    @ui.page('/logout')
    def mock_logout(request: Request, client: Client) -> Optional[RedirectResponse]:
        root_path = request.scope.get('root_path', '') or ''
        app.storage.user.clear()
        return RedirectResponse(tenant_home(root_path))

    @ui.page('/oauth/callback')
    def mock_oauth_callback(request: Request, client: Client) -> RedirectResponse:
        root_path = request.scope.get('root_path', '') or ''
        return RedirectResponse(tenant_home(root_path))
