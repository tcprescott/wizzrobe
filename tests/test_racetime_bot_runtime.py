"""Runtime tests for the racetime bot connection loop (PR 4).

Drives the mock transport through every health path — connect, auth failure,
transient failure + reconnect, liveness heartbeat — and exercises the scripted
room lifecycle, tenant routing, re-adoption, and handler crash containment. No
network: the :class:`MockRacetimeTransport` is a scripted event-emitting fake.
"""

import asyncio

from application.services import RacetimeBotService, RacetimeRoomService
from application.tenant_context import tenant_scope
from models import BotStatus, RaceRoomStatus, RacetimeBot, RacetimeRoom, Tenant, User
from racetimebot import connection as connection_module
from racetimebot.connection import CategoryConnection
from racetimebot.handler import RaceHandler, RoomStatusLifecycle
from racetimebot.manager import RacetimeBotManager
from racetimebot.mock import MockRacetimeTransport
from racetimebot.transport import RaceRoomEvent


async def _system_user() -> User:
    return await User.create(username='system', discord_id=999000111)


async def _bot(category: str = 'alttpr') -> RacetimeBot:
    return await RacetimeBot.create(
        category=category, client_id='cid', client_secret='sec', name=category.upper(),
    )


def _connection(bot, sysuser, transport, *, handler=None):
    return CategoryConnection(
        bot_id=bot.id,
        category=bot.category,
        client_id=bot.client_id,
        client_secret=bot.client_secret,
        handler=handler or _StubHandler(),
        bot_service=RacetimeBotService(),
        system_user=sysuser,
        transport_factory=lambda **_: transport,
    )


class _StubHandler:
    async def on_event(self, event) -> None:  # pragma: no cover - unused in health tests
        pass


async def test_connect_marks_connected_and_heartbeats(db):
    sysuser = await _system_user()
    bot = await _bot()
    transport = MockRacetimeTransport(category='alttpr', heartbeats=2)
    conn = _connection(bot, sysuser, transport)

    outcome, connected = await conn._attempt()

    assert (outcome, connected) == ('stopped', True)
    bot = await RacetimeBot.get(id=bot.id)
    assert bot.status == BotStatus.CONNECTED
    assert bot.last_connected_at is not None
    assert bot.last_checked_at is not None
    assert transport.closed is True


async def test_auth_failure_sets_error_and_stops(db):
    sysuser = await _system_user()
    bot = await _bot()
    transport = MockRacetimeTransport(category='alttpr', fail_auth=True)
    conn = _connection(bot, sysuser, transport)

    await asyncio.wait_for(conn.run_forever(), timeout=1)

    bot = await RacetimeBot.get(id=bot.id)
    assert bot.status == BotStatus.ERROR
    assert bot.status_message and 'auth' in bot.status_message.lower()


async def test_transient_then_reconnect(db, monkeypatch):
    monkeypatch.setattr(connection_module, 'INITIAL_BACKOFF_SECONDS', 0)
    sysuser = await _system_user()
    bot = await _bot()
    attempts = {'n': 0}

    def factory(**_):
        n = attempts['n']
        attempts['n'] += 1
        if n == 0:
            return MockRacetimeTransport(category='alttpr', fail_transient=True)
        return MockRacetimeTransport(category='alttpr', heartbeats=1)

    conn = CategoryConnection(
        bot_id=bot.id, category='alttpr', client_id='c', client_secret='s',
        handler=_StubHandler(), bot_service=RacetimeBotService(),
        system_user=sysuser, transport_factory=factory,
    )

    await asyncio.wait_for(conn.run_forever(), timeout=2)

    assert attempts['n'] == 2
    bot = await RacetimeBot.get(id=bot.id)
    assert bot.status == BotStatus.DISCONNECTED


async def test_transient_attempt_reports_error(db):
    sysuser = await _system_user()
    bot = await _bot()
    transport = MockRacetimeTransport(category='alttpr', fail_transient=True)
    conn = _connection(bot, sysuser, transport)

    outcome, connected = await conn._attempt()

    assert (outcome, connected) == ('transient', False)
    bot = await RacetimeBot.get(id=bot.id)
    assert bot.status == BotStatus.ERROR


def _lifecycle_handler():
    room_service = RacetimeRoomService()
    return RaceHandler(
        category='alttpr', room_service=room_service,
        lifecycle=RoomStatusLifecycle(room_service),
    )


async def test_scripted_room_lifecycle_updates_status(db):
    sysuser = await _system_user()
    bot = await _bot()
    room = await RacetimeRoom.create(slug='alttpr/live', category='alttpr')
    assert room.status == RaceRoomStatus.OPEN

    script = [
        RaceRoomEvent(slug='alttpr/live', category='alttpr', status=RaceRoomStatus.IN_PROGRESS),
        RaceRoomEvent(slug='alttpr/live', category='alttpr', status=RaceRoomStatus.FINISHED),
    ]
    transport = MockRacetimeTransport(category='alttpr', script=script)
    conn = _connection(bot, sysuser, transport, handler=_lifecycle_handler())

    await conn._attempt()

    room = await RacetimeRoom.get(id=room.id)
    assert room.status == RaceRoomStatus.FINISHED
    assert room.opened_at is not None


async def test_event_routes_to_correct_tenant(db):
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='B', slug='b')
    with tenant_scope(a.id):
        room_a = await RacetimeRoom.create(slug='alttpr/a', category='alttpr')
    with tenant_scope(b.id):
        room_b = await RacetimeRoom.create(slug='alttpr/b', category='alttpr')

    handler = _lifecycle_handler()
    await handler.on_event(
        RaceRoomEvent(slug='alttpr/b', category='alttpr', status=RaceRoomStatus.FINISHED)
    )

    assert (await RacetimeRoom.get(id=room_b.id)).status == RaceRoomStatus.FINISHED
    assert (await RacetimeRoom.get(id=room_a.id)).status == RaceRoomStatus.OPEN


async def test_unknown_slug_is_ignored(db):
    handler = _lifecycle_handler()
    await handler.on_event(
        RaceRoomEvent(slug='alttpr/nope', category='alttpr', status=RaceRoomStatus.FINISHED)
    )


async def test_handler_crash_is_contained(db):
    room = await RacetimeRoom.create(slug='alttpr/x', category='alttpr')

    class _Boom:
        async def handle_event(self, room, event):
            raise RuntimeError('boom')

    handler = RaceHandler(category='alttpr', room_service=RacetimeRoomService(), lifecycle=_Boom())
    await handler.on_event(
        RaceRoomEvent(slug='alttpr/x', category='alttpr', status=RaceRoomStatus.FINISHED)
    )
    assert (await RacetimeRoom.get(id=room.id)).status == RaceRoomStatus.OPEN


async def test_readopt_open_rooms_groups_by_category(db):
    a = await Tenant.get(id=1)
    b = await Tenant.create(name='B', slug='b')
    with tenant_scope(a.id):
        await RacetimeRoom.create(slug='alttpr/a', category='alttpr')
        await RacetimeRoom.create(slug='alttpr/done', category='alttpr', status=RaceRoomStatus.FINISHED)
    with tenant_scope(b.id):
        await RacetimeRoom.create(slug='smz3/b', category='smz3', status=RaceRoomStatus.IN_PROGRESS)

    grouping = await RacetimeBotManager().readopt_open_rooms()

    assert grouping['alttpr'] == ['alttpr/a']
    assert grouping['smz3'] == ['smz3/b']


async def test_manager_start_is_noop_when_disabled(db, monkeypatch):
    monkeypatch.delenv('RACETIME_BOT_ENABLED', raising=False)
    mgr = RacetimeBotManager()
    await mgr.start()
    assert mgr._connections == {}
