"""The DM outbox: a Discord send that may work later stays queued and the sweeper sends it."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import discordbot  # noqa: F401  registers the button views
from application.services import dm_outbox_worker
from application.services.discord import discord_service as ds
from application.services.discord.discord_service import DM_REFUSED, VIEW_MATCH_ACK
from application.services.discord.dm_outbox import DM_BOT_OFFLINE, DM_DISCORD_TROUBLE
from models import SentMessage, SentMessageStatus
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def discord_bot(monkeypatch):
    monkeypatch.setattr(ds, '_mirror_dm_to_web_push', lambda *a, **kw: None)
    monkeypatch.setattr(ds, '_mirror_dm_to_channels', lambda *a, **kw: None)
    user = MagicMock()
    user.send = AsyncMock(return_value=MagicMock(channel=MagicMock(id=1), id=2))
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.get_user.return_value = user
    svc = object.__new__(ds._RealDiscordService)
    svc._bot = bot
    monkeypatch.setattr(ds, 'DiscordService', lambda: svc)
    return svc, bot, user


async def test_an_offline_bot_leaves_the_dm_queued_with_its_buttons(discord_bot):
    svc, bot, user = discord_bot
    await make_user(discord_id=801, username='fynn')
    bot.is_ready.return_value = False

    embed = discord.Embed(title='Match soon', description='Round 2')
    assert await svc.send_dm_with_acknowledgment_button(801, 'Match soon', 55, embed=embed) == (False, DM_BOT_OFFLINE)
    row = await SentMessage.get(discord_id='801')
    assert row.status == SentMessageStatus.PENDING and row.attempts == 1 and row.claimed_until is None
    assert row.outbox['view'] == [VIEW_MATCH_ACK, [55]]

    bot.is_ready.return_value = True
    await dm_outbox_worker._tick()

    sent = user.send.await_args.kwargs
    assert sent['embed'].description == 'Round 2'
    assert sent['view'].children[0].custom_id.endswith(':55')
    row = await SentMessage.get(id=row.id)
    assert row.status == SentMessageStatus.DELIVERED and row.outbox is None


async def test_discord_trouble_is_retried_and_a_refusal_is_not(discord_bot):
    svc, _bot, user = discord_bot
    await make_user(discord_id=802, username='a')
    await make_user(discord_id=803, username='b')
    user.send.side_effect = discord.HTTPException(MagicMock(status=503), 'down')
    assert await svc.send_dm(802, 'Hello') == (False, DM_DISCORD_TROUBLE)
    user.send.side_effect = discord.Forbidden(MagicMock(status=403), 'no')
    assert await svc.send_dm(803, 'Hello') == (False, DM_REFUSED)

    assert (await SentMessage.get(discord_id='802')).status == SentMessageStatus.PENDING
    refused = await SentMessage.get(discord_id='803')
    assert refused.status == SentMessageStatus.BLOCKED and refused.outbox is None


async def test_a_send_that_died_mid_way_is_sent_once_its_claim_runs_out(discord_bot):
    _svc, _bot, user = discord_bot
    await make_user(discord_id=804, username='c')
    outbox = {'content': 'Hi', 'embed': None, 'view': None, 'link': ['Open', 'https://x.test/a'], 'silent': True}
    held = await SentMessage.create(
        tenant_id=1, discord_id='804', body='Hi', outbox=outbox,
        claimed_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    await dm_outbox_worker._tick()
    assert not user.send.await_count, 'a live claim belongs to the sender holding it'

    await SentMessage.filter(id=held.id).update(claimed_until=datetime.now(timezone.utc) - timedelta(seconds=1))
    await dm_outbox_worker._tick()
    assert user.send.await_args.args == ('Hi',)
    assert user.send.await_args.kwargs['silent'] is True
    assert (await SentMessage.get(id=held.id)).status == SentMessageStatus.DELIVERED


async def test_the_sweeper_gives_up_on_old_or_much_tried_dms(discord_bot):
    _svc, _bot, user = discord_bot
    outbox = {'content': 'Hi', 'embed': None, 'view': None, 'link': None, 'silent': False}
    tried = await SentMessage.create(tenant_id=1, discord_id='805', body='Hi', outbox=outbox,
                                     attempts=dm_outbox_worker.MAX_ATTEMPTS, error=DM_BOT_OFFLINE)
    old = await SentMessage.create(tenant_id=1, discord_id='806', body='Hi', outbox=outbox, attempts=1)
    await SentMessage.filter(id=old.id).update(created_at=datetime.now(timezone.utc) - timedelta(hours=1))

    await dm_outbox_worker._tick()

    assert not user.send.await_count
    for row_id in (tried.id, old.id):
        row = await SentMessage.get(id=row_id)
        assert row.status == SentMessageStatus.FAILED and row.outbox is None
        assert row.error.startswith('Gave up')


async def test_an_ad_hoc_view_is_sent_once_without_the_outbox(discord_bot):
    svc, bot, _user = discord_bot
    await make_user(discord_id=807, username='d')
    bot.is_ready.return_value = False
    await svc.send_dm(807, 'Hello', lambda: discord.ui.View())
    row = await SentMessage.get(discord_id='807')
    assert row.status == SentMessageStatus.FAILED and row.outbox is None
