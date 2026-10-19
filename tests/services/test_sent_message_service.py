"""Every DM lands in the Sent messages log, with what Discord did with it.

Staff open the log mid-event to answer "did Fynn get told?", so the row has to
exist whatever happened on Discord, and only staff may read it.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from application.services import SentMessageService
from application.services.discord.discord_service import (
    DM_DISCORD_OFF,
    DM_REFUSED,
    MockDiscordService,
    sent_message_status,
)
from application.tenant_context import set_tenant_id
from application.utils.discord_messages import DMLink
from models import Role, SentMessage, SentMessageStatus, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")


def test_status_mapping():
    assert sent_message_status((True, 'Message sent successfully.')) == (SentMessageStatus.DELIVERED, None)
    assert sent_message_status((True, DM_DISCORD_OFF)) == (SentMessageStatus.DISCORD_OFF, None)
    assert sent_message_status((False, DM_REFUSED)) == (SentMessageStatus.BLOCKED, DM_REFUSED)
    assert sent_message_status((False, 'Discord bot is not connected.')) == (
        SentMessageStatus.FAILED, 'Discord bot is not connected.')


async def test_mock_send_logs_the_card_link_and_result():
    player = await make_user(discord_id=901, username='fynn')
    embed = discord.Embed(title='Your match is ready to book', description='Round 3 vs Aerinon')
    ok, _ = await MockDiscordService().send_dm(
        901, 'Your match is ready to book', embed=embed,
        link=DMLink('Pick a time', 'https://x.test/t/default/home'), silent=True,
    )
    assert ok
    row = await SentMessage.get(recipient_id=player.id)
    assert row.tenant_id == DEFAULT_TEST_TENANT_ID
    assert row.title == 'Your match is ready to book'
    assert row.embed['description'] == 'Round 3 vs Aerinon'
    assert (row.link_label, row.silent, row.status) == ('Pick a time', True, SentMessageStatus.DELIVERED)


async def test_discord_off_is_logged_as_off():
    await make_user(discord_id=902, username='quiet', dm_notifications=False)
    await MockDiscordService().send_dm(902, 'Match checked in')
    assert (await SentMessage.get(discord_id='902')).status == SentMessageStatus.DISCORD_OFF


async def test_real_send_failure_is_logged_with_its_reason(monkeypatch):
    from application.services.discord import discord_service as ds

    monkeypatch.setattr(ds, '_mirror_dm_to_web_push', lambda *a, **kw: None)
    discord_user = MagicMock()
    discord_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), 'no'))
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.get_user.return_value = discord_user
    svc = object.__new__(ds._RealDiscordService)
    svc._bot = bot
    await make_user(discord_id=903, username='blocker')

    assert await svc.send_dm(903, 'New reschedule request') == (False, DM_REFUSED)
    row = await SentMessage.get(discord_id='903')
    assert (row.status, row.error) == (SentMessageStatus.BLOCKED, DM_REFUSED)


async def test_no_community_no_row():
    set_tenant_id(None)
    await MockDiscordService().send_dm(904, 'Platform notice')
    assert await SentMessage.all().count() == 0


async def test_only_staff_read_the_log():
    staff = await make_user(discord_id=905, username='staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    player = await make_user(discord_id=906, username='player')
    await MockDiscordService().send_dm(906, 'Stage call')
    await MockDiscordService().send_dm(905, 'Hello staff')

    service = SentMessageService()
    with pytest.raises(PermissionError):
        await service.list_messages(actor=player)
    rows = await service.list_messages(actor=staff, search='stage')
    assert [r.discord_id for r in rows] == ['906']
    assert (await service.totals(actor=staff))['sent'] == 2
