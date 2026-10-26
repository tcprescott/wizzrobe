"""The restream !ready gate: a match with a broadcaster holds its seed for them."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services import event_service as event_service_module
from application.services.race_room_bot_service import RaceRoomBotService
from application.services.race_room_service import RaceRoomLifecycle
from application.services.system_config_service import KEY_BROADCASTERS
from models import Broadcaster
from racetimebot.transport import RoomChat
from tests import test_race_room_bot
from tests.factories import set_setting
from tests.test_race_room_bot import _event, _messages, _racer, _setup

sent = test_race_room_bot.sent

SEED = 'Seed: https://alttpr.com/h/abc · Code: Bow · Hammer'


@pytest.fixture
def dms(monkeypatch):
    queued = []

    async def fake_dm(match, room, title, sends):
        queued.extend((user.preferred_name, notice.recipient.value) for user, _m, notice in sends)

    monkeypatch.setattr(RaceRoomBotService, '_dm', staticmethod(fake_dm))
    return queued


async def _restream(*, minutes_to_start, on=True, approved=True, rtid='b1'):
    if on:
        await set_setting(KEY_BROADCASTERS, 'true')
    event_service_module._clear_cache()
    start = datetime.now(timezone.utc) + timedelta(minutes=minutes_to_start)
    _t, match, _p, room = await _setup(scheduled_at=start, seed='https://alttpr.com/h/abc')
    caster = await _racer('caster', rtid)
    await Broadcaster.create(match_id=match.id, user_id=caster.id, approved=approved)
    return match, room


async def _say(room, user_id, name, text, **kw):
    chat = RoomChat(user_id=user_id, display_name=name, text=text)
    await RaceRoomLifecycle().handle_event(room, _event(room, kind='chat', chat=chat, **kw))


async def test_the_seed_waits_for_ready_then_posts(db, sent, dms):
    _match, room = await _restream(minutes_to_start=5)
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    assert _messages(sent) == []

    await _say(room, 'u1', 'alice', '!seed')
    await _say(room, 'u1', 'alice', '!ready')
    assert _messages(sent) == [
        'The seed is waiting for the broadcaster to type !ready.',
        "Only this match's broadcaster can use !ready. Staff can use !forceroll.",
    ]

    sent.clear()
    await _say(room, 'b1', 'caster', '!ready')
    assert _messages(sent) == ['Broadcast is ready. Posting the seed.', SEED]
    await room.refresh_from_db()
    assert room.broadcaster_ready_at is not None and room.seed_posted_at is not None
    assert dms == []


async def test_an_early_ready_lets_the_seed_post_on_time(db, sent, dms):
    _match, room = await _restream(minutes_to_start=45)
    await _say(room, 'b1', 'caster', '!ready')
    assert _messages(sent) == ['Broadcast is ready. The seed posts in 35 minutes.']

    sent.clear()
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc) + timedelta(minutes=40))
    assert _messages(sent) == [SEED]


async def test_at_the_start_the_bot_nudges_once(db, sent, dms):
    _match, room = await _restream(minutes_to_start=-1)
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    assert _messages(sent) == [
        'Waiting on caster to type !ready before the seed goes out. Staff can type !forceroll to send it now.',
    ]
    assert ('caster', 'crew') in dms

    sent.clear()
    await _say(room, 'm1', 'Mod', '!forceroll', monitors=['m1'])
    assert _messages(sent) == [SEED]


@pytest.mark.parametrize('on, approved', [(False, True), (True, False)])
async def test_no_gate_without_broadcasters_on_or_an_approved_one(db, sent, dms, on, approved):
    _match, room = await _restream(minutes_to_start=5, on=on, approved=approved)
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    assert _messages(sent) == [SEED]
    sent.clear()
    await _say(room, 'b1', 'caster', '!ready')
    assert _messages(sent) == ["This match isn't waiting on a broadcaster."]


def test_dm_text():
    from application.utils.discord_messages_racetime import racetime_broadcaster_waiting_dm

    assert '!ready' in racetime_broadcaster_waiting_dm('Main', [], to_broadcaster=True)
    assert 'link racetime.gg' in racetime_broadcaster_waiting_dm('Main', [], to_broadcaster=True, linked=False)
    assert racetime_broadcaster_waiting_dm('Main', ['Cas'], to_broadcaster=False).startswith("**Cas** hasn't")
