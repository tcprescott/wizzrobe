"""Verify a Google-signed OIDC ID token, such as the one Cloud Scheduler sends.

Cloud Scheduler signs each HTTP call with an ID token for the service account
named on the job: an RS256 JWT whose ``aud`` is the audience set on the job and
whose ``email`` is that service account. Google publishes the signing keys as a
JWKS; they rotate every few days, so a token naming a key we haven't seen
triggers one refetch (at most every :data:`_MIN_REFETCH_SECONDS`).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

import httpx
import jwt

CERTS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')

_DEFAULT_MAX_AGE = 3600.0
_MIN_REFETCH_SECONDS = 60.0
_LEEWAY_SECONDS = 30

_keys: Dict[str, jwt.PyJWK] = {}
_expires_at = 0.0
_last_fetch = 0.0


class InvalidToken(Exception):
    """The token is missing, malformed, unsigned by Google, or for someone else."""


def _max_age(cache_control: Optional[str]) -> float:
    match = re.search(r'max-age=(\d+)', cache_control or '')
    return float(match.group(1)) if match else _DEFAULT_MAX_AGE


async def _fetch_keys() -> None:
    global _keys, _expires_at, _last_fetch
    _last_fetch = time.monotonic()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(CERTS_URL)
        response.raise_for_status()
    keys = {}
    for raw in response.json().get('keys', []):
        try:
            keys[raw['kid']] = jwt.PyJWK(raw)
        except (KeyError, jwt.PyJWTError):
            continue
    _keys = keys
    _expires_at = time.monotonic() + _max_age(response.headers.get('cache-control'))


async def _signing_key(kid: str) -> jwt.PyJWK:
    now = time.monotonic()
    if now >= _expires_at or (kid not in _keys and now - _last_fetch >= _MIN_REFETCH_SECONDS):
        try:
            await _fetch_keys()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            if kid not in _keys:
                raise InvalidToken(f'could not fetch Google signing keys: {exc}') from exc
    key = _keys.get(kid)
    if key is None:
        raise InvalidToken('token is signed with an unknown key')
    return key


async def verify_id_token(token: str, *, audience: str, email: str) -> Dict[str, Any]:
    """The token's claims, once its signature, issuer, audience, expiry and
    service-account email all check out; :class:`InvalidToken` otherwise."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidToken('malformed token') from exc
    if header.get('alg') != 'RS256' or not header.get('kid'):
        raise InvalidToken('token is not an RS256 Google ID token')
    key = await _signing_key(header['kid'])
    try:
        claims = jwt.decode(
            token, key.key, algorithms=['RS256'], audience=audience, leeway=_LEEWAY_SECONDS,
            options={'require': ['exp', 'iat', 'iss', 'aud']},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if claims.get('iss') not in ISSUERS:
        raise InvalidToken('token was not issued by Google')
    if claims.get('email') != email or claims.get('email_verified') is not True:
        raise InvalidToken('token is for a different service account')
    return claims
