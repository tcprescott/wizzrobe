"""The racetime room bot: held results, Check result, chat commands and the seed post.

Driven through the services with a fake room sender, so what the bot said and
whom it invited are read back from ``sent``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services import race_room_bot_service
from application.services.match.match_display_service import MatchDisplayService
from application.services.race_room_bot_service import RaceRoomBotService
from application.services.race_room_service import RaceRoomLifecycle, RaceRoomService
from application.services.racetime_room_service import RacetimeRoomService
from application.utils.clients import racetime_room_actions
from models import (
    AuditLog,
    GeneratedSeeds,
    Match,
    MatchPlayers,
    RaceRoomHoldReason,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeRoom,
    Role,
    Tournament,
    User,
    UserRole,
)
from racetimebot.transport import EntrantStatus, RaceEntrant, RaceRoomEvent, RoomChat
from tests.factories import make_user

_ids = [700000]


@pytest.fixture
def sent():
    said = []

    async def sender(slug, action):
        said.append((slug, action))
        return True

    racetime_room_actions.register_sender(sender)
    race_room_bot_service._last_seed_repeat.clear()
    race_room_bot_service._forced_ids.clear()
    yield said
    racetime_room_actions.register_sender(None)


def _messages(sent):
    return [a['data']['message'] for _s, a in sent if a['action'] == 'message']


def _invites(sent):
    return [a['data']['user'] for _s, a in sent if a['action'] == 'invite_to_race']


async def _racer(name, rtid=None) -> User:
    _ids[0] += 1
    return await make_user(_ids[0], name, racetime_user_id=rtid)


async def _setup(*, bot_runs=True, users=(('alice', 'u1'), ('bob', 'u2')), scheduled_at=None, seed=None):
    bot, _ = await RacetimeBot.get_or_create(
        category='alttpr', defaults={'client_id': 'c', 'client_secret': 's', 'name': 'A'},
    )
    tournament = await Tournament.create(
        name='Main', racetime_bot_id=bot.id, racetime_default_goal='Beat the game',
        racetime_bot_runs_room=bot_runs,
    )
    match = await Match.create(tournament_id=tournament.id, scheduled_at=scheduled_at)
    players = [await _racer(name, rtid) for name, rtid in users]
    for user in players:
        await MatchPlayers.create(match_id=match.id, user_id=user.id)
    if seed:
        generated = await GeneratedSeeds.create(seed_url=seed, seed_code=['Bow', 'Hammer'])
        match.generated_seed_id = generated.id
        await match.save(update_fields=['generated_seed_id'])
    room = await RacetimeRoom.create(
        bot_id=bot.id, slug=f'alttpr/room-{match.id}', category='alttpr',
        status=RaceRoomStatus.OPEN, match_id=match.id,
    )
    return tournament, match, players, room


def _event(room, status=RaceRoomStatus.OPEN, entrants=(), *, kind='race', chat=None,
           monitors=(), started_at=None):
    return RaceRoomEvent(
        slug=room.slug, category='alttpr', status=status, entrants=list(entrants), kind=kind,
        chat=chat, monitor_ids=list(monitors), started_at=started_at,
    )


def _done(uid, ms, place):
    return RaceEntrant(user_id=uid, display_name=uid, status=EntrantStatus.DONE,
                       finish_time=ms // 1000, place=place, finish_ms=ms)


async def _staff() -> User:
    staff = await _racer('staffer')
    await UserRole.create(user_id=staff.id, role=Role.STAFF)
    return staff


async def test_clean_result_records_itself(db, sent):
    _t, match, players, room = await _setup()
    await RaceRoomService().record_finish(room, [_done('u1', 3_600_000, 1), _done('u2', 3_700_000, 2)])

    ranks = {mp.user_id: mp.finish_rank for mp in await MatchPlayers.filter(match_id=match.id)}
    assert ranks == {players[0].id: 1, players[1].id: 2}
    await room.refresh_from_db()
    assert room.status == RaceRoomStatus.FINISHED and room.held_reason is None


async def test_close_finish_is_held_and_staff_confirm_it(db, sent):
    _t, match, players, room = await _setup()
    await RaceRoomService().record_finish(room, [_done('u1', 3_600_000, 1), _done('u2', 3_600_300, 2)])

    await room.refresh_from_db()
    assert room.status == RaceRoomStatus.FINISHED
    assert room.held_reason == RaceRoomHoldReason.CLOSE_FINISH
    assert all(mp.finish_rank is None for mp in await MatchPlayers.filter(match_id=match.id))
    assert await AuditLog.filter(action='race_room.result_held').exists()

    staff = await _staff()
    held = await RaceRoomService().held_result(staff, match.id)
    assert [p.place for p in held.players] == [1, 2]
    assert held.players[0].finish_seconds == 3600

    mps = {mp.user_id: mp.id for mp in await MatchPlayers.filter(match_id=match.id)}
    # Staff reviewed the footage: Bob actually won.
    placements = {mps[players[0].id]: (2, 3600), mps[players[1].id]: (1, 3600)}
    await RaceRoomService().confirm_held_result(staff, match.id, placements)

    ranks = {mp.user_id: mp.finish_rank for mp in await MatchPlayers.filter(match_id=match.id)}
    assert ranks == {players[0].id: 2, players[1].id: 1}
    await room.refresh_from_db()
    assert room.held_reason is None

    with pytest.raises(ValueError):
        await RaceRoomService().confirm_held_result(staff, match.id, placements)


async def test_confirm_validates_places_and_permission(db, sent):
    _t, match, players, room = await _setup()
    await RaceRoomService().record_finish(
        room, [_done('u1', 3_600_000, 1), RaceEntrant(user_id='u2', display_name='u2', status=EntrantStatus.DID_NOT_FINISH)],
    )
    mps = {mp.user_id: mp.id for mp in await MatchPlayers.filter(match_id=match.id)}
    staff = await _staff()
    with pytest.raises(ValueError, match='Places must run'):
        await RaceRoomService().confirm_held_result(staff, match.id, {mps[players[0].id]: (2, 3600), mps[players[1].id]: (None, None)})
    with pytest.raises(ValueError, match='Every finisher'):
        await RaceRoomService().confirm_held_result(staff, match.id, {mps[players[0].id]: (1, None), mps[players[1].id]: (None, None)})
    with pytest.raises(PermissionError):
        await RaceRoomService().confirm_held_result(players[0], match.id, {mps[players[0].id]: (1, 3600), mps[players[1].id]: (None, None)})

    await RaceRoomService().confirm_held_result(staff, match.id, {mps[players[0].id]: (1, 3600), mps[players[1].id]: (None, None)})
    ranks = {mp.user_id: mp.finish_rank for mp in await MatchPlayers.filter(match_id=match.id)}
    assert ranks == {players[0].id: 1, players[1].id: None}


async def test_a_tournament_the_bot_doesnt_run_still_records_everything(db, sent):
    _t, match, _players, room = await _setup(bot_runs=False)
    await RaceRoomService().record_finish(room, [_done('u1', 3_600_000, 1), _done('u2', 3_600_100, 2)])
    await room.refresh_from_db()
    assert room.held_reason is None
    assert {mp.finish_rank for mp in await MatchPlayers.filter(match_id=match.id)} == {1, 2}


async def test_board_rows_show_the_hold_and_unlinked_players(db, sent):
    _t, held_match, _p, room = await _setup()
    await RaceRoomService().record_finish(room, [_done('u1', 3_600_000, 1), _done('u2', 3_600_300, 2)])
    row = await MatchDisplayService().get_match_for_display(held_match.id)
    assert row['racetime_held']['label'] == 'Close finish'

    tournament = await Tournament.get(id=held_match.tournament_id)
    upcoming = await Match.create(tournament_id=tournament.id, scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))
    cleo = await _racer('cleo')
    await MatchPlayers.create(match_id=upcoming.id, user_id=cleo.id)
    row = await MatchDisplayService().get_match_for_display(upcoming.id)
    assert row['racetime_unlinked'] == [cleo.preferred_name]
    # The board loads players through a trimmed user prefetch; the bot fields must survive it.
    rows = await MatchDisplayService().get_matches_for_display(match_ids=[held_match.id, upcoming.id])
    by_id = {r['id']: r for r in rows}
    assert by_id[upcoming.id]['racetime_unlinked'] == [cleo.preferred_name]
    assert by_id[held_match.id]['racetime_held']['reason'] == 'close_finish'


async def test_joining_invites_missing_players_and_greets_once(db, sent):
    _t, _match, _players, room = await _setup(users=(('alice', 'u1'), ('bob', 'u2'), ('cleo', None)))
    entrant = RaceEntrant(user_id='u1', display_name='alice', status=EntrantStatus.IN_PROGRESS)
    await RaceRoomLifecycle().handle_event(room, _event(room, entrants=[entrant], kind='joined'))
    await RaceRoomLifecycle().handle_event(room, _event(room, entrants=[entrant], kind='joined'))

    assert _invites(sent) == ['u2', 'u2']
    greetings = [m for m in _messages(sent) if m.startswith('Hi!')]
    assert len(greetings) == 1
    assert "can't invite cleo" in greetings[0]


async def test_seed_posts_once_when_due(db, sent):
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    _t, match, _p, room = await _setup(scheduled_at=start, seed='https://alttpr.com/h/abc')
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    assert _messages(sent) == ['Seed: https://alttpr.com/h/abc · Code: Bow · Hammer']


async def test_seed_waits_for_its_time(db, sent):
    start = datetime.now(timezone.utc) + timedelta(minutes=45)
    _t, _match, _p, room = await _setup(scheduled_at=start, seed='https://alttpr.com/h/abc')
    await RaceRoomBotService().post_due_seeds(datetime.now(timezone.utc))
    assert _messages(sent) == []

    chat = RoomChat(user_id='u1', display_name='alice', text='!seed')
    await RaceRoomLifecycle().handle_event(room, _event(room, kind='chat', chat=chat))
    assert _messages(sent) == ['The seed posts in 35 minutes.']


async def test_forceroll_needs_a_monitor_or_staff(db, sent):
    start = datetime.now(timezone.utc) + timedelta(minutes=45)
    _t, _match, _p, room = await _setup(scheduled_at=start, seed='https://alttpr.com/h/abc')
    chat = RoomChat(user_id='u1', display_name='alice', text='!forceroll')
    await RaceRoomLifecycle().handle_event(room, _event(room, kind='chat', chat=chat))
    assert _messages(sent) == ['Only room monitors and staff can use !forceroll.']

    sent.clear()
    mod = RoomChat(user_id='m1', display_name='Mod', text='!forceroll')
    await RaceRoomLifecycle().handle_event(room, _event(room, kind='chat', chat=mod, monitors=['m1']))
    assert _messages(sent) == ['Seed: https://alttpr.com/h/abc · Code: Bow · Hammer']

    sent.clear()
    await RaceRoomLifecycle().handle_event(room, _event(room, kind='chat', chat=mod, monitors=['m1']))
    assert _messages(sent) == ['The seed is already out. Type !seed to see it again.']


async def test_fpa_is_logged_acknowledged_and_holds_the_result(db, sent):
    _t, match, _p, room = await _setup()
    started = datetime.now(timezone.utc) - timedelta(minutes=25)
    runners = [RaceEntrant(user_id=u, display_name=u, status=EntrantStatus.IN_PROGRESS) for u in ('u1', 'u2')]
    fpa = RoomChat(user_id='u2', display_name='bob', text='!fpa game crashed')
    stranger = RoomChat(user_id='x9', display_name='lurker', text='!fpa')

    await RaceRoomLifecycle().handle_event(room, _event(room, RaceRoomStatus.IN_PROGRESS, runners, kind='chat', chat=stranger, started_at=started))
    await RaceRoomLifecycle().handle_event(room, _event(room, RaceRoomStatus.IN_PROGRESS, runners, kind='chat', chat=fpa, started_at=started))
    await RaceRoomLifecycle().handle_event(room, _event(room, RaceRoomStatus.IN_PROGRESS, runners, kind='chat', chat=fpa, started_at=started))

    messages = _messages(sent)
    assert messages[0] == 'Only runners in this race can call !fpa.'
    assert messages[1].startswith('FPA noted at 0:25:0')
    assert len(messages) == 2, 'a repeated !fpa inside a minute is ignored'

    await room.refresh_from_db()
    assert len(room.fpa_calls) == 1 and room.fpa_calls[0]['note'] == 'game crashed'
    assert await AuditLog.filter(action='race_room.fpa_called').exists()

    await RaceRoomService().record_finish(room, [_done('u1', 3_600_000, 1), _done('u2', 3_700_000, 2)])
    await room.refresh_from_db()
    assert room.held_reason == RaceRoomHoldReason.FPA


async def test_commands_are_ignored_where_the_bot_doesnt_run_the_room(db, sent):
    _t, _match, _p, room = await _setup(bot_runs=False)
    chat = RoomChat(user_id='u1', display_name='alice', text='!seed')
    await RaceRoomLifecycle().handle_event(room, _event(room, kind='chat', chat=chat))
    assert sent == []


async def test_the_bot_sits_only_in_rooms_it_runs(db, sent):
    _t, _match, _p, room = await _setup()
    _t2, _m2, _p2, other = await _setup(bot_runs=False, users=(('cara', 'u7'),))
    rooms = await RacetimeRoomService().list_bot_run_rooms('alttpr')
    assert rooms == [(room.slug, RaceRoomStatus.OPEN)]
    assert other.slug not in dict(rooms)


async def test_a_room_the_bot_runs_opens_for_unlinked_players(db, sent):
    bot, _ = await RacetimeBot.get_or_create(
        category='alttpr', defaults={'client_id': 'c', 'client_secret': 's', 'name': 'A'},
    )
    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    for runs in (False, True):
        tournament = await Tournament.create(
            name=f'T{runs}', racetime_bot_id=bot.id, racetime_default_goal='Beat the game',
            racetime_auto_create_rooms=True, racetime_bot_runs_room=runs,
        )
        match = await Match.create(tournament_id=tournament.id, scheduled_at=start)
        await MatchPlayers.create(match_id=match.id, user_id=(await _racer(f'p{runs}', None)).id)
        match = await Match.get(id=match.id).prefetch_related('tournament', 'players', 'players__user')
        room = await RaceRoomService().auto_open_if_eligible(match, now=datetime.now(timezone.utc))
        assert (room is not None) is runs
