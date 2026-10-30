"""Twitch OAuth + Helix transport client.

A thin async ``aiohttp`` wrapper over the two Twitch endpoints we need to link a
user's account: the OAuth token exchange and the Helix ``users`` lookup. It is
deliberately small — we only capture identity (id / login / display name) and
never retain the access token.

The client returns a **normalized** identity dict so the service layer never has
to know the wire shape:

    get_me -> {'user_id', 'username', 'display_name'}

The transport (``build_authorize_url`` / ``exchange_code`` / ``get_me``) lives in
``oauth_identity_client``; this module is the Twitch-specific configuration.
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

AUTHORIZE_URL = 'https://id.twitch.tv/oauth2/authorize'
OAUTH_EXCHANGE_URL = 'https://id.twitch.tv/oauth2/token'
USERS_URL = 'https://api.twitch.tv/helix/users'


class TwitchAPIError(Exception):
    """Raised when the Twitch API returns an error or unexpected payload."""


def build_authorize_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    """Return the Twitch OAuth authorize URL to redirect the browser to."""
    return _build_authorize_url(AUTHORIZE_URL, client_id, redirect_uri, scope, state)


def _extract_identity(payload: Any) -> Dict[str, Any]:
    records = payload.get('data') or []
    if not records:
        raise TwitchAPIError(f"Twitch users response contained no user: {payload}")
    record = records[0]
    return {
        'user_id': opt_str(record.get('id')),
        'username': record.get('login'),
        'display_name': record.get('display_name'),
    }


class TwitchClient(OAuthIdentityClient):
    """Async Twitch OAuth + Helix client."""

    config = ProviderConfig(
        label='Twitch',
        token_url=OAUTH_EXCHANGE_URL,
        userinfo_url=USERS_URL,
        error_class=TwitchAPIError,
        userinfo_noun='users',
        # Helix requires the app Client-Id header alongside the bearer token.
        userinfo_headers=lambda client_id: {'Client-Id': client_id},
        extract_identity=_extract_identity,
    )


# Mock client (MOCK_TWITCH) — a deterministic canned identity so local dev
# can click through link/unlink without a real OAuth app.
_MOCK_IDENTITIES = [
    {'user_id': '20001', 'username': 'mocktwitchone', 'display_name': 'MockTwitchOne'},
    {'user_id': '20002', 'username': 'mocktwitchtwo', 'display_name': 'MockTwitchTwo'},
    {'user_id': '20003', 'username': 'mocktwitchthree', 'display_name': 'MockTwitchThree'},
    {'user_id': '20004', 'username': 'mocktwitchfour', 'display_name': 'MockTwitchFour'},
]


class MockTwitchClient(TwitchClient):
    """Canned client used when MOCK_TWITCH is enabled."""

    def __init__(self, *args, **kwargs):
        super().__init__('mock', 'mock')

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        return {'access_token': 'mock-access', 'token_type': 'bearer', 'expires_in': 14400}

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        # Which canned identity the next link binds to, chosen via env hint so
        # distinct dev users can link to distinct Twitch identities.
        idx = int(os.environ.get('MOCK_TWITCH_IDENTITY', '1'))
        return dict(_MOCK_IDENTITIES[(idx - 1) % len(_MOCK_IDENTITIES)])
