"""The four live-race defects the quality-drift audit found, and the fixes for them.

Each test pins one finding recorded in
[docs/reviews/2026-08-code-quality-drift.md](../../docs/reviews/2026-08-code-quality-drift.md)
and asserts the behaviour that finding says it should have. All four shipped in one
commit and were found by the audit of that commit, so they share a file:

- **T2.1** a hand-recorded race cleared the whole unmatched-handle to-do, hiding
  racers who still had no run — while the card that sent the admin there said the
  opposite,
- **T3.1** a racetime ``DONE`` entrant with no ``finish_time`` was written approved
  with no time, then dropped from the leaderboard entirely,
- **T5.12** "Record results" was offered on a permalink-less race the capture
  refuses, and nothing anywhere could assign the permalink,
- **T2.2** a blank racer row in the record dialog was skipped silently and the rest
  counted as a success.

The T3.1 fix is a shared predicate, so the invariant worth guarding is that both
capture paths ask the same question — not just that this one now answers it right.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier.async_qualifier_live_race_service import (
    AsyncQualifierLiveRaceService,
    ManualResult,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from application.services.audit_service import AuditActions
from application.utils.racetime_entrants import is_scored_finish
from models import (
    AsyncQualifierLiveRace,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    AuditLog,
    Role,
    User,
    UserRole,
)
from pages.admin_tabs.admin_qualifiers.live_races import missing_racer_message
from racetimebot.transport import EntrantStatus, RaceEntrant

pytestmark = pytest.mark.anyio


async def _staff(discord_id: int = 932001, name: str = 'driftstaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _racer(discord_id: int, name: str, rtid: str | None = None,
                 rtname: str | None = None) -> User:
    return await User.create(discord_id=discord_id, username=name,
                             racetime_user_id=rtid, racetime_username=rtname)


async def _live_race(qsvc, lrsvc, staff, *, runs_per_pool: int = 2, permalink: bool = True):
    now = datetime.now(timezone.utc)
    q = await qsvc.create_qualifier(
        staff, name='Drift Q', opens_at=now - timedelta(days=1),
        closes_at=now + timedelta(days=1), runs_per_pool=runs_per_pool,
    )
    pool = await qsvc.create_pool(staff, q.id, name='Drift Pool')
    pl = await qsvc.add_permalink(
        staff, pool.id, url='https://seed.test/drift-1', live_race=True)
    race = await lrsvc.create_live_race(
        staff, pool.id, match_title='Drift Race',
        permalink_id=pl.id if permalink else None,
    )
    return q, pool, pl, race


async def test_a_hand_recorded_race_keeps_the_handles_it_did_not_record(db):
    """The to-do outlives a manual record that had nothing to do with it.

    Clearing it left the racer with no run, no warning, and par computed from a
    field the admin believed complete — so every score in the pool came off the
    wrong denominator.
    """
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff()
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    await _racer(932011, 'linked', 'rt-linked', 'linked')
    latecomer = await _racer(932012, 'latecomer')

    await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-linked', display_name='linked',
                    status=EntrantStatus.DONE, finish_time=1000),
        RaceEntrant(user_id='rt-stranger', display_name='Stranger',
                    status=EntrantStatus.DONE, finish_time=1100),
    ], actor=staff)
    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles == ['Stranger'], 'fixture precondition'

    # Recording somebody else by hand says nothing about Stranger either way.
    await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(latecomer.id, AsyncQualifierRunStatus.FINISHED, 1500),
    ])

    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles == ['Stranger']


async def test_recording_the_unmatched_racer_by_hand_clears_their_handle(db):
    """The to-do is a to-do: doing it has to tick it off, or it never clears."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932002, 'driftstaff2')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    await _racer(932021, 'linked', 'rt-linked', 'linked')

    await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-linked', display_name='linked',
                    status=EntrantStatus.DONE, finish_time=1000),
        RaceEntrant(user_id='rt-stranger', display_name='Stranger',
                    status=EntrantStatus.DONE, finish_time=1100),
    ], actor=staff)

    # The account gets linked, and the admin types the result in rather than waiting
    # for a room event that already came and went.
    stranger = await _racer(932022, 'stranger', 'rt-stranger', 'Stranger')
    await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(stranger.id, AsyncQualifierRunStatus.FINISHED, 1100),
    ])

    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles is None
    assert await AsyncQualifierRun.filter(live_race_id=race.id).count() == 2


async def test_a_handle_matching_nobody_recorded_is_left_standing(db):
    """Matched on the racetime id when the handle is the id rather than a name."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932003, 'driftstaff3')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)

    # No display name, so the handle stored is the raw account id.
    await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-anon', display_name=None,
                    status=EntrantStatus.DONE, finish_time=900),
    ], actor=staff)
    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles == ['rt-anon']

    anon = await _racer(932031, 'anon', 'rt-anon')
    await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(anon.id, AsyncQualifierRunStatus.FINISHED, 900),
    ])

    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles is None


async def test_both_capture_paths_ask_the_same_question_of_a_finisher(db):
    """The predicate is shared, so a finish with no time cannot be a finish in one path.

    ``application/utils/racetime_entrants.py`` documented the two paths as sharing an
    idiom while they shared only the handle formatter.
    """
    assert is_scored_finish(RaceEntrant(
        user_id='x', display_name='x', status=EntrantStatus.DONE, finish_time=60))
    assert not is_scored_finish(RaceEntrant(
        user_id='x', display_name='x', status=EntrantStatus.DONE, finish_time=None))
    assert not is_scored_finish(RaceEntrant(
        user_id='x', display_name='x', status=EntrantStatus.DID_NOT_FINISH, finish_time=None))


async def test_a_done_entrant_with_no_time_is_not_written_as_an_approved_run(db):
    """It used to be approved with elapsed NULL, then vanish from the board.

    Nothing flagged it: no reviewer queue entry, no to-do, no audit note — and the
    racer's pool slot was spent on a run nobody could see.
    """
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932004, 'driftstaff4')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    good = await _racer(932041, 'good', 'rt-good', 'good')
    weird = await _racer(932042, 'weird', 'rt-weird', 'weird')

    captured = await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-good', display_name='good',
                    status=EntrantStatus.DONE, finish_time=3600),
        RaceEntrant(user_id='rt-weird', display_name='weird',
                    status=EntrantStatus.DONE, finish_time=None),
    ], actor=staff)

    assert [r.user_id for r in captured] == [good.id]
    assert await AsyncQualifierRun.filter(user_id=weird.id).count() == 0
    # And the racer reaches staff instead of disappearing, on the same to-do as an
    # unlinked account — the record dialog resolves both.
    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles == ['weird']


async def test_the_racer_racetime_could_not_time_is_recordable_by_hand(db):
    """The to-do has to lead somewhere: staff type the real time and it scores."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932005, 'driftstaff5')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    await _racer(932051, 'good', 'rt-good', 'good')
    weird = await _racer(932052, 'weird', 'rt-weird', 'weird')

    await lrsvc.record_finish(race, [
        RaceEntrant(user_id='rt-good', display_name='good',
                    status=EntrantStatus.DONE, finish_time=3600),
        RaceEntrant(user_id='rt-weird', display_name='weird',
                    status=EntrantStatus.DONE, finish_time=None),
    ], actor=staff)
    captured = await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(weird.id, AsyncQualifierRunStatus.FINISHED, 4200),
    ])

    by_user = {r.user_id: r for r in captured}
    assert by_user[weird.id].elapsed_seconds == 4200
    assert by_user[weird.id].score is not None
    race = await AsyncQualifierLiveRace.get(id=race.id)
    assert race.unmatched_handles is None


async def test_a_scheduled_race_can_be_given_the_permalink_it_was_created_without(db):
    """"(assign later)" had no later: no page control and no route set one.

    So a race scheduled before its seed was chosen could never be recorded at all,
    and the capture's own refusal named an action that existed nowhere.
    """
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932006, 'driftstaff6')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, permalink=False)
    racer = await _racer(932061, 'seedless')

    with pytest.raises(ValueError, match='permalink'):
        await lrsvc.record_manual_finish(staff, race.id, [
            ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1000)])

    assigned = await lrsvc.assign_permalink(staff, race.id, pl.id)
    assert assigned.permalink_id == pl.id
    assert await AuditLog.filter(
        action=AuditActions.ASYNC_QUALIFIER_LIVE_RACE_PERMALINK_ASSIGNED).count() == 1

    captured = await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1000)])
    assert captured[0].permalink_id == pl.id


async def test_a_permalink_from_another_pool_is_refused(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932007, 'driftstaff7')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, permalink=False)
    other_pool = await qsvc.create_pool(staff, q.id, name='Other Pool')
    other_pl = await qsvc.add_permalink(
        staff, other_pool.id, url='https://seed.test/other-1', live_race=True)

    with pytest.raises(ValueError, match='does not belong'):
        await lrsvc.assign_permalink(staff, race.id, other_pl.id)


async def test_moving_a_recorded_race_to_another_permalink_is_refused(db):
    """Par is per permalink, so moving it would score captured runs against a seed
    nobody in the race played."""
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932008, 'driftstaff8')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff)
    second = await qsvc.add_permalink(
        staff, pool.id, url='https://seed.test/drift-2', live_race=True)
    racer = await _racer(932081, 'recorded')

    await lrsvc.record_manual_finish(staff, race.id, [
        ManualResult(racer.id, AsyncQualifierRunStatus.FINISHED, 1000)])

    with pytest.raises(ValueError, match='already recorded'):
        await lrsvc.assign_permalink(staff, race.id, second.id)
    # Re-asserting the permalink it already has is not a move, so it is allowed.
    assert (await lrsvc.assign_permalink(staff, race.id, pl.id)).permalink_id == pl.id


async def test_a_non_admin_cannot_assign_a_permalink(db):
    qsvc, lrsvc = AsyncQualifierService(), AsyncQualifierLiveRaceService()
    staff = await _staff(932009, 'driftstaff9')
    q, pool, pl, race = await _live_race(qsvc, lrsvc, staff, permalink=False)
    outsider = await _racer(932091, 'outsider')

    with pytest.raises(PermissionError):
        await lrsvc.assign_permalink(outsider, race.id, pl.id)


def test_a_blank_row_is_named_rather_than_dropped():
    """Four rows filled, one blank, and the answer used to be "Recorded 3 run(s)"."""
    assert missing_racer_message([]) is None
    assert missing_racer_message([3]) == (
        'No racer picked on row 3. Choose one, or remove the row.')
    assert missing_racer_message([2, 4]) == (
        'No racer picked on rows 2, 4. Choose them, or remove the rows.')
