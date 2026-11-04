"""The Group races board: one list of tournament group races and qualifier live races, and staff's actions."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services import race_room_bot_service
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from application.services.discord.discord_service import DiscordService
from application.services.group_race_board_service import GroupRaceBoardService
from application.utils.clients import racetime_room_actions
from models import (
    AsyncQualifierLiveRace,
    GeneratedSeeds,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeBotTenant,
    RacetimeRoom,
    Role,
    Tenant,
    Tournament,
    User,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

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
        calls.append({'channel': channel_id, 'message': message})
        return True, 'ok'

    monkeypatch.setattr(DiscordService, 'post_to_channel', post)
    return calls


@pytest.fixture(autouse=True)
def racetime_data(monkeypatch):
    from application.utils.clients import racetime_rooms_client

    data = {'entrants': []}

    async def fake(slug):
        return data

    monkeypatch.setattr(racetime_rooms_client, 'race_data', fake)
    return data


def _said(sent):
    return [a['data']['message'] for _s, a in sent if a['action'] == 'message']


async def _staff() -> User:
    staff = await make_user(960001, 'boardstaff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return staff


async def _bot() -> RacetimeBot:
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=111)
    bot = await RacetimeBot.create(category='alttpr', client_id='c', client_secret='s', name='A')
    await RacetimeBotTenant.create(bot=bot, tenant_id=DEFAULT_TEST_TENANT_ID, is_active=True)
    return bot


async def _daily(bot, *, lock=False) -> Tournament:
    return await Tournament.create(
        name='ALTTPR Daily', racetime_bot_id=bot.id, racetime_default_goal='Beat the game',
        racetime_group_race=True, racetime_bot_runs_room=True, racetime_auto_create_rooms=True,
        room_open_minutes_before=60, racetime_seed_post_minutes=10, racetime_lock_at_seed=lock,
        racetime_announce_channel_id='222', racetime_announce_role_id='333',
    )


async def _race(tournament, *, in_minutes=30, title='ALTTPR Daily — Open') -> Match:
    return await Match.create(
        tournament_id=tournament.id, title=title,
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=in_minutes),
    )


async def _room(match: Match) -> RacetimeRoom:
    seed = await GeneratedSeeds.create(seed_url='https://alttpr.com/h/abc', seed_code=['Bow'])
    await Match.filter(id=match.id).update(generated_seed_id=seed.id)
    return await RacetimeRoom.create(
        bot_id=(await Tournament.get(id=match.tournament_id)).racetime_bot_id,  # type: ignore[attr-defined]
        slug=f'alttpr/daily-{match.id}', category='alttpr', status=RaceRoomStatus.OPEN, match_id=match.id,
        opened_at=datetime.now(timezone.utc),
    )


async def _qualifier_race(staff, *, in_minutes=90):
    qsvc = AsyncQualifierService()
    now = datetime.now(timezone.utc)
    q = await qsvc.create_qualifier(
        staff, name='Weekly Q', opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=3),
        live_race_announce_channel_id='224',
    )
    pool = await qsvc.create_pool(staff, q.id, name='Pool A')
    link = await qsvc.add_permalink(staff, pool.id, url='https://alttpr.com/h/live', live_race=True)
    return pool, link


async def test_the_board_lists_group_races_and_live_races_but_not_head_to_head(db, posts):
    bot = await _bot()
    staff = await _staff()
    daily = await _daily(bot)
    await _race(daily)
    h2h = await Tournament.create(name='Main', racetime_bot_id=bot.id)
    await Match.create(tournament_id=h2h.id, title='Final', scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))
    pool, link = await _qualifier_race(staff)
    board = GroupRaceBoardService()
    await board.create(
        staff, title='Weekly Q — Race 1', scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        pool_id=pool.id, permalink_id=link.id,
    )

    rows = await board.list_races(staff)

    assert [(r.kind, r.title, r.state) for r in rows] == [
        ('match', 'ALTTPR Daily — Open', 'scheduled'), ('qualifier', 'Weekly Q — Race 1', 'scheduled'),
    ]
    assert rows[0].channel_id == '222' and rows[0].opens_at < rows[0].seed_at < rows[0].scheduled_at
    assert await board.list_races(staff, finished=True) == []


async def test_staff_add_a_race_by_hand(db):
    staff = await _staff()
    daily = await _daily(await _bot())

    view = await GroupRaceBoardService().create(
        staff, title='Sunday Special', tournament_id=daily.id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    match = await Match.get(id=view.id).prefetch_related('players')
    assert match.title == 'Sunday Special' and list(match.players) == []
    assert view.state == 'scheduled' and not view.from_speedgaming


async def test_a_race_by_hand_needs_a_group_race_series_and_a_future_start(db):
    staff = await _staff()
    h2h = await Tournament.create(name='Main')
    board = GroupRaceBoardService()
    with pytest.raises(ValueError, match="isn't a group race series"):
        await board.create(staff, title='X', tournament_id=h2h.id,
                           scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
    with pytest.raises(ValueError, match='in the future'):
        await board.create(staff, title='X', tournament_id=h2h.id,
                           scheduled_at=datetime.now(timezone.utc) - timedelta(days=1))


async def test_open_now_opens_and_posts_the_room(db, racetime_rooms, posts):
    staff = await _staff()
    match = await _race(await _daily(await _bot()), in_minutes=180)

    view = await GroupRaceBoardService().open_now(staff, 'match', match.id)

    assert view.state == 'open' and view.room_url and view.announced_at is not None
    assert len(racetime_rooms.started) == 1 and len(posts) == 1


async def test_post_seed_now_and_lock_now(db, posts, sent):
    staff = await _staff()
    match = await _race(await _daily(await _bot()), in_minutes=120)
    await _room(match)
    board = GroupRaceBoardService()

    view = await board.post_seed_now(staff, 'match', match.id)
    assert view.seed_posted_at is not None and view.locked_at is None
    assert _said(sent)[0].startswith('Seed: https://alttpr.com/h/abc')

    view = await board.lock_now(staff, 'match', match.id)
    assert view.locked_at is not None
    assert {'action': 'make_invitational'} in [a for _s, a in sent]
    with pytest.raises(ValueError, match='already locked'):
        await board.lock_now(staff, 'match', match.id)


async def test_an_open_room_lists_who_has_joined(db, racetime_data):
    staff = await _staff()
    match = await _race(await _daily(await _bot()))
    await _room(match)
    await make_user(960030, 'Andy', racetime_user_id='rt-andy')
    racetime_data['status'] = {'value': 'open'}
    racetime_data['entrants'] = [
        {'user': {'id': 'rt-zaruvi', 'name': 'zaruvi'}, 'status': {'value': 'not_ready'}},
        {'user': {'id': 'rt-andy', 'name': 'andy_rt'}, 'status': {'value': 'ready'}},
    ]

    detail = await GroupRaceBoardService().get_race(staff, 'match', match.id)

    assert [(e.name, e.linked) for e in detail.entrants] == [('Andy', True), ('zaruvi', False)]


async def test_repost_sends_the_announcement_again(db, posts):
    staff = await _staff()
    match = await _race(await _daily(await _bot()))
    room = await _room(match)
    await RacetimeRoom.filter(id=room.id).update(announced_at=datetime.now(timezone.utc))

    await GroupRaceBoardService().repost(staff, 'match', match.id)

    assert [p['channel'] for p in posts] == [222]


async def test_actions_need_an_open_room(db, posts):
    staff = await _staff()
    match = await _race(await _daily(await _bot()))
    with pytest.raises(ValueError, match="isn't open"):
        await GroupRaceBoardService().post_seed_now(staff, 'match', match.id)


async def test_a_qualifier_live_race_seed_posts_now_and_cancel_removes_it(db, racetime_rooms, posts, sent):
    staff = await _staff()
    await _bot()
    pool, link = await _qualifier_race(staff)
    board = GroupRaceBoardService()
    view = await board.create(
        staff, title='Weekly Q — Race 1', scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        pool_id=pool.id, permalink_id=link.id,
    )
    view = await board.open_now(staff, 'qualifier', view.id)
    assert view.state == 'open'

    view = await board.post_seed_now(staff, 'qualifier', view.id)
    assert view.seed_posted_at is not None and _said(sent)[0] == 'Seed: https://alttpr.com/h/live'

    await board.cancel(staff, 'qualifier', view.id)
    assert not await AsyncQualifierLiveRace.exists(id=view.id)
    assert (await RacetimeRoom.get(slug=view.room_slug)).status == RaceRoomStatus.CANCELLED


async def test_a_finished_race_lists_its_results(db):
    staff = await _staff()
    match = await _race(await _daily(await _bot()), in_minutes=-60)
    room = await _room(match)
    racer = await make_user(960010, 'Andy')
    await MatchPlayers.create(match_id=match.id, user=racer, finish_rank=2, finish_time=5400)
    await RacetimeRoom.filter(id=room.id).update(
        status=RaceRoomStatus.FINISHED,
        unmatched_entrants=[{'name': 'zaruvi', 'place': 1, 'finish_seconds': 5300, 'status': 'done'}],
    )
    await Match.filter(id=match.id).update(finished_at=datetime.now(timezone.utc))
    board = GroupRaceBoardService()

    [row] = await board.list_races(staff, finished=True)
    detail = await board.get_race(staff, 'match', match.id)

    assert row.finishers == 2
    assert [(e.name, e.linked, e.place) for e in detail.entrants] == [('zaruvi', False, 1), ('Andy', True, 2)]


async def test_players_cannot_see_the_board(db):
    player = await make_user(960020, 'someone')
    with pytest.raises(PermissionError):
        await GroupRaceBoardService().list_races(player)


async def test_a_head_to_head_match_is_not_a_group_race(db):
    staff = await _staff()
    h2h = await Tournament.create(name='Main')
    match = await Match.create(tournament_id=h2h.id, title='Final')
    with pytest.raises(ValueError, match='not found'):
        await GroupRaceBoardService().get_race(staff, 'match', match.id)
