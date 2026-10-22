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


class _Resp:
    def __init__(self, payload):
        self.status = 200
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self.payload


class _ScriptedSession:
    def __init__(self, payloads):
        self.payloads = payloads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        return _Resp(self.payloads.pop(0))


_INTERNAL = {'errors': [{'message': 'An unknown error has occurred', 'extensions': {'category': 'internal'}}]}


def _scripted(monkeypatch, payloads):
    monkeypatch.setattr(startgg_client.aiohttp, 'ClientSession', lambda **kw: _ScriptedSession(payloads))
    monkeypatch.setattr(startgg_client.asyncio, 'sleep', _no_sleep)
    return payloads


async def _no_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_internal_error_is_retried_once(monkeypatch):
    """Sentry SGLMAN-2H: start.gg's own "unknown error" clears on the next request."""
    payloads = _scripted(monkeypatch, [_INTERNAL, {'data': {'phaseGroup': {'id': 1}}}])
    data = await StartggClient(token='t')._query('query', {})
    assert data == {'phaseGroup': {'id': 1}}
    assert payloads == []


@pytest.mark.asyncio
async def test_internal_error_twice_still_raises(monkeypatch):
    _scripted(monkeypatch, [_INTERNAL, _INTERNAL])
    with pytest.raises(StartggAPIError, match='unknown error'):
        await StartggClient(token='t')._query('query', {})


@pytest.mark.asyncio
async def test_other_graphql_errors_are_not_retried(monkeypatch):
    payloads = _scripted(monkeypatch, [{'errors': [{'message': 'Bad slug'}]}, {'data': {}}])
    with pytest.raises(StartggAPIError, match='Bad slug'):
        await StartggClient(token='t')._query('query', {})
    assert len(payloads) == 1
