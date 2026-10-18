"""The save-time clash warning: ``MatchSuggestionService.find_clashes``.

Advisory reasons a chosen time might not work. The booking dialogs show them and
let the person save anyway, so these tests only pin what is reported and how
much of another player's private time it gives away.
"""

import json
from datetime import date, datetime, timedelta

import pytest

from application.services.match.match_suggestion_service import ClashKind, MatchSuggestionService
from application.utils.timezone import EASTERN_TZ
from models import (
    Commentator,
    Match,
    MatchPlayers,
    PlayerAvailability,
    Role,
    SystemConfiguration,
    Tournament,
    UserRole,
    VolunteerAssignment,
    VolunteerAvailabilityStatus,
    VolunteerPosition,
    VolunteerShift,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_member

THU, FRI, SAT = date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 24)
T = DEFAULT_TEST_TENANT_ID


def _at(d: date, h: int, mi: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=EASTERN_TZ)


@pytest.fixture
async def event(db):
    await SystemConfiguration.create(name='event_start_date', value=THU.isoformat())
    await SystemConfiguration.create(name='event_end_date', value=SAT.isoformat())
    await SystemConfiguration.create(
        name='tournament_hours_by_date',
        value=json.dumps({d.isoformat(): {'open': '08:00', 'close': '23:59'} for d in (THU, FRI)}),
    )
    await SystemConfiguration.create(name='max_concurrent_players', value='4')
    tournament = await Tournament.create(name='ALttPR', average_match_duration=90, tenant_id=T)
    alice = await make_member(9001, 'alice')
    bob = await make_member(9002, 'bob')
    return tournament, alice, bob


async def _match(tournament, when, *users) -> Match:
    match = await Match.create(tournament=tournament, scheduled_at=when, tenant_id=T)
    for u in users:
        await MatchPlayers.create(match=match, user=u, tenant_id=T)
    return match


async def _clashes(tournament, actor, players, when, **kw):
    return await MatchSuggestionService().find_clashes(
        tournament.id, [p.id for p in players], when, actor=actor, **kw,
    )


async def test_a_clear_time_has_no_clashes(event):
    tournament, alice, bob = event
    assert await _clashes(tournament, alice, [alice, bob], _at(THU, 10)) == []


async def test_reports_an_overlapping_match_by_name(event):
    tournament, alice, bob = event
    carol = await make_member(9003, 'carol')
    await _match(tournament, _at(THU, 9, 30), bob, carol)

    clashes = await _clashes(tournament, alice, [alice, bob], _at(THU, 10))

    assert [c.kind for c in clashes] == [ClashKind.PLAYING]
    assert 'bob is already playing at 09:30' in clashes[0].message
    assert 'vs carol' in clashes[0].message


async def test_the_match_being_moved_does_not_clash_with_itself(event):
    tournament, alice, bob = event
    match = await _match(tournament, _at(THU, 10), alice, bob)

    assert await _clashes(
        tournament, alice, [alice, bob], _at(THU, 10, 30), exclude_match_id=match.id,
    ) == []


async def test_an_approved_crew_slot_clashes_but_a_pending_one_does_not(event):
    tournament, alice, bob = event
    carol, dave = await make_member(9003, 'carol'), await make_member(9004, 'dave')
    other = await _match(tournament, _at(THU, 10), carol, dave)
    await Commentator.create(match=other, user=bob, approved=True, tenant_id=T)
    await Commentator.create(match=other, user=alice, approved=False, tenant_id=T)

    clashes = await _clashes(tournament, alice, [alice, bob], _at(THU, 10))

    assert [c.message for c in clashes if c.kind == ClashKind.CREW] == [
        'bob is commentating a match at 10:00.',
    ]


async def test_room_full_counts_everyone_on_the_floor(event):
    tournament, alice, bob = event
    others = [await make_member(9100 + i, f'p{i}') for i in range(4)]
    await _match(tournament, _at(THU, 10), others[0], others[1])
    await _match(tournament, _at(THU, 10), others[2], others[3])

    clashes = await _clashes(tournament, alice, [alice, bob], _at(THU, 10))

    assert [c.kind for c in clashes] == [ClashKind.ROOM_FULL]
    assert '6 players at once' in clashes[0].message


async def test_running_past_closing_is_flagged(event):
    tournament, alice, bob = event
    clashes = await _clashes(tournament, alice, [alice, bob], _at(THU, 23))
    assert [c.kind for c in clashes] == [ClashKind.PAST_CLOSE]
    assert 'runs to 00:30' in clashes[0].message


async def test_a_day_with_no_hours_is_flagged(event):
    tournament, alice, bob = event
    clashes = await _clashes(tournament, alice, [alice, bob], _at(SAT, 10))
    assert [c.kind for c in clashes] == [ClashKind.NO_HOURS]


async def test_a_day_outside_the_event_is_flagged(event):
    tournament, alice, bob = event
    clashes = await _clashes(tournament, alice, [alice, bob], _at(SAT + timedelta(days=1), 10))
    assert [c.kind for c in clashes] == [ClashKind.OUTSIDE_EVENT]


async def _private_time(bob):
    await PlayerAvailability.create(
        user=bob, starts_at=_at(THU, 9), ends_at=_at(THU, 12),
        status=VolunteerAvailabilityStatus.UNAVAILABLE, tenant_id=T,
    )
    position = await VolunteerPosition.create(name='Check-in desk', tenant_id=T)
    shift = await VolunteerShift.create(
        position=position, starts_at=_at(THU, 10), ends_at=_at(THU, 14), tenant_id=T,
    )
    await VolunteerAssignment.create(shift=shift, user=bob, tenant_id=T)


async def test_an_opponent_sees_only_that_a_player_is_not_free(event):
    tournament, alice, bob = event
    await _private_time(bob)

    clashes = await _clashes(tournament, alice, [alice, bob], _at(THU, 10))

    assert [(c.kind, c.message) for c in clashes] == [
        (ClashKind.NOT_FREE, "bob isn't free then."),
    ]


async def test_a_player_sees_their_own_private_time(event):
    tournament, alice, bob = event
    await _private_time(bob)

    clashes = await _clashes(tournament, bob, [alice, bob], _at(THU, 10))

    kinds = sorted(c.kind for c in clashes)
    assert kinds == sorted([ClashKind.BLOCKED, ClashKind.SHIFT])
    assert all('bob (you)' in c.message for c in clashes)
    assert any('Check-in desk' in c.message for c in clashes)


async def test_staff_see_everyones_private_time(event):
    tournament, alice, bob = event
    await _private_time(bob)
    staff = await make_member(9999, 'staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=T)

    clashes = await _clashes(tournament, staff, [alice, bob], _at(THU, 10))

    assert sorted(c.kind for c in clashes) == sorted([ClashKind.BLOCKED, ClashKind.SHIFT])


async def test_a_draft_shift_is_not_reported(event):
    tournament, alice, bob = event
    position = await VolunteerPosition.create(name='Desk', tenant_id=T)
    shift = await VolunteerShift.create(
        position=position, starts_at=_at(THU, 10), ends_at=_at(THU, 14), tenant_id=T,
    )
    await VolunteerAssignment.create(shift=shift, user=bob, auto_generated=True, tenant_id=T)

    assert await _clashes(tournament, bob, [alice, bob], _at(THU, 10)) == []


async def test_a_suggested_time_comes_back_clean(event, monkeypatch):
    import application.services.match.match_suggestion_service as mss

    tournament, alice, bob = event
    monkeypatch.setattr(mss, 'now_local', lambda tz=None: _at(THU, 9).astimezone(tz or EASTERN_TZ))
    carol = await make_member(9003, 'carol')
    await _match(tournament, _at(THU, 9, 30), bob, carol)
    await _private_time(alice)

    when = await MatchSuggestionService().suggest_match_time(tournament.id, [alice.id, bob.id])

    assert [c.kind for c in await _clashes(tournament, alice, [alice, bob], when)] == [
        ClashKind.SHIFT,
    ]
