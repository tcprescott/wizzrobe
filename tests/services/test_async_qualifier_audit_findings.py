"""Failing tests for the open findings of the async-qualifier leaderboard audit.

Each test pins one defect recorded in
[docs/reviews/async-qualifier-leaderboard-ux.md](../../docs/reviews/async-qualifier-leaderboard-ux.md)
and asserts the behaviour that finding says it *should* have. They are expected to
fail until the corresponding wave ships — that is the point: the audit's method
was to probe the running app, and a probe script is thrown away while a test is
not. Each carries its finding id so a fix can find its target.

The four policy questions the audit raised were settled with the maintainer, and
these tests encode those answers rather than the current behaviour:

- a slot spent on a forfeit or rejection counts as a **realised zero** (F4b),
- a racer exceeding ``runs_per_pool`` in a live race is **recorded as voided** (F10a),
- live-race and self-paced runs share **one board**, with slot counting fixed (F4a),
- a runner's Score **coarsens to three bands** while the window is open (F7).

Each still-open finding was marked ``xfail(strict=True)`` so the suite stayed green
while the finding was open and *failed loudly the moment a fix landed without the
marker being removed* — the reminder to delete the marker rather than the test. A
test with no marker is one whose finding has shipped; it now guards the fix.

Waves 1 and 2 (F1-F3) were performance and had no assertions here. Wave 3 closed
F4a-e and F10a-b; wave 4 closed F5 (all three) and F6; wave 6 closed F12's pair.
**No marker is left**: every finding this file pins has shipped, so every test
here now guards a fix rather than predicting one.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import FeatureDisabledError
from application.services.async_qualifier.async_qualifier_scoring import (
    ScoredRun,
    build_leaderboard,
)
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import (
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    Role,
    User,
    UserRole,
)

pytestmark = pytest.mark.anyio


async def _staff(discord_id: int = 970001, name: str = 'auditstaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _player(discord_id: int, name: str) -> User:
    return await User.create(discord_id=discord_id, username=name)


async def _open_qualifier(service, staff, *, runs_per_pool=1, allowed_reattempts=1):
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Audit Q', opens_at=now - timedelta(days=1),
        closes_at=now + timedelta(days=1), runs_per_pool=runs_per_pool,
        allowed_reattempts=allowed_reattempts,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    return q, pool


async def _submit(service, player, run, seconds: int):
    """Submit ``seconds``, backdating the draw so the wall clock agrees.

    ``submit_run`` refuses a claim longer than the run has existed, so a fixture
    that starts a run and immediately claims an hour is claiming the impossible.
    """
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=seconds + 60),
    )
    run = await AsyncQualifierRun.get(id=run.id)
    return await service.submit_run(player, run.id, elapsed_seconds=seconds)


async def _approved_run(service, staff, player, qualifier, pool, seconds):
    run = await service.start_run(player, qualifier.id, pool.id)
    run = await _submit(service, player, run, seconds)
    return await service.review_run(staff, run.id, approved=True)


async def test_leaderboard_ignores_a_live_race_only_pool(db):
    """A pool a self-paced runner can never draw from must not contribute slots.

    ``get_run_availability`` already excludes such a pool (pinned by
    ``test_availability_ignores_a_live_race_only_pool``); the board does not, so
    every self-paced player reads inflated ``slots_total`` and an ``estimate``
    projected across slots that were never reachable.
    """
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=1)
    await service.add_permalink(staff, pool.id, url='https://x/async-1')

    live_pool = await service.create_pool(staff, q.id, name='Live only')
    await service.add_permalink(staff, live_pool.id, url='https://x/live-1', live_race=True)

    player = await _player(970101, 'runner_one')
    await _approved_run(service, staff, player, q, pool, 3600)

    board = await service.get_leaderboard(staff, q.id)
    assert board, 'the runner should be on the board'
    entry = board[0]
    assert entry.slots_total == 1, (
        f'only the async pool is reachable, so slots_total should be 1, got {entry.slots_total}'
    )
    assert entry.estimate == entry.actual, (
        'a runner who filled every reachable slot should have estimate == actual, '
        f'got estimate={entry.estimate} actual={entry.actual}'
    )


async def test_a_forfeited_slot_counts_as_a_realised_zero(db):
    """Decided with the maintainer: a slot the player cannot refill counts as zero.

    Today a forfeit is written ``FORFEIT/APPROVED/score 0`` and the board's
    ``FINISHED`` filter drops it, so ``slots_filled`` cannot distinguish
    "forfeited" from "not yet run" and ``estimate`` projects the player's
    good-run average over a slot that is already spent.
    """
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=2)
    await service.add_permalink(staff, pool.id, url='https://x/a')
    await service.add_permalink(staff, pool.id, url='https://x/b')

    player = await _player(970102, 'runner_two')
    await _approved_run(service, staff, player, q, pool, 3600)   # scores 100 (par == own time)
    forfeited = await service.start_run(player, q.id, pool.id)
    await service.forfeit_run(player, forfeited.id)

    board = await service.get_leaderboard(staff, q.id)
    entry = board[0]
    assert entry.slots_filled == 2, (
        f'both slots are spent — one scored, one forfeited — got {entry.slots_filled}'
    )
    assert entry.estimate == entry.actual, (
        'with no slots left to run, estimate must not project anything; '
        f'got estimate={entry.estimate} actual={entry.actual}'
    )


async def test_build_leaderboard_emits_competition_ranks(db):
    """Equal scores must share a rank, and the rank must come from one place.

    Both pages and the MCP tool each derive rank from an ``enumerate`` index, and
    ``LeaderboardEntryResponse`` has no rank field at all — four derivations of
    one number, which is why a three-way tie renders 1, 2, 3 in alphabetical
    order.
    """
    runs = [
        ScoredRun(user_id=1, username='Adam', pool_id=10, score=100.0),
        ScoredRun(user_id=2, username='Mia', pool_id=10, score=100.0),
        ScoredRun(user_id=3, username='Zoe', pool_id=10, score=90.0),
    ]
    board = build_leaderboard(pool_ids=[10], runs_per_pool=1, scored_runs=runs)
    ranks = [getattr(e, 'rank', None) for e in board]
    assert ranks == [1, 1, 3], (
        f'a two-way tie on 100.0 should rank 1, 1, 3 — got {ranks}'
    )


async def test_an_entrant_who_scored_nothing_is_ranked_last_not_omitted(db):
    """"I came last" and "I am not on the list" are different messages."""
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=1)
    await service.add_permalink(staff, pool.id, url='https://x/a')
    await service.add_permalink(staff, pool.id, url='https://x/b')

    scorer = await _player(970103, 'runner_scored')
    await _approved_run(service, staff, scorer, q, pool, 3600)

    zeroed = await _player(970104, 'runner_zeroed')
    run = await service.start_run(zeroed, q.id, pool.id)
    await service.forfeit_run(zeroed, run.id)

    board = await service.get_leaderboard(staff, q.id)
    names = [e.username for e in board]
    assert 'runner_zeroed' in names, (
        f'a player whose only run forfeited should appear on 0, not vanish; got {names}'
    )
    assert names[-1] == 'runner_zeroed', 'and should rank last'


async def test_rejecting_a_run_clears_its_score(db):
    """The recompute only rescores the approved set, so a rejected run keeps its score.

    The board filters on approved and is unaffected, but the runner's own table
    renders "rejected" beside a score, and ``AsyncQualifierRunResponse`` reports
    the pre-rejection value over REST and MCP.
    """
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=1)
    await service.add_permalink(staff, pool.id, url='https://x/a')

    player = await _player(970105, 'runner_three')
    run = await _approved_run(service, staff, player, q, pool, 3600)
    assert run.score is not None, 'precondition: the approved run is scored'

    # Reversing a settled verdict is an override since F5; the score-clearing this
    # test is about is the same on either path.
    await service.review_run(
        staff, run.id, approved=False, note='VoD does not match', override=True,
    )
    run = await AsyncQualifierRun.get(id=run.id)
    assert run.review_status == AsyncQualifierReviewStatus.REJECTED
    assert run.score is None, f'a rejected run must not carry a score, got {run.score}'


async def test_a_reattempted_run_clears_its_score(db):
    service = AsyncQualifierService()
    staff = await _staff()
    q, pool = await _open_qualifier(service, staff, runs_per_pool=1, allowed_reattempts=1)
    await service.add_permalink(staff, pool.id, url='https://x/a')
    await service.add_permalink(staff, pool.id, url='https://x/b')

    player = await _player(970106, 'runner_four')
    run = await _approved_run(service, staff, player, q, pool, 3600)
    await service.reattempt_run(player, run.id, reason='bad seed')

    run = await AsyncQualifierRun.get(id=run.id)
    assert run.reattempted is True
    assert run.score is None, f'a voided run must not carry a score, got {run.score}'


async def test_review_run_refuses_a_run_claimed_by_another_reviewer(db):
    """The claim is documented as a lock; ``review_run`` never reads it."""
    service = AsyncQualifierService()
    staff_a = await _staff(970002, 'reviewer_a')
    staff_b = await _staff(970003, 'reviewer_b')
    q, pool = await _open_qualifier(service, staff_a, runs_per_pool=1)
    await service.add_permalink(staff_a, pool.id, url='https://x/a')

    player = await _player(970107, 'runner_five')
    run = await service.start_run(player, q.id, pool.id)
    run = await _submit(service, player, run, 3600)

    await service.claim_run(staff_a, run.id)
    with pytest.raises(ValueError, match='claim'):
        await service.review_run(staff_b, run.id, approved=True)


async def test_review_run_refuses_to_silently_re_review_a_settled_run(db):
    """Reversing a verdict is legitimate; doing it indistinguishably is not.

    Today a second call flips an approved run to rejected, DMs the runner in the
    shape of a first verdict, and leaves nothing saying an earlier verdict was
    overridden.
    """
    service = AsyncQualifierService()
    staff = await _staff(970004, 'reviewer_c')
    q, pool = await _open_qualifier(service, staff, runs_per_pool=1)
    await service.add_permalink(staff, pool.id, url='https://x/a')

    player = await _player(970108, 'runner_six')
    run = await _approved_run(service, staff, player, q, pool, 3600)

    with pytest.raises(ValueError):
        await service.review_run(staff, run.id, approved=False, note='changed my mind')


async def test_two_concurrent_verdicts_do_not_both_commit(db):
    """Driven against Postgres, both calls succeeded and both notes were attached,
    leaving the run with contradictory reviewer notes and the runner with two
    contradictory DMs. The second writer must lose on a stale read.
    """
    import asyncio

    service = AsyncQualifierService()
    staff_a = await _staff(970005, 'reviewer_d')
    staff_b = await _staff(970006, 'reviewer_e')
    q, pool = await _open_qualifier(service, staff_a, runs_per_pool=1)
    await service.add_permalink(staff_a, pool.id, url='https://x/a')

    player = await _player(970109, 'runner_seven')
    run = await service.start_run(player, q.id, pool.id)
    run = await _submit(service, player, run, 3600)

    results = await asyncio.gather(
        service.review_run(staff_a, run.id, approved=True, note='approved by A'),
        service.review_run(staff_b, run.id, approved=False, note='rejected by B'),
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1, (
        f'exactly one verdict should win; both committed ({results})'
    )

    notes = await service.get_run_notes(staff_a, run.id)
    assert len(notes) == 1, (
        f'a losing verdict must not leave its note behind, got {[n.note for n in notes]}'
    )


async def test_get_leaderboard_refuses_when_the_feature_is_disabled(db):
    """The one public read in the subsystem without the guard its siblings carry.

    Every current caller is gated at its own boundary, so nothing leaks today —
    which is exactly why the service-side half of the obligation matters: the next
    caller will not be. Driven against Postgres with the flag forced off, this read
    returned a full 499-entry board while ``list_open_qualifiers`` refused.

    A bare tenant has no flag rows, which is the same idiom
    ``test_volunteer_hours.TestFeatureGate`` uses.
    """
    from application.tenant_context import tenant_scope
    from models import Tenant

    tenant = await Tenant.create(name='Bare Qualifiers', slug='bare-qualifiers')
    service = AsyncQualifierService()
    with tenant_scope(tenant.id):
        with pytest.raises(FeatureDisabledError):
            await service.get_leaderboard(None, 1)


async def test_a_live_race_run_over_the_pool_cap_is_recorded_as_voided(db):
    """Decided with the maintainer: record it voided rather than dropping it silently.

    ``runs_per_pool`` is enforced only in ``start_run``. A racer entering a second
    live race in a one-run pool ends up holding two runs, and
    ``build_leaderboard`` discards the surplus at scoring time — so the racer ran
    a race that could never count and nothing told them.
    """
    from application.services.async_qualifier.async_qualifier_live_race_service import (
        AsyncQualifierLiveRaceService,
    )
    from racetimebot.transport import EntrantStatus, RaceEntrant

    service = AsyncQualifierService()
    live_service = AsyncQualifierLiveRaceService()
    staff = await _staff(970007, 'reviewer_f')
    q, _ = await _open_qualifier(service, staff, runs_per_pool=1)

    live_pool = await service.create_pool(staff, q.id, name='Live')
    first = await service.add_permalink(staff, live_pool.id, url='https://x/l1', live_race=True)
    second = await service.add_permalink(staff, live_pool.id, url='https://x/l2', live_race=True)

    racer = await User.create(discord_id=970110, username='racer_one',
                              racetime_user_id='rt-audit-1')
    entrant = RaceEntrant(user_id='rt-audit-1', display_name='racer_one',
                          status=EntrantStatus.DONE, finish_time=3600, place=1)

    race_one = await live_service.create_live_race(
        staff, live_pool.id, match_title='R1', permalink_id=first.id)
    await live_service.record_finish(race_one, [entrant])

    race_two = await live_service.create_live_race(
        staff, live_pool.id, match_title='R2', permalink_id=second.id)
    await live_service.record_finish(race_two, [entrant])

    runs = await AsyncQualifierRun.filter(qualifier_id=q.id, user_id=racer.id).order_by('id')
    assert len(runs) == 2, 'both races captured a run'
    assert runs[1].reattempted is True, (
        'the run beyond the pool cap should be recorded as voided, with a reason, '
        'rather than counted and then silently discarded at scoring time'
    )
    assert runs[1].reattempt_reason, 'and a voided run owes the racer a reason'


async def test_a_live_race_with_no_permalink_refuses_to_record(db):
    """"(assign later)" produces runs that score None forever and never reach the board."""
    from application.services.async_qualifier.async_qualifier_live_race_service import (
        AsyncQualifierLiveRaceService,
    )
    from racetimebot.transport import EntrantStatus, RaceEntrant

    service = AsyncQualifierService()
    live_service = AsyncQualifierLiveRaceService()
    staff = await _staff(970008, 'reviewer_g')
    q, _ = await _open_qualifier(service, staff, runs_per_pool=1)
    live_pool = await service.create_pool(staff, q.id, name='Live')

    await User.create(discord_id=970111, username='racer_two', racetime_user_id='rt-audit-2')
    entrant = RaceEntrant(user_id='rt-audit-2', display_name='racer_two',
                          status=EntrantStatus.DONE, finish_time=3600, place=1)

    race = await live_service.create_live_race(
        staff, live_pool.id, match_title='No permalink', permalink_id=None)
    with pytest.raises(ValueError, match='permalink'):
        await live_service.record_finish(race, [entrant])


async def test_permalink_entry_refuses_a_value_that_is_not_an_http_url(db):
    """Because reveal equals start, a typo'd permalink costs a runner their slot."""
    service = AsyncQualifierService()
    staff = await _staff(970009, 'reviewer_h')
    _, pool = await _open_qualifier(service, staff)

    with pytest.raises(ValueError):
        await service.add_permalink(staff, pool.id, url='not-a-url-at-all')
    with pytest.raises(ValueError):
        await service.add_permalink(staff, pool.id, url='javascript:alert(1)')


async def test_bulk_permalinks_reports_the_lines_it_skipped(db):
    """A typo is indistinguishable from a success when only a count comes back.

    The return shape changed with the fix — a bare list cannot carry the report —
    so this reads ``created`` where the finding measured the list itself.
    """
    service = AsyncQualifierService()
    staff = await _staff(970010, 'reviewer_i')
    _, pool = await _open_qualifier(service, staff)

    result = await service.add_permalinks_bulk(staff, pool.id, urls=[
        'https://alttpr.com/en/h/good-one', 'not-a-url-at-all',
        'https://alttpr.com/en/h/good-two',
    ])
    assert [(n, line) for n, line, _ in result.rejected] == [(2, 'not-a-url-at-all')]
    urls = [p.url for p in result.created]
    assert urls == ['https://alttpr.com/en/h/good-one', 'https://alttpr.com/en/h/good-two'], (
        f'only real URLs should become permalinks, got {urls}'
    )
