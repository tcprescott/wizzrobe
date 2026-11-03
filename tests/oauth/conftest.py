"""Harness for the third-party app authorization server suite.

An app with the REST API under ``/api`` and the ``/oauth`` routes mounted by
the production ``oauth_server.mount``, plus helpers that drive the flow with
the consent screen's browser step replaced by a direct service call.
"""

import base64
import functools
import hashlib
import secrets
from typing import Iterable, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api
from api import oauth_server
from application.services import OAuthAppService, OAuthGrantService
from models import OAuthApp, OAuthClientType, User

REDIRECT = 'https://app.example.com/callback'


@functools.cache
def build_oauth_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    oauth_server.mount(app)
    return app


def client(raw_token: Optional[str] = None) -> AsyncClient:
    headers = {'Authorization': f'Bearer {raw_token}'} if raw_token else {}
    return AsyncClient(
        transport=ASGITransport(app=build_oauth_test_app()), base_url='http://test', headers=headers,
    )


def pkce() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip('=')


def basic(client_id: str, secret: str) -> dict:
    raw = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
    return {'Authorization': f'Basic {raw}'}


def query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def make_app(
    owner: User,
    *,
    client_type: OAuthClientType = OAuthClientType.CONFIDENTIAL,
    scopes: Iterable[str] = ('profile', 'schedule:read', 'play', 'staff:read'),
    redirect_uris: Iterable[str] = (REDIRECT,),
) -> Tuple[OAuthApp, Optional[str]]:
    return await OAuthAppService().create_app(
        owner, name='Test App', client_type=client_type,
        redirect_uris=list(redirect_uris), allowed_scopes=list(scopes),
    )


async def start(http: AsyncClient, app: OAuthApp, challenge: str, *, scope: Optional[str] = None,
                state: str = 'st4te', redirect_uri: str = REDIRECT) -> str:
    """Run /oauth/authorize and return the parked request id."""
    params = {
        'client_id': app.client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state,
    }
    if scope is not None:
        params['scope'] = scope
    resp = await http.get('/oauth/authorize', params=params)
    assert resp.status_code == 302, resp.text
    location = resp.headers['location']
    assert location.startswith('/oauth/consent?'), location
    return query(location)['request']


async def authorize_code(http: AsyncClient, app: OAuthApp, user: User, tenant_id: int,
                         scopes: Iterable[str], challenge: str, **kwargs) -> str:
    txn = await start(http, app, challenge, **kwargs)
    redirect = await OAuthGrantService().approve(txn, user, tenant_id, list(scopes))
    params = query(redirect)
    assert params['state'] == kwargs.get('state', 'st4te')
    return params['code']


async def exchange(http: AsyncClient, app: OAuthApp, secret: Optional[str], code: str, verifier: str,
                   redirect_uri: str = REDIRECT):
    data = {'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier,
            'redirect_uri': redirect_uri}
    if secret is None:
        data['client_id'] = app.client_id
        return await http.post('/oauth/token', data=data)
    return await http.post('/oauth/token', data=data, headers=basic(app.client_id, secret))


async def grant_tokens(http: AsyncClient, app: OAuthApp, secret: Optional[str], user: User,
                       tenant_id: int, scopes: Iterable[str]) -> dict:
    verifier, challenge = pkce()
    code = await authorize_code(http, app, user, tenant_id, scopes, challenge)
    resp = await exchange(http, app, secret, code, verifier)
    assert resp.status_code == 200, resp.text
    return resp.json()
