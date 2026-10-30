"""The audit's F11 — query patterns that bite as a qualifier grows.

Not about correctness of the answers (the suites above cover those) but about how
the answers are obtained: a tally that belongs in a `GROUP BY`, a count that went
through a prefetching list read, and a par recompute that wrote a fresh par and the
scores derived from it in separate, unwrapped statements.

The per-pool query shape is guarded in `tests/test_query_budget.py`, alongside the
other read budgets.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.repositories import AsyncQualifierRunRepository
from application.services.async_qualifier.async_qualifier_scoring import compute_score
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import (
    AsyncQualifierPermalink,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    Role,
    User,
    UserRole,
)

pytestmark = pytest.mark.anyio


async def _staff(discord_id: int = 900701, name: str = 'shapestaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _player(discord_id: int, name: str) -> User:
    return await User.create(discord_id=discord_id, username=name)


async def _qualifier(service, staff, *, runs_per_pool: int = 2, reattempts: int = 2):
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Shape Q', opens_at=now - timedelta(days=1),
        closes_at=now + timedelta(days=1), runs_per_pool=runs_per_pool,
        allowed_reattempts=reattempts,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    for i in range(4):
        await service.add_permalink(staff, pool.id, url=f'https://seed.test/shape-{i}')
    return q, pool


async def _finish(service, player, run, seconds: int):
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=seconds + 60))
    run = await AsyncQualifierRun.get(id=run.id)
    return await service.submit_run(player, run.id, elapsed_seconds=seconds)


async def test_play_counts_per_permalink_match_the_runs_taken(db):
    """The draw's fairness input, tallied by the database rather than in Python."""
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _qualifier(service, staff)
    repo = AsyncQualifierRunRepository()

    assert await repo.valid_run_counts_by_permalink_for_pool(pool.id) == {}

    drawn = []
    for i, discord_id in enumerate((900711, 900712, 900713)):
        player = await _player(discord_id, f'shape{i}')
        run = await service.start_run(player, q.id, pool.id)
        drawn.append(run.permalink_id)

    counts = await repo.valid_run_counts_by_permalink_for_pool(pool.id)
    assert sum(counts.values()) == 3
    for permalink_id in set(drawn):
        assert counts[permalink_id] == drawn.count(permalink_id)


async def test_a_voided_run_stops_counting_toward_fairness(db):
    service = AsyncQualifierService()
    staff = await _staff(900702, 'shapestaff2')
    q, pool = await _qualifier(service, staff)
    player = await _player(900714, 'shapevoid')
    repo = AsyncQualifierRunRepository()

    run = await service.start_run(player, q.id, pool.id)
    run = await _finish(service, player, run, 1200)
    assert await repo.valid_run_counts_by_permalink_for_pool(pool.id) == {run.permalink_id: 1}

    await service.reattempt_run(player, run.id, reason='Emulator crashed at Aga.')
    assert await repo.valid_run_counts_by_permalink_for_pool(pool.id) == {}


async def test_spent_slots_and_played_seeds_come_back_per_pool(db):
    """One query each for every pool, keyed by pool — what the availability read reads."""
    service = AsyncQualifierService()
    staff = await _staff(900703, 'shapestaff3')
    q, pool_a = await _qualifier(service, staff, runs_per_pool=1)
    pool_b = await service.create_pool(staff, q.id, name='Pool B')
    await service.add_permalink(staff, pool_b.id, url='https://seed.test/b-1')
    player = await _player(900715, 'shapeboth')
    repo = AsyncQualifierRunRepository()

    run = await service.start_run(player, q.id, pool_a.id)
    await _finish(service, player, run, 1500)

    spent = await repo.valid_run_counts_for_user_by_pool([pool_a.id, pool_b.id], player.id)
    assert spent == {pool_a.id: 1}, 'a pool with no runs is absent, not zero-keyed'

    played = await repo.played_permalink_ids_for_user_by_pool([pool_a.id, pool_b.id], player.id)
    assert played[pool_a.id].valid == {run.permalink_id}
    assert played[pool_a.id].voided == set()
    # Present and empty, because the no-repeat filter asks about every pool.
    assert played[pool_b.id].valid == set() and played[pool_b.id].voided == set()
    assert await repo.valid_run_counts_for_user_by_pool([], player.id) == {}
    assert await repo.played_permalink_ids_for_user_by_pool([], player.id) == {}


async def test_self_spent_reattempts_are_counted_not_listed(db):
    """A reviewer's grant is not the runner's allowance — the count must exclude it."""
    service = AsyncQualifierService()
    staff = await _staff(900704, 'shapestaff4')
    q, pool = await _qualifier(service, staff)
    player = await _player(900716, 'shapereatt')
    repo = AsyncQualifierRunRepository()

    assert await repo.count_self_spent_reattempts(q.id, player.id) == 0

    own = await service.start_run(player, q.id, pool.id)
    own = await _finish(service, player, own, 1200)
    await service.reattempt_run(player, own.id, reason='Crashed on the Ganon fight.')
    assert await repo.count_self_spent_reattempts(q.id, player.id) == 1

    granted = await service.start_run(player, q.id, pool.id)
    granted = await _finish(service, player, granted, 1300)
    await service.grant_reattempt(staff, granted.id, reason='Seed would not load.')
    assert await repo.count_self_spent_reattempts(q.id, player.id) == 1, (
        'a granted reattempt must not spend the runner’s own allowance'
    )
    allowance = await service.get_reattempt_allowance(player, q.id)
    assert (allowance.spent, allowance.allowed) == (1, 2)


async def test_par_and_every_score_derived_from_it_move_together(db):
    """The par write and the rescore are one transaction, so no run keeps a score
    computed against a par that no longer exists."""
    service = AsyncQualifierService()
    staff = await _staff(900705, 'shapestaff5')
    q, pool = await _qualifier(service, staff, runs_per_pool=1)
    permalink = (await AsyncQualifierPermalink.filter(pool_id=pool.id).order_by('id'))[0]

    for i, seconds in enumerate((3600, 4200, 4800)):
        player = await _player(900720 + i, f'shapepar{i}')
        run = await service.start_run(player, q.id, pool.id)
        # Force every run onto the same permalink so they share one par.
        await AsyncQualifierRun.filter(id=run.id).update(permalink_id=permalink.id)
        run = await AsyncQualifierRun.get(id=run.id)
        run = await _finish(service, player, run, seconds)
        await service.review_run(staff, run.id, approved=True)

    permalink = await AsyncQualifierPermalink.get(id=permalink.id)
    par = permalink.par_time
    assert par is not None
    for run in await AsyncQualifierRun.filter(
        permalink_id=permalink.id, status=AsyncQualifierRunStatus.FINISHED,
    ):
        # Every stored score is the one this par implies — the pair a part-written
        # recompute used to be able to break, undetectably, one row at a time.
        assert run.score == pytest.approx(compute_score(run.elapsed_seconds, par))
