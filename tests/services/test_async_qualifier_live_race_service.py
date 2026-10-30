"""Service tests for AsyncQualifierLiveRaceService (PR 10): open, capture, score."""

from datetime import datetime, timedelta, timezone

import pytest

from application.events import EventType, event_bus
from application.services.async_qualifier.async_qualifier_live_race_service import (
    AsyncQualifierLiveRaceService,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from application.services.race_room_service import RaceRoomLifecycle
from models import (
    AsyncQualifierLiveRace,
    AsyncQualifierLiveRaceStatus,
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeBotTenant,
    RacetimeRoom,
    Role,
    User,
    UserRole,
)
from racetimebot.transport import EntrantStatus, RaceEntrant, RaceRoomEvent

pytestmark = pytest.mark.anyio


async def _staff() -> User:
    u = await User.create(discord_id=930001, username='staffy')
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _racer(discord_id: int, name: str, rtid: str) -> User:
    return await User.create(discord_id=discord_id, username=name, racetime_user_id=rtid)


async def _authorized_bot() -> RacetimeBot:
    bot = await RacetimeBot.create(
        category='alttpr', client_id='cid', client_secret='sec', name='ALTTPR Bot',
    )
    await RacetimeBotTenant.create(bot=bot, tenant_id=1, is_active=True)
    return bot


async def _live_race(qsvc, lrsvc, staff):
    now = datetime.now(timezone.utc)
    q = await qsvc.create_qualifier(
        staff, name='Live Q', opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=1),
    )
    pool = await qsvc.create_pool(staff, q.id, name='Live Pool')
    pl = await qsvc.add_permalink(staff, pool.id, url='https://seed/live-1', live_race=True)
    lr = await lrsvc.create_live_race(staff, pool.id, match_title='Race 1', permalink_id=pl.id)
    return q, pool, pl, lr


async def test_create_requires_title(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    now = datetime.now(timezone.utc)
    q = await qsvc.create_qualifier(staff, name='Q', opens_at=now - timedelta(days=1),
                                    closes_at=now + timedelta(days=1))
    pool = await qsvc.create_pool(staff, q.id, name='P')
    with pytest.raises(ValueError):
        await lrsvc.create_live_race(staff, pool.id, match_title='  ')


async def test_non_admin_cannot_create(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    now = datetime.now(timezone.utc)
    q = await qsvc.create_qualifier(staff, name='Q', opens_at=now - timedelta(days=1),
                                    closes_at=now + timedelta(days=1))
    pool = await qsvc.create_pool(staff, q.id, name='P')
    outsider = await User.create(discord_id=930099, username='out')
    with pytest.raises(PermissionError):
        await lrsvc.create_live_race(outsider, pool.id, match_title='X')


async def test_open_room_creates_room_and_slug(db, racetime_rooms):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)

    opened = await lrsvc.open_room(staff, lr.id)
    # racetime's slug, not one made up locally.
    assert opened.racetime_slug.startswith('alttpr/mock-room-')
    [(category, settings)] = racetime_rooms.started
    assert category == 'alttpr' and settings.info_user == lr.match_title
    assert opened.status == AsyncQualifierLiveRaceStatus.PENDING
    room = await RacetimeRoom.get_or_none(slug=opened.racetime_slug)
    assert room is not None and room.match_id is None


async def test_open_room_requires_authorized_bot(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)
    with pytest.raises(ValueError):
        await lrsvc.open_room(staff, lr.id)


async def test_record_finish_captures_and_scores(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    q, pool, pl, lr = await _live_race(qsvc, lrsvc, staff)
    lr = await lrsvc.open_room(staff, lr.id)
    winner = await _racer(930011, 'winner', 'rt-win')
    dnf = await _racer(930012, 'quitter', 'rt-dnf')

    captured = await lrsvc.record_finish(lr, [
        RaceEntrant(user_id='rt-win', display_name='winner',
                    status=EntrantStatus.DONE, finish_time=1000, place=1),
        RaceEntrant(user_id='rt-dnf', display_name='quitter',
                    status=EntrantStatus.DID_NOT_FINISH),
    ], actor=staff)
    assert len(captured) == 2
    by_user = {r.user_id: r for r in captured}

    win_run = by_user[winner.id]
    assert win_run.status == AsyncQualifierRunStatus.FINISHED
    assert win_run.review_status == AsyncQualifierReviewStatus.APPROVED  # skips review
    assert win_run.elapsed_seconds == 1000
    assert win_run.score == 100.0  # par == its own time → full score

    dnf_run = by_user[dnf.id]
    assert dnf_run.status == AsyncQualifierRunStatus.FORFEIT
    assert dnf_run.score == 0.0

    await pl.refresh_from_db()
    assert pl.par_time == 1000

    # Live-race runs never enter the reviewer queue (already approved).
    assert await qsvc.list_review_queue(staff, q.id) == []

    board = await qsvc.get_leaderboard(staff, q.id)
    assert any(e.username == 'winner' and e.actual == 100.0 for e in board)


async def test_record_refused_while_racing(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)
    lr = await lrsvc.open_room(staff, lr.id)
    await _racer(930021, 'a', 'rt-a')
    with pytest.raises(ValueError):
        await lrsvc.record_finish(lr, [
            RaceEntrant(user_id='rt-a', display_name='a', status=EntrantStatus.IN_PROGRESS),
        ], actor=staff)


async def test_record_finish_emits_event(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)
    lr = await lrsvc.open_room(staff, lr.id)
    await _racer(930031, 'w', 'rt-w')

    seen = []
    event_bus.subscribe_sync(lambda e: seen.append(e.event_type),
                             [EventType.ASYNC_QUALIFIER_LIVE_RACE_RECORDED])
    await lrsvc.record_finish(lr, [
        RaceEntrant(user_id='rt-w', display_name='w', status=EntrantStatus.DONE, finish_time=900),
    ], actor=staff)
    assert EventType.ASYNC_QUALIFIER_LIVE_RACE_RECORDED in seen


async def test_lifecycle_routes_finished_room_to_capture(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)
    lr = await lrsvc.open_room(staff, lr.id)
    racer = await _racer(930041, 'r', 'rt-r')

    room = await RacetimeRoom.get(slug=lr.racetime_slug)
    await RaceRoomLifecycle().handle_event(room, RaceRoomEvent(
        slug=room.slug, category=room.category, status=RaceRoomStatus.FINISHED,
        entrants=[RaceEntrant(user_id='rt-r', display_name='r',
                              status=EntrantStatus.DONE, finish_time=1200, place=1)],
    ))
    runs = await lrsvc.list_runs(staff, lr.id)
    assert len(runs) == 1 and runs[0].user_id == racer.id
    assert runs[0].status == AsyncQualifierRunStatus.FINISHED
    await room.refresh_from_db()
    assert room.status == RaceRoomStatus.FINISHED


async def test_record_finish_refuses_a_race_with_no_permalink(db):
    """"(assign later)" used to capture runs that could never score.

    They were written with ``permalink_id`` NULL, the par recompute was skipped, and
    the entrants were absent from the board with nothing explaining why.
    """
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    q, pool, _, _ = await _live_race(qsvc, lrsvc, staff)
    await _racer(930101, 'unlucky', 'rt-unlucky')

    race = await lrsvc.create_live_race(staff, pool.id, match_title='No seed',
                                        permalink_id=None)
    with pytest.raises(ValueError, match='permalink'):
        await lrsvc.record_finish(race, [
            RaceEntrant(user_id='rt-unlucky', display_name='unlucky',
                        status=EntrantStatus.DONE, finish_time=1000, place=1),
        ], actor=staff)
    assert await AsyncQualifierRun.filter(live_race_id=race.id).count() == 0, (
        'nothing may be written when the results cannot be scored'
    )


async def test_a_second_live_race_over_the_pool_cap_is_recorded_as_voided(db):
    """``runs_per_pool`` is enforced in ``start_run``, which a live race skips.

    Decided with the maintainer: record it voided rather than counting it and then
    dropping the surplus silently at scoring time.
    """
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    q, pool, first, race_one = await _live_race(qsvc, lrsvc, staff)
    assert q.runs_per_pool == 1, 'fixture precondition'
    racer = await _racer(930111, 'twice', 'rt-twice')
    entrant = RaceEntrant(user_id='rt-twice', display_name='twice',
                          status=EntrantStatus.DONE, finish_time=1000, place=1)

    await lrsvc.record_finish(race_one, [entrant], actor=staff)
    second = await qsvc.add_permalink(staff, pool.id, url='https://x/live-2',
                                      live_race=True)
    race_two = await lrsvc.create_live_race(staff, pool.id, match_title='R2',
                                            permalink_id=second.id)
    await lrsvc.record_finish(race_two, [entrant], actor=staff)

    runs = await AsyncQualifierRun.filter(qualifier_id=q.id, user_id=racer.id).order_by('id')
    assert len(runs) == 2, 'both results are kept'
    assert runs[0].reattempted is False, 'the first is within the cap'
    assert runs[1].reattempted is True, 'the second is over it, so it is voided'
    assert runs[1].reattempt_reason, 'and the racer is owed a reason'
    assert runs[1].score is None, 'a voided run carries no score'


async def test_re_recording_the_same_race_does_not_void_its_own_run(db):
    """Re-recording must be idempotent: the racer's own prior run from this race is
    already in the pool count, so it is discounted before the cap is compared."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    q, _, _, race = await _live_race(qsvc, lrsvc, staff)
    await _racer(930121, 'again', 'rt-again')
    entrant = RaceEntrant(user_id='rt-again', display_name='again',
                          status=EntrantStatus.DONE, finish_time=1000, place=1)

    await lrsvc.record_finish(race, [entrant], actor=staff)
    await lrsvc.record_finish(race, [entrant], actor=staff)

    runs = await AsyncQualifierRun.filter(live_race_id=race.id)
    assert len(runs) == 1, 'the same race writes one run per racer, not one per record'
    assert runs[0].reattempted is False, 're-recording must not void the run it updates'


async def test_a_cancelled_room_cancels_the_race(db):
    """The handler used to update only the room, leaving the race indistinguishable
    from one still to come."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)
    lr = await lrsvc.open_room(staff, lr.id)

    room = await RacetimeRoom.get(slug=lr.racetime_slug)
    await RaceRoomLifecycle().handle_event(room, RaceRoomEvent(
        slug=room.slug, category=room.category, status=RaceRoomStatus.CANCELLED,
        entrants=[],
    ))

    lr = await AsyncQualifierLiveRace.get(id=lr.id)
    assert lr.status is AsyncQualifierLiveRaceStatus.CANCELLED
    room = await RacetimeRoom.get(id=room.id)
    assert room.status is RaceRoomStatus.CANCELLED, 'the room still moves too'


async def test_cancelling_a_room_after_the_results_are_in_leaves_them_alone(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    await _authorized_bot()
    _, _, _, lr = await _live_race(qsvc, lrsvc, staff)
    lr = await lrsvc.open_room(staff, lr.id)
    await _racer(930131, 'done', 'rt-done')
    await lrsvc.record_finish(lr, [
        RaceEntrant(user_id='rt-done', display_name='done',
                    status=EntrantStatus.DONE, finish_time=1000, place=1),
    ], actor=staff)

    lr = await AsyncQualifierLiveRace.get(id=lr.id)
    await lrsvc.mark_cancelled(lr)

    lr = await AsyncQualifierLiveRace.get(id=lr.id)
    assert lr.status is AsyncQualifierLiveRaceStatus.FINISHED, (
        'cancelling the room afterwards does not un-score captured runs'
    )
