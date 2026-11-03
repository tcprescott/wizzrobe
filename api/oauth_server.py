"""HTTP surface of the third-party app authorization server (issuer ``{BASE_URL}/oauth``).

A separate issuer from the MCP server's root one, so its RFC 8414 document sits
at the path-insertion location ``/.well-known/oauth-authorization-server/oauth``
and the MCP SDK's ``/.well-known/oauth-authorization-server`` is untouched.

Pure protocol translation: parameters in, :class:`OAuthGrantService` calls out,
RFC 6749 responses back. Policy (PKCE, client auth, rotation, the flag) lives in
the service. The consent page itself (``/oauth/consent``) is a NiceGUI page.

Mounted on the outer app, outside ``/api``, so it carries the shared rate limit
itself. ``main.py`` must include it before ``frontend.init`` — NiceGUI mounts at
``/`` and would otherwise answer these paths.
"""

import base64
import binascii
from typing import Optional, Tuple
from urllib.parse import unquote_plus

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from api.rate_limit import rate_limit
from application.api_scopes import ApiScope
from application.services.oauth_grant_service import OAuthError, OAuthGrantService, issuer
from application.utils.environment import get_base_url

METADATA_PATH = '/.well-known/oauth-authorization-server/oauth'
_NO_STORE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}

router = APIRouter(prefix='/oauth', dependencies=[Depends(rate_limit)], include_in_schema=False)


def authorization_server_metadata() -> dict:
    base = issuer()
    return {
        'issuer': base,
        'authorization_endpoint': f'{base}/authorize',
        'token_endpoint': f'{base}/token',
        'revocation_endpoint': f'{base}/revoke',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': ['client_secret_basic', 'client_secret_post', 'none'],
        'revocation_endpoint_auth_methods_supported': ['client_secret_basic', 'client_secret_post', 'none'],
        'scopes_supported': [s.value for s in ApiScope],
        'authorization_response_iss_parameter_supported': True,
        'service_documentation': f'{get_base_url()}/api/docs',
    }


def _token_error(exc: OAuthError) -> JSONResponse:
    headers = dict(_NO_STORE)
    if exc.status == 401:
        headers['WWW-Authenticate'] = 'Basic realm="wizzrobe"'
    return JSONResponse(
        {'error': exc.error, 'error_description': exc.description},
        status_code=exc.status, headers=headers,
    )


def _error_page(message: str) -> HTMLResponse:
    """Shown when the request can't safely be bounced back to the app."""
    from html import escape

    body = (
        '<!doctype html><meta charset="utf-8"><title>Sign-in problem</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<main style="font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem">'
        f'<h1>We couldn\'t start this sign-in</h1><p>{escape(message)}</p>'
        '<p>Go back to the app and try again. If it keeps happening, tell whoever made it.</p></main>'
    )
    return HTMLResponse(body, status_code=400, headers=_NO_STORE)


def _field(form, name: str) -> Optional[str]:
    value = form.get(name)
    return value if isinstance(value, str) else None


def _client_credentials(request: Request, form) -> Tuple[Optional[str], Optional[str]]:
    """Client id and secret from HTTP Basic (RFC 6749 §2.3.1) or the form body.

    Using both at once is refused: a client must pick one method.
    """
    header = request.headers.get('authorization', '')
    form_id, form_secret = _field(form, 'client_id'), _field(form, 'client_secret')
    if header.lower().startswith('basic '):
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode('utf-8')
        except (binascii.Error, UnicodeDecodeError):
            raise OAuthError('invalid_client', 'Malformed Basic credentials.', status=401) from None
        if ':' not in decoded:
            raise OAuthError('invalid_client', 'Malformed Basic credentials.', status=401)
        client_id, secret = (unquote_plus(part) for part in decoded.split(':', 1))
        if form_secret is not None or (form_id is not None and form_id != client_id):
            raise OAuthError('invalid_request', 'Use one client authentication method, not two.')
        return client_id, secret
    return form_id, form_secret


@router.get('/authorize')
async def authorize(request: Request) -> Response:
    q = request.query_params
    try:
        txn_id = await OAuthGrantService().begin_authorization(
            client_id=q.get('client_id'),
            redirect_uri=q.get('redirect_uri'),
            response_type=q.get('response_type'),
            code_challenge=q.get('code_challenge'),
            code_challenge_method=q.get('code_challenge_method'),
            scope=q.get('scope'),
            state=q.get('state'),
        )
    except OAuthError as exc:
        target = exc.redirect_url()
        if target is None:
            return _error_page(exc.description)
        return RedirectResponse(target, status_code=302)
    return RedirectResponse(OAuthGrantService.consent_url(txn_id), status_code=302)


@router.post('/token')
async def token(request: Request) -> Response:
    form = await request.form()
    service = OAuthGrantService()
    try:
        client_id, secret = _client_credentials(request, form)
        app = await service.authenticate_client(client_id, secret)
        grant_type = form.get('grant_type')
        if grant_type == 'authorization_code':
            result = await service.exchange_code(
                app,
                code=_field(form, 'code'),
                redirect_uri=_field(form, 'redirect_uri'),
                code_verifier=_field(form, 'code_verifier'),
            )
        elif grant_type == 'refresh_token':
            result = await service.exchange_refresh(
                app, refresh_token=_field(form, 'refresh_token'), scope=_field(form, 'scope'),
            )
        else:
            raise OAuthError('unsupported_grant_type', 'Use authorization_code or refresh_token.')
    except OAuthError as exc:
        return _token_error(exc)
    return JSONResponse(result.as_dict(), headers=_NO_STORE)


@router.post('/revoke')
async def revoke(request: Request) -> Response:
    form = await request.form()
    service = OAuthGrantService()
    try:
        client_id, secret = _client_credentials(request, form)
        app = await service.authenticate_client(client_id, secret)
        await service.revoke_token(app, _field(form, 'token'))
    except OAuthError as exc:
        return _token_error(exc)
    return Response(status_code=200, headers=_NO_STORE)


def mount(app: FastAPI) -> None:
    """Register the routes and the issuer metadata on the outer app."""

    async def _metadata(request: Request) -> JSONResponse:
        await rate_limit(request)
        return JSONResponse(authorization_server_metadata())

    app.add_api_route(METADATA_PATH, _metadata, methods=['GET'], include_in_schema=False)
    app.include_router(router)
