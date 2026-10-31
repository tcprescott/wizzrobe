"""Rate limiting for the REST API.

A single process counts in memory (a sliding minute). With ``MULTI_INSTANCE``
set, counts go to Postgres through ``RateLimitService`` so every instance
shares one budget per key. Requests are keyed by bearer token when present (so one client's tokens
are limited independently) and otherwise by client IP.

Attached as a dependency on the aggregating API router so it covers every REST
endpoint without touching the NiceGUI frontend mounted on the same app.
"""

import hashlib
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from application.services import RateLimitService, shared_counters_enabled
from application.services.api_token_service import BEARER_PREFIXES


def limit_per_minute() -> int:
    try:
        return max(1, int(os.environ.get('API_RATE_LIMIT_PER_MIN', '120')))
    except ValueError:
        return 120


def app_limit_per_minute() -> int:
    try:
        return max(1, int(os.environ.get('API_APP_RATE_LIMIT_PER_MIN', '600')))
    except ValueError:
        return 600


_WINDOW_SECONDS = 60.0
_MAX_TRACKED_KEYS = 10_000
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_last_sweep = 0.0


def _sweep(now: float) -> None:
    """Drop keys whose window has fully expired so idle/garbage keys don't
    accumulate. Runs at most once per window, or immediately whenever the
    tracked-key count crosses ``_MAX_TRACKED_KEYS`` so a burst of distinct keys
    within a single window cannot exhaust memory."""
    global _last_sweep
    over_cap = len(_hits) > _MAX_TRACKED_KEYS
    if not over_cap and now - _last_sweep < _WINDOW_SECONDS:
        return
    _last_sweep = now
    cutoff = now - _WINDOW_SECONDS
    for key in [k for k, b in _hits.items() if not b or b[-1] < cutoff]:
        del _hits[key]
    if len(_hits) > _MAX_TRACKED_KEYS:
        overflow = len(_hits) - _MAX_TRACKED_KEYS
        for key in sorted(_hits, key=lambda k: _hits[k][-1] if _hits[k] else 0.0)[:overflow]:
            del _hits[key]


def _trust_forwarded_for() -> bool:
    """Whether to derive the client IP from ``X-Forwarded-For``.

    Off by default: the header is client-controlled and trivially spoofable, so
    trusting it would let an attacker evade IP-based limiting by rotating it.
    Enable only when the app sits behind a reverse proxy that overwrites the
    header with the real client IP.
    """
    return os.environ.get('TRUST_PROXY_FORWARDED_FOR', '').strip().lower() in (
        '1', 'true', 'yes', 'on',
    )


def _client_ip_key(request: Request) -> str:
    if _trust_forwarded_for():
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            return f'ip:{forwarded.split(",")[0].strip()}'
    return f'ip:{request.client.host if request.client else "unknown"}'


def _client_key(request: Request) -> str:
    auth = request.headers.get('authorization')
    if auth:
        token = auth[7:].strip() if auth[:7].lower() == 'bearer ' else auth.strip()
        # Bucket only well-formed tokens, keyed by hash: random bearer values fall to the IP key so they can't mint buckets.
        for prefix in BEARER_PREFIXES:
            if token.startswith(prefix) and len(token) >= len(prefix) + 20:
                return f'token:{hashlib.sha256(token.encode()).hexdigest()}'
    return _client_ip_key(request)


async def rate_limit(request: Request) -> None:
    """Reject the request with 429 when its key exceeds the per-minute limit.

    The client IP is always enforced as a ceiling. This runs as a router-level
    dependency *before* authentication, so it fires for unauthenticated requests
    too — and the token prefix (``wizzrobe_pat_``) is public, so bucketing solely
    by a presented bearer value would let an attacker mint a fresh, always-empty
    bucket per request by rotating the suffix and never trip the limit (and grow
    ``_hits`` without bound). Keying by IP as well caps that flood at the source.
    A well-formed token additionally gets its own bucket so one client's traffic
    is throttled independently below the shared IP ceiling.
    """
    limit = limit_per_minute()
    ip_key = _client_ip_key(request)
    keys = [ip_key]
    client_key = _client_key(request)
    if client_key != ip_key:
        keys.append(client_key)

    if shared_counters_enabled():
        retry_after = await RateLimitService().over_limit(keys, {key: limit for key in keys})
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Rate limit exceeded',
                headers={'Retry-After': str(retry_after)},
            )
        return

    now = time.monotonic()
    _sweep(now)
    window_start = now - _WINDOW_SECONDS

    # Check every bucket before recording any, IP first, so a rejection never half-counts a request.
    for key in keys:
        bucket = _hits[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(bucket[0] + _WINDOW_SECONDS - now) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Rate limit exceeded',
                headers={'Retry-After': str(retry_after)},
            )

    for key in keys:
        _hits[key].append(now)


async def enforce_app_limit(app_id: int) -> None:
    """Cap one third-party app's calls across all of its users.

    Each grant already has its own token bucket; this is the ceiling over all
    of them, so one popular or runaway app can't take the single worker from
    everyone else. Runs after the token is resolved, since only then is the app
    known, and so counts only calls that got that far.
    """
    key = f'app:{app_id}'
    if shared_counters_enabled():
        retry = await RateLimitService().over_limit([key], {key: app_limit_per_minute()})
        if retry is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='This app is making too many requests. Try again shortly.',
                headers={'Retry-After': str(retry)},
            )
        return
    now = time.monotonic()
    _sweep(now)
    bucket = _hits[key]
    while bucket and bucket[0] < now - _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= app_limit_per_minute():
        retry_after = max(1, int(bucket[0] + _WINDOW_SECONDS - now) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='This app is making too many requests. Try again shortly.',
            headers={'Retry-After': str(retry_after)},
        )
    bucket.append(now)
