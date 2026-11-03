"""One execution envelope for every outbound seed-provider call.

Randomizer upstreams are third-party services with no availability promise, and a
seed roll is the most time-critical thing this app does — a race is scheduled,
the players are waiting, and the roll happens minutes before. Before this module
each generator in :mod:`application.services.seedgen_service` opened its own
client with its own error handling, no timeout, and no retry: a hung upstream
held the request open indefinitely, and a transient 502 was a hard failure where
one retry would have succeeded.

Every provider call now runs through :func:`call_provider`, which enforces:

* a **per-attempt timeout** (:data:`PROVIDER_TIMEOUT_SECONDS`),
* **bounded retries with backoff** for the failures a retry can fix — timeouts,
  connection errors, 429 and 5xx — and none for the failures it cannot: a 4xx,
  a malformed payload, a missing credential,
* a **normalized exception taxonomy** carrying the provider's own message, so the
  caller can tell "alttpr.com is down, try again" from "this preset will never
  roll",
* **measurement** (attempts, latency) returned alongside the value, so the seed's
  provenance record does not need a second clock.

Every exception here subclasses ``ValueError``, which is what makes the taxonomy
usable without touching a single caller's error handling: the UI's
``except ValueError`` → ``ui.notify`` convention and the REST 400 mapping already
do the right thing with them.

A utility rather than a service: it enforces no business rule and reaches no
repository, so ``seedgen_service`` can import it without a cycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, TypeVar

import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Bounds a hang, not a slow success: ALTTPR rolls take tens of seconds under load.
PROVIDER_TIMEOUT_SECONDS = 60.0
PROVIDER_MAX_ATTEMPTS = 3
PROVIDER_BACKOFF_SECONDS = (1.0, 2.0)


class SeedProviderError(ValueError):
    """A normalized seed-provider failure.

    Subclasses ``ValueError`` so the existing UI/REST error handling surfaces it
    unchanged. ``str(e)`` is written for the person who has to act on it and
    carries the provider's own words when the provider gave any.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        operation: str,
        attempts: int = 1,
        status_code: Optional[int] = None,
        provider_message: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation
        self.attempts = attempts
        self.status_code = status_code
        self.provider_message = provider_message


class SeedProviderTimeout(SeedProviderError):
    """The provider did not answer inside the per-attempt budget."""


class SeedProviderUnavailable(SeedProviderError):
    """The provider is reachable-but-broken (5xx) or unreachable (connection)."""


class SeedProviderRateLimited(SeedProviderError):
    """HTTP 429 — retryable, but the provider is asking us to slow down."""


class SeedProviderInvalidRequest(SeedProviderError):
    """The provider rejected what we sent (4xx). Retrying sends the same thing."""


class SeedProviderBadResponse(SeedProviderError):
    """The provider answered with something we could not parse or use."""


@dataclass
class ProviderCall:
    """A successful provider call, with what it cost.

    ``attempts`` and ``latency_ms`` are the provenance a ``GeneratedSeeds`` row
    records — measured here so no caller re-measures (and gets a different answer
    by including its own work in the number).
    """

    value: Any
    provider: str
    operation: str
    attempts: int = 1
    latency_ms: int = 0
    surface: Optional[str] = None

    def as_meta(self) -> dict:
        """The provider-metadata blob stored beside a rolled seed."""
        meta = {
            'provider': self.provider,
            'operation': self.operation,
            'attempts': self.attempts,
            'latency_ms': self.latency_ms,
        }
        if self.surface:
            meta['surface'] = self.surface
        return meta


@dataclass
class _Classified:
    """An attempt's failure, normalized and marked retryable or not."""

    error: SeedProviderError
    retryable: bool = field(default=False)


def _status_error(
    status: int, text: str, *, provider: str, operation: str, attempts: int,
) -> _Classified:
    """Map an HTTP status onto the taxonomy.

    The split is the whole point: 429 and 5xx are the provider's problem and may
    pass, so they retry; a 4xx is *our* payload and will be rejected identically
    every time, so retrying only delays an error the caller could already act on.
    """
    detail = (text or '').strip()[:200] or None
    if status == 429:
        return _Classified(
            SeedProviderRateLimited(
                f'{provider} is rate-limiting seed generation. Try again shortly.',
                provider=provider, operation=operation, attempts=attempts,
                status_code=status, provider_message=detail,
            ),
            retryable=True,
        )
    if 500 <= status < 600:
        return _Classified(
            SeedProviderUnavailable(
                f'{provider} returned an error (HTTP {status})'
                + (f': {detail}' if detail else '.'),
                provider=provider, operation=operation, attempts=attempts,
                status_code=status, provider_message=detail,
            ),
            retryable=True,
        )
    return _Classified(
        SeedProviderInvalidRequest(
            f'{provider} rejected the request (HTTP {status})'
            + (f': {detail}' if detail else '.'),
            provider=provider, operation=operation, attempts=attempts,
            status_code=status, provider_message=detail,
        ),
        retryable=False,
    )


def _classify(
    exc: BaseException,
    *,
    provider: str,
    operation: str,
    attempts: int,
    attempt_timeout: float,
) -> Optional[_Classified]:
    """Normalize an attempt's failure — ``None`` when it is not ours to classify."""
    if isinstance(exc, SeedProviderError):
        retryable = isinstance(
            exc, (SeedProviderTimeout, SeedProviderUnavailable, SeedProviderRateLimited)
        )
        exc.attempts = attempts
        return _Classified(exc, retryable=retryable)

    if isinstance(exc, asyncio.TimeoutError):
        return _Classified(
            SeedProviderTimeout(
                # Use the budget this call ran under, not the module default (DK64R's is minutes).
                f'{provider} did not respond within '
                f'{int(attempt_timeout)} seconds.',
                provider=provider, operation=operation, attempts=attempts,
            ),
            retryable=True,
        )

    if isinstance(exc, aiohttp.ClientResponseError):
        return _status_error(
            exc.status, str(exc.message or ''),
            provider=provider, operation=operation, attempts=attempts,
        )

    if isinstance(exc, aiohttp.ClientConnectionError):
        return _Classified(
            SeedProviderUnavailable(
                f'Could not reach {provider}.',
                provider=provider, operation=operation, attempts=attempts,
                provider_message=str(exc)[:200] or None,
            ),
            retryable=True,
        )

    if isinstance(exc, ValueError):
        # Deliberately terminal: the payload is wrong, not the moment.
        return _Classified(
            SeedProviderBadResponse(
                str(exc) or f'{provider} returned an unexpected response.',
                provider=provider, operation=operation, attempts=attempts,
                provider_message=str(exc)[:200] or None,
            ),
            retryable=False,
        )

    # Unknown failures propagate unchanged; don't invent a user-facing sentence for them.
    return None


async def call_provider(
    fn: Callable[[], Awaitable[T]],
    *,
    provider: str,
    operation: str,
    surface: Optional[str] = None,
    attempt_timeout: float = PROVIDER_TIMEOUT_SECONDS,
    max_attempts: int = PROVIDER_MAX_ATTEMPTS,
) -> ProviderCall:
    """Run ``fn`` under the provider contract; return it with its cost.

    ``fn`` takes no arguments — close over what it needs — and is re-invoked from
    scratch on each attempt, so it must be safe to repeat. **Pass
    ``max_attempts=1`` for anything that is not**: a retried "submit a generation
    task" enqueues two generations, and the second one is invisible.

    Raises the normalized :class:`SeedProviderError` subclass for the last
    attempt's failure; the retryable ones have already been retried.
    """
    started = time.monotonic()
    attempts = 0
    last: Optional[_Classified] = None

    while attempts < max_attempts:
        attempts += 1
        try:
            value = await asyncio.wait_for(fn(), timeout=attempt_timeout)
        except asyncio.CancelledError:
            raise
        # Broad by design: anything unrecognised is re-raised untouched.
        except BaseException as exc:
            classified = _classify(
                exc, provider=provider, operation=operation, attempts=attempts,
                attempt_timeout=attempt_timeout,
            )
            if classified is None:
                logger.warning(
                    'seed provider %s/%s attempt %d raised an unclassified %s',
                    provider, operation, attempts, type(exc).__name__,
                )
                raise
            last = classified
            logger.warning(
                'seed provider %s/%s attempt %d/%d failed: %s',
                provider, operation, attempts, max_attempts, last.error,
            )
            if not last.retryable or attempts >= max_attempts:
                break
            backoff = PROVIDER_BACKOFF_SECONDS
            await asyncio.sleep(backoff[min(attempts - 1, len(backoff) - 1)])
            continue

        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            'seed provider %s/%s succeeded in %dms (attempt %d)',
            provider, operation, latency_ms, attempts,
        )
        return ProviderCall(
            value=value, provider=provider, operation=operation,
            attempts=attempts, latency_ms=latency_ms, surface=surface,
        )

    assert last is not None
    last.error.attempts = attempts
    logger.error(
        'seed provider %s/%s failed after %d attempt(s): %s',
        provider, operation, attempts, last.error,
    )
    raise last.error


async def raise_for_status(resp: aiohttp.ClientResponse, *, provider: str, operation: str) -> None:
    """Normalize a non-2xx response, reading the body for the provider's message.

    ``aiohttp``'s own ``raise_for_status`` discards the body, which is where every
    one of these providers puts the reason. Call this instead inside a provider
    function so the envelope classifies a real message rather than a bare status.
    """
    if resp.status < 400:
        return
    try:
        body = await resp.text()
    # Failing to read the body must not replace a clear status with a decoding error.
    except Exception:
        body = ''
    raise _status_error(
        resp.status, body, provider=provider, operation=operation, attempts=1,
    ).error
