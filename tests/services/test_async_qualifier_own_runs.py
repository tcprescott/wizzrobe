"""What a runner may see of their own runs, and what the surface owes them.

The audit's F7 and F8. F7: an exact score is exactly solvable for the seed's par —
the runner knows their own elapsed time — so publishing it during the window hands
every runner the number the lockdown exists to hide. F8: the record already held
the seed played, why a reviewer voided a run, and whether the clock or the runner
ended it, and none of it reached a screen.

``list_user_runs`` answers both by projecting to ``OwnRun``, so the page and
``GET /{id}/me/runs`` share one decision instead of each reading ``run.score``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier.async_qualifier_scoring import (
    NEAR_PAR_FLOOR,
    SCORE_MAX,
    ScoreBand,
    score_band,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import AsyncQualifierRun, AsyncQualifierRunStatus, Role, User, UserRole

pytestmark = pytest.mark.anyio


async def _staff(discord_id: int = 900301, name: str = 'ownstaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _qualifier(service, staff, *, closed: bool, runs_per_pool: int = 2):
    now = datetime.now(timezone.utc)
    window = ((now - timedelta(days=3), now - timedelta(days=1)) if closed
              else (now - timedelta(days=1), now + timedelta(days=1)))
    q = await service.create_qualifier(
        staff, name='Own Q', opens_at=window[0], closes_at=window[1],
        runs_per_pool=runs_per_pool, allowed_reattempts=1,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    await service.add_permalink(staff, pool.id, url='https://example.test/seed-a')
    await service.add_permalink(staff, pool.id, url='https://example.test/seed-b')
    return q, pool


async def _approved(service, staff, player, q, pool, seconds: int):
    """A run the reviewer approved, backdated so the claim is plausible."""
    run = await service.start_run(player, q.id, pool.id)
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=seconds + 60))
    run = await AsyncQualifierRun.get(id=run.id)
    run = await service.submit_run(player, run.id, elapsed_seconds=seconds)
    return await service.review_run(staff, run.id, approved=True)


def test_score_band_mirrors_the_cap():
    """The fast side is already banded by the 105 cap, so the slow edge mirrors it."""
    assert score_band(None) is None
    assert score_band(SCORE_MAX) is ScoreBand.UNDER
    assert score_band(SCORE_MAX + 10) is ScoreBand.UNDER
    assert score_band(100.0) is ScoreBand.NEAR
    assert score_band(NEAR_PAR_FLOOR) is ScoreBand.NEAR
    assert score_band(NEAR_PAR_FLOOR - 0.1) is ScoreBand.ABOVE
    assert score_band(0.0) is ScoreBand.ABOVE


async def test_an_open_qualifier_withholds_the_exact_score(db):
    """`par = elapsed / (2 - score/100)`, and the runner knows their own elapsed."""
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900311, username='own_runner')
    approved = await _approved(service, staff, player, q, pool, 3600)
    assert approved.score is not None, 'precondition: the run is scored in the database'

    runs = await service.list_user_runs(player, q.id)
    assert runs[0].score is None, (
        f'an open qualifier must not hand back the number, got {runs[0].score}'
    )
    assert runs[0].score_band is ScoreBand.NEAR, (
        'the runner still learns which side of par they landed on'
    )


async def test_a_forfeit_has_no_band_rather_than_above_par(db):
    """A forfeit is written as 0, which would band as "above par" for a pace never set."""
    service = AsyncQualifierService()
    staff = await _staff(900398, 'ownstaff_ff')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900399, username='own_forfeiter')
    run = await service.start_run(player, q.id, pool.id)
    await service.forfeit_run(player, run.id)

    runs = await service.list_user_runs(player, q.id)
    assert runs[0].status == AsyncQualifierRunStatus.FORFEIT
    assert runs[0].score_band is None


async def test_a_closed_qualifier_gives_the_exact_score(db):
    """The lockdown ends when the window does — that is the whole point of it."""
    service = AsyncQualifierService()
    staff = await _staff(900302, 'ownstaff_b')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900312, username='own_runner_b')
    approved = await _approved(service, staff, player, q, pool, 3600)
    await service.update_qualifier(
        staff, q.id, closes_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    runs = await service.list_user_runs(player, q.id)
    assert runs[0].score == approved.score
    assert runs[0].score_band is ScoreBand.NEAR


async def test_own_runs_carry_the_seed_that_was_played(db):
    """A runner disputing a verdict could not cite the seed they were given."""
    service = AsyncQualifierService()
    staff = await _staff(900303, 'ownstaff_c')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900313, username='own_runner_c')
    await service.start_run(player, q.id, pool.id)

    runs = await service.list_user_runs(player, q.id)
    assert runs[0].permalink_url and runs[0].permalink_url.startswith('https://example.test/')
    assert runs[0].pool_name == 'Pool A'


async def test_an_in_progress_run_carries_its_deadline(db):
    """The card counted up with no hint a countdown existed."""
    service = AsyncQualifierService()
    staff = await _staff(900304, 'ownstaff_d')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900314, username='own_runner_d')
    run = await service.start_run(player, q.id, pool.id)

    runs = await service.list_user_runs(player, q.id)
    assert runs[0].status == AsyncQualifierRunStatus.IN_PROGRESS
    assert runs[0].deadline is not None, 'a running run auto-forfeits at a known time'
    assert runs[0].deadline > run.started_at

    await service.forfeit_run(player, run.id)
    runs = await service.list_user_runs(player, q.id)
    assert runs[0].deadline is None


async def test_a_granted_void_carries_its_reason_and_says_who(db):
    """The reason went out by DM and never reached the page the runner returns to."""
    service = AsyncQualifierService()
    staff = await _staff(900305, 'ownstaff_e')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900315, username='own_runner_e')
    run = await service.start_run(player, q.id, pool.id)
    await service.forfeit_run(player, run.id)
    await service.grant_reattempt(staff, run.id, reason='Seed was mis-rolled')

    voided = next(r for r in await service.list_user_runs(player, q.id) if r.reattempted)
    assert voided.reattempt_reason == 'Seed was mis-rolled'
    assert voided.reattempt_was_granted is True, 'a reviewer did this, not the runner'


async def test_a_runners_own_reattempt_is_not_marked_as_granted(db):
    """The distinction is which of the two spent something."""
    service = AsyncQualifierService()
    staff = await _staff(900306, 'ownstaff_f')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900316, username='own_runner_f')
    run = await service.start_run(player, q.id, pool.id)
    await service.forfeit_run(player, run.id)
    await service.reattempt_run(player, run.id, reason='Mis-clicked the forfeit')

    voided = next(r for r in await service.list_user_runs(player, q.id) if r.reattempted)
    assert voided.reattempt_was_granted is False
    assert voided.reattempt_reason == 'Mis-clicked the forfeit'


async def test_an_expired_run_is_distinguishable_from_a_chosen_forfeit(db):
    """``expired_at`` was read by neither page, so the column proved nothing."""
    service = AsyncQualifierService()
    staff = await _staff(900307, 'ownstaff_g')
    q, pool = await _qualifier(service, staff, closed=False)
    expired_player = await User.create(discord_id=900317, username='own_runner_g')
    chose_player = await User.create(discord_id=900318, username='own_runner_h')

    abandoned = await service.start_run(expired_player, q.id, pool.id)
    await service.expire_run(await AsyncQualifierRun.get(id=abandoned.id))
    chosen = await service.start_run(chose_player, q.id, pool.id)
    await service.forfeit_run(chose_player, chosen.id)

    expired = (await service.list_user_runs(expired_player, q.id))[0]
    forfeited = (await service.list_user_runs(chose_player, q.id))[0]
    assert expired.status == forfeited.status, 'precondition: the same terminal state'
    assert expired.was_expired is True
    assert forfeited.was_expired is False


async def test_awaits_review_is_false_while_the_run_is_still_going(db):
    """An in-progress run is PENDING too, which read as "a reviewer has this"."""
    service = AsyncQualifierService()
    staff = await _staff(900308, 'ownstaff_h')
    q, pool = await _qualifier(service, staff, closed=False)
    player = await User.create(discord_id=900319, username='own_runner_i')
    run = await service.start_run(player, q.id, pool.id)

    running = (await service.list_user_runs(player, q.id))[0]
    assert running.awaits_review is False

    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=1300))
    await service.submit_run(player, run.id, elapsed_seconds=1200)
    submitted = (await service.list_user_runs(player, q.id))[0]
    assert submitted.awaits_review is True
