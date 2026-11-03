"""The racetime runtime reached across processes (API beside a ``bots`` container)."""

import asyncio
import json

from application.events import live_relay
from application.services import RacetimeBotService
from application.services.audit_service import AuditActions
from application.utils.clients import racetime_room_actions
from models import AuditLog, RacetimeBot, Role, UserRole
from racetimebot import manager as manager_module
from tests.factories import make_user


def _relay_on(monkeypatch):
    forwarded = []
    monkeypatch.setattr(live_relay, 'active', lambda: True)
    monkeypatch.setattr(live_relay, 'forward', lambda kind, *args: forwarded.append((kind, args)))
    return forwarded


def _from_elsewhere(kind, *args) -> str:
    return json.dumps({'o': 'another-process', 'k': kind, 'a': list(args)})


async def test_send_without_runtime_or_relay_is_dropped(monkeypatch):
    racetime_room_actions.register_sender(None)
    monkeypatch.setattr(live_relay, 'active', lambda: False)
    assert await racetime_room_actions.say('alttpr/room-1', 'hi') is False


async def test_send_without_runtime_goes_over_the_relay(monkeypatch):
    racetime_room_actions.register_sender(None)
    forwarded = _relay_on(monkeypatch)

    assert await racetime_room_actions.say('alttpr/room-1', 'Seed is up') is True
    assert len(forwarded) == 1
    kind, (slug, action) = forwarded[0]
    assert kind == racetime_room_actions.RELAY_KIND
    assert slug == 'alttpr/room-1'
    assert action['data']['message'] == 'Seed is up'


async def test_send_with_runtime_stays_local(monkeypatch):
    forwarded = _relay_on(monkeypatch)
    said = []

    async def sender(slug, action):
        said.append(slug)
        return True

    racetime_room_actions.register_sender(sender)
    try:
        assert await racetime_room_actions.say('alttpr/room-1', 'hi') is True
    finally:
        racetime_room_actions.register_sender(None)
    assert said == ['alttpr/room-1']
    assert forwarded == []


async def test_relayed_action_is_sent_by_the_runtime_process():
    said = []

    async def sender(slug, action):
        said.append((slug, action['data']['message']))
        return True

    racetime_room_actions.register_sender(sender)
    try:
        action = racetime_room_actions.message_action('From the API')
        live_relay.receive(_from_elsewhere(racetime_room_actions.RELAY_KIND, 'alttpr/room-2', action))
        for _ in range(50):
            if said:
                break
            await asyncio.sleep(0.01)
    finally:
        racetime_room_actions.register_sender(None)
    assert said == [('alttpr/room-2', 'From the API')]


async def test_relayed_action_is_ignored_without_runtime():
    racetime_room_actions.register_sender(None)
    action = racetime_room_actions.message_action('nobody home')
    live_relay.receive(_from_elsewhere(racetime_room_actions.RELAY_KIND, 'alttpr/room-3', action))


async def test_restart_without_runtime_audits_and_relays(db, monkeypatch):
    monkeypatch.delenv('RACETIME_BOT_ENABLED', raising=False)
    forwarded = _relay_on(monkeypatch)
    admin = await make_user(discord_id=4242)
    await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
    bot = await RacetimeBot.create(category='alttpr', client_id='cid', client_secret='sec', name='ALTTPR')

    await RacetimeBotService().restart_connection(admin, bot.id)

    assert forwarded == [(racetime_room_actions.RESTART_KIND, (bot.id,))]
    assert await AuditLog.filter(action=AuditActions.RACETIME_BOT_RESTARTED).count() == 1


async def test_relayed_restart_respawns_on_the_runtime_process(monkeypatch):
    monkeypatch.setenv('RACETIME_BOT_ENABLED', 'on')
    respawned = []

    class _Running:
        running = True

        async def respawn(self, bot_id):
            respawned.append(bot_id)

    monkeypatch.setattr(manager_module, '_manager', _Running())
    live_relay.receive(_from_elsewhere(racetime_room_actions.RESTART_KIND, 7))
    for _ in range(50):
        if respawned:
            break
        await asyncio.sleep(0.01)
    assert respawned == [7]


async def test_relayed_restart_is_ignored_where_bots_are_off(monkeypatch):
    monkeypatch.delenv('RACETIME_BOT_ENABLED', raising=False)
    respawned = []

    class _Running:
        running = True

        async def respawn(self, bot_id):  # pragma: no cover - must not run
            respawned.append(bot_id)

    monkeypatch.setattr(manager_module, '_manager', _Running())
    live_relay.receive(_from_elsewhere(racetime_room_actions.RESTART_KIND, 7))
    await asyncio.sleep(0.05)
    assert respawned == []
