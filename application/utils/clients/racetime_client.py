"""racetime.gg OAuth transport client.

A thin async ``aiohttp`` wrapper over the two racetime.gg endpoints we need to
link a user's account: the OAuth token exchange and the ``o/userinfo`` identity
lookup. Deliberately small — we only capture identity (id / name) and never
retain the access token.

The client returns a **normalized** identity dict so the service layer never has
to know the wire shape:

    get_me -> {'user_id', 'username'}

The transport (``build_authorize_url`` / ``exchange_code`` / ``get_me``) lives in
``oauth_identity_client``; this module is the racetime-specific configuration.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from application.utils.clients.oauth_identity_client import (
    OAuthIdentityClient,
    ProviderConfig,
    opt_str,
)
from application.utils.clients.oauth_identity_client import (
    build_authorize_url as _build_authorize_url,
)

RACETIME_BASE = 'https://racetime.gg'
AUTHORIZE_URL = f'{RACETIME_BASE}/o/authorize'
OAUTH_EXCHANGE_URL = f'{RACETIME_BASE}/o/token'
USERINFO_URL = f'{RACETIME_BASE}/o/userinfo'

IDENTITY_SCOPE = 'read'


class RacetimeAPIError(Exception):
    """Raised when the racetime API returns an error or unexpected payload."""


def build_authorize_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    """Return the racetime OAuth authorize URL to redirect the browser to."""
    return _build_authorize_url(AUTHORIZE_URL, client_id, redirect_uri, scope, state)


def _extract_identity(payload: Any) -> Dict[str, Any]:
    user_id = opt_str(payload.get('id'))
    if not user_id:
        raise RacetimeAPIError(f"racetime userinfo response missing id: {payload}")
    return {
        'user_id': user_id,
        'username': payload.get('name'),
    }


class RacetimeClient(OAuthIdentityClient):
    """Async racetime.gg OAuth client."""

    config = ProviderConfig(
        label='racetime',
        token_url=OAUTH_EXCHANGE_URL,
        userinfo_url=USERINFO_URL,
        error_class=RacetimeAPIError,
        userinfo_noun='userinfo',
        extract_identity=_extract_identity,
    )


_MOCK_IDENTITIES = [
    {'user_id': 'mockrt0001', 'username': 'MockRacerOne'},
    {'user_id': 'mockrt0002', 'username': 'MockRacerTwo'},
    {'user_id': 'mockrt0003', 'username': 'MockRacerThree'},
    {'user_id': 'mockrt0004', 'username': 'MockRacerFour'},
]


class MockRacetimeClient(RacetimeClient):
    """Canned client used when MOCK_RACETIME is enabled."""

    def __init__(self, *args, **kwargs):
        super().__init__('mock', 'mock')

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        return {'access_token': 'mock-access', 'token_type': 'bearer', 'expires_in': 36000}

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        idx = int(os.environ.get('MOCK_RACETIME_IDENTITY', '1'))
        return dict(_MOCK_IDENTITIES[(idx - 1) % len(_MOCK_IDENTITIES)])
