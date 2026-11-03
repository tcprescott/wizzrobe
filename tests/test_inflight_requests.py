"""The shutdown-diagnostic middleware names a request the shutdown cancelled."""

import asyncio

import pytest

from middleware.inflight_requests import InFlightRequestLogMiddleware


async def _noop_receive():
    return {'type': 'http.request'}


async def _noop_send(message):
    return None


async def test_logs_a_request_cancelled_while_open(caplog):
    async def hanging(scope, receive, send):
        await asyncio.sleep(3600)

    middleware = InFlightRequestLogMiddleware(hanging)
    scope = {'type': 'http', 'method': 'GET', 'path': '/stuck'}

    # wait_for cancels on timeout, as uvicorn's shutdown deadline does.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(middleware(scope, _noop_receive, _noop_send), timeout=0.05)
    assert 'Request cancelled while still open: GET /stuck' in caplog.text


async def test_completed_request_logs_nothing(caplog):
    async def finishes(scope, receive, send):
        return None

    middleware = InFlightRequestLogMiddleware(finishes)
    await middleware({'type': 'http', 'method': 'GET', 'path': '/ok'}, _noop_receive, _noop_send)
    assert 'cancelled' not in caplog.text


async def test_lifespan_scope_passes_straight_through():
    seen = []

    async def records(scope, receive, send):
        seen.append(scope['type'])

    await InFlightRequestLogMiddleware(records)({'type': 'lifespan'}, _noop_receive, _noop_send)
    assert seen == ['lifespan']
