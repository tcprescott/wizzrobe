"""A socket.io long-poll for a session engine.io just closed answers 400, not 500 (SGLMAN-1P)."""

import engineio
import pytest

from application.utils import socketio_closed_session


class _Closed:
    closed = True


def _post_scope(sid: str) -> dict:
    return {
        'type': 'http',
        'method': 'POST',
        'path': '/socket.io/',
        'query_string': f'EIO=4&transport=polling&sid={sid}'.encode(),
        'headers': [(b'content-type', b'text/plain'), (b'content-length', b'0')],
        'raw_path': b'/socket.io/',
        'root_path': '',
        'scheme': 'https',
        'server': ('test', 443),
        'client': ('1.2.3.4', 5),
    }


async def _receive() -> dict:
    return {'type': 'http.request', 'body': b'', 'more_body': False}


def _collector():
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    return sent, send


@pytest.fixture
def server():
    return engineio.AsyncServer(async_mode='asgi')


@pytest.mark.asyncio
async def test_unpatched_engineio_still_raises(server):
    """Guards the premise: drop the workaround once engine.io stops raising here."""
    server.sockets['abc'] = _Closed()
    _, send = _collector()
    with pytest.raises(KeyError, match='Session is disconnected'):
        await server.handle_request(_post_scope('abc'), _receive, send)


@pytest.mark.asyncio
async def test_closed_session_answers_400(server):
    socketio_closed_session.install(server)
    server.sockets['abc'] = _Closed()
    sent, send = _collector()

    await server.handle_request(_post_scope('abc'), _receive, send)

    assert sent[0]['type'] == 'http.response.start'
    assert sent[0]['status'] == 400
    assert 'abc' not in server.sockets


@pytest.mark.asyncio
async def test_unknown_session_keeps_engineio_answer(server):
    socketio_closed_session.install(server)
    sent, send = _collector()

    await server.handle_request(_post_scope('gone'), _receive, send)

    assert sent[0]['status'] == 400


@pytest.mark.asyncio
async def test_other_key_errors_still_raise(server):
    async def boom(*args, **kwargs):
        raise KeyError('something else')

    server.handle_request = boom
    socketio_closed_session.install(server)
    _, send = _collector()
    with pytest.raises(KeyError, match='something else'):
        await server.handle_request(_post_scope('abc'), _receive, send)


def test_install_is_idempotent(server):
    socketio_closed_session.install(server)
    first = server.handle_request
    socketio_closed_session.install(server)
    assert server.handle_request is first
