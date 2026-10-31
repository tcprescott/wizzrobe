"""The seed-provider execution envelope.

What these lock down is the distinction the old code could not make: which
failures are worth retrying (the upstream's problem — it may pass) and which are
not (our payload's problem — it will fail identically), and that the caller is
told *which* happened rather than "seed generation failed".
"""

import asyncio

import aiohttp
import pytest

from application.utils import seed_provider
from application.utils.seed_provider import (
    ProviderCall,
    SeedProviderBadResponse,
    SeedProviderInvalidRequest,
    SeedProviderRateLimited,
    SeedProviderTimeout,
    SeedProviderUnavailable,
    call_provider,
)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Collapse the retry backoff so the suite doesn't sleep for real.

    Zeroes the *constants* rather than patching ``asyncio.sleep`` — that patch is
    module-global, so it would also neutralise the deliberate hang the timeout
    test needs.
    """
    monkeypatch.setattr(seed_provider, 'PROVIDER_BACKOFF_SECONDS', (0.0, 0.0))


def _response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=None, history=(), status=status, message=f'boom {status}',
    )


class TestSuccess:
    async def test_returns_value_with_measurement(self):
        async def ok():
            return 'https://seed'

        call = await call_provider(ok, provider='alttpr', operation='generate_seed')

        assert isinstance(call, ProviderCall)
        assert call.value == 'https://seed'
        assert call.attempts == 1
        assert call.latency_ms >= 0

    async def test_surface_reaches_the_metadata(self):
        async def ok():
            return 'x'

        call = await call_provider(
            ok, provider='ootr', operation='generate_seed', surface='match',
        )

        assert call.as_meta() == {
            'provider': 'ootr', 'operation': 'generate_seed',
            'attempts': 1, 'latency_ms': call.latency_ms, 'surface': 'match',
        }


class TestRetryable:
    async def test_retries_a_5xx_then_succeeds(self):
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise _response_error(502)
            return 'https://seed'

        call = await call_provider(flaky, provider='alttpr', operation='generate_seed')

        assert call.value == 'https://seed'
        assert call.attempts == 3

    async def test_retries_a_429(self):
        calls = []

        async def rate_limited():
            calls.append(1)
            raise _response_error(429)

        with pytest.raises(SeedProviderRateLimited) as exc:
            await call_provider(rate_limited, provider='alttpr', operation='generate_seed')

        assert len(calls) == 3
        assert exc.value.attempts == 3
        assert exc.value.status_code == 429

    async def test_retries_a_connection_error(self):
        calls = []

        async def unreachable():
            calls.append(1)
            raise aiohttp.ClientConnectionError('no route to host')

        with pytest.raises(SeedProviderUnavailable):
            await call_provider(unreachable, provider='smmap', operation='generate_seed')

        assert len(calls) == 3

    async def test_timeout_is_retried_and_normalized(self):
        calls = []

        async def hangs():
            calls.append(1)
            await asyncio.sleep(3600)

        with pytest.raises(SeedProviderTimeout) as exc:
            await call_provider(
                hangs, provider='alttpr', operation='generate_seed', attempt_timeout=0.01,
            )

        assert len(calls) == 3
        assert exc.value.provider == 'alttpr'
        assert exc.value.operation == 'generate_seed'


class TestNonRetryable:
    async def test_a_4xx_is_not_retried(self):
        calls = []

        async def rejected():
            calls.append(1)
            raise _response_error(400)

        with pytest.raises(SeedProviderInvalidRequest):
            await call_provider(rejected, provider='ootr', operation='generate_seed')

        assert len(calls) == 1

    async def test_a_value_error_is_not_retried(self):
        calls = []

        async def malformed():
            calls.append(1)
            raise ValueError('DK64 Randomizer returned no seed identifier.')

        with pytest.raises(SeedProviderBadResponse) as exc:
            await call_provider(malformed, provider='dk64r', operation='generate_seed')

        assert len(calls) == 1
        assert 'no seed identifier' in str(exc.value)

    async def test_max_attempts_one_never_retries_a_retryable_failure(self):
        """The double-submit guard: a non-idempotent call opts out of retry."""
        calls = []

        async def submit():
            calls.append(1)
            raise _response_error(503)

        with pytest.raises(SeedProviderUnavailable):
            await call_provider(
                submit, provider='dk64r', operation='submit_task', max_attempts=1,
            )

        assert len(calls) == 1


class TestErrorContract:
    async def test_every_error_is_a_value_error(self):
        """Which is what lets the existing UI/REST handling surface it unchanged."""
        async def down():
            raise _response_error(500)

        with pytest.raises(ValueError):
            await call_provider(down, provider='alttpr', operation='generate_seed')

    async def test_cancellation_is_not_swallowed(self):
        async def cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await call_provider(cancelled, provider='alttpr', operation='generate_seed')
