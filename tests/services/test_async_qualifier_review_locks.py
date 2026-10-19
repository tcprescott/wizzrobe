"""Review integrity: the claim lock, its expiry, and overturning a settled verdict.

The three collisions in ``review_run``'s docstring are pinned in
``test_async_qualifier_audit_findings.py`` (F5), which is where the audit recorded
them. This file covers what those probes do not: that the lock lets go — by
release, by expiry, by the holder's own second action — and that an override is
distinguishable from a first verdict everywhere it lands (audit row, event, DM).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from application.services.audit_service import AuditActions
from models import AsyncQualifierReviewStatus, AsyncQualifierRun, AuditLog, Role, User, UserRole

pytestmark = pytest.mark.anyio


@pytest.fixture
def captured_dms(monkeypatch):
    """Capture the DM text the qualifier notifications send.

    The notification module imports ``DiscordService`` lazily inside each function,
    so patching the class where it lives covers every call site. Mirrors the real
    ``send_dm`` signature: a fake narrower than the thing it stands in for turns an
    added argument into "no DM was sent at all".
    """
    sent: list[tuple[int, str, object]] = []

    class _Fake:
        async def send_dm(self, discord_id, message, view_factory=None, embed=None, link=None, notice=None):
            sent.append((discord_id, message, link))
            return True, 'ok'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService', _Fake,
    )
    return sent


def _details(row) -> dict:
    """``AuditLog.details`` is stored as JSON text on some backends, a dict on others."""
    return row.details if isinstance(row.details, dict) else json.loads(row.details)


async def _staff(discord_id: int, name: str) -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _pending_run(service, staff, player, *, seconds: int = 3600):
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Locks Q', opens_at=now - timedelta(days=1),
        closes_at=now + timedelta(days=1), runs_per_pool=1,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    await service.add_permalink(staff, pool.id, url='https://x/a')
    run = await service.start_run(player, q.id, pool.id)
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=now - timedelta(seconds=seconds + 60),
    )
    run = await AsyncQualifierRun.get(id=run.id)
    return q, await service.submit_run(player, run.id, elapsed_seconds=seconds)


# ------------------------------------------------------------------ the lock

async def test_the_holder_can_review_their_own_claim(db):
    """The lock keeps others out, not its owner."""
    service = AsyncQualifierService()
    staff = await _staff(971001, 'holder')
    player = await User.create(discord_id=971101, username='runner_a')
    _, run = await _pending_run(service, staff, player)

    await service.claim_run(staff, run.id)
    reviewed = await service.review_run(staff, run.id, approved=True)
    assert reviewed.review_status == AsyncQualifierReviewStatus.APPROVED


async def test_a_verdict_drops_the_claim_it_was_made_under(db):
    """A settled run has left the queue, so its claim protects nothing."""
    service = AsyncQualifierService()
    staff = await _staff(971002, 'holder_b')
    player = await User.create(discord_id=971102, username='runner_b')
    _, run = await _pending_run(service, staff, player)

    await service.claim_run(staff, run.id)
    await service.review_run(staff, run.id, approved=True)
    run = await AsyncQualifierRun.get(id=run.id)
    assert run.review_claimed_by_id is None
    assert run.review_claimed_at is None


async def test_any_reviewer_may_release_another_reviewers_claim(db):
    """The reason a claim needs releasing is usually that its holder has gone."""
    service = AsyncQualifierService()
    holder = await _staff(971003, 'holder_c')
    other = await _staff(971004, 'other_c')
    player = await User.create(discord_id=971103, username='runner_c')
    _, run = await _pending_run(service, holder, player)

    await service.claim_run(holder, run.id)
    await service.release_claim(other, run.id)
    # Refused a moment ago; now open to the same reviewer.
    reviewed = await service.review_run(other, run.id, approved=True)
    assert reviewed.review_status == AsyncQualifierReviewStatus.APPROVED


async def test_an_expired_claim_does_not_block_the_next_reviewer(db):
    """Nothing releases a claim when the reviewer closes the tab, so it ages out.

    Read at review time as well as swept by the worker: a claim that expired since
    the last tick must not block for the rest of the minute.
    """
    service = AsyncQualifierService()
    holder = await _staff(971005, 'holder_d')
    other = await _staff(971006, 'other_d')
    player = await User.create(discord_id=971104, username='runner_d')
    _, run = await _pending_run(service, holder, player)

    await service.claim_run(holder, run.id)
    await AsyncQualifierRun.filter(id=run.id).update(
        review_claimed_at=datetime.now(timezone.utc) - rules.REVIEW_CLAIM_TTL - timedelta(minutes=1),
    )
    reviewed = await service.review_run(other, run.id, approved=True)
    assert reviewed.review_status == AsyncQualifierReviewStatus.APPROVED


async def test_release_stale_claim_is_idempotent(db):
    """The worker sweeps on a timer, so it will see the same run twice."""
    service = AsyncQualifierService()
    holder = await _staff(971007, 'holder_e')
    player = await User.create(discord_id=971105, username='runner_e')
    _, run = await _pending_run(service, holder, player)

    await service.claim_run(holder, run.id)
    run = await AsyncQualifierRun.get(id=run.id)
    await service.release_stale_claim(run)
    run = await AsyncQualifierRun.get(id=run.id)
    await service.release_stale_claim(run)
    run = await AsyncQualifierRun.get(id=run.id)
    assert run.review_claimed_by_id is None


# --------------------------------------------------------------- the override

async def test_an_override_audits_under_its_own_action_and_keeps_the_old_status(db):
    """"Who overturned this, and from what" is the question an appeal asks."""
    service = AsyncQualifierService()
    staff = await _staff(971008, 'reviewer_f')
    player = await User.create(discord_id=971106, username='runner_f')
    _, run = await _pending_run(service, staff, player)
    await service.review_run(staff, run.id, approved=True)

    await service.review_run(
        staff, run.id, approved=False, note='The VoD skips a dungeon', override=True,
    )

    rows = await AuditLog.filter(
        action=AuditActions.ASYNC_QUALIFIER_RUN_REVIEW_OVERRIDDEN
    ).all()
    assert len(rows) == 1, 'an override is not a second first-verdict audit row'
    details = _details(rows[0])
    assert details['previous_status'] == AsyncQualifierReviewStatus.APPROVED.value
    assert details['approved'] is False


async def test_an_override_dm_says_the_verdict_changed(db, captured_dms):
    """Arriving in the shape of a first verdict, a reversal reads as a duplicate DM."""
    service = AsyncQualifierService()
    staff = await _staff(971009, 'reviewer_g')
    player = await User.create(discord_id=971107, username='runner_g')
    _, run = await _pending_run(service, staff, player)
    await service.review_run(staff, run.id, approved=True)
    captured_dms.clear()

    await service.review_run(
        staff, run.id, approved=False, note='Timer does not match', override=True,
    )

    assert captured_dms, 'the runner is told their result changed'
    body = captured_dms[-1][1]
    assert 'changed the verdict' in body
    assert 'rejected' in body
    assert 'Timer does not match' in body


async def test_an_override_needs_a_reason(db):
    """It reaches the runner, who was already told the old verdict."""
    service = AsyncQualifierService()
    staff = await _staff(971010, 'reviewer_h')
    player = await User.create(discord_id=971108, username='runner_h')
    _, run = await _pending_run(service, staff, player)
    await service.review_run(staff, run.id, approved=True, note='fine')

    with pytest.raises(ValueError, match='reason'):
        await service.review_run(staff, run.id, approved=True, override=True)
    run = await AsyncQualifierRun.get(id=run.id)
    assert run.review_status == AsyncQualifierReviewStatus.APPROVED


async def test_the_verdict_dm_points_at_the_runners_own_runs(db, captured_dms):
    """Not the leaderboard, which a player cannot see until the qualifier closes."""
    service = AsyncQualifierService()
    staff = await _staff(971011, 'reviewer_i')
    player = await User.create(discord_id=971109, username='runner_i')
    q, run = await _pending_run(service, staff, player)
    captured_dms.clear()

    await service.review_run(staff, run.id, approved=True)

    _, body, link = captured_dms[-1]
    assert link is not None
    assert link.label == 'See your runs'
    assert link.url.endswith(f'/qualifiers/{q.id}?focus=runs')
    assert 'leaderboard' not in body.lower()
