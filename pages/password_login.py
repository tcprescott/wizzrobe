"""Password sign-in at ``/password-login``: unlisted, for accounts a platform
admin gave a password.

Nothing links here. It works on the platform host and under ``/t/<slug>``; on a
community's own domain it redirects to that community's ``/t/<slug>`` copy on the
platform host, because a custom domain's operator can read what is typed there.
Every failure reads the same, so the page never says whether a name exists.
"""

from typing import Optional
from urllib.parse import urlencode

from nicegui import Client, app, ui
from starlette.requests import Request
from starlette.responses import RedirectResponse

from application.services import PasswordAuthService, TenantService
from application.services.password_auth_service import normalize_login_name
from application.tenant_context import is_host_mode
from application.utils import login_throttle
from application.utils.tenant_urls import return_path_for_login, safe_local_path, strip_root_path
from pages._oauth_link import platform_link_redirect
from pages._session import start_session
from theme.banner import banner
from theme.chrome import render_platform_chrome
from theme.page_header import page_title

_MISMATCH = "That sign-in name and password don't match. Check both and try again."
_BLOCKED = 'Too many tries. Wait a few minutes, then try again.'


async def main_site_sign_in(next_param: Optional[str]) -> Optional[str]:
    """On a custom domain, this community's sign-in page on the platform host.

    Whoever runs a custom domain's DNS or TLS can read what is typed there, and
    unlike a host-bound session a password works on every host. ``None`` off a
    custom domain, where the page signs in in place.
    """
    if not is_host_mode():
        return None
    local_next = safe_local_path(next_param)
    here = f'/password-login?{urlencode({"next": local_next})}' if local_next else '/password-login'
    return await platform_link_redirect(here) or '/'


def create() -> None:
    @ui.page('/password-login')
    async def password_login(request: Request, client: Client) -> Optional[RedirectResponse]:
        root_path = request.scope.get('root_path', '') or ''
        next_param = request.query_params.get('next')
        main_site = await main_site_sign_in(next_param)
        if main_site is not None:
            return RedirectResponse(main_site)
        if app.storage.user.get('authenticated', False):
            return RedirectResponse(return_path_for_login(root_path, next_param, None))
        return_to = return_path_for_login(root_path, next_param, app.storage.user.get('referrer_path'))

        ui.page_title('Sign in')
        render_platform_chrome(await TenantService.current_community_name() or None)

        state: dict[str, Optional[str]] = {'error': None}

        @ui.refreshable
        def error_slot() -> None:
            if state['error']:
                with banner(state['error'], tone='danger'):
                    pass

        async def submit() -> None:
            name = normalize_login_name(name_input.value)
            try:
                browser_id = app.storage.browser.get('id')
            except Exception:
                browser_id = None
            if not login_throttle.begin_attempt(name, browser_id):
                state['error'] = _BLOCKED
                error_slot.refresh()
                return
            button.props('loading')
            try:
                user = await PasswordAuthService().authenticate(name, password_input.value or '')
            finally:
                button.props(remove='loading')
            if user is None:
                state['error'] = _MISMATCH
                password_input.value = ''
                error_slot.refresh()
                return
            login_throttle.record_success(name, browser_id)
            app.storage.user.clear()
            start_session(user, {'username': user.username, 'avatar': None}, password=True)
            # Tenant-local: the client prepends root_path, so a qualified path would double it.
            ui.navigate.to(strip_root_path(root_path, return_to))

        with ui.column().classes('password-login w-full items-center q-pa-md'):
            with ui.card().classes('password-login__card w-full gap-3').props('flat bordered'):
                page_title('Sign in')
                error_slot()
                name_input = ui.input('Sign-in name').props(
                    'outlined dense autocomplete=username autocapitalize=none spellcheck=false',
                ).classes('w-full')
                password_input = ui.input(
                    'Password', password=True, password_toggle_button=True,
                ).props('outlined dense autocomplete=current-password').classes('w-full')
                button = ui.button('Sign in', on_click=submit).props('color=primary unelevated').classes('w-full')
                name_input.on('keydown.enter', submit)
                password_input.on('keydown.enter', submit)
        return None
