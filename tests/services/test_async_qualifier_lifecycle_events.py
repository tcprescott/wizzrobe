"""The audit's F14 — what a webhook subscriber can learn about a qualifier.

Four `EventType` members covered run submitted, reviewed, expired and live-race
recorded. Nothing published when the qualifier itself **opened** — the announcement
a Discord integration would most want to make — nor when a runner forfeited or
voided a run, both of which move a standing exactly as an expiry does.

The window is a state, not an edit: `opens_at` was set weeks ago by a PATCH that
also touched six other fields, and the crossing happens later with no request in
flight. So it is observed and published once, stamped on the qualifier so a worker
tick that changes nothing says nothing.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.events import EventType, event_bus
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import AsyncQualifier, AsyncQualifierRun, Role, User, UserRole

pytestmark = pytest.mark.anyio

_WINDOW_EVENTS = [EventType.ASYNC_QUALIFIER_OPENED, EventType.ASYNC_QUALIFIER_CLOSED]


def _capture(event_types):
    """Collect (event_type, payload) for the given types as they are published."""
    seen: list = []
    event_bus.subscribe_sync(lambda e: seen.append((e.event_type, e.payload)), event_types)
    return seen


async def _staff(discord_id: int = 900801, name: str = 'evstaff') -> User:
    u = await User.create(discord_id=discord_id, username=name)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    return u


async def _player(discord_id: int, name: str) -> User:
    return await User.create(discord_id=discord_id, username=name)


async def _qualifier(service, staff, *, opens_in=None, closes_in=None, name='Event Q'):
    now = datetime.now(timezone.utc)
    return await service.create_qualifier(
        staff, name=name,
        opens_at=now + opens_in if opens_in is not None else None,
        closes_at=now + closes_in if closes_in is not None else None,
        runs_per_pool=2, allowed_reattempts=1,
    )


async def test_a_qualifier_created_inside_its_window_announces_itself(db):
    service = AsyncQualifierService()
    staff = await _staff()
    seen = _capture(_WINDOW_EVENTS)

    q = await _qualifier(service, staff, opens_in=timedelta(days=-1), closes_in=timedelta(days=1))

    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_OPENED]
    assert seen[0][1]['qualifier_id'] == q.id
    assert seen[0][1]['name'] == 'Event Q'
    assert (await AsyncQualifier.get(id=q.id)).window_state_notified == 'open'


async def test_a_qualifier_that_has_not_opened_yet_announces_nothing(db):
    service = AsyncQualifierService()
    staff = await _staff(900802, 'evstaff2')
    seen = _capture(_WINDOW_EVENTS)

    q = await _qualifier(service, staff, opens_in=timedelta(days=1), closes_in=timedelta(days=2))

    assert seen == []
    assert (await AsyncQualifier.get(id=q.id)).window_state_notified == 'pending'


async def test_the_worker_announces_the_crossing_when_the_clock_reaches_it(db):
    """Nobody performs an opening, so the tick that observes it is what publishes."""
    service = AsyncQualifierService()
    staff = await _staff(900803, 'evstaff3')
    q = await _qualifier(service, staff, opens_in=timedelta(hours=1), closes_in=timedelta(days=2))
    seen = _capture(_WINDOW_EVENTS)

    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert await service.sync_window_state(q, now=later) is rules.WindowState.OPEN
    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_OPENED]

    q = await AsyncQualifier.get(id=q.id)
    assert await service.sync_window_state(q, now=later + timedelta(minutes=1)) is None
    assert len(seen) == 1


async def test_closing_is_announced_only_for_a_qualifier_that_was_open(db):
    service = AsyncQualifierService()
    staff = await _staff(900804, 'evstaff4')
    q = await _qualifier(service, staff, opens_in=timedelta(days=-1), closes_in=timedelta(hours=1))
    seen = _capture(_WINDOW_EVENTS)

    after = datetime.now(timezone.utc) + timedelta(hours=2)
    assert await service.sync_window_state(
        await AsyncQualifier.get(id=q.id), now=after) is rules.WindowState.CLOSED
    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_CLOSED]


async def test_a_qualifier_whose_window_ended_before_anyone_looked_stays_quiet(db):
    """Recorded, not announced: nothing closed — it was never open to a subscriber."""
    service = AsyncQualifierService()
    staff = await _staff(900805, 'evstaff5')
    seen = _capture(_WINDOW_EVENTS)

    q = await _qualifier(service, staff, opens_in=timedelta(days=-3), closes_in=timedelta(days=-1))

    assert seen == []
    assert (await AsyncQualifier.get(id=q.id)).window_state_notified == 'closed'


async def test_switching_a_qualifier_off_closes_it_for_subscribers(db):
    """The one crossing the worker cannot see: an inactive qualifier leaves its scan."""
    service = AsyncQualifierService()
    staff = await _staff(900806, 'evstaff6')
    q = await _qualifier(service, staff, opens_in=timedelta(days=-1), closes_in=timedelta(days=1))
    seen = _capture(_WINDOW_EVENTS)

    await service.update_qualifier(staff, q.id, is_active=False)

    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_CLOSED]
    assert (await AsyncQualifier.get(id=q.id)).window_state_notified == 'closed'


async def test_reopening_announces_again(db):
    service = AsyncQualifierService()
    staff = await _staff(900807, 'evstaff7')
    q = await _qualifier(service, staff, opens_in=timedelta(days=-1), closes_in=timedelta(days=1))
    await service.update_qualifier(staff, q.id, is_active=False)
    seen = _capture(_WINDOW_EVENTS)

    await service.update_qualifier(staff, q.id, is_active=True)

    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_OPENED]


async def test_a_forfeit_and_a_reattempt_both_reach_subscribers(db):
    service = AsyncQualifierService()
    staff = await _staff(900808, 'evstaff8')
    q = await _qualifier(service, staff, opens_in=timedelta(days=-1), closes_in=timedelta(days=1))
    pool = await service.create_pool(staff, q.id, name='Pool A')
    for i in range(2):
        await service.add_permalink(staff, pool.id, url=f'https://seed.test/ev-{i}')
    player = await _player(900820, 'evplayer')
    seen = _capture([EventType.ASYNC_QUALIFIER_RUN_FORFEITED,
                     EventType.ASYNC_QUALIFIER_RUN_REATTEMPTED])

    run = await service.start_run(player, q.id, pool.id)
    await service.forfeit_run(player, run.id)
    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_RUN_FORFEITED]
    assert seen[0][1]['user_id'] == player.id

    await service.reattempt_run(player, run.id, reason='Mis-clicked forfeit.')
    assert [t for t, _ in seen][-1] == EventType.ASYNC_QUALIFIER_RUN_REATTEMPTED
    assert seen[-1][1]['granted'] is False


async def test_a_granted_reattempt_publishes_the_same_fact_marked_granted(db):
    """One external fact — a run voided, a slot reopened — with who spent it on it."""
    service = AsyncQualifierService()
    staff = await _staff(900809, 'evstaff9')
    q = await _qualifier(service, staff, opens_in=timedelta(days=-1), closes_in=timedelta(days=1))
    pool = await service.create_pool(staff, q.id, name='Pool A')
    await service.add_permalink(staff, pool.id, url='https://seed.test/ev-grant')
    player = await _player(900821, 'evplayer2')

    run = await service.start_run(player, q.id, pool.id)
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=1300))
    run = await AsyncQualifierRun.get(id=run.id)
    run = await service.submit_run(player, run.id, elapsed_seconds=1200)

    seen = _capture([EventType.ASYNC_QUALIFIER_RUN_REATTEMPTED])
    await service.grant_reattempt(staff, run.id, reason='Seed would not load.')

    assert [t for t, _ in seen] == [EventType.ASYNC_QUALIFIER_RUN_REATTEMPTED]
    details = seen[0][1]
    assert details['granted'] is True
    assert details['user_id'] == player.id
