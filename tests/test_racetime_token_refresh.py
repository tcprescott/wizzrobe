"""The live racetime transport replaces its access token before it expires.

A racetime access token lives hours; these connections are meant to live weeks.
Authenticating once at connect and never again leaves the bot holding a dead
credential long before the next race needs it — and, because the liveness loop
keeps ticking, with nothing to say so.
"""

import asyncio
import time

import pytest

from racetimebot.transport import RacetimeAuthError, RealRacetimeTransport

pytestmark = pytest.mark.anyio


def _transport() -> RealRacetimeTransport:
    return RealRacetimeTransport(
        client_id='id', client_secret='secret', category='alttpr',
    )


class TestExpiryBookkeeping:
    def test_a_fresh_transport_has_no_deadline(self):
        assert _transport()._token_is_stale() is False

    def test_close_clears_the_deadline(self):
        t = _transport()
        t._token_expires_at = time.monotonic() - 1
        assert t._token_is_stale() is True

    async def test_close_resets_state(self):
        t = _transport()
        t._access_token = 'tok'
        t._token_expires_at = time.monotonic() + 100
        await t.close()
        assert t._access_token is None
        assert t._token_expires_at is None


class TestRefreshLoop:
    async def _run_ticks(self, transport, monkeypatch, ticks: int):
        """Drive ``run`` for a bounded number of heartbeats, then stop it."""
        beats = {'n': 0}

        async def on_heartbeat():
            beats['n'] += 1
            if beats['n'] >= ticks:
                transport._stop = True

        async def instant(_seconds):
            return None

        monkeypatch.setattr(asyncio, 'sleep', instant)

        async def on_event(_event):
            return None

        await transport.run(on_event, on_heartbeat)
        return beats['n']

    async def test_reauthenticates_once_the_token_is_stale(self, monkeypatch):
        transport = _transport()
        calls = {'n': 0}

        async def fake_auth():
            calls['n'] += 1
            transport._token_expires_at = time.monotonic() + 3600

        monkeypatch.setattr(transport, 'authenticate', fake_auth)
        transport._token_expires_at = time.monotonic() - 1  # already stale

        await self._run_ticks(transport, monkeypatch, ticks=3)

        assert calls['n'] == 1

    async def test_does_not_reauthenticate_while_the_token_is_fresh(self, monkeypatch):
        transport = _transport()
        calls = {'n': 0}

        async def fake_auth():
            calls['n'] += 1

        monkeypatch.setattr(transport, 'authenticate', fake_auth)
        transport._token_expires_at = time.monotonic() + 3600

        await self._run_ticks(transport, monkeypatch, ticks=5)

        assert calls['n'] == 0

    async def test_rejected_credentials_propagate(self, monkeypatch):
        """A refresh that is *rejected* is a real credential problem.

        It must reach the connection loop, which stops retrying rather than
        hammering a revoked secret into a rate limit.
        """
        transport = _transport()

        async def fake_auth():
            raise RacetimeAuthError('revoked')

        monkeypatch.setattr(transport, 'authenticate', fake_auth)
        transport._token_expires_at = time.monotonic() - 1

        with pytest.raises(RacetimeAuthError):
            await self._run_ticks(transport, monkeypatch, ticks=3)
