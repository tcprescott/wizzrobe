"""Archived events are read-only: the ORM guard, its bulk-write helpers and the guardrail."""

import ast
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.errors import EventArchivedError
from application.event_context import event_scope
from application.event_writes import (
    GUARDED_MODELS,
    archive_maintenance,
    ensure_event_writable,
    guarded_delete,
    guarded_update,
)
from mcpserver.errors import map_service_error
from models import (
    Event,
    EventStatus,
    Match,
    MatchPlayers,
    MatchWatcher,
    Tournament,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member

ROOT = Path(__file__).resolve().parents[1]


async def _event_with_a_match(slug: str, player) -> tuple[Event, Match]:
    event = await Event.create(tenant_id=1, name=slug.upper(), slug=slug, status=EventStatus.LIVE)
    with event_scope(event.id):
        cup = await Tournament.create(name=f'{slug} cup', is_active=True)
        match = await Match.create(tournament=cup, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
        await MatchPlayers.create(match=match, user=player)
    return event, match


async def _archive(event: Event) -> None:
    event.status = EventStatus.ARCHIVED
    await event.save()


def test_children_are_guarded_through_their_parents():
    names = {m.__name__ for m in GUARDED_MODELS}
    assert {'Match', 'Tournament', 'VolunteerShift'} <= names
    assert {'MatchPlayers', 'VolunteerAssignment', 'BracketMatchGame', 'StartggSet'} <= names
    assert 'AuditLog' not in names and 'User' not in names


async def test_an_archived_events_rows_refuse_saves_creates_and_deletes(db):
    player = await make_member(discord_id=9001)
    event, match = await _event_with_a_match('old', player)
    await _archive(event)

    match.title = 'Renamed'
    with pytest.raises(EventArchivedError, match='OLD is archived'):
        await match.save()
    with pytest.raises(EventArchivedError):
        await MatchWatcher.create(match=match, user=player)
    seat = await MatchPlayers.get(match=match)
    with pytest.raises(EventArchivedError):
        await seat.delete()
    assert await MatchPlayers.filter(match=match).count() == 1


async def test_a_live_events_rows_are_untouched(db):
    player = await make_member(discord_id=9002)
    old, _ = await _event_with_a_match('old', player)
    _, match = await _event_with_a_match('now', player)
    await _archive(old)

    match.title = 'Renamed'
    await match.save()
    await MatchWatcher.create(match=match, user=player)
    assert await guarded_update(Match.filter(id=match.id), title='Again') == 1


async def test_bulk_writes_check_every_row_they_touch(db):
    player = await make_member(discord_id=9003)
    old, archived_match = await _event_with_a_match('old', player)
    _, live_match = await _event_with_a_match('now', player)
    await _archive(old)

    with pytest.raises(EventArchivedError):
        await guarded_update(Match.filter(id__in=[live_match.id, archived_match.id]), title='Both')
    with pytest.raises(EventArchivedError):
        await guarded_delete(MatchPlayers.filter(match_id=archived_match.id))
    with pytest.raises(EventArchivedError):
        await ensure_event_writable(old.id)
    assert (await Match.get(id=live_match.id)).title != 'Both'


async def test_grandchildren_find_their_event(db):
    event = await Event.create(tenant_id=1, name='Shifts', slug='shifts', status=EventStatus.LIVE)
    volunteer = await make_member(discord_id=9004)
    with event_scope(event.id):
        position = await VolunteerPosition.create(name='Runner')
        start = datetime.now(timezone.utc) + timedelta(days=2)
        shift = await VolunteerShift.create(position=position, starts_at=start, ends_at=start + timedelta(hours=2))
    await _archive(event)
    with pytest.raises(EventArchivedError):
        await VolunteerAssignment.create(shift=shift, user=volunteer)


async def test_archive_maintenance_may_write(db):
    player = await make_member(discord_id=9005)
    event, match = await _event_with_a_match('old', player)
    await _archive(event)
    with archive_maintenance():
        match.title = 'Closed out'
        await match.save()
        await guarded_delete(MatchPlayers.filter(match=match))
    assert (await Match.get(id=match.id)).title == 'Closed out'


async def test_rest_answers_409(db, app):
    _, raw = await create_user_token(username='fan')
    player = await make_member(discord_id=9006)
    event, match = await _event_with_a_match('old', player)
    await _archive(event)
    async with client_for(app, raw) as c:
        response = await c.post(f'/api/matches/{match.id}/watch')
    assert response.status_code == 409
    assert 'archived' in response.json()['detail']


def test_mcp_reports_read_only():
    assert str(map_service_error(EventArchivedError('SGL 2025'))).startswith('read_only: SGL 2025 is archived')


def _guardrail():
    spec = importlib.util.spec_from_file_location('gating', ROOT / '.claude/scripts/check_event_write_gating.py')
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(ROOT / '.claude/scripts'))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


@pytest.mark.parametrize('source, flagged', [
    ('async def f(x):\n    await scoped(Match.filter(id=x)).update(title="t")\n', True),
    ('async def f(x):\n    qs = scoped(MatchPlayers.filter(match_id=x))\n    await qs.delete()\n', True),
    ('async def f(rows):\n    await Match.bulk_create(rows)\n', True),
    ('async def f(x):\n    qs = Match.filter(id=x)\n    await ensure_writable(qs)\n    await qs.update(title="t")\n', False),
    ('async def f(x):\n    await guarded_update(Match.filter(id=x), title="t")\n', False),
    ('async def f(match):\n    await match.delete()\n', False),
    ('async def f(x):\n    await scoped(AuditLog.filter(id=x)).delete()\n', False),
    ('async def f(x):\n    # event-write: exempt — archiving itself\n    await Match.filter(id=x).update(title="t")\n', False),
])
def test_guardrail_flags_unchecked_bulk_writes(source, flagged):
    gating = _guardrail()
    guarded = gating.guarded_models(str(ROOT / 'models'))
    found = gating.find_violations(ast.parse(source), guarded, source.splitlines())
    assert bool(found) is flagged
