"""Racetime room lifecycle tests (PR 6).

Mock-driven, end to end: room create/open (idempotent), in-progress, finish with
result capture (including forfeit / no-show / one-finisher terminal states and
unlinked-handle reconcile), cancel, the transport→service lifecycle adapter, the
manual-create permission gate, and the auto-open worker's eligibility rules.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.services import race_room_worker
from application.services.race_room_service import RaceRoomLifecycle, RaceRoomService
from models import (
    AuditLog,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeRoom,
    Role,
    Tournament,
    User,
    UserRole,
)
from racetimebot.transport import EntrantStatus, RaceEntrant, RaceRoomEvent


async def _bot(category: str = 'alttpr') -> RacetimeBot:
    return await RacetimeBot.create(
        category=category, client_id='c', client_secret='s', name='A',
    )


async def _tournament(bot, *, auto_open=False, lead=30, seed=None) -> Tournament:
    return await Tournament.create(
        name='T', racetime_bot_id=bot.id,
        racetime_auto_create_rooms=auto_open, room_open_minutes_before=lead,
        seed_generator=seed, racetime_default_goal='Beat the game',
    )


_next_discord_id = [500000]


async def _user(name, rtid=None) -> User:
    _next_discord_id[0] += 1
    return await User.create(
        username=name, discord_id=_next_discord_id[0], racetime_user_id=rtid,
    )


async def _match(tournament, *, scheduled_at=None) -> Match:
    return await Match.create(tournament_id=tournament.id, scheduled_at=scheduled_at)


async def _add_player(match, user) -> MatchPlayers:
    return await MatchPlayers.create(match_id=match.id, user_id=user.id)


async def _match_with_players(tournament, users, *, scheduled_at=None) -> Match:
    match = await _match(tournament, scheduled_at=scheduled_at)
    for u in users:
        await _add_player(match, u)
    return await Match.get(id=match.id).prefetch_related('tournament', 'players', 'players__user')


async def test_create_room_is_idempotent(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    svc = RaceRoomService()

    room1 = await svc.create_room_for_match(match)
    room2 = await svc.create_room_for_match(match)

    assert room1.id == room2.id
    assert room1.status == RaceRoomStatus.OPEN
    assert room1.category == 'alttpr'
    assert room1.slug.startswith('alttpr/mock-room-')
    assert await RacetimeRoom.filter(match_id=match.id).count() == 1


async def test_create_room_requires_bot(db):
    tourn = await Tournament.create(name='NoBot')
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    with pytest.raises(ValueError):
        await RaceRoomService().create_room_for_match(match)


async def test_manual_create_requires_sync_permission(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    plain = await _user('plain')

    with pytest.raises(PermissionError):
        await RaceRoomService().manual_create_room(plain, match.id)


async def test_manual_create_allows_sync_admin(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    admin = await _user('admin')
    await UserRole.create(user_id=admin.id, role=Role.SYNC_ADMIN)

    room = await RaceRoomService().manual_create_room(admin, match.id)
    assert room.match_id == match.id


async def test_mark_in_progress_starts_match(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    svc = RaceRoomService()
    room = await svc.create_room_for_match(match)

    await svc.mark_in_progress(room)

    room = await RacetimeRoom.get(id=room.id)
    match = await Match.get(id=match.id)
    assert room.status == RaceRoomStatus.IN_PROGRESS
    assert match.started_at is not None


async def test_cancel_room(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    svc = RaceRoomService()
    room = await svc.create_room_for_match(match)

    await svc.cancel_room(room, reason='called off')

    assert (await RacetimeRoom.get(id=room.id)).status == RaceRoomStatus.CANCELLED


async def test_record_finish_captures_results(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    ua = await _user('a', 'rt-a')
    ub = await _user('b', 'rt-b')
    match = await _match_with_players(tourn, [ua, ub])
    svc = RaceRoomService()
    room = await svc.create_room_for_match(match)

    entrants = [
        RaceEntrant(user_id='rt-a', display_name='a', status=EntrantStatus.DONE, finish_time=3600),
        RaceEntrant(user_id='rt-b', display_name='b', status=EntrantStatus.DONE, finish_time=3720),
    ]
    await svc.record_finish(room, entrants)

    assert (await RacetimeRoom.get(id=room.id)).status == RaceRoomStatus.FINISHED
    assert (await Match.get(id=match.id)).finished_at is not None
    pa = await MatchPlayers.get(match_id=match.id, user_id=ua.id)
    pb = await MatchPlayers.get(match_id=match.id, user_id=ub.id)
    assert (pa.finish_rank, pa.finish_time) == (1, 3600)
    assert (pb.finish_rank, pb.finish_time) == (2, 3720)


async def test_record_finish_tells_every_bracket_site(db, monkeypatch):
    from application.services import bracket_sources

    told = []

    async def record(match, actor):
        told.append(match.id)

    monkeypatch.setattr(bracket_sources, 'on_match_confirmed', record)
    bot = await _bot()
    tourn = await _tournament(bot)
    ua = await _user('a', 'rt-a')
    ub = await _user('b', 'rt-b')
    match = await _match_with_players(tourn, [ua, ub])
    svc = RaceRoomService()
    room = await svc.create_room_for_match(match)

    await svc.record_finish(room, [
        RaceEntrant(user_id='rt-a', display_name='a', status=EntrantStatus.DONE, finish_time=3600),
        RaceEntrant(user_id='rt-b', display_name='b', status=EntrantStatus.DONE, finish_time=3720),
    ])

    assert told == [match.id]


async def test_record_finish_forfeit_and_one_finisher(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    ua = await _user('a', 'rt-a')
    ub = await _user('b', 'rt-b')
    match = await _match_with_players(tourn, [ua, ub])
    svc = RaceRoomService()
    room = await svc.create_room_for_match(match)

    entrants = [
        RaceEntrant(user_id='rt-a', display_name='a', status=EntrantStatus.DONE, finish_time=3600),
        RaceEntrant(user_id='rt-b', display_name='b', status=EntrantStatus.DID_NOT_FINISH),
    ]
    await svc.record_finish(room, entrants)

    pa = await MatchPlayers.get(match_id=match.id, user_id=ua.id)
    pb = await MatchPlayers.get(match_id=match.id, user_id=ub.id)
    assert pa.finish_rank == 1
    assert pb.finish_rank is None and pb.finish_time is None


async def test_record_finish_notes_unmatched_handles(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    ua = await _user('a', 'rt-a')
    match = await _match_with_players(tourn, [ua])
    svc = RaceRoomService()
    room = await svc.create_room_for_match(match)

    entrants = [
        RaceEntrant(user_id='rt-a', display_name='a', status=EntrantStatus.DONE, finish_time=3600),
        RaceEntrant(user_id='rt-stranger', display_name='Stranger', status=EntrantStatus.DONE, finish_time=3500),
    ]
    await svc.record_finish(room, entrants)

    log = await AuditLog.filter(action='race_room.result_recorded').order_by('-id').first()
    assert log is not None
    details = log.details if isinstance(log.details, dict) else json.loads(log.details)
    assert 'Stranger' in details.get('unmatched_handles', [])


async def test_lifecycle_adapter_routes_events(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    room = await RaceRoomService().create_room_for_match(match)
    adapter = RaceRoomLifecycle()

    await adapter.handle_event(
        room, RaceRoomEvent(slug=room.slug, category='alttpr', status=RaceRoomStatus.IN_PROGRESS),
    )
    assert (await RacetimeRoom.get(id=room.id)).status == RaceRoomStatus.IN_PROGRESS

    await adapter.handle_event(
        await RacetimeRoom.get(id=room.id),
        RaceRoomEvent(slug=room.slug, category='alttpr', status=RaceRoomStatus.CANCELLED),
    )
    assert (await RacetimeRoom.get(id=room.id)).status == RaceRoomStatus.CANCELLED


async def test_auto_open_creates_room_for_eligible_match(db):
    bot = await _bot()
    tourn = await _tournament(bot, auto_open=True, lead=30)
    soon = datetime.now(timezone.utc) + timedelta(minutes=10)
    await _match_with_players(tourn, [await _user('a', 'rt-a'), await _user('b', 'rt-b')], scheduled_at=soon)

    await race_room_worker._tick()

    assert await RacetimeRoom.all().count() == 1


async def test_auto_open_skips_unlinked_entrant(db):
    bot = await _bot()
    tourn = await _tournament(bot, auto_open=True, lead=30)
    soon = datetime.now(timezone.utc) + timedelta(minutes=10)
    await _match_with_players(
        tourn, [await _user('a', 'rt-a'), await _user('b')],
        scheduled_at=soon,
    )

    await race_room_worker._tick()

    assert await RacetimeRoom.all().count() == 0


async def test_auto_open_skips_outside_lead_window(db):
    bot = await _bot()
    tourn = await _tournament(bot, auto_open=True, lead=30)
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    await _match_with_players(tourn, [await _user('a', 'rt-a')], scheduled_at=later)

    await race_room_worker._tick()

    assert await RacetimeRoom.all().count() == 0


async def test_auto_open_is_idempotent(db):
    bot = await _bot()
    tourn = await _tournament(bot, auto_open=True, lead=30)
    soon = datetime.now(timezone.utc) + timedelta(minutes=10)
    await _match_with_players(tourn, [await _user('a', 'rt-a')], scheduled_at=soon)

    await race_room_worker._tick()
    await race_room_worker._tick()

    assert await RacetimeRoom.all().count() == 1


async def test_room_is_opened_on_racetime_with_the_profile(db, racetime_rooms):
    """The row holds racetime's slug, and startrace got the profile's settings."""
    from models import RaceRoomProfile

    bot = await _bot()
    tourn = await _tournament(bot)
    tourn.race_room_profile = await RaceRoomProfile.create(
        name='House', goal='All Dungeons', invitational=True, start_delay=30,
        streaming_required=True,
    )
    await tourn.save()
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])

    room = await RaceRoomService().create_room_for_match(match, attach_seed=False)

    [(category, settings)] = racetime_rooms.started
    assert category == 'alttpr'
    assert settings.goal == 'All Dungeons'
    assert settings.invitational and settings.streaming_required
    assert settings.start_delay == 30
    assert room.slug.startswith('alttpr/mock-room-')
    assert room.url == f'https://racetime.gg/{room.slug}'
    assert room.status == RaceRoomStatus.OPEN


async def test_a_refused_startrace_leaves_nothing_behind(db, racetime_rooms, monkeypatch, stub_discord_queue):
    from application.utils.clients.racetime_client import RacetimeAPIError

    async def refuse(**kwargs):
        raise RacetimeAPIError('racetime refused (400): bad goal')

    monkeypatch.setattr(racetime_rooms, 'start_race', refuse)
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])

    with pytest.raises(ValueError, match=r"racetime\.gg didn't open the room"):
        await RaceRoomService().create_room_for_match(match)

    assert await RacetimeRoom.filter(match_id=match.id).count() == 0
    assert not await AuditLog.filter(action__startswith='race_room.').exists()
    assert stub_discord_queue == []


async def test_a_room_needs_a_goal(db, racetime_rooms):
    bot = await _bot()
    tourn = await _tournament(bot)
    tourn.racetime_default_goal = None
    await tourn.save()
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])

    with pytest.raises(ValueError, match='Set a racetime goal'):
        await RaceRoomService().create_room_for_match(match)
    assert racetime_rooms.started == []


async def test_concurrent_openers_open_one_room(db, racetime_rooms):
    """The poll and the series push racing for one match must not open two rooms."""
    import asyncio

    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    svc = RaceRoomService()

    rooms = await asyncio.gather(
        svc.create_room_for_match(match, attach_seed=False),
        svc.create_room_for_match(match, attach_seed=False),
    )

    assert rooms[0].id == rooms[1].id
    assert len(racetime_rooms.started) == 1


async def test_creating_a_room_is_refused_with_the_flag_off(db, racetime_rooms):
    from application.errors import FeatureDisabledError
    from application.services.feature_flag_service import reset_flag_cache
    from models import FeatureFlag, TenantFeatureFlag
    from tests.conftest import DEFAULT_TEST_TENANT_ID

    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.RACETIME_ROOMS.value,
    ).update(enabled=False)
    reset_flag_cache()
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    admin = await _user('admin')
    await UserRole.create(user_id=admin.id, role=Role.SYNC_ADMIN)

    with pytest.raises(FeatureDisabledError):
        await RaceRoomService().manual_create_room(admin, match.id)
    with pytest.raises(FeatureDisabledError):
        await RaceRoomService().open_rooms_for_player(admin)
    assert racetime_rooms.started == []


async def test_opening_a_room_dms_each_player_a_button_into_it(db, stub_discord_queue, monkeypatch):
    from unittest.mock import AsyncMock

    sent = []

    async def _send(user_id, message, *args, **kwargs):
        sent.append((user_id, message, kwargs))
        return True, 'ok'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService.send_dm',
        AsyncMock(side_effect=_send),
    )
    bot = await _bot()
    tourn = await _tournament(bot)
    a, b = await _user('a', 'rt-a'), await _user('b', 'rt-b')
    muted = await _user('muted', 'rt-m')
    muted.dm_notifications = False
    await muted.save()
    match = await _match_with_players(tourn, [a, b, muted])

    room = await RaceRoomService().create_room_for_match(match, attach_seed=False)
    for coro in list(stub_discord_queue):
        await coro

    # Discord off only drops the Discord copy, inside send_dm; devices still need the call.
    assert {user_id for user_id, _, _ in sent} == {a.discord_id, b.discord_id, muted.discord_id}
    for _, message, kwargs in sent:
        assert message.startswith('Your race room for **T** is open.')
        assert kwargs['link'].url == room.url == f'https://racetime.gg/{room.slug}'


async def test_an_existing_room_is_not_announced_twice(db, stub_discord_queue):
    bot = await _bot()
    tourn = await _tournament(bot)
    match = await _match_with_players(tourn, [await _user('a', 'rt-a')])
    svc = RaceRoomService()
    await svc.create_room_for_match(match, attach_seed=False)
    queued = len(stub_discord_queue)

    await svc.create_room_for_match(match, attach_seed=False)

    assert len(stub_discord_queue) == queued


async def test_a_player_sees_only_their_own_open_rooms(db):
    bot = await _bot()
    tourn = await _tournament(bot)
    a, b, c = await _user('a', 'rt-a'), await _user('b', 'rt-b'), await _user('c', 'rt-c')
    svc = RaceRoomService()
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    soon = datetime.now(timezone.utc) + timedelta(minutes=20)
    first = await svc.create_room_for_match(
        await _match_with_players(tourn, [a, b], scheduled_at=later), attach_seed=False,
    )
    second = await svc.create_room_for_match(
        await _match_with_players(tourn, [a, c], scheduled_at=soon), attach_seed=False,
    )
    done = await svc.create_room_for_match(
        await _match_with_players(tourn, [a, b], scheduled_at=soon), attach_seed=False,
    )
    await svc.cancel_room(done)

    assert [r.id for r in await svc.open_rooms_for_player(a)] == [second.id, first.id]
    assert [r.id for r in await svc.open_rooms_for_player(b)] == [first.id]


async def test_auto_open_skips_a_tournament_with_no_goal(db, racetime_rooms):
    bot = await _bot()
    tourn = await _tournament(bot, auto_open=True)
    tourn.racetime_default_goal = None
    await tourn.save()
    match = await _match_with_players(
        tourn, [await _user('a', 'rt-a')],
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    assert await RaceRoomService().auto_open_if_eligible(
        match, now=datetime.now(timezone.utc),
    ) is None
    assert racetime_rooms.started == []
