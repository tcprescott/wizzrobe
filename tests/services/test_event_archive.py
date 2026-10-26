"""What archiving an event does beyond the status: closes race rooms, drops seed
rolls, wakes the Discord role sync, and (30 days on) deletes private data."""

from datetime import datetime, timedelta, timezone

import pytest

from application.event_context import event_scope
from application.events import EventType
from application.services import event_retention
from application.services.discord import discord_role_sync_worker
from application.services.event_service import EventService
from models import (
    AccommodationRequest,
    EmergencyContact,
    Event,
    EventStatus,
    Match,
    ProviderTask,
    ProviderTaskStatus,
    RaceRoomStatus,
    RacetimeRoom,
    Role,
    Tournament,
    User,
    UserRole,
)

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def staff(db) -> User:
    user = await User.create(discord_id=7300, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


@pytest.fixture
async def winter(db) -> Event:
    return await Event.create(tenant_id=1, name='Winter', slug='winter', status=EventStatus.LIVE)


async def _match(event: Event) -> Match:
    with event_scope(event.id):
        cup = await Tournament.create(name='Cup', is_active=True)
        return await Match.create(tournament=cup, scheduled_at=NOW)


async def test_archiving_closes_open_rooms_and_drops_unfinished_seed_rolls(staff, winter):
    match = await _match(winter)
    played = await _match(winter)
    room = await RacetimeRoom.create(slug='alttpr/cool-room-1234', category='alttpr', status=RaceRoomStatus.OPEN, match=match)
    finished = await RacetimeRoom.create(slug='alttpr/old-room-0001', category='alttpr', status=RaceRoomStatus.FINISHED, match=played)
    task = await ProviderTask.create(provider='alttpr', operation='roll', match=match, status=ProviderTaskStatus.RUNNING)

    await EventService.archive(staff, winter.id)

    assert (await RacetimeRoom.get(id=room.id)).status == RaceRoomStatus.CANCELLED
    assert (await RacetimeRoom.get(id=finished.id)).status == RaceRoomStatus.FINISHED
    assert (await ProviderTask.get(id=task.id)).status == ProviderTaskStatus.ABANDONED


def test_archiving_wakes_the_role_sync():
    assert EventType.EVENT_ARCHIVED in discord_role_sync_worker.TRIGGERS
    assert EventType.EVENT_UNARCHIVED in discord_role_sync_worker.TRIGGERS


async def _private_rows(event: Event, discord_id: int) -> User:
    person = await User.create(discord_id=discord_id, username=f'p{discord_id}')
    with event_scope(event.id):
        await EmergencyContact.create(user=person, name='Sam', phone='555-0100')
        await AccommodationRequest.create(user=person, details='Seat near the stage')
    return person


async def _archived(slug: str, archived_at: datetime) -> Event:
    event = await Event.create(tenant_id=1, name=slug.title(), slug=slug, status=EventStatus.LIVE)
    await _private_rows(event, 8000 + len(slug))
    event.status = EventStatus.ARCHIVED
    event.archived_at = archived_at
    await event.save()
    return event


async def test_private_data_goes_thirty_days_after_archive(db):
    due = await _archived('old', NOW - timedelta(days=31))
    recent = await _archived('recent', NOW - timedelta(days=29))
    live = await Event.get(tenant_id=1, is_default=True)
    await _private_rows(live, 8100)

    assert await event_retention.purge(NOW) == 1

    assert not await EmergencyContact.filter(event_id=due.id).exists()
    assert not await AccommodationRequest.filter(event_id=due.id).exists()
    assert (await Event.get(id=due.id)).pii_purged_at == NOW
    assert await EmergencyContact.filter(event_id=recent.id).exists()
    assert await EmergencyContact.filter(event_id=live.id).exists()
    assert await event_retention.purge(NOW) == 0


async def test_open_pages_hear_about_archive_and_unarchive(staff, winter):
    from application.events import event_status_live

    heard: list[int] = []
    token = event_status_live.subscribe(heard.append)
    try:
        await EventService.archive(staff, winter.id)
        await EventService.unarchive(staff, winter.id)
    finally:
        event_status_live.unsubscribe(token)
    assert heard == [winter.id, winter.id]


async def test_the_banner_says_when_private_data_is_gone(db):
    from theme.archived_banner import banner_message

    event = await _archived('gone', NOW - timedelta(days=31))
    before = await EventService.ref(1, event.id)
    assert before is not None and banner_message(before) == 'Everything here is read-only.'
    await event_retention.purge(NOW)
    after = await EventService.ref(1, event.id)
    assert after is not None and after.pii_purged
    assert 'Emergency contacts and accommodation requests were deleted' in banner_message(after)


def test_personal_and_community_tabs_stay_writable():
    from theme.archived_banner import tab_shows_event

    assert tab_shows_event({'label': 'Event'})
    assert tab_shows_event({'label': 'Shift Board', 'half': 'event'})
    assert not tab_shows_event({'label': 'Members', 'half': 'community'})
    assert not tab_shows_event({'label': 'My Schedule', 'half': 'personal'})
