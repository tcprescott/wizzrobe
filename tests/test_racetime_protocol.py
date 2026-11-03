"""racetime's bot websocket as data, and the transport's room bookkeeping.

No network: the parsing is pure, and the transport's message routing and room
supervision are driven with fake callbacks.
"""

import asyncio
from datetime import datetime, timezone

from models import RaceRoomStatus
from racetimebot import protocol
from racetimebot.transport import EntrantStatus, RealRacetimeTransport, RoomState


def _race(status='in_progress', entrants=None, **extra):
    return {
        'name': 'alttpr/quick-link-1234',
        'status': {'value': status},
        'entrants': entrants or [],
        **extra,
    }


def _entrant(uid, name, status='done', finish_time='P0DT01H42M10.231407S', place=1):
    return {'user': {'id': uid, 'name': name}, 'status': {'value': status},
            'finish_time': finish_time, 'place': place}


def test_duration_ms_reads_racetime_durations():
    assert protocol.duration_ms('P0DT01H42M10.231407S') == 6130231
    assert protocol.duration_ms('PT45.5S') == 45500
    assert protocol.duration_ms('P1DT00H00M00S') == 86_400_000
    assert protocol.duration_ms(None) is None
    assert protocol.duration_ms('garbage') is None


def test_room_status_maps_pre_race_states_to_open():
    for value in ('open', 'invitational', 'pending'):
        assert protocol.room_status(_race(value)) == RaceRoomStatus.OPEN
    assert protocol.room_status(_race('finished')) == RaceRoomStatus.FINISHED
    assert protocol.room_status(_race('cancelled')) == RaceRoomStatus.CANCELLED
    assert protocol.room_status(_race('something-new')) == RaceRoomStatus.OPEN


def test_race_event_carries_entrants_monitors_and_start():
    race = _race(
        'finished',
        [_entrant('u1', 'Alice'), _entrant('u2', 'Bob', status='dnf', finish_time=None, place=None)],
        monitors=[{'id': 'm1', 'name': 'Mod'}],
        started_at='2026-10-26T18:00:00Z',
    )
    event = protocol.race_event(race, slug='alttpr/x', category='alttpr')
    assert event.status == RaceRoomStatus.FINISHED
    assert event.kind == 'race'
    alice, bob = event.entrants
    assert (alice.user_id, alice.finish_time, alice.finish_ms, alice.place) == ('u1', 6130, 6130231, 1)
    assert bob.status == EntrantStatus.DID_NOT_FINISH and bob.finish_ms is None
    assert event.monitor_ids == ['m1']
    assert event.started_at == datetime(2026, 10, 26, 18, tzinfo=timezone.utc)
    assert protocol.entrant_ids(event) == ['u1', 'u2']


def test_chat_ignores_blank_lines_and_flags_bots():
    assert protocol.chat({'message': '   '}) is None
    line = protocol.chat({'message': '!fpa lag', 'user': {'id': 'u1', 'name': 'Alice'}})
    assert (line.user_id, line.text, line.is_bot) == ('u1', '!fpa lag', False)
    bot = protocol.chat({'message': 'hello', 'bot': 'Wizzrobe', 'is_bot': True})
    assert bot.is_bot and bot.display_name == 'Wizzrobe'


def test_room_state_reports_each_status_once():
    state = RoomState(RaceRoomStatus.OPEN)
    event = protocol.race_event(_race('open'), slug='s', category='c')
    assert not state.advance(event)
    started = protocol.race_event(_race('in_progress'), slug='s', category='c')
    assert state.advance(started)
    assert not state.advance(started)


def _transport(room_source=None):
    return RealRacetimeTransport(client_id='c', client_secret='s', category='alttpr', room_source=room_source)


async def test_on_message_emits_joined_status_changes_and_chat():
    transport = _transport()
    seen = []

    async def on_event(event):
        seen.append((event.kind, event.status, event.chat.text if event.chat else None))

    state = RoomState(RaceRoomStatus.OPEN)
    assert await transport.on_race(state, _race('open'), 'alttpr/x', on_event, joined=True)
    assert await transport.on_message(state, {'type': 'race.data', 'race': _race('open')}, 'alttpr/x', on_event)
    assert await transport.on_message(
        state, {'type': 'chat.message', 'message': {'message': '!seed', 'user': {'id': 'u1'}}}, 'alttpr/x', on_event,
    )
    assert await transport.on_message(
        state, {'type': 'chat.message', 'message': {'message': 'Seed: x', 'is_bot': True, 'bot': 'W'}},
        'alttpr/x', on_event,
    )
    assert await transport.on_message(state, {'type': 'race.data', 'race': _race('in_progress')}, 'alttpr/x', on_event)
    done = await transport.on_message(state, {'type': 'race.data', 'race': _race('finished')}, 'alttpr/x', on_event)

    assert not done, 'a finished race lets the bot leave the room'
    assert seen == [
        ('joined', RaceRoomStatus.OPEN, None),
        ('chat', RaceRoomStatus.OPEN, '!seed'),
        ('race', RaceRoomStatus.IN_PROGRESS, None),
        ('race', RaceRoomStatus.FINISHED, None),
    ]


async def test_on_race_reports_a_finish_missed_while_away():
    """Rejoined after a restart, a room already finished upstream still reports it once."""
    transport = _transport()
    seen = []

    async def on_event(event):
        seen.append(event.kind)

    state = RoomState(RaceRoomStatus.IN_PROGRESS)
    stay = await transport.on_race(state, _race('finished'), 'alttpr/x', on_event, joined=True)
    assert not stay
    assert seen == ['joined', 'race']


async def test_sync_rooms_joins_wanted_rooms_and_leaves_the_rest(monkeypatch):
    wanted = [('alttpr/a', RaceRoomStatus.OPEN), ('alttpr/b', RaceRoomStatus.OPEN)]

    async def room_source():
        return list(wanted)

    transport = _transport(room_source)
    sat = []
    release = asyncio.Event()

    async def fake_sit(slug, recorded, on_event):
        sat.append(slug)
        await release.wait()

    monkeypatch.setattr(transport, '_sit_in_room', fake_sit)

    async def on_event(_event):
        pass

    await transport.sync_rooms(on_event)
    await asyncio.sleep(0)
    assert sorted(sat) == ['alttpr/a', 'alttpr/b']

    await transport.sync_rooms(on_event)
    await asyncio.sleep(0)
    assert len(sat) == 2, 'a room already joined is not joined twice'

    wanted.pop()
    await transport.sync_rooms(on_event)
    await asyncio.sleep(0)
    assert set(transport._room_tasks) == {'alttpr/a'}

    release.set()
    await transport.close()
    assert transport._room_tasks == {}


async def test_send_without_a_socket_is_refused():
    assert not await _transport().send('alttpr/x', {'action': 'message'})
