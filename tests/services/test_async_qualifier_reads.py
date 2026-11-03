"""The reads the admin drill-down was split onto.

Both exist because the Qualifiers drill-down used to load seven datasets on every
mutation: a leaderboard read that projects instead of hydrating, and a per-runner
outcome tally that scales with the review queue rather than with the tournament.
Split from ``test_async_qualifier_service.py`` when that file crossed the 800-line
guideline.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import AsyncQualifierRun, Role, User, UserRole

pytestmark = pytest.mark.anyio


async def _staff() -> User:
    u = await User.create(discord_id=900201, username='readstaff')
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _player(discord_id: int, name: str, **extra) -> User:
    return await User.create(discord_id=discord_id, username=name, **extra)


async def _open_qualifier(service, staff, *, runs_per_pool=1):
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Q', opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=1),
        runs_per_pool=runs_per_pool,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    return q, pool


async def _submit(service, player, run, seconds: int):
    """Submit ``seconds``, backdating the draw so the wall clock agrees.

    ``submit_run`` refuses a claim longer than the run has existed.
    """
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=seconds),
    )
    return await service.submit_run(player, run.id, elapsed_seconds=seconds)


async def test_leaderboard_read_is_projected_but_scores_the_same_board(db):
    """The board is assembled from four scalars per run, not from hydrated runs.

    ``list_valid_for_qualifier`` filtered on ``reattempted`` alone and prefetched
    ``user`` and ``permalink__pool`` for every row, leaving the status checks to
    Python — so a fifth of the rows were built as models and discarded. This pins
    the replacement to the same numbers it replaced.
    """
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=2)
    await service.add_permalinks_bulk(staff, pool.id, urls=['https://seed.test/u1', 'https://seed.test/u2', 'https://seed.test/u3'])

    fast = await _player(900101, 'fast_runner', display_name='Fast Runner')
    slow = await _player(900102, 'slow_runner')

    for seconds in (1200, 1300):
        run = await service.start_run(fast, q.id, pool.id)
        await _submit(service, fast, run, seconds)
        await service.review_run(staff, run.id, approved=True)
    run = await service.start_run(slow, q.id, pool.id)
    await _submit(service, slow, run, 2400)
    await service.review_run(staff, run.id, approved=True)
    pending = await service.start_run(slow, q.id, pool.id)
    await _submit(service, slow, pending, 1250)

    rows = await service.run_repository.list_scored_for_leaderboard(q.id)
    assert all(set(row) == {'user_id', 'score', 'status', 'review_status',
                            'permalink__pool_id', 'user__display_name',
                            'user__username'} for row in rows)
    assert len(rows) == 3, (
        'the three approved finishers reach the board; the pending run does not, '
        'because a run still awaiting a verdict has not spent its slot'
    )

    board = await service.get_leaderboard(staff, q.id)
    by_name = {e.username: e for e in board}
    assert 'Fast Runner' in by_name and 'slow_runner' in by_name
    assert by_name['Fast Runner'].slots_filled == 2
    assert by_name['slow_runner'].slots_filled == 1
    assert board[0].username == 'Fast Runner', 'ranked by realised total'


async def test_review_queue_context_counts_only_the_queues_runners(db):
    """The reviewer's "other runs" line costs a grouped count over the queue's own
    runners, not every run in the qualifier hydrated twice over."""
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=2)
    await service.add_permalinks_bulk(staff, pool.id, urls=['https://seed.test/u1', 'https://seed.test/u2'])

    queued = await _player(900111, 'queued_runner')
    other = await _player(900112, 'unrelated_runner')

    approved = await service.start_run(queued, q.id, pool.id)
    await _submit(service, queued, approved, 1200)
    await service.review_run(staff, approved.id, approved=True)
    pending = await service.start_run(queued, q.id, pool.id)
    await _submit(service, queued, pending, 1300)

    theirs = await service.start_run(other, q.id, pool.id)
    await _submit(service, other, theirs, 1400)
    await service.review_run(staff, theirs.id, approved=True)

    queue = await service.list_review_queue(staff, q.id)
    assert [r.id for r in queue] == [pending.id]

    context = await service.review_queue_context(staff, q.id, queue)
    assert set(context) == {queued.id}, 'only the queue\'s runners are counted'
    assert context[queued.id] == {'approved': 1, 'pending': 1}


async def test_review_queue_context_requires_qualifier_admin(db):
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff)
    await service.add_permalinks_bulk(staff, pool.id, urls=['https://seed.test/u1'])
    player = await _player(900121, 'outsider')
    run = await service.start_run(player, q.id, pool.id)
    await _submit(service, player, run, 1200)

    queue = await service.list_review_queue(staff, q.id)
    with pytest.raises(PermissionError):
        await service.review_queue_context(player, q.id, queue)
