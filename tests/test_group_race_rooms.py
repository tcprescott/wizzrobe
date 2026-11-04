"""Group races: open rooms with no player list, a Discord post, lock at seed, everyone recorded."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services import race_room_bot_service
from application.services.discord.discord_service import DiscordService
from application.services.group_race_service import GroupRaceService
from application.services.race_room_service import RaceRoomService
from application.services.tournament_service import TournamentService
from application.utils.clients import racetime_room_actions
from models import (
    AuditLog,
    GeneratedSeeds,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeRoom,
    Role,
    Tenant,
    Tournament,
    User,
    UserRole,
)
from racetimebot.transport import EntrantStatus, RaceEntrant
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

_ids = [810000]


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
                      'embed': embed, 'link': link, 'role': ping_role_id})
        return True, 'ok'

    monkeypatch.setattr(DiscordService, 'post_to_channel', post)
    return calls


async def _racer(name, rtid=None) -> User:
    _ids[0] += 1
    return await make_user(_ids[0], name, racetime_user_id=rtid)


async def _daily(*, lock=False, channel='222', role='333', guild=111) -> Tournament:
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=guild)
    bot, _ = await RacetimeBot.get_or_create(
        category='alttpr', defaults={'client_id': 'c', 'client_secret': 's', 'name': 'A'},
    )
    return await Tournament.create(
        name='ALTTPR Daily', racetime_bot_id=bot.id, racetime_default_goal='Beat the game',
        racetime_group_race=True, racetime_bot_runs_room=True, racetime_auto_create_rooms=True,
        room_open_minutes_before=60, racetime_lock_at_seed=lock,
        racetime_announce_channel_id=channel, racetime_announce_role_id=role,
    )


async def _race(tournament, *, title='ALTTPR Daily — Open', in_minutes=30) -> Match:
    match = await Match.create(
        tournament_id=tournament.id, title=title,
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=in_minutes),
    )
    return await Match.get(id=match.id).prefetch_related('tournament', 'players', 'players__user')


def _done(uid, name, seconds, place):
    return RaceEntrant(user_id=uid, display_name=name, status=EntrantStatus.DONE,
                       finish_time=seconds, place=place, finish_ms=seconds * 1000)


async def test_a_group_race_opens_with_no_players_and_open_to_all(db, racetime_rooms, posts):
    match = await _race(await _daily())

    room = await RaceRoomService().auto_open_if_eligible(match, now=datetime.now(timezone.utc))

    assert room is not None and room.status == RaceRoomStatus.OPEN
    [(_category, settings)] = racetime_rooms.started
    assert settings.invitational is False and settings.team_race is False


async def test_a_head_to_head_match_with_no_players_still_waits(db, racetime_rooms):
    tournament = await _daily()
    tournament.racetime_group_race = False
    await tournament.save()
    match = await _race(tournament)

    assert await RaceRoomService().auto_open_if_eligible(match, now=datetime.now(timezone.utc)) is None
    assert racetime_rooms.started == []


async def test_co_op_in_the_title_opens_a_team_race(db, racetime_rooms, posts):
    match = await _race(await _daily(), title='ALTTPR Daily (Co-op)')

    await RaceRoomService().create_room_for_match(match)

    [(_category, settings)] = racetime_rooms.started
    assert settings.team_race is True


async def test_the_open_room_is_posted_once_with_the_role_ping(db, racetime_rooms, posts):
    match = await _race(await _daily())

    room = await RaceRoomService().create_room_for_match(match)
    await GroupRaceService().announce(room, match, actor=await _racer('staff'))
    await GroupRaceService().announce_pending()

    [post] = posts
    assert (post['guild'], post['channel'], post['role']) == (111, 222, 333)
    assert post['message'].startswith('<@&333> The race room for ALTTPR Daily — Open is open.')
    assert post['link'].url == room.url
    await room.refresh_from_db()
    assert room.announced_at is not None
    assert await AuditLog.filter(action='race_room.announced').count() == 1


async def test_a_failed_post_is_retried_by_the_worker(db, racetime_rooms, monkeypatch):
    tries = []

    async def flaky(self, *args, **kwargs):
        tries.append(1)
        return (len(tries) > 1), 'offline'

    monkeypatch.setattr(DiscordService, 'post_to_channel', flaky)
    room = await RaceRoomService().create_room_for_match(await _race(await _daily()))
    await room.refresh_from_db()
    assert room.announced_at is None

    await GroupRaceService().announce_pending()

    await room.refresh_from_db()
    assert len(tries) == 2 and room.announced_at is not None


async def test_no_channel_means_no_post(db, racetime_rooms, posts):
    await RaceRoomService().create_room_for_match(await _race(await _daily(channel=None)))
    assert posts == []


async def _open_room(match) -> RacetimeRoom:
    generated = await GeneratedSeeds.create(seed_url='https://alttpr.com/h/abc', seed_code=['Bow'])
    match.generated_seed_id = generated.id
    await match.save(update_fields=['generated_seed_id'])
    return await RacetimeRoom.create(
        bot_id=match.tournament.racetime_bot_id, slug=f'alttpr/daily-{match.id}', category='alttpr',
        status=RaceRoomStatus.OPEN, match_id=match.id,
    )


async def test_the_room_locks_as_the_seed_posts_when_switched_on(db, sent):
    match = await _race(await _daily(lock=True), in_minutes=5)
    room = await _open_room(match)

    await _post_due_seeds()

    actions = [a for _s, a in sent]
    assert {'action': 'make_invitational'} in actions
    messages = [a['data']['message'] for a in actions if a['action'] == 'message']
    assert messages[0].startswith('Seed: https://alttpr.com/h/abc')
    assert messages[-1] == 'This room is now locked. Late entries are not permitted.'
    await room.refresh_from_db()
    assert room.locked_at is not None


async def test_the_room_stays_open_when_locking_is_off(db, sent):
    match = await _race(await _daily(lock=False), in_minutes=5)
    await _open_room(match)

    await _post_due_seeds()

    assert {'action': 'make_invitational'} not in [a for _s, a in sent]


async def _post_due_seeds():
    bot = race_room_bot_service.RaceRoomBotService()
    await bot.post_due_seeds(datetime.now(timezone.utc))


async def test_finish_records_linked_entrants_and_lists_the_rest(db, sent, stub_discord_queue):
    match = await _race(await _daily(), in_minutes=-60)
    room = await _open_room(match)
    alice, bob = await _racer('alice', 'rt-a'), await _racer('bob', 'rt-b')

    await RaceRoomService().record_finish(room, [
        _done('rt-a', 'Alice', 3600, 1),
        _done('rt-x', 'Stranger', 3700, 2),
        _done('rt-b', 'Bob', 3800, 3),
        RaceEntrant(user_id='rt-y', display_name='Quitter', status=EntrantStatus.DID_NOT_FINISH),
    ])

    rows = {mp.user_id: (mp.finish_rank, mp.finish_time) for mp in await MatchPlayers.filter(match_id=match.id)}
    assert rows == {alice.id: (1, 3600), bob.id: (3, 3800)}
    await room.refresh_from_db()
    assert room.status == RaceRoomStatus.FINISHED and room.held_reason is None
    assert [e['name'] for e in room.unmatched_entrants] == ['Stranger', 'Quitter']
    assert room.unmatched_entrants[0]['place'] == 2
    await match.refresh_from_db()
    assert match.finished_at is not None


async def test_an_fpa_does_not_hold_a_group_race(db, sent, stub_discord_queue):
    match = await _race(await _daily(), in_minutes=-60)
    room = await _open_room(match)
    room.fpa_calls = [{'racetime_user_id': 'rt-a', 'name': 'Alice', 'at': datetime.now(timezone.utc).isoformat()}]
    await room.save()
    await _racer('alice', 'rt-a')

    await RaceRoomService().record_finish(room, [_done('rt-a', 'Alice', 3600, 1)])

    await room.refresh_from_db()
    assert room.held_reason is None
    assert await MatchPlayers.filter(match_id=match.id, finish_rank=1).count() == 1


async def test_turning_group_race_on_turns_on_auto_rooms_and_the_bot(db):
    staff = await _racer('staffer')
    await UserRole.create(user_id=staff.id, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    bot, _ = await RacetimeBot.get_or_create(
        category='alttpr', defaults={'client_id': 'c', 'client_secret': 's', 'name': 'A'},
    )
    tournament = await Tournament.create(name='Weekly', racetime_bot_id=bot.id)

    await TournamentService().update_tournament(
        tournament, racetime_group_race=True, racetime_bot_runs_room=False,
        racetime_announce_channel_id=' 12345 ', actor=staff,
    )

    await tournament.refresh_from_db()
    assert tournament.racetime_group_race and tournament.racetime_auto_create_rooms
    assert tournament.racetime_bot_runs_room
    assert tournament.racetime_announce_channel_id == '12345'


async def test_a_channel_must_be_a_discord_id(db):
    staff = await _racer('staffer2')
    await UserRole.create(user_id=staff.id, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    tournament = await Tournament.create(name='Weekly')

    with pytest.raises(ValueError, match="isn't a Discord id"):
        await TournamentService().update_tournament(
            tournament, racetime_announce_channel_id='#daily-races', actor=staff,
        )
