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


async def test_the_notice_is_logged_and_an_off_notice_is_not():
    from application.services.notification_catalog import MATCH_STARTED, MATCHUP_READY, PLAYERS

    await make_user(discord_id=907, username='notice')
    await MockDiscordService().send_dm(907, 'Ready to book', notice=MATCHUP_READY.to(PLAYERS))
    row = await SentMessage.get(discord_id='907')
    assert (row.notice_key, row.audience, row.silent) == ('matchup_ready', 'players', True)

    await MockDiscordService().send_dm(907, 'Match started', notice=MATCH_STARTED.to(PLAYERS))
    assert await SentMessage.filter(discord_id='907').count() == 1


async def test_anyone_reads_their_own_messages_and_only_theirs():
    from application.services.notification_catalog import MATCHUP_READY, PLAYERS, SHIFT_REMINDER, VOLUNTEERS

    me = await make_user(discord_id=910, username='me')
    await make_user(discord_id=911, username='them')
    await MockDiscordService().send_dm(910, 'Ready to book', notice=MATCHUP_READY.to(PLAYERS))
    await MockDiscordService().send_dm(910, 'Your shift is soon', notice=SHIFT_REMINDER.to(VOLUNTEERS))
    await MockDiscordService().send_dm(911, 'Not yours', notice=MATCHUP_READY.to(PLAYERS))

    service = SentMessageService()
    mine = await service.list_mine(actor=me)
    assert [m.body for m in mine] == ['Your shift is soon', 'Ready to book']
    assert await service.count_mine(actor=me) == 2
    assert await service.my_groups(actor=me) == ['Brackets', 'Volunteer shifts']
    assert [m.body for m in await service.list_mine(actor=me, group='Volunteer shifts')] == ['Your shift is soon']

    theirs = await SentMessage.get(discord_id='911')
    assert await service.get_mine(theirs.id, actor=me) is None
    assert (await service.get_mine(mine[0].id, actor=me)).body == 'Your shift is soon'


async def test_another_communitys_messages_stay_there():
    from models import Tenant

    me = await make_user(discord_id=912, username='me2')
    other = await Tenant.create(name='Elsewhere', slug='elsewhere')
    set_tenant_id(other.id)
    await MockDiscordService().send_dm(912, 'From elsewhere')
    row = await SentMessage.get(discord_id='912')
    set_tenant_id(DEFAULT_TEST_TENANT_ID)

    service = SentMessageService()
    assert await service.list_mine(actor=me) == []
    assert await service.get_mine(row.id, actor=me) is None


async def test_the_footnote_opens_the_message_in_your_history(capsys):
    from application.services.notification_catalog import MATCHUP_READY, PLAYERS

    await make_user(discord_id=913, username='linked')
    await MockDiscordService().send_dm(913, 'Ready to book', notice=MATCHUP_READY.to(PLAYERS))
    row = await SentMessage.get(discord_id='913')
    assert f'/messages?message={row.id}' in capsys.readouterr().out
