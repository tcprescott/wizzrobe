"""Recording a live race by hand — the audit's F10 remedy, and the run's own clock.

`record_finish` is reachable only from the inbound racetime FINISHED event, so a
dropped connection, a bot restart mid-race or a room closed by hand left the race
stuck and its entrants unscored, with nothing in the admin page or REST able to fix
it. `record_manual_finish` is that fix, and it funnels through the same capture, so
these tests are mostly about proving the two paths cannot diverge: the permalink
requirement, the pool cap, the par recompute and the FINISHED transition all still
apply when a human types the results.

Also here: the two smaller F10 findings. Live-race runs stamped `started_at` at
*record* time, so every one read Started == Finished with a blank Timed column; and
an entrant whose racetime account matched nobody was mentioned only in the audit
detail, which is not a place anyone looks for work.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier.async_qualifier_live_race_service import (
    AsyncQualifierLiveRaceService,
    ManualResult,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from application.services.audit_service import AuditActions
from models import (
    AsyncQualifierLiveRace,
    AsyncQualifierLiveRaceStatus,
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    AuditLog,
    Role,
    User,
    UserRole,
)
from racetimebot.transport import EntrantStatus, RaceEntrant

pytestmark = pytest.mark.anyio


async def _staff(discord_id: int = 931001, name: str = 'manualstaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _racer(discord_id: int, name: str, rtid: str | None = None) -> User:
    return await User.create(discord_id=discord_id, username=name, racetime_user_id=rtid)


async def _live_race(qsvc, lrsvc, staff, *, runs_per_pool: int = 1, permalink: bool = True):
    now = datetime.now(timezone.utc)
    q = await qsvc.create_qualifier(
        staff, name='Manual Q', opens_at=now - timedelta(days=1),
        closes_at=now + timedelta(days=1), runs_per_pool=runs_per_pool,
    )
    pool = await qsvc.create_pool(staff, q.id, name='Manual Pool')
    pl = await qsvc.add_permalink(
        staff, pool.id, url='https://seed.test/manual-1', live_race=True)
    race = await lrsvc.create_live_race(
        staff, pool.id, match_title='Missed Race',
        permalink_id=pl.id if permalink else None,
    )
    return q, pool, pl, race


async def test_a_hand_recorded_race_scores_exactly_like_a_racetime_one(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, runs_per_pool=2)
    fast = await _racer(931011, 'fast')
    slow = await _racer(931012, 'slow')
    quitter = await _racer(931013, 'quitter')

    captured = await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(fast.id, AsyncQualifierRunStatus.FINISHED, 3600),
        ManualResult(slow.id, AsyncQualifierRunStatus.FINISHED, 5400),
        ManualResult(quitter.id, AsyncQualifierRunStatus.FORFEIT),
    ])

    assert len(captured) == 3
    by_user = {r.user_id: r for r in captured}
    # Approved without review, like any live-race run: the result is self-attributing
    # once a human with the authority to record it has said so.
    assert all(r.review_status == AsyncQualifierReviewStatus.APPROVED for r in captured)
    assert by_user[quitter.id].status == AsyncQualifierRunStatus.FORFEIT
    assert by_user[quitter.id].score == 0.0
    # Par is the mean of the fastest approved runs, so the faster racer scores higher
    # and both are scored — the recompute ran.
    assert by_user[fast.id].score > by_user[slow.id].score
    assert (await AsyncQualifierLiveRace.get(id=race.id)).status == (
        AsyncQualifierLiveRaceStatus.FINISHED)


async def test_the_race_start_is_derived_so_a_run_has_a_duration(db):
    """Every run used to read Started == Finished with a blank Timed column."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931002, 'manualstaff2')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, runs_per_pool=2)
    fast = await _racer(931021, 'quick')
    slow = await _racer(931022, 'steady')

    captured = await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(fast.id, AsyncQualifierRunStatus.FINISHED, 1800),
        ManualResult(slow.id, AsyncQualifierRunStatus.FINISHED, 3600),
    ])
    by_user = {r.user_id: r for r in captured}

    # One race, one start: everybody in the room began at the same instant.
    starts = {r.started_at for r in captured}
    assert len(starts) == 1
    for run in captured:
        assert run.finished_at > run.started_at
        # Each finisher's own finish is their start plus their own time.
        assert int((run.finished_at - run.started_at).total_seconds()) == run.elapsed_seconds
        assert run.measured_seconds == run.elapsed_seconds
    assert by_user[slow.id].finished_at > by_user[fast.id].finished_at


async def test_a_finisher_needs_a_time_and_a_forfeit_may_not_have_one(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931003, 'manualstaff3')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    racer = await _racer(931031, 'timeless')

    with pytest.raises(ValueError, match='finish time'):
        await lrsvc.record_manual_finish(
            staff, race.id, [ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED)])
    with pytest.raises(ValueError, match='longer than a week'):
        await lrsvc.record_manual_finish(
            staff, race.id,
            [ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 8 * 24 * 3600)])

    # A time typed against a forfeit is dropped rather than stored beside a zero.
    captured = await lrsvc.record_manual_finish(
        staff, race.id, [ManualResult(racer.id, AsyncQualifierRunStatus.FORFEIT, 1200)])
    assert captured[0].elapsed_seconds is None
    assert captured[0].score == 0.0


async def test_an_empty_or_duplicated_result_set_is_refused(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931004, 'manualstaff4')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    racer = await _racer(931041, 'twice')

    with pytest.raises(ValueError, match='at least one'):
        await lrsvc.record_manual_finish(staff, race.id, [])
    with pytest.raises(ValueError, match='only appear once'):
        await lrsvc.record_manual_finish(staff, race.id, [
            ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1000),
            ManualResult(racer.id, AsyncQualifierRunStatus.FORFEIT),
        ])
    assert await AsyncQualifierRun.filter(live_race_id=race.id).count() == 0


async def test_the_manual_path_inherits_the_permalink_requirement(db):
    """The same refusal the racetime path gives: without a seed nothing can score."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931005, 'manualstaff5')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, permalink=False)
    racer = await _racer(931051, 'seedless')

    with pytest.raises(ValueError, match='permalink'):
        await lrsvc.record_manual_finish(
            staff, race.id, [ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1000)])


async def test_the_manual_path_inherits_the_pool_cap(db):
    """A racer already at their pool limit is recorded voided, not counted twice."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931006, 'manualstaff6')
    q, pool, pl, first = await _live_race(qsvc, lrsvc, staff, runs_per_pool=1)
    second = await lrsvc.create_live_race(
        staff, pool.id, match_title='Second race', permalink_id=pl.id)
    racer = await _racer(931061, 'greedy')

    await lrsvc.record_manual_finish(
        staff, first.id, [ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1000)])
    again = await lrsvc.record_manual_finish(
        staff, second.id, [ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 900)])

    assert again[0].reattempted is True
    assert 'already used all' in (again[0].reattempt_reason or '')
    assert again[0].score is None


async def test_recording_by_hand_is_audited_as_its_own_act(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931007, 'manualstaff7')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    racer = await _racer(931071, 'recorded')

    await lrsvc.record_manual_finish(
        staff, race.id, [ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1500)])

    actions = await AuditLog.filter(
        action=AuditActions.ASYNC_QUALIFIER_LIVE_RACE_RECORDED_MANUALLY).count()
    assert actions == 1, 'a hand-typed result is a distinct, more contestable act'
    assert await AuditLog.filter(
        action=AuditActions.ASYNC_QUALIFIER_LIVE_RACE_RECORDED).count() == 0


async def test_a_non_admin_cannot_record_a_race(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931008, 'manualstaff8')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    outsider = await _racer(931081, 'nobody')

    with pytest.raises(PermissionError):
        await lrsvc.record_manual_finish(
            outsider, race.id, [ManualResult(outsider.id, AsyncQualifierRunStatus.FINISHED, 1000)])


async def test_an_unmatched_handle_is_recorded_on_the_race_not_just_the_audit(db):
    """Nobody reads the audit log looking for work, so the race carries the to-do."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(931009, 'manualstaff9')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, runs_per_pool=2)
    await _racer(931091, 'linked', 'rt-linked')

    await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-linked', display_name='linked',
                    status=EntrantStatus.DONE, finish_time=1000),
        RaceEntrant(user_id='rt-stranger', display_name='Stranger',
                    status=EntrantStatus.DONE, finish_time=1100),
    ], actor=staff)

    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles == ['Stranger']
    assert await AsyncQualifierRun.filter(live_race_id=race.id).count() == 1

    await User.filter(id=(await _racer(931092, 'stranger')).id).update(
        racetime_user_id='rt-stranger')
    await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-linked', display_name='linked',
                    status=EntrantStatus.DONE, finish_time=1000),
        RaceEntrant(user_id='rt-stranger', display_name='Stranger',
                    status=EntrantStatus.DONE, finish_time=1100),
    ], actor=staff)

    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles is None
    assert await AsyncQualifierRun.filter(live_race_id=race.id).count() == 2
