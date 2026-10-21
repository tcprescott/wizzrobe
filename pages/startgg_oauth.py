"""start.gg OAuth pages.

Two flows share the one registered redirect URI ``/startgg/oauth/callback``:

* **Player identity** (``/startgg/link``): a logged-in player links their
  start.gg account. Same scaffolding as ``pages/twitch_oauth.py``
  (``pages/_oauth_link.py``); custom-domain communities reach the callback
  through the existing handoff.
* **Community sign-in** (``/startgg/connect``): a staff member signs the
  community in with ``tournament.reporter`` so syncs and result reports run as
  their account. The pending ``startgg_connect_state`` says a callback is this
  flow's. Like Challonge's service connect it stays on the platform host
  rather than crossing hosts, because it stores real tokens.

When ``MOCK_STARTGG`` is on both initiation pages complete locally.
"""

import logging
import secrets

from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import RedirectResponse

from application.services.auth_service import AuthService, get_user_from_session
from application.services.feature_flag_service import FeatureFlagService
from application.services.startgg_connection_service import StartggConnectionService
from application.services.startgg_link_service import StartggLinkService
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.mocks.mock_startgg import is_mock_startgg
from models import FeatureFlag
from pages._oauth_link import (
    IdentityLinkFlow,
    platform_link_redirect,
    read_callback_code,
    register_identity_link_pages,
    returned_state,
)
from theme.notice import stash_notice

logger = logging.getLogger(__name__)

_CONNECT_RETURN = '/admin/settings/integrations'
_STATE_KEY = 'startgg_connect_state'
_RETURN_KEY = 'startgg_connect_return'
_TENANT_KEY = 'startgg_connect_tenant'


async def _finish_connect(url: str) -> bool:
    """Complete a pending community sign-in, or answer False to leave the callback to the player link."""
    expected = app.storage.user.get(_STATE_KEY)
    if expected is None or returned_state(url) != expected:
        return False
    app.storage.user.pop(_STATE_KEY, None)
    return_path = app.storage.user.pop(_RETURN_KEY, None) or _CONNECT_RETURN
    tenant_id = app.storage.user.pop(_TENANT_KEY, None)
    user = await get_user_from_session(app.storage.user)
    code = read_callback_code(url, expected, 'start.gg')
    if user is None or code is None:
        stash_notice('start.gg sign-in was cancelled or failed.', color='warning')
        ui.navigate.to(return_path)
        return True
    with tenant_scope(tenant_id):
        service = StartggConnectionService()
        try:
            await service.save_connection(await service.exchange_connect_code(code), user)
            stash_notice('start.gg connected.', color='positive')
        except ValueError as e:
            stash_notice(str(e), color='warning')
        except Exception:
            logger.exception('start.gg community sign-in failed')
            stash_notice('Could not connect start.gg. Please try again.', color='negative')
    ui.navigate.to(return_path)
    return True


_FLOW = IdentityLinkFlow(
    provider_key='startgg',
    provider_label='start.gg',
    link_route='/startgg/link',
    callback_route='/startgg/oauth/callback',
    state_key='startgg_link_state',
    return_key='startgg_link_return',
    profile_return='/home/profile',
    service_factory=StartggLinkService,
    authorize_url=StartggLinkService.player_authorize_url,
    is_mock=is_mock_startgg,
    display_name=lambda me: me.get('display_name') or me.get('username'),
    other_callback=_finish_connect,
)


def create() -> None:
    register_identity_link_pages(_FLOW)

    @ui.page('/startgg/connect')
    async def startgg_connect(request: Request) -> RedirectResponse:
        # Restart this route on the platform host, not the settings page, so
        # Connect isn't pressed twice.
        detour = await platform_link_redirect('/startgg/connect')
        if detour:
            return RedirectResponse(detour)
        root_path = request.scope.get('root_path', '') or ''
        user = await get_user_from_session(app.storage.user)
        if user is None:
            return RedirectResponse(f'{root_path}/login')
        # A bare @ui.page (it serves a cross-host OAuth leg), so it checks the
        # staff gate and the flag itself.
        if not await AuthService.is_staff(user) or not await FeatureFlagService().is_enabled(FeatureFlag.STARTGG):
            return RedirectResponse(f'{root_path}/admin')
        if not StartggConnectionService.is_configured():
            stash_notice("start.gg sign-in isn't set up on this server yet.", color='warning')
            return RedirectResponse(f'{root_path}{_CONNECT_RETURN}')
        if is_mock_startgg():
            service = StartggConnectionService()
            try:
                await service.save_connection(await service.exchange_connect_code('mock'), user)
                stash_notice('start.gg connected.', color='positive')
            except ValueError as e:
                stash_notice(str(e), color='warning')
            return RedirectResponse(f'{root_path}{_CONNECT_RETURN}')
        state = secrets.token_urlsafe(32)
        app.storage.user[_STATE_KEY] = state
        app.storage.user[_RETURN_KEY] = f'{root_path}{_CONNECT_RETURN}'
        app.storage.user[_TENANT_KEY] = get_current_tenant_id()
        return RedirectResponse(StartggConnectionService.connect_authorize_url(state))
