"""The OAuth consent screen for MCP clients.

A bespoke ``@ui.page`` rather than ``@protected_page``, for a structural reason:
every ``@protected_page`` is a *tenant* page and 404s when reached with no
tenant, but an MCP grant is deliberately platform-wide — it authenticates a
person, not a membership. So this route lives alongside the other tenant-less
OAuth pages and opts into authentication by registering itself in
``protected_routes`` directly, which is what makes ``AuthMiddleware`` bounce an
anonymous visitor to ``/login`` and back here afterwards.

There is no community picker. The issued token carries no tenant; each MCP tool
call names its own community and is authorized against the user's role there.
Asking here would imply a scoping the credential does not actually have.

The card is the same scope checklist outside apps get (``theme.consent_scopes``):
what it may see, ticked, and what it may do as you, unticked. Write access is
the one thing a grant can carry that the user cannot undo by reading more
carefully, so it is asked for here and nowhere else: a client cannot request
its way past this, and a refresh cannot widen a grant that was approved
without it. Ticking a change is not enough on its own: the person also types
"this is dangerous", which the service checks again, so a hurried click
through the card still ends in a read-only grant.
"""

import logging
from urllib.parse import urlencode, urlparse

from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.api_scopes import WRITE_SCOPES, ApiScope
from application.errors import NotFoundError
from application.services import McpAuthService
from application.services.auth_service import get_user_from_session
from application.services.mcp_auth_service import (
    WRITE_CONFIRMATION_PHRASE,
    is_allowed_redirect_uri,
    write_confirmation_matches,
)
from middleware.auth import protected_routes
from theme.banner import banner
from theme.chrome import render_platform_chrome
from theme.consent_scopes import render_scope_checklist

logger = logging.getLogger(__name__)

CONSENT_PATH = '/oauth/mcp/consent'

def _redirect_target(redirect_uri: str) -> str:
    """Where approving actually sends the code, as a person can check it.

    Client registration is open (RFC 7591 — that is what lets a client configure
    itself from a URL), so ``client_name`` is a string the requester chose and
    proves nothing. The redirect URI is the one part of the request that cannot
    be faked: it is where the authorization code goes. Showing it is what lets
    someone notice that "Claude" wants to hand the code to a host they have never
    heard of.

    Host and port only — a full URL with query string is noise on a card whose
    whole job is to be read.
    """
    parsed = urlparse(redirect_uri or '')
    if parsed.scheme in ('http', 'https') and parsed.netloc:
        return parsed.netloc
    # A native client's custom scheme (``cursor://…``) has no netloc; the scheme
    # is the identifying part, so show the URI as given rather than nothing.
    return redirect_uri or 'an unknown address'


def _render_notice(headline: str, message: str) -> None:
    """The consent card's failure shape — an expired or unknown transaction.

    Deliberately not the themed error page: nothing is broken and there is no
    tenant to frame it in, so it stays on the same tenant-less consent surface
    and simply says the request can be started again.
    """
    render_platform_chrome('Authorize')
    with ui.column().classes('consent-page'):
        with ui.card().classes('consent-card consent-card--warn'):
            ui.icon('hourglass_disabled').classes('consent-badge consent-badge--warn')
            ui.label(headline).classes('consent-title')
            ui.label(message).classes('consent-lede')
            ui.label(
                'Nothing was shared. Start the connection again from the app '
                'that sent you here.'
            ).classes('consent-note')


def create() -> None:
    """Register the consent page."""

    # Opt into AuthMiddleware's redirect-to-login without becoming a tenant page.
    protected_routes.add(CONSENT_PATH)

    @ui.page(CONSENT_PATH)
    async def mcp_consent(txn: str = ''):
        service = McpAuthService()
        user = await get_user_from_session(app.storage.user)
        if user is None:
            # AuthMiddleware normally prevents this; if the session is half-built
            # the safe move is to send them to sign in rather than render a
            # consent screen with no identity behind it.
            return RedirectResponse('/login', status_code=302)

        ui.page_title('Authorize — Wizzrobe')

        try:
            pending = service.get_pending(txn)
        except NotFoundError as exc:
            _render_notice('Authorization request expired', str(exc))
            return None

        client = await service.get_client(pending.client_id)
        client_name = client.client_name if client else 'An MCP client'

        offered = await service.offered_scopes(user)
        # Reads ticked, changes not: everything below reads this set and
        # nothing else decides the grant.
        ticked = {scope for scope in offered if scope not in WRITE_SCOPES}
        confirmation = {'value': ''}

        async def approve() -> None:
            try:
                code, redirect_uri, state = await service.approve(
                    txn, user, scopes=ticked, write_confirmation=confirmation['value'],
                )
            except (ValueError, NotFoundError) as exc:
                ui.notify(str(exc), color='warning')
                return
            params = {'code': code}
            if state:
                params['state'] = state
            sep = '&' if '?' in redirect_uri else '?'
            ui.navigate.to(f'{redirect_uri}{sep}{urlencode(params)}')

        def deny() -> None:
            service.discard_pending(txn)
            if not is_allowed_redirect_uri(pending.redirect_uri):
                ui.navigate.to('/')
                return
            sep = '&' if '?' in pending.redirect_uri else '?'
            params = {
                'error': 'access_denied',
                'error_description': 'The user denied the request.',
            }
            if pending.state:
                params['state'] = pending.state
            ui.navigate.to(f'{pending.redirect_uri}{sep}{urlencode(params)}')

        def _signed_in_as() -> None:
            """Whose grant this is, in the header — the same identity the card names."""
            ui.label(user.preferred_name).classes('user-name')

        def writes_ticked() -> bool:
            return bool(ticked & WRITE_SCOPES)

        def sync_approve() -> None:
            approve_button.set_enabled(
                bool(ticked)
                and (not writes_ticked() or write_confirmation_matches(confirmation['value']))
            )

        def set_tick(scope: ApiScope, on: bool) -> None:
            (ticked.add if on else ticked.discard)(scope)
            write_confirm.set_visibility(writes_ticked())
            sync_approve()

        def on_confirmation(event) -> None:
            confirmation['value'] = event.value or ''
            sync_approve()

        render_platform_chrome('Authorize', right=_signed_in_as)

        with ui.column().classes('consent-page'):
            with ui.card().classes('consent-card'):
                ui.icon('link').classes('consent-badge')
                ui.label('Connect to Wizzrobe').classes('consent-title')
                ui.label(
                    f'{client_name} wants to access Wizzrobe as '
                    f'{user.preferred_name}.'
                ).classes('consent-lede')
                with ui.row().classes('consent-note consent-note--lock no-wrap'):
                    ui.icon('open_in_new').props('size=sm')
                    ui.label(
                        f'Approving sends your access to {_redirect_target(pending.redirect_uri)}. '
                        'Any app can call itself anything here, so check that address '
                        'is the one you started from.'
                    )
                with ui.row().classes('consent-note consent-note--lock no-wrap'):
                    ui.icon('groups').props('size=sm')
                    ui.label(
                        'Works in every community you belong to, as whatever you are in each one.'
                    )
                ui.separator().classes('consent-rule')
                render_scope_checklist(offered, ticked, set_tick, every_community=True)
                with ui.column().classes('consent-write-confirm') as write_confirm:
                    with banner(
                        'Anything you can change, it can change too, without '
                        'asking you first: cancel matches, overwrite results, '
                        'drop people from crew. A model that misreads a request '
                        'acts on the misreading.',
                        title='This is dangerous',
                        tone='warning',
                    ):
                        pass
                    ui.input(
                        label=f'Type "{WRITE_CONFIRMATION_PHRASE}" to allow changes',
                        on_change=on_confirmation,
                    ).props('outlined dense autocomplete=off spellcheck=false').classes(
                        'consent-write-input'
                    )
                write_confirm.set_visibility(False)
                with ui.row().classes('consent-note consent-note--lock no-wrap'):
                    ui.icon('lock').props('size=sm')
                    ui.label(
                        'Changes are off until you tick them. It never sees more than you '
                        'can, and anything it changes is logged under your name.'
                    )
                ui.label(
                    'You can disconnect it at any time from your profile.'
                ).classes('consent-note')
                with ui.row().classes('consent-actions'):
                    ui.button('Deny', on_click=deny).props('flat')
                    approve_button = ui.button(
                        'Approve', icon='check', on_click=approve,
                    ).props('color=primary')
        return None
