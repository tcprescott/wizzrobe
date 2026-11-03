"""The live relay's routing, without a database: what it forwards and what it replays."""

import json

import pytest

from application.events import check_in_live, event_status_live, live_relay, match_live


@pytest.fixture
def relay_on(monkeypatch):
    sent = []
    monkeypatch.setattr(live_relay, '_dsn', 'postgres://example/db')
    monkeypatch.setattr(live_relay._q, 'enqueue', lambda coro: (sent.append(coro), coro.close()))
    return sent


def _remote(kind, *args):
    return json.dumps({'o': 'another-instance', 'k': kind, 'a': list(args)})


def test_publish_without_the_relay_stays_local():
    seen = []
    token = match_live.subscribe(lambda m, c: seen.append((m, c)))
    try:
        match_live.publish(5, match_live.CHANGED)
    finally:
        match_live.unsubscribe(token)
    assert seen == [(5, 'changed')]
    assert not live_relay.active()


def test_publish_delivers_locally_and_forwards_once(relay_on):
    seen = []
    token = match_live.subscribe(lambda m, c: seen.append((m, c)))
    try:
        match_live.publish(7, match_live.DELETED)
    finally:
        match_live.unsubscribe(token)
    assert seen == [(7, 'deleted')]
    assert len(relay_on) == 1


def test_a_signal_from_another_instance_reaches_local_subscribers():
    got = {'match': [], 'check_in': [], 'event_status': []}
    tokens = [
        (match_live, match_live.subscribe(lambda m, c: got['match'].append((m, c)))),
        (check_in_live, check_in_live.subscribe(lambda e, n, c: got['check_in'].append((e, n, c)))),
        (event_status_live, event_status_live.subscribe(lambda e: got['event_status'].append(e))),
    ]
    try:
        live_relay.receive(_remote('match', 3, 'created'))
        live_relay.receive(_remote('check_in', 9, None, 'roster'))
        live_relay.receive(_remote('event_status', 4))
    finally:
        for module, token in tokens:
            module.unsubscribe(token)
    assert got == {'match': [(3, 'created')], 'check_in': [(9, None, 'roster')], 'event_status': [4]}


def test_own_echo_and_garbage_are_ignored():
    seen = []
    token = match_live.subscribe(lambda m, c: seen.append(m))
    try:
        live_relay.receive(json.dumps({'o': live_relay.ORIGIN, 'k': 'match', 'a': [1, 'changed']}))
        live_relay.receive('not json')
        live_relay.receive(_remote('no-such-kind', 1))
    finally:
        match_live.unsubscribe(token)
    assert seen == []


async def test_start_is_off_without_multi_instance(monkeypatch):
    monkeypatch.delenv('MULTI_INSTANCE', raising=False)
    assert await live_relay.start('postgres://example/db') is False
    assert not live_relay.active()
