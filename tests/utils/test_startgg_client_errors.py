"""Sentry SGLMAN-15: a start.gg network failure surfaces as ``StartggAPIError``.

A raw ``TimeoutError`` slipped past ``sync_linked_events``' per-event handler,
so one slow answer aborted the community's whole sync and opened an error issue.
"""

import asyncio

import aiohttp
import pytest

from application.utils.clients import startgg_client
from application.utils.clients.startgg_client import StartggAPIError, StartggClient


class _RaisingSession:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        raise self.exc


@pytest.mark.asyncio
@pytest.mark.parametrize('exc, needle', [
    (asyncio.TimeoutError(), "didn't answer in time"),
    (aiohttp.ClientConnectionError('refused'), "Couldn't reach start.gg"),
])
async def test_network_failures_become_api_errors(monkeypatch, exc, needle):
    monkeypatch.setattr(startgg_client.aiohttp, 'ClientSession', lambda **kw: _RaisingSession(exc))
    with pytest.raises(StartggAPIError, match=needle):
        await StartggClient(token='t').get_event('tournament/x/event/y')
