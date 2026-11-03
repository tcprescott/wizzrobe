"""SANDBOX_MODE: the beta stack (deploy/beta/) must never reach a real person.

Each outbound channel stops at its last step. The seed's half (no published
secrets on a sandbox) lives in test_seed_coverage.py, the one module allowed to
run the seed itself.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from application.utils import environment


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.setenv('SANDBOX_MODE', 'on')


def test_off_by_default(monkeypatch):
    monkeypatch.delenv('SANDBOX_MODE', raising=False)
    assert environment.sandbox_mode() is False
    assert environment.outbound_blocked('email') is False


def test_workers_that_reach_outside_stay_off(sandbox, monkeypatch):
    for name in ('RACETIME_BOT_ENABLED', 'SPEEDGAMING_SYNC_ENABLED',
                 'DISCORD_EVENTS_SYNC_ENABLED', 'SERVICE_HEALTH_ALERT_DM'):
        monkeypatch.setenv(name, 'on')
    assert environment.racetime_bot_enabled() is False
    assert environment.speedgaming_sync_enabled() is False
    assert environment.discord_events_sync_enabled() is False
    assert environment.service_health_alert_dm_enabled() is False


async def test_discord_never_signs_in(sandbox, monkeypatch):
    from application import bot_runtime
    from application.services.discord import discord_service

    monkeypatch.setenv('DISCORD_TOKEN', 'not-a-real-token')
    with patch('asyncio.create_task') as create_task:
        await bot_runtime.start_discord(gateway=False)
    create_task.assert_not_called()
    assert await discord_service.sign_in_for_rest() is False


async def test_email_and_telegram_send_nothing(sandbox):
    from application.utils.clients import sendgrid_client, telegram_client

    with patch.object(sendgrid_client, '_get_client') as sendgrid, \
            patch.object(telegram_client, '_get_client') as telegram:
        assert await sendgrid_client.send_email('a@example.com', 's', 't', '<p>t</p>') is False
        assert await telegram_client.send_message(1, 'hi') is False
    sendgrid.assert_not_called()
    telegram.assert_not_called()


async def test_native_push_sends_nothing(sandbox):
    from application.services.native_push_service import NativePushService

    service = NativePushService.__new__(NativePushService)
    with patch.object(NativePushService, '_send_fcm', new=AsyncMock()) as fcm, \
            patch.object(NativePushService, '_send_apns', new=AsyncMock()) as apns:
        delivered = await service._deliver_one(SimpleNamespace(platform='android', token='t'), object())
    assert delivered is False
    fcm.assert_not_called()
    apns.assert_not_called()


async def test_personal_webhook_posts_nothing(sandbox):
    from application.services.personal_webhook_service import PersonalWebhookService

    service = PersonalWebhookService.__new__(PersonalWebhookService)
    with patch('httpx.AsyncClient') as client:
        ok, status, error = await service._post(
            SimpleNamespace(url='https://example.com/hook', secret='s'), 'match.created', None, None,
        )
    assert (ok, status) == (False, None)
    assert 'sandbox' in error
    client.assert_not_called()


async def test_community_webhook_closes_the_delivery_unsent(sandbox):
    from application.services.webhook_service import WebhookService

    service = WebhookService.__new__(WebhookService)
    service.delivery_repository = SimpleNamespace(finish=AsyncMock())
    with patch('httpx.AsyncClient') as client:
        await service._send(
            SimpleNamespace(id=1, url='https://example.com/hook', secret='s'), object(),
            body='{}', event_type='match.created', delivery_uuid='u',
        )
    client.assert_not_called()
    kwargs = service.delivery_repository.finish.await_args.kwargs
    assert kwargs['success'] is False
    assert 'sandbox' in kwargs['error']

