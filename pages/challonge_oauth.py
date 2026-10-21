"""Challonge OAuth pages.

Two flows share one registered OAuth app:

* **Service account** (``/challonge/connect``): a STAFF member authorizes the
  shared Wizzrobe Challonge account once. Tokens are stored centrally in
  :class:`ChallongeConnection`.
* **Player identity** (``/challonge/link``): a player authorizes with scope
  ``me`` so we can record their verified Challonge account id/username. The
  token is used once and discarded.

Both flows share the single registered redirect URI
``/challonge/oauth/callback`` — Challonge OAuth apps validate against one URI —
and the pending CSRF ``state`` identifies which flow a callback completes.

The CSRF ``state`` + JS-read-callback structure mirrors ``pages/auth.py``.
When ``MOCK_CHALLONGE`` is enabled the initiation pages complete the flow
locally without contacting Challonge.
"""

import logging
import secrets
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlencode

from nicegui import Client, app, ui
from starlette.requests import Request
from starlette.responses import RedirectResponse

from application.services.auth_service import AuthService, get_user_from_session
from application.services.challonge_service import ChallongeService
from application.services.feature_flag_service import FeatureFlagService
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.app_links import HOME_TOURNAMENTS, home_url
from application.utils.mocks.mock_challonge import is_mock_challonge
from models import FeatureFlag
from pages._browser_url import browser_url
from pages._oauth_link import (
    LinkHandoffProvider,
    handle_link_handoff_callback,
    maybe_start_link_handoff,
    platform_link_redirect,
    read_callback_code,
    register_link_handoff_provider,
    returned_state,
)
from theme.notice import stash_notice

logger = logging.getLogger(__name__)

_PROVIDER_LABEL = 'Challonge'
_PROVIDER_KEY = 'challonge'

_ADMIN_RETURN = '/admin/settings/integrations'
_PROFILE_RETURN = '/home/profile'
# Where a link started from the Tournaments tab's "Link Challonge to sign up"
# lands, so the player finishes signing up where they began. A fixed key, never
# a path from the query string, so the parameter can't become an open redirect.
_RETURNS = {HOME_TOURNAMENTS: home_url(HOME_TOURNAMENTS)}


async def _challonge_live() -> bool:
    """Whether this community has the Challonge feature.

    These are bare ``@ui.page`` routes (they serve cross-host OAuth legs, so they
    cannot use ``@protected_page``'s ``feature=`` gate), which leaves them
    reachable by URL after the profile/admin entry points are hidden. Without
    this the mock-mode branches would raise ``FeatureDisabledError`` out of a page
    handler, and the real flow would send the player to Challonge's consent
    screen only to fail on the way back.
    """
    return await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE)


async def _player_handoff_exchange(code: str) -> dict:
    """Exchange a player's code for the public Challonge identity to hand across."""
    service = ChallongeService()
    me = await service.exchange_player_code(code)
    return {'user_id': me['user_id'], 'name': me.get('username')}


async def _player_handoff_record(user, data: dict) -> None:
    await ChallongeService().record_player_link(
        user, data['user_id'], data.get('name'), actor=user,
    )


def create() -> None:
    # Only the player-identity link uses the cross-host handoff (it carries the
    # public Challonge id/name, like racetime/Twitch). The STAFF service-connect
    # keeps the platform-host detour: it writes a tenant-scoped connection holding
    # real OAuth tokens, so it stays on the main site rather than crossing hosts.
    register_link_handoff_provider(LinkHandoffProvider(
        key=_PROVIDER_KEY,
        label=_PROVIDER_LABEL,
        profile_return=_PROFILE_RETURN,
        authorize_url=ChallongeService.player_authorize_url,
        exchange=_player_handoff_exchange,
        record=_player_handoff_record,
        is_mock=is_mock_challonge,
        callback_route='/challonge/oauth/callback',
    ))

    @ui.page('/challonge/connect')
    async def challonge_connect(request: Request) -> RedirectResponse:
        # Custom domain: the shared callback is on the platform host, so restart
        # this route there. Bouncing to the settings page instead made staff
        # press Reconnect a second time.
        community = request.query_params.get('community')
        detour = await platform_link_redirect(
            f'/challonge/connect?{urlencode({"community": community})}' if community else '/challonge/connect'
        )
        if detour:
            return RedirectResponse(detour)
        # Initiation runs with tenant context; the shared callback lands on the
        # bare platform host, so pin the tenant-qualified return here.
        root_path = request.scope.get('root_path', '') or ''
        user = await get_user_from_session(app.storage.user)
        if user is None:
            return RedirectResponse(f'{root_path}/login')
        if not await AuthService.is_staff(user):
            return RedirectResponse(f'{root_path}/admin')
        if not await _challonge_live():
            return RedirectResponse(f'{root_path}/admin')
        service = ChallongeService()
        try:
            queue = await service.communities_to_grant(request.query_params.get('community'))
        except ValueError as e:
            stash_notice(str(e), color='warning')
            return RedirectResponse(f'{root_path}{_ADMIN_RETURN}')
        if is_mock_challonge():
            for community in queue or [None]:
                payload = await service.exchange_service_code('mock')
                await service.save_service_connection(payload, user, community=community)
            stash_notice(_connected_notice(queue), color='positive')
            return RedirectResponse(f'{root_path}{_ADMIN_RETURN}')
        app.storage.user['challonge_service_return'] = f'{root_path}{_ADMIN_RETURN}'
        app.storage.user['challonge_service_tenant'] = get_current_tenant_id()
        app.storage.user['challonge_service_granted'] = []
        return RedirectResponse(_next_service_grant(queue))

    @ui.page('/challonge/oauth/callback')
    async def challonge_callback(client: Client) -> None:
        url = await browser_url(client)
        if url is None:
            return
        # Cross-host player-link handoff leg (platform host): exchange + mint +
        # hand the verified identity back to the custom domain's /oauth/link/claim.
        if await handle_link_handoff_callback(url):
            return
        user = await get_user_from_session(app.storage.user)
        service_state = app.storage.user.pop('challonge_service_state', None)
        player_state = app.storage.user.pop('challonge_player_state', None)
        service_return = app.storage.user.pop('challonge_service_return', None) or _ADMIN_RETURN
        player_return = app.storage.user.pop('challonge_player_return', None) or _PROFILE_RETURN
        # The callback is on the bare platform host, so no tenant is in scope here:
        # the feature gate and the tenant-scoped writes need the community the
        # flow started in, pinned server-side at initiation.
        service_tenant = app.storage.user.pop('challonge_service_tenant', None)
        service_community = app.storage.user.pop('challonge_service_community', None)
        service_queue = app.storage.user.pop('challonge_service_queue', None) or []
        service_granted = app.storage.user.pop('challonge_service_granted', None) or []
        player_tenant = app.storage.user.pop('challonge_player_tenant', None)
        if user is None:
            # Not authenticated: bounce to the originating tenant's login rather
            # than the platform host (the returns carry the /t/<slug> prefix).
            stash_notice('Please log in and try linking again.', color='warning')
            ui.navigate.to(service_return if service_state is not None else player_return)
            return
        # One registered redirect URI serves two flows, so the pending CSRF state
        # says which one this callback completes. Four outcomes, named — the old
        # two-branch guess fell through to the service flow whenever *neither*
        # state was pending (a replay, a stale tab, a hand-typed URL), and that
        # flow returns to the admin area, so a player was answered with a 403
        # about the admin area.
        state = returned_state(url)
        if player_state is not None and state == player_state:
            with tenant_scope(player_tenant):
                await _finish_player_link(
                    user, read_callback_code(url, player_state, _PROVIDER_LABEL), player_return,
                )
        elif service_state is not None and state == service_state:
            with tenant_scope(service_tenant):
                await _finish_service_connect(
                    user, read_callback_code(url, service_state, _PROVIDER_LABEL), service_return,
                    _ServiceGrant(service_community, service_queue, service_granted, service_tenant),
                )
        elif (player_state is None) != (service_state is None):
            # Exactly one flow pending and the state did not match it: a provider
            # redirect carrying an error and no usable state. Complete that flow
            # with no code so it reports cancelled/failed and returns to its own
            # path — what the old `or service_state is None` clause was for.
            if player_state is not None:
                with tenant_scope(player_tenant):
                    await _finish_player_link(user, None, player_return)
            else:
                with tenant_scope(service_tenant):
                    await _finish_service_connect(
                        user, None, service_return,
                        _ServiceGrant(service_community, service_queue, service_granted, service_tenant),
                    )
        else:
            # Nothing pending, or both pending and neither matched: this callback
            # belongs to neither flow, so complete neither. Return to the profile,
            # never the admin area — every actor can reach their own profile, and
            # a staff member replaying a service-connect callback lands there with
            # an accurate message instead of a player hitting a 403. With no
            # markers there is no tenant to prefix, so in path mode the fallback
            # resolves on the platform host as the community picker — with the
            # message, which is the honest answer for a callback that names no
            # community.
            stash_notice(
                f"That {_PROVIDER_LABEL} link didn't complete. "
                f'Try again from your profile.', color='warning',
            )
            ui.navigate.to(player_return)

    @ui.page('/challonge/link')
    async def challonge_link(request: Request) -> RedirectResponse:
        root_path = request.scope.get('root_path', '') or ''
        player_return = _RETURNS.get(request.query_params.get('next', ''), _PROFILE_RETURN)
        user = await get_user_from_session(app.storage.user)
        if user is None:
            return RedirectResponse(f'{root_path}/login')
        if not await _challonge_live():
            return RedirectResponse(f'{root_path}{player_return}')
        # Custom domain + handoff: run the player OAuth on the platform host and
        # hand the verified identity back here (where the session/tenant live).
        # Tried before the mock short-circuit so the handoff is reachable under
        # MOCK_CHALLONGE; returns None in path mode and with handoff off.
        handoff = await maybe_start_link_handoff(_PROVIDER_KEY, f'{root_path}{player_return}')
        if handoff is not None:
            return handoff
        if is_mock_challonge():
            # Mock mode records the link here rather than in the callback, so this
            # page owns its outcome. A bare call answered an already-linked id
            # with a 500.
            service = ChallongeService()
            me = await service.exchange_player_code('mock')
            try:
                await service.record_player_link(user, me['user_id'], me.get('username'), actor=user)
                stash_notice(f'{_PROVIDER_LABEL} account linked.', color='positive')
            except ValueError as e:
                stash_notice(str(e), color='warning')
            except Exception:
                logger.exception('Challonge mock player linking failed')
                stash_notice(f'Could not link {_PROVIDER_LABEL}. Please try again.', color='negative')
            return RedirectResponse(f'{root_path}{player_return}')
        # Custom domain + Design A (handoff off): bounce to the platform-host
        # path-mode surface. No-op in path mode / platform surface.
        detour = await platform_link_redirect(player_return)
        if detour:
            return RedirectResponse(detour)
        state = secrets.token_urlsafe(32)
        app.storage.user['challonge_player_state'] = state
        app.storage.user['challonge_player_return'] = f'{root_path}{player_return}'
        app.storage.user['challonge_player_tenant'] = get_current_tenant_id()
        return RedirectResponse(ChallongeService.player_authorize_url(state))


@dataclass
class _ServiceGrant:
    """Where a reconnect is in its walk through the remembered communities."""

    community: Optional[str]
    queue: List[str]
    granted: List[str]
    tenant_id: Optional[int]


def _connected_notice(communities: List[str]) -> str:
    if not communities:
        return 'Challonge account connected.'
    if len(communities) == 1:
        return f'Challonge connected for {communities[0]}.'
    return f"Challonge connected for {', '.join(communities[:-1])} and {communities[-1]}."


def _next_service_grant(queue: List[str]) -> str:
    """Pin the next community's state in the session and return Challonge's consent URL.

    Each grant names one community, so a reconnect asks once per remembered
    community. The rest of the queue rides the session to the callback.
    """
    community, rest = (queue[0], queue[1:]) if queue else (None, [])
    state = secrets.token_urlsafe(32)
    app.storage.user['challonge_service_state'] = state
    app.storage.user['challonge_service_community'] = community
    app.storage.user['challonge_service_queue'] = rest
    return ChallongeService.service_authorize_url(state, community=community)


async def _finish_service_connect(user, code: str | None, return_path: str, grant: _ServiceGrant) -> None:
    granted = list(grant.granted)
    if code is None:
        if granted:
            stash_notice(
                f'{_connected_notice(granted)} Challonge access for '
                f'{grant.community or "the next community"} was cancelled.',
                color='warning',
            )
        else:
            stash_notice('Challonge connection was cancelled or failed.', color='warning')
        ui.navigate.to(return_path)
        return
    try:
        service = ChallongeService()
        payload = await service.exchange_service_code(code)
        await service.save_service_connection(payload, user, community=grant.community)
    except ValueError as e:
        stash_notice(str(e), color='warning')
        ui.navigate.to(return_path)
        return
    except Exception:
        logger.exception('Challonge service connection failed')
        stash_notice('Could not connect Challonge. Please try again.', color='negative')
        ui.navigate.to(return_path)
        return
    if grant.community:
        granted.append(grant.community)
    if grant.queue:
        app.storage.user['challonge_service_return'] = return_path
        app.storage.user['challonge_service_tenant'] = grant.tenant_id
        app.storage.user['challonge_service_granted'] = granted
        ui.navigate.to(_next_service_grant(grant.queue))
        return
    stash_notice(_connected_notice(granted), color='positive')
    ui.navigate.to(return_path)


async def _finish_player_link(user, code: str | None, return_path: str) -> None:
    if code is None:
        stash_notice('Challonge linking was cancelled or failed.', color='warning')
    else:
        try:
            service = ChallongeService()
            me = await service.exchange_player_code(code)
            await service.record_player_link(user, me['user_id'], me.get('username'), actor=user)
            stash_notice('Challonge account linked.', color='positive')
        except ValueError as e:
            stash_notice(str(e), color='warning')
        except Exception:
            logger.exception('Challonge player linking failed')
            stash_notice('Could not link Challonge. Please try again.', color='negative')
    ui.navigate.to(return_path)
