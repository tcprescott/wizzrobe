"""The review queue's signal to reviewers — F9's second half.

``submit_run`` audited and published an event and nothing reached a human, so the
only way to discover work was to open a drill-down and look. This is the nudge that
replaces that silence, and the two gates that keep it from becoming the opposite
problem: a message per submission, which at real scale trains every reviewer to
mute the bot.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import AsyncQualifier, AsyncQualifierRun, Role, TenantMembership, User, UserRole

pytestmark = pytest.mark.anyio


@pytest.fixture
def captured_dms(monkeypatch):
    sent: list[tuple[int, str, object]] = []

    class _Fake:
        async def send_dm(self, discord_id, message, view_factory=None, embed=None, link=None, notice=None):
            sent.append((discord_id, message, link))
            return True, 'ok'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService', _Fake,
    )
    return sent


async def _staff(discord_id: int, name: str, role: Role = Role.STAFF) -> User:
    u = await User.create(discord_id=str(discord_id), username=name)
    await UserRole.create(user=u, role=role, tenant_id=1)
    # Holding a role in a tenant implies membership in it (see
    # ``UserRepository.get_community_people``), which is the basis the
    # QUALIFIER_ADMIN fallback reads — so a fixture that skips it is not a
    # production state.
    await TenantMembership.create(user=u, tenant_id=1)
    return u


async def _submitted_run(service, staff, player, *, waited: timedelta):
    """A run sitting in the review queue, submitted ``waited`` ago."""
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        staff, name='Backlog Q', opens_at=now - timedelta(days=2),
        closes_at=now + timedelta(days=2), runs_per_pool=1,
    )
    pool = await service.create_pool(staff, q.id, name='Pool A')
    await service.add_permalink(staff, pool.id, url='https://example.test/a')
    run = await service.start_run(player, q.id, pool.id)
    await AsyncQualifierRun.filter(id=run.id).update(started_at=now - timedelta(seconds=3700))
    run = await AsyncQualifierRun.get(id=run.id)
    run = await service.submit_run(player, run.id, elapsed_seconds=3600)
    # Backdate the submission itself — the wait is what decides.
    await AsyncQualifierRun.filter(id=run.id).update(finished_at=now - waited)
    return await AsyncQualifier.get(id=q.id), run


# ------------------------------------------------------------------ the gates

def test_a_fresh_queue_is_not_worth_interrupting_anyone_about():
    now = datetime.now(timezone.utc)
    assert rules.backlog_is_worth_reporting(now - timedelta(minutes=5), None, now) is False
    assert rules.backlog_is_worth_reporting(None, None, now) is False, 'an empty queue'


def test_an_old_queue_is():
    now = datetime.now(timezone.utc)
    old = now - rules.REVIEW_BACKLOG_AGE - timedelta(minutes=1)
    assert rules.backlog_is_worth_reporting(old, None, now) is True


def test_the_reminder_does_not_repeat_faster_than_its_interval():
    now = datetime.now(timezone.utc)
    old = now - rules.REVIEW_BACKLOG_AGE - timedelta(hours=5)
    assert rules.backlog_is_worth_reporting(old, now - timedelta(minutes=1), now) is False
    assert rules.backlog_is_worth_reporting(
        old, now - rules.REVIEW_BACKLOG_REMINDER - timedelta(minutes=1), now,
    ) is True


def test_a_naive_timestamp_is_read_as_utc():
    """Storage keeps datetimes naive on some paths; a wrong reading skews the gate."""
    now = datetime.now(timezone.utc)
    naive_old = (now - rules.REVIEW_BACKLOG_AGE - timedelta(hours=1)).replace(tzinfo=None)
    assert rules.backlog_is_worth_reporting(naive_old, None, now) is True


# ------------------------------------------------------------- the nudge itself

async def test_a_backlog_dms_the_qualifiers_own_reviewers(db, captured_dms):
    service = AsyncQualifierService()
    staff = await _staff(900401, 'backlog_staff')
    reviewer = await _staff(900402, 'backlog_reviewer')
    player = await User.create(discord_id='900403', username='backlog_runner')
    qualifier, _ = await _submitted_run(
        service, staff, player, waited=rules.REVIEW_BACKLOG_AGE + timedelta(hours=2))
    await service.add_admin(staff, qualifier.id, reviewer)
    qualifier = await AsyncQualifier.get(id=qualifier.id)

    sent = await service.notify_review_backlog(qualifier)
    assert sent == 1, 'the qualifier has exactly one reviewer'
    recipient, body, link = captured_dms[-1]
    assert recipient == 900402
    assert '1 run awaiting review' in body
    assert 'waiting 8 hours' in body
    assert link is not None, 'the DM lands on the queue, not on a page to search'


async def test_a_fresh_submission_dms_nobody(db, captured_dms):
    service = AsyncQualifierService()
    staff = await _staff(900404, 'backlog_staff_b')
    reviewer = await _staff(900405, 'backlog_reviewer_b')
    player = await User.create(discord_id='900406', username='backlog_runner_b')
    qualifier, _ = await _submitted_run(service, staff, player, waited=timedelta(minutes=2))
    await service.add_admin(staff, qualifier.id, reviewer)
    qualifier = await AsyncQualifier.get(id=qualifier.id)

    assert await service.notify_review_backlog(qualifier) == 0
    assert captured_dms == []


async def test_the_nudge_does_not_repeat_on_the_next_tick(db, captured_dms):
    """The worker runs every 60 seconds; the backlog will still be there."""
    service = AsyncQualifierService()
    staff = await _staff(900407, 'backlog_staff_c')
    reviewer = await _staff(900408, 'backlog_reviewer_c')
    player = await User.create(discord_id='900409', username='backlog_runner_c')
    qualifier, _ = await _submitted_run(
        service, staff, player, waited=rules.REVIEW_BACKLOG_AGE + timedelta(hours=1))
    await service.add_admin(staff, qualifier.id, reviewer)
    qualifier = await AsyncQualifier.get(id=qualifier.id)

    assert await service.notify_review_backlog(qualifier) == 1
    qualifier = await AsyncQualifier.get(id=qualifier.id)
    assert qualifier.review_backlog_notified_at is not None, 'stamped before sending'
    assert await service.notify_review_backlog(qualifier) == 0
    assert len(captured_dms) == 1


async def test_with_no_named_reviewers_it_falls_back_to_the_qualifier_admin_role(db, captured_dms):
    """A qualifier with no roster is administered by whoever holds the role."""
    service = AsyncQualifierService()
    staff = await _staff(900410, 'backlog_staff_d')
    qa = await _staff(900411, 'backlog_qa', role=Role.QUALIFIER_ADMIN)
    player = await User.create(discord_id='900412', username='backlog_runner_d')
    qualifier, _ = await _submitted_run(
        service, staff, player, waited=rules.REVIEW_BACKLOG_AGE + timedelta(hours=1))

    sent = await service.notify_review_backlog(qualifier)
    assert sent == 1
    assert captured_dms[-1][0] == 900411, 'the role holder, not community staff at large'
    assert qa.username in 'backlog_qa'


async def test_with_nobody_to_tell_it_stays_unstamped(db, captured_dms):
    """So the first reviewer added hears about the backlog that was already there."""
    service = AsyncQualifierService()
    # A staff member creates the qualifier but holds no QUALIFIER_ADMIN role, and
    # the qualifier has no roster of its own.
    staff = await _staff(900413, 'backlog_staff_e')
    player = await User.create(discord_id='900414', username='backlog_runner_e')
    qualifier, _ = await _submitted_run(
        service, staff, player, waited=rules.REVIEW_BACKLOG_AGE + timedelta(hours=1))

    assert await service.notify_review_backlog(qualifier) == 0
    qualifier = await AsyncQualifier.get(id=qualifier.id)
    assert qualifier.review_backlog_notified_at is None
    assert captured_dms == []

    reviewer = await _staff(900415, 'backlog_reviewer_e')
    await service.add_admin(staff, qualifier.id, reviewer)
    qualifier = await AsyncQualifier.get(id=qualifier.id)
    assert await service.notify_review_backlog(qualifier) == 1


async def test_a_reviewer_without_discord_is_skipped_rather_than_crashing(db, captured_dms):
    service = AsyncQualifierService()
    staff = await _staff(900416, 'backlog_staff_f')
    silent = await User.create(username='backlog_no_discord')
    await TenantMembership.create(user=silent, tenant_id=1)
    player = await User.create(discord_id='900417', username='backlog_runner_f')
    qualifier, _ = await _submitted_run(
        service, staff, player, waited=rules.REVIEW_BACKLOG_AGE + timedelta(hours=1))
    await service.add_admin(staff, qualifier.id, silent)
    qualifier = await AsyncQualifier.get(id=qualifier.id)

    assert await service.notify_review_backlog(qualifier) == 0
    assert captured_dms == []


async def test_a_settled_queue_reports_nothing(db, captured_dms):
    service = AsyncQualifierService()
    staff = await _staff(900418, 'backlog_staff_g')
    reviewer = await _staff(900419, 'backlog_reviewer_g')
    player = await User.create(discord_id='900420', username='backlog_runner_g')
    qualifier, run = await _submitted_run(
        service, staff, player, waited=rules.REVIEW_BACKLOG_AGE + timedelta(hours=1))
    await service.add_admin(staff, qualifier.id, reviewer)
    await service.review_run(staff, run.id, approved=True)
    qualifier = await AsyncQualifier.get(id=qualifier.id)

    assert await service.notify_review_backlog(qualifier) == 0
