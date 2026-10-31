"""Design B — cross-host login handoff for custom domains.

When ``HOST_OAUTH_MODE=handoff``, Discord OAuth always completes on the platform
host (one registered redirect URI, regardless of how many custom domains exist —
unlike Design A, which needs a registered URI per domain). The platform-host
callback then **mints** a short-lived, single-use, host-bound token and redirects
the browser to the target custom domain's ``/session/claim``, which **claims** it
and writes the host-local session so the auth cookie lands on the right host.

Why this is safe despite a token travelling in a URL:

- **Single-use.** The token's nonce is deleted from the ``oauthhandoff`` table on
  first claim; a replay after the victim's own claim finds nothing. The table is
  shared, so the claim may land on a different instance from the mint.
- **Short TTL.** The signer enforces ``_TTL_SECONDS``; a leaked URL (browser
  history / ``Referer``) is only briefly replayable, and the redirect→claim round
  trip is sub-second in practice.
- **Host-bound.** The target host is signed into the token *and* held in the
  store, and the claim rejects a token presented on any other host — so a token
  minted for ``foo.gg`` cannot be replayed against ``bar.gg``.
- **Identity carried server-side.** Only the nonce + host travel in the URL; the
  ``discord_id``/username/avatar live in the store, so no PII is exposed in the
  URL or logs.
"""

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from itsdangerous import BadData, URLSafeTimedSerializer

from application.repositories import OAuthHandoffRepository
from application.utils.hostname import normalize_hostname

logger = logging.getLogger(__name__)

_SALT = 'wizzrobe-oauth-handoff'
_TTL_SECONDS = 30


def _serializer() -> URLSafeTimedSerializer:
    # Read lazily so a test/tooling override applies without reimporting.
    import os
    return URLSafeTimedSerializer(os.environ.get('STORAGE_SECRET') or '', salt=_SALT)


async def reset() -> None:
    """Drop every pending handoff (test isolation)."""
    await OAuthHandoffRepository.delete_all()


def _at(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


async def _mint(fields: dict, target_host: str, next_path: str,
                bind_commit: Optional[str]) -> Optional[str]:
    """Store ``fields`` under a fresh nonce bound to ``target_host``; return the token.

    ``target_host`` must already be validated as a known active tenant domain by
    the caller; it is normalized here so the claim's host comparison is apples to
    apples. Returns ``None`` if the host is not normalizable.
    """
    host = normalize_hostname(target_host)
    if host is None:
        return None
    now = time.time()
    await OAuthHandoffRepository.delete_expired(_at(now))
    nonce = secrets.token_urlsafe(24)
    await OAuthHandoffRepository.create(
        nonce, host,
        {**fields, 'host': host, 'next': next_path, 'bind_commit': bind_commit},
        _at(now + _TTL_SECONDS),
    )
    return _serializer().dumps({'n': nonce, 'h': host})


async def mint(*, discord_id, username: Optional[str], avatar: Optional[str],
               target_host: str, next_path: str, bind_commit: Optional[str] = None) -> Optional[str]:
    """Mint a Discord-login handoff token for ``discord_id`` bound to ``target_host``.

    ``bind_commit`` is a hash the *initiating* browser committed to at ``/login``
    (the raw secret stays in that browser's custom-domain session). The claim
    route re-derives it from the browser's cookie and compares, so a token minted
    in one browser can't be delivered to another (login-CSRF / forced login).
    """
    return await _mint(
        {'discord_id': discord_id, 'username': username, 'avatar': avatar},
        target_host, next_path, bind_commit,
    )


async def mint_data(*, data: dict, target_host: str, next_path: str,
                    bind_commit: Optional[str] = None) -> Optional[str]:
    """Mint a generic handoff token carrying an arbitrary ``data`` payload.

    Used by the secondary-provider link handoff (``pages/_oauth_link.py``): the
    platform host runs the provider OAuth, then hands the **verified public
    identity** (``{'key', 'user_id', 'name'}``) back to the custom domain where
    the user's session lives. Same single-use / TTL / host-binding / browser-
    binding guarantees as :func:`mint`; only the payload shape differs — the
    claim returns it under ``payload['data']``.
    """
    return await _mint({'data': data}, target_host, next_path, bind_commit)


async def claim(token: str, request_host: str) -> Optional[dict]:
    """Validate + consume a handoff token presented on ``request_host``.

    Returns the stored payload (``discord_id``/``username``/``avatar``/``next``)
    on success, or ``None`` for an invalid, expired, wrong-host, or
    already-claimed token. Single-use: the nonce is popped whether or not the
    later host check passes, so a token can never be tried twice.
    """
    host = normalize_hostname(request_host)
    if host is None:
        return None
    try:
        data = _serializer().loads(token, max_age=_TTL_SECONDS)
    except BadData:
        return None
    if not isinstance(data, dict):
        return None
    nonce = data.get('n')
    signed_host = data.get('h')
    if not nonce:
        return None
    row = await OAuthHandoffRepository.take(nonce)  # consume regardless of outcome (single-use)
    if row is None:
        return None
    if row.expires_at <= _at(time.time()):
        return None
    payload = row.payload
    # All three must match: a token minted for one host is useless on any other.
    if signed_host != host or payload['host'] != host:
        logger.warning('OAuth handoff token host mismatch (presented on %r)', host)
        return None
    return payload
