"""Discord interactions over signed HTTP POSTs (``/discord/interactions``).

The handlers are the gateway ones, unchanged; these tests drive real
discord.py ``Interaction`` objects through ``parse_interaction_create`` and
check that each first response comes back as the HTTP body instead of a
callback POST, and that a late one still reaches the person.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import inbound_webhooks
from application.services.discord import discord_service
from application.services.discord.bot_state import bot_ready
from discordbot import http_interactions as hi

PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_HEX = PRIVATE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _sign(body: bytes, timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    return {
        'X-Signature-Ed25519': PRIVATE_KEY.sign(timestamp.encode() + body).hex(),
        'X-Signature-Timestamp': timestamp,
    }


def _payload(interaction_id: int = 111, *, kind: int = hi.MESSAGE_COMPONENT, custom_id: str = 'probe:1') -> dict:
    return {
        'id': str(interaction_id), 'application_id': '222', 'type': kind, 'token': 'tok', 'version': 1,
        'channel_id': '333', 'channel': {'id': '333', 'type': 1},
        'user': {'id': '777', 'username': 'pressy', 'discriminator': '0', 'avatar': None, 'global_name': None},
        'data': {'custom_id': custom_id, 'component_type': 2},
        'app_permissions': '0', 'entitlements': [], 'authorizing_integration_owners': {},
        'context': 1, 'locale': 'en-US', 'attachment_size_limit': 10 * 1024 * 1024,
    }


@pytest.fixture
async def bot(monkeypatch):
    """A fresh shared client, wired the way ``get_discord_bot`` wires it, not signed in."""
    monkeypatch.setattr(discord_service, '_bot_instance', None)
    client = discord_service.get_discord_bot()
    await client._async_setup_hook()

    async def state():
        return client._connection

    monkeypatch.setattr(hi, '_signed_in_state', state)
    yield client
    monkeypatch.setattr(discord_service, '_bot_instance', None)


@pytest.fixture
def handler(monkeypatch):
    """Register a component handler under ``probe`` and hand back a slot for its body."""
    slot: dict = {}

    async def run(interaction: discord.Interaction) -> None:
        await slot['body'](interaction)

    monkeypatch.setitem(discord_service._interaction_handlers, 'probe', run)
    return slot


class TestSignature:
    def test_a_signed_recent_body_passes(self):
        body = b'{"type":1}'
        headers = _sign(body)
        assert hi.verify_signature(PUBLIC_HEX, headers['X-Signature-Ed25519'], headers['X-Signature-Timestamp'], body)

    def test_a_changed_body_fails(self):
        headers = _sign(b'{"type":1}')
        assert not hi.verify_signature(PUBLIC_HEX, headers['X-Signature-Ed25519'], headers['X-Signature-Timestamp'], b'{"type":2}')

    def test_an_old_signature_fails_so_it_cannot_be_replayed(self):
        body = b'{"type":1}'
        headers = _sign(body, str(int(time.time()) - 3600))
        assert not hi.verify_signature(PUBLIC_HEX, headers['X-Signature-Ed25519'], headers['X-Signature-Timestamp'], body)

    def test_garbage_headers_fail_rather_than_raise(self):
        assert not hi.verify_signature(PUBLIC_HEX, 'zz', 'not-a-time', b'')
        assert not hi.verify_signature(PUBLIC_HEX, 'abcd', str(int(time.time())), b'')


class TestEndpoint:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        inbound_webhooks.mount(app)
        return TestClient(app)

    def test_absent_until_the_public_key_is_set(self, client, monkeypatch):
        monkeypatch.delenv(hi.PUBLIC_KEY_ENV, raising=False)
        assert client.post(inbound_webhooks.DISCORD_PATH, content=b'{}').status_code == 404

    def test_an_unsigned_request_is_refused(self, client, monkeypatch):
        monkeypatch.setenv(hi.PUBLIC_KEY_ENV, PUBLIC_HEX)
        body = b'{"type":1}'
        headers = _sign(b'something else')
        assert client.post(inbound_webhooks.DISCORD_PATH, content=body, headers=headers).status_code == 401

    def test_ping_gets_pong(self, client, monkeypatch):
        monkeypatch.setenv(hi.PUBLIC_KEY_ENV, PUBLIC_HEX)
        body = json.dumps({'type': hi.PING, 'id': '1', 'application_id': '2'}).encode()
        response = client.post(inbound_webhooks.DISCORD_PATH, content=body, headers=_sign(body))
        assert response.status_code == 200
        assert response.json() == {'type': hi.PONG}

    def test_503_while_the_client_is_not_signed_in(self, client, monkeypatch):
        monkeypatch.setenv(hi.PUBLIC_KEY_ENV, PUBLIC_HEX)

        async def unavailable():
            raise hi.InteractionsUnavailable

        monkeypatch.setattr(hi, '_signed_in_state', unavailable)
        body = json.dumps(_payload()).encode()
        assert client.post(inbound_webhooks.DISCORD_PATH, content=body, headers=_sign(body)).status_code == 503


class TestDispatch:
    async def test_a_deferring_button_handler_answers_in_the_body_and_keeps_running(self, bot, handler):
        finished = asyncio.Event()

        async def body(interaction):
            await interaction.response.defer(ephemeral=True)
            assert interaction.response.is_done()
            assert interaction.user.id == 777
            finished.set()

        handler['body'] = body
        assert await hi.dispatch(_payload(201)) == {'type': hi.DEFERRED_UPDATE_MESSAGE}
        await asyncio.wait_for(finished.wait(), 1)

    async def test_an_ephemeral_reply_comes_back_as_a_channel_message(self, bot, handler):
        async def body(interaction):
            await interaction.response.send_message('Done.', ephemeral=True)

        handler['body'] = body
        answer = await hi.dispatch(_payload(202))
        assert answer['type'] == hi.CHANNEL_MESSAGE
        assert answer['data']['content'] == 'Done.'
        assert answer['data']['flags'] & hi.EPHEMERAL_FLAG

    async def test_a_modal_is_the_first_response(self, bot, handler):
        async def body(interaction):
            modal = discord.ui.Modal(title='Why?', custom_id='probe_reason:1', timeout=None)
            modal.add_item(discord.ui.TextInput(label='Reason', custom_id='reason', required=False))
            await interaction.response.send_modal(modal)

        handler['body'] = body
        answer = await hi.dispatch(_payload(203))
        assert answer['type'] == 9
        assert answer['data']['custom_id'] == 'probe_reason:1'

    async def test_a_slow_handler_is_deferred_and_its_late_reply_becomes_a_followup(self, bot, handler, monkeypatch):
        monkeypatch.setattr(hi, 'RESPONSE_DEADLINE_SECONDS', 0.05)
        execute = AsyncMock(return_value={'id': '9'})
        monkeypatch.setattr(hi._ADAPTER, 'execute_webhook', execute)
        replied = asyncio.Event()

        async def body(interaction):
            await asyncio.sleep(0.2)
            await interaction.response.send_message('Late but here.', ephemeral=True)
            replied.set()

        handler['body'] = body
        assert await hi.dispatch(_payload(204)) == {'type': hi.DEFERRED_UPDATE_MESSAGE}
        await asyncio.wait_for(replied.wait(), 2)
        execute.assert_awaited_once()
        assert execute.await_args.args[:2] == (222, 'tok')
        assert execute.await_args.kwargs['payload']['content'] == 'Late but here.'

    async def test_a_slow_slash_command_gets_a_private_thinking_defer(self, bot):
        assert hi._stand_in_response(hi.APPLICATION_COMMAND) == {
            'type': hi.DEFERRED_CHANNEL_MESSAGE, 'data': {'flags': hi.EPHEMERAL_FLAG},
        }

    async def test_other_interactions_still_use_the_callback_endpoint(self, monkeypatch):
        sent = MagicMock(return_value='real')
        monkeypatch.setattr(hi.AsyncWebhookAdapter, 'create_interaction_response', sent)
        assert hi._ADAPTER.create_interaction_response(999999, 't', session=None, params=MagicMock()) == 'real'


class TestBotReady:
    def _bot(self, *, ready: bool, user: object | None) -> MagicMock:
        client = MagicMock()
        client.is_ready.return_value = ready
        client.user = user
        client.is_closed.return_value = False
        return client

    def test_the_gateway_counts(self, monkeypatch):
        monkeypatch.delenv('DISCORD_GATEWAY', raising=False)
        assert bot_ready(self._bot(ready=True, user=object()))

    def test_signed_in_without_gateway_counts_only_when_the_gateway_is_off(self, monkeypatch):
        monkeypatch.delenv('DISCORD_GATEWAY', raising=False)
        assert not bot_ready(self._bot(ready=False, user=object()))
        monkeypatch.setenv('DISCORD_GATEWAY', 'off')
        assert bot_ready(self._bot(ready=False, user=object()))
        assert not bot_ready(self._bot(ready=False, user=None))
        assert not bot_ready(None)

    async def test_list_guilds_asks_discord_when_there_is_no_gateway_cache(self, monkeypatch):
        monkeypatch.setenv('DISCORD_GATEWAY', 'off')
        client = self._bot(ready=False, user=object())

        async def guilds(limit=None):
            for gid, name in ((1, 'One'), (2, 'Two')):
                guild = MagicMock()
                guild.id, guild.name = gid, name
                yield guild

        client.fetch_guilds = guilds
        service = discord_service._RealDiscordService.__new__(discord_service._RealDiscordService)
        service._bot = client
        assert await service.list_guilds() == (True, [{'id': 1, 'name': 'One'}, {'id': 2, 'name': 'Two'}])
