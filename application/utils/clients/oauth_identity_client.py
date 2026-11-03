"""Shared OAuth identity-link transport.

Twitch and racetime.gg both link a user's account through the same three-step
dance: redirect to an authorize URL, exchange the returned code for a token, then
read the account identity from a userinfo endpoint (discarding the token). The
wire mechanics are identical; only the endpoint URLs, the extra userinfo header,
the error class, and the identity field mapping differ per provider.

This module captures that common transport so each provider client becomes a
small ``ProviderConfig`` plus a normalizing identity extractor. The base client
returns a **normalized** identity dict so the service layer never sees the wire
shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import quote

import aiohttp

_TIMEOUT = aiohttp.ClientTimeout(total=20)


def opt_str(value: Any) -> Optional[str]:
    """Coerce a present value to ``str`` while preserving ``None``."""
    return None if value is None else str(value)


def build_authorize_url(
    authorize_url: str, client_id: str, redirect_uri: str, scope: str, state: str
) -> str:
    """Build a standard OAuth ``authorization_code`` authorize URL."""
    return (
        f"{authorize_url}"
        f"?client_id={quote(client_id, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope={quote(scope, safe='')}"
        f"&state={quote(state, safe='')}"
    )


@dataclass(frozen=True)
class ProviderConfig:
    """Per-provider wire configuration for :class:`OAuthIdentityClient`.

    - ``label`` / ``userinfo_noun`` shape the error messages
      (``"{label} token request failed"``, ``"{label} {userinfo_noun} request failed"``).
    - ``userinfo_headers`` yields any provider-specific userinfo headers given the
      client id (e.g. Twitch's ``Client-Id``).
    - ``extract_identity`` maps the decoded userinfo payload to the normalized
      identity dict, raising ``error_class`` on a missing/unexpected shape.
    """

    label: str
    token_url: str
    userinfo_url: str
    error_class: type[Exception]
    userinfo_noun: str = 'userinfo'
    userinfo_headers: Callable[[str], Mapping[str, str]] = lambda client_id: {}
    extract_identity: Callable[[Any], Dict[str, Any]] = lambda payload: dict(payload)


class OAuthIdentityClient:
    """Async OAuth identity client shared by the Twitch and racetime clients."""

    config: ProviderConfig

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange an authorization code for a token payload."""
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
        }
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(self.config.token_url, data=data) as resp:
                payload = await resp.json()
                if resp.status >= 400:
                    raise self.config.error_class(
                        f"{self.config.label} token request failed ({resp.status}): {payload}"
                    )
                return payload

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        """Fetch the authenticated account identity for a raw access token."""
        headers = {
            'Authorization': f'Bearer {access_token}',
            **self.config.userinfo_headers(self.client_id),
        }
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(self.config.userinfo_url, headers=headers) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise self.config.error_class(
                        f"{self.config.label} {self.config.userinfo_noun} "
                        f"request failed ({resp.status}): {text}"
                    )
                try:
                    payload = json.loads(text)
                except ValueError as e:
                    raise self.config.error_class(
                        f"{self.config.label} API returned non-JSON: {text[:200]}"
                    ) from e
        return self.config.extract_identity(payload)
