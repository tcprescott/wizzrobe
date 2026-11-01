"""Telegram updates by webhook (``/telegram/webhook``) instead of long polling."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import inbound_webhooks
from application.services import telegram_bot_worker
from application.utils.clients import telegram_client

UPDATE = {
    'update_id': 42,
    'message': {'chat': {'id': 5555, 'type': 'private'}, 'from': {'username': 'samk'}, 'text': ' /stop '},
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'bot-token')
    app = FastAPI()
    inbound_webhooks.mount(app)
    return TestClient(app)


@pytest.fixture
def handled(monkeypatch):
    seen = AsyncMock()
    monkeypatch.setattr(telegram_bot_worker, 'handle_update', seen)
    return seen


def test_absent_without_a_secret(client, handled, monkeypatch):
    monkeypatch.delenv('TELEGRAM_WEBHOOK_SECRET', raising=False)
    assert client.post(telegram_bot_worker.WEBHOOK_PATH, json=UPDATE).status_code == 404
    handled.assert_not_awaited()


def test_a_wrong_secret_is_refused(client, handled, monkeypatch):
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'right')
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'wrong'}
    assert client.post(telegram_bot_worker.WEBHOOK_PATH, json=UPDATE, headers=headers).status_code == 401
    handled.assert_not_awaited()


def test_an_update_is_answered_like_a_polled_one(client, handled, monkeypatch):
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'right')
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'right'}
    assert client.post(telegram_bot_worker.WEBHOOK_PATH, json=UPDATE, headers=headers).status_code == 200
    update = handled.await_args.args[0]
    assert (update.update_id, update.chat_id, update.chat_type, update.username, update.text) == (
        42, 5555, 'private', 'samk', '/stop',
    )


def test_a_malformed_update_is_dropped_not_retried(client, handled, monkeypatch):
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'right')
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'right'}
    assert client.post(telegram_bot_worker.WEBHOOK_PATH, json={'nope': 1}, headers=headers).status_code == 200
    handled.assert_not_awaited()


async def test_start_registers_the_webhook_instead_of_polling(monkeypatch):
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'right')
    monkeypatch.setenv('BASE_URL', 'https://wizzrobe.test')
    register = AsyncMock(return_value=True)
    monkeypatch.setattr(telegram_client, 'set_webhook', register)
    loop = MagicMock()
    monkeypatch.setattr(telegram_bot_worker, '_loop', loop)
    telegram_bot_worker.start()
    await telegram_bot_worker._register_task
    register.assert_awaited_once_with('https://wizzrobe.test/telegram/webhook', 'right')
    loop.start.assert_not_called()


async def test_start_polls_without_a_secret(monkeypatch):
    monkeypatch.delenv('TELEGRAM_WEBHOOK_SECRET', raising=False)
    loop = MagicMock()
    monkeypatch.setattr(telegram_bot_worker, '_loop', loop)
    telegram_bot_worker.start()
    loop.start.assert_called_once()


async def test_polling_clears_a_leftover_webhook_first(monkeypatch):
    monkeypatch.setattr(telegram_bot_worker, '_webhook_cleared', False)
    cleared = AsyncMock(return_value=True)
    monkeypatch.setattr(telegram_client, 'delete_webhook', cleared)
    monkeypatch.setattr(telegram_client, 'get_updates', AsyncMock(return_value=[]))
    await telegram_bot_worker._tick()
    await telegram_bot_worker._tick()
    cleared.assert_awaited_once()
