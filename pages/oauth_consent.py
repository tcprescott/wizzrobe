"""The consent screen for third-party apps (``/oauth/consent``).

Tenant-less for the same reason as the MCP consent page: the person has not
picked a community yet, and picking one is part of what they decide here. So
it is a bare ``@ui.page`` that opts into ``AuthMiddleware``'s sign-in redirect
through ``protected_routes``.

One card: the app and who made it, where approving sends you, a community
picker, then the scopes the app asked for in two groups. What it can *see* is
ticked; what it can *do as you* is not, so a hurried click through gives an
app reads only. Staff scopes appear only in communities where the person holds
a role that could use them, and are never ticked for them.

Everything about the app (name, maker, homepage) is player-written and is
rendered as plain labels.

Wizzrobe's own app (``OAuthApp.first_party``) skips this screen entirely: once
the person is signed in, the request is approved and they're sent straight
back to it.
"""

import logging
from typing import Dict, Optional

from fastapi import Query
from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.api_scopes import ApiScope
from application.errors import NotFoundError
from application.services import OAuthGrantService
from application.services.auth_service import get_user_from_session
from middleware.auth import protected_routes
from theme.chrome import render_platform_chrome
from theme.consent_scopes import default_ticks, render_scope_checklist
from theme.notify import notify_error

logger = logging.getLogger(__name__)

CONSENT_PATH = '/oauth/consent'

def _render_notice(headline: str, message: str) -> None:
    render_platform_chrome('Authorize')
    with ui.column().classes('consent-page'):
        with ui.card().classes('consent-card consent-card--warn'):
            ui.icon('hourglass_disabled').classes('consent-badge consent-badge--warn')
            ui.label(headline).classes('consent-title')
            ui.label(message).classes('consent-lede')
            ui.label(
                'Nothing was shared. Start again from the app that sent you here.'
            ).classes('consent-note')


def create() -> None:
    """Register the consent page."""

    protected_routes.add(CONSENT_PATH)

    @ui.page(CONSENT_PATH)
    async def app_consent(request_id: str = Query('', alias='request')):
        # Not a parameter named `request`: NiceGUI hands a page its Starlette Request under that name.
        request = request_id
        service = OAuthGrantService()
        user = await get_user_from_session(app.storage.user)
        if user is None:
            return RedirectResponse('/login', status_code=302)

        try:
            first_party = await service.approve_first_party(request, user)
        except NotFoundError as exc:
            _render_notice('This sign-in request has expired', str(exc))
            return None
        if first_party is not None:
            return RedirectResponse(first_party, status_code=302)

        ui.page_title('Authorize an app — Wizzrobe')
        try:
            view = await service.consent_view(request, user)
        except NotFoundError as exc:
            _render_notice('This sign-in request has expired', str(exc))
            return None

        communities = {c.tenant_id: c for c in view.communities}
        state: Dict[str, object] = {
            'tenant_id': view.communities[0].tenant_id if view.communities else None,
            'ticked': set(),
        }

        if state['tenant_id'] is not None:
            state['ticked'] = default_ticks(communities[state['tenant_id']].scopes)  # type: ignore[index]

        async def approve() -> None:
            tenant_id = state['tenant_id']
            if tenant_id is None:
                return
            try:
                target = await service.approve(request, user, int(tenant_id), state['ticked'])  # type: ignore[arg-type,call-overload]
            except (ValueError, PermissionError) as exc:
                notify_error(exc)
                return
            ui.navigate.to(target)

        def deny() -> None:
            try:
                target = service.deny(request)
            except NotFoundError:
                ui.navigate.to('/')
                return
            ui.navigate.to(target)

        def _signed_in_as() -> None:
            ui.label(user.preferred_name).classes('user-name')

        def set_tick(scope: ApiScope, on: bool) -> None:
            ticked: set = state['ticked']  # type: ignore[assignment]
            (ticked.add if on else ticked.discard)(scope)
            approve_button.set_enabled(bool(ticked))

        @ui.refreshable
        def scope_list() -> None:
            tenant_id = state['tenant_id']
            if tenant_id is None:
                return
            render_scope_checklist(
                communities[tenant_id].scopes,  # type: ignore[index]
                state['ticked'],  # type: ignore[arg-type]
                set_tick,
            )
            with ui.row().classes('consent-note consent-note--lock no-wrap'):
                ui.icon('lock').props('size=sm')
                ui.label(
                    'Changes are off until you tick them. It never sees more than you can, '
                    'and anything it changes is logged under your name.'
                )

        def pick_community(tenant_id: Optional[int]) -> None:
            state['tenant_id'] = tenant_id
            state['ticked'] = default_ticks(communities[tenant_id].scopes) if tenant_id else set()
            scope_list.refresh()
            approve_button.set_enabled(bool(state['ticked']))

        render_platform_chrome('Authorize', right=_signed_in_as)

        with ui.column().classes('consent-page'):
            with ui.card().classes('consent-card'):
                with ui.row().classes('items-center no-wrap gap-3'):
                    ui.icon('apps').classes('consent-badge')
                    with ui.column().classes('gap-1 min-w-0'):
                        ui.label(f'{view.app_name} wants to use your Wizzrobe account') \
                            .classes('consent-title consent-title--app')
                        with ui.row().classes('items-center gap-2'):
                            if not view.verified:
                                ui.badge('Unverified app').props('color=warning outline') \
                                    .tooltip("Wizzrobe hasn't checked who runs this app")
                            ui.label(f'made by {view.owner_name}').classes('consent-note')
                if view.app_description:
                    ui.label(view.app_description).classes('consent-lede')
                with ui.row().classes('consent-note consent-note--lock no-wrap'):
                    ui.icon('open_in_new').props('size=sm')
                    ui.label(
                        f'Approving sends you back to {view.redirect_host}. Any app can call '
                        "itself anything, so check that's where you started."
                    )

                if not view.communities:
                    ui.separator().classes('consent-rule')
                    ui.label(
                        "None of your communities let members connect apps, so there's "
                        'nothing to approve. Ask a community\'s staff to turn on '
                        'Third-party Apps under Admin → Features.'
                    ).classes('consent-lede')
                    with ui.row().classes('consent-actions'):
                        ui.button('Go back', on_click=deny).props('flat')
                    return None

                ui.select(
                    {c.tenant_id: c.name for c in view.communities},
                    value=state['tenant_id'],
                    label='Community',
                    on_change=lambda e: pick_community(e.value),
                ).props('outlined').classes('w-full q-mt-sm')
                ui.label(
                    'It only works in the community you pick. Connect again for another one.'
                ).classes('consent-note')
                ui.separator().classes('consent-rule')
                scope_list()
                ui.label('You can disconnect it any time from your profile.').classes('consent-note')
                with ui.row().classes('consent-actions'):
                    ui.button('Deny', on_click=deny).props('flat')
                    approve_button = ui.button('Approve', icon='check', on_click=approve) \
                        .props('color=primary')
                    approve_button.set_enabled(bool(state['ticked']))
        return None
