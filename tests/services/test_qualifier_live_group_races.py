"""Qualifier live races as group races: open on time, post, permalink at seed, lock."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services import race_room_bot_service
from application.services.async_qualifier.async_qualifier_live_race_room import LiveRaceRoomService
from application.services.async_qualifier.async_qualifier_live_race_service import (
    AsyncQualifierLiveRaceService,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from application.services.discord.discord_service import DiscordService
from application.services.race_room_service import RaceRoomLifecycle
from application.utils.clients import racetime_room_actions
from models import (
    AsyncQualifierLiveRace,
    AsyncQualifierLiveRaceStatus,
    AuditLog,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeBotTenant,
    RacetimeRoom,
    Role,
    Tenant,
    User,
    UserRole,
)
from racetimebot.transport import RaceRoomEvent, RoomChat
from tests.conftest import DEFAULT_TEST_TENANT_ID

pytestmark = pytest.mark.anyio


@pytest.fixture
def sent():
    said = []

    async def sender(slug, action):
        said.append((slug, action))
        return True

    racetime_room_actions.register_sender(sender)
    race_room_bot_service._forced_ids.clear()
    yield said
    racetime_room_actions.register_sender(None)


@pytest.fixture
def posts(monkeypatch):
    calls = []

    async def post(self, guild_id, channel_id, message, embed=None, link=None, ping_role_id=None):
        calls.append({'guild': guild_id, 'channel': channel_id, 'message': message,
                      'link': link, 'role': ping_role_id})
        return True, 'ok'

    monkeypatch.setattr(DiscordService, 'post_to_channel', post)
    return calls


def _said(sent):
    return [a['data']['message'] for _s, a in sent if a['action'] == 'message']


async def _staff() -> User:
    staff = await User.create(discord_id=940001, username='qstaff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return staff


async def _setup(*, in_minutes=30, lock=None, channel='222', **qualifier_fields):
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=111)
    bot = await RacetimeBot.create(category='alttpr', client_id='c', client_secret='s', name='A')
    await RacetimeBotTenant.create(bot=bot, tenant_id=DEFAULT_TEST_TENANT_ID, is_active=True)
    staff = await _staff()
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    now = datetime.now(timezone.utc)
    if lock is not None:
        qualifier_fields['live_race_lock_at_seed'] = lock
    q = await qsvc.create_qualifier(
        staff, name='Weekly Q', opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=1),
        live_race_announce_channel_id=channel, live_race_announce_role_id='333', **qualifier_fields,
    )
    pool = await qsvc.create_pool(staff, q.id, name='Pool A')
    pl = await qsvc.add_permalink(staff, pool.id, url='https://alttpr.com/h/live', live_race=True)
    lr = await lrsvc.create_live_race(
        staff, pool.id, match_title='Weekly Q — Race 1', permalink_id=pl.id,
        scheduled_at=now + timedelta(minutes=in_minutes),
    )
    return staff, q, lr


async def _room(lr) -> RacetimeRoom:
    lr = await AsyncQualifierLiveRace.get(id=lr.id)
    return await RacetimeRoom.get(slug=lr.racetime_slug)


async def test_a_scheduled_live_race_opens_on_its_own_and_is_posted(db, racetime_rooms, posts):
    _staff_user, _q, lr = await _setup(in_minutes=30)

    await LiveRaceRoomService().open_due_races(datetime.now(timezone.utc), 7 * 24 * 60)

    lr = await AsyncQualifierLiveRace.get(id=lr.id)
    assert lr.status == AsyncQualifierLiveRaceStatus.PENDING and lr.racetime_slug
    [(_category, settings)] = racetime_rooms.started
    assert settings.invitational is False
    [post] = posts
    assert (post['guild'], post['channel'], post['role']) == (111, 222, 333)
    assert post['message'].startswith('<@&333> The race room for Weekly Q — Race 1 is open.')
    assert (await _room(lr)).announced_at is not None


async def test_a_live_race_too_far_out_waits(db, racetime_rooms, posts):
    await _setup(in_minutes=120)

    await LiveRaceRoomService().open_due_races(datetime.now(timezone.utc), 7 * 24 * 60)

    assert racetime_rooms.started == [] and posts == []


async def test_the_permalink_posts_and_the_room_locks_by_default(db, racetime_rooms, posts, sent):
    staff, _q, lr = await _setup(in_minutes=5)
    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)

    await LiveRaceRoomService().step_open_rooms(datetime.now(timezone.utc))

    said = _said(sent)
    assert said[0] == 'Seed: https://alttpr.com/h/live'
    assert said[-1] == 'This room is now locked. Late entries are not permitted.'
    assert {'action': 'make_invitational'} in [a for _s, a in sent]
    room = await _room(lr)
    assert room.seed_posted_at is not None and room.locked_at is not None
    assert await AuditLog.filter(action='race_room.locked').count() == 1


async def test_lock_off_leaves_the_room_open(db, racetime_rooms, posts, sent):
    staff, _q, lr = await _setup(in_minutes=5, lock=False)
    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)

    await LiveRaceRoomService().step_open_rooms(datetime.now(timezone.utc))

    assert _said(sent) == ['Seed: https://alttpr.com/h/live']


async def test_the_seed_waits_for_its_time(db, racetime_rooms, posts, sent):
    staff, _q, lr = await _setup(in_minutes=30)
    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)

    await LiveRaceRoomService().step_open_rooms(datetime.now(timezone.utc))

    assert _said(sent) == []


async def test_the_bot_greets_and_answers_seed(db, racetime_rooms, posts, sent):
    staff, _q, lr = await _setup(in_minutes=30)
    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)
    room = await _room(lr)
    lifecycle = RaceRoomLifecycle()

    def event(kind, text=None):
        chat = RoomChat(text=text, user_id='rt-x', display_name='X') if text else None
        return RaceRoomEvent(slug=room.slug, category='alttpr', status=RaceRoomStatus.OPEN, kind=kind, chat=chat)

    await lifecycle.handle_event(room, event('joined'))
    await lifecycle.handle_event(room, event('joined'))
    await lifecycle.handle_event(room, event('chat', '!seed'))

    said = _said(sent)
    assert len([s for s in said if s.startswith("Hi! I'm running this room for Weekly Q")]) == 1
    assert said[-1].startswith('The seed posts in ')


async def test_a_failed_post_is_retried(db, racetime_rooms, monkeypatch):
    tries = []

    async def flaky(self, *args, **kwargs):
        tries.append(1)
        return (len(tries) > 1), 'offline'

    monkeypatch.setattr(DiscordService, 'post_to_channel', flaky)
    staff, _q, lr = await _setup(in_minutes=30)
    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)
    assert (await _room(lr)).announced_at is None

    await LiveRaceRoomService().step_open_rooms(datetime.now(timezone.utc))

    assert len(tries) == 2 and (await _room(lr)).announced_at is not None


async def test_the_bot_sits_in_live_race_rooms(db, racetime_rooms, posts):
    from application.repositories import RacetimeRoomRepository

    staff, _q, lr = await _setup(in_minutes=30)
    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)

    rooms = await RacetimeRoomRepository().list_bot_run_open('alttpr')

    assert [r.slug for r in rooms] == [(await _room(lr)).slug]


async def test_the_seed_must_post_after_the_room_opens(db):
    staff = await _staff()
    with pytest.raises(ValueError, match='after the room opens'):
        await AsyncQualifierService().create_qualifier(
            staff, name='Bad Q', live_race_room_open_minutes=5, live_race_seed_post_minutes=10,
        )


async def test_the_bot_must_be_one_the_community_may_use(db):
    staff = await _staff()
    stranger = await RacetimeBot.create(category='smz3', client_id='c', client_secret='s', name='S')
    with pytest.raises(ValueError, match="isn't available"):
        await AsyncQualifierService().create_qualifier(staff, name='Bot Q', live_race_bot_id=stranger.id)


async def test_the_qualifier_bot_opens_the_room(db, racetime_rooms, posts):
    other = await RacetimeBot.create(category='smz3', client_id='c2', client_secret='s2', name='SMZ3')
    await RacetimeBotTenant.create(bot=other, tenant_id=DEFAULT_TEST_TENANT_ID, is_active=True)
    staff, _q, lr = await _setup(in_minutes=30, live_race_bot_id=other.id)

    await AsyncQualifierLiveRaceService().open_room(staff, lr.id)

    [(category, _settings)] = racetime_rooms.started
    assert category == 'smz3'
