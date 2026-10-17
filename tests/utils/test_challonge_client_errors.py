"""A hung or unreachable Challonge surfaces as ``ChallongeAPIError``.

Callers only catch ``ChallongeAPIError``; before this the client had no timeout
(aiohttp's 300s default) and let raw network errors escape.
"""

import asyncio

import aiohttp
import pytest

from application.utils.clients import challonge_client
from application.utils.clients.challonge_client import ChallongeAPIError, ChallongeClient


class _RaisingSession:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, *args, **kwargs):
        raise self.exc

    request = post = get


@pytest.mark.asyncio
@pytest.mark.parametrize('exc, needle', [
    (asyncio.TimeoutError(), 'did not respond in time'),
    (aiohttp.ClientConnectionError('refused'), 'Could not reach Challonge'),
])
async def test_network_failures_become_api_errors(monkeypatch, exc, needle):
    seen = {}

    def factory(**kw):
        seen.update(kw)
        return _RaisingSession(exc)

    monkeypatch.setattr(challonge_client.aiohttp, 'ClientSession', factory)
    with pytest.raises(ChallongeAPIError, match=needle):
        await ChallongeClient('id', 'secret').get_me('token')
    assert seen['timeout'].total == 20


@pytest.mark.asyncio
async def test_api_errors_pass_through_unchanged(monkeypatch):
    monkeypatch.setattr(
        challonge_client.aiohttp, 'ClientSession',
        lambda **kw: _RaisingSession(ChallongeAPIError('boom', status=422)),
    )
    with pytest.raises(ChallongeAPIError, match='boom') as info:
        await ChallongeClient('id', 'secret').get_me('token')
    assert info.value.status == 422
