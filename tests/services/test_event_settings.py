"""Event settings: the five moved keys and the dates are each event's own, and an
event can set its own timezone over the community's."""

from datetime import date

import pytest

from application.event_context import event_scope
from application.services.system_config_service import (
    KEY_DISCORD_INVITE_URL,
    KEY_EVENT_START_DATE,
    KEY_MAX_CONCURRENT_PLAYERS,
    SystemConfigService,
)
from application.services.timezone_service import MODE_PINNED, TimezoneService
from models import Event, EventStatus, Role, SystemConfiguration, User, UserRole


@pytest.fixture
async def staff(db) -> User:
    user = await User.create(discord_id=7300, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


@pytest.fixture
async def two_events(db):
    first = await Event.get(tenant_id=1, is_default=True)
    second = await Event.create(tenant_id=1, name='Winter', slug='winter', status=EventStatus.LIVE)
    return first, second


async def test_capacity_is_set_per_event(staff, two_events):
    first, second = two_events
    with event_scope(first.id):
        await SystemConfigService.set_raw(KEY_MAX_CONCURRENT_PLAYERS, '40', staff)
    with event_scope(second.id):
        assert await SystemConfigService.get_max_concurrent_players() == 60
        await SystemConfigService.set_raw(KEY_MAX_CONCURRENT_PLAYERS, '12', staff)
    with event_scope(first.id):
        assert await SystemConfigService.get_max_concurrent_players() == 40
    await first.refresh_from_db()
    assert first.config[KEY_MAX_CONCURRENT_PLAYERS] == '40'
    assert not await SystemConfiguration.filter(name=KEY_MAX_CONCURRENT_PLAYERS).exists()


async def test_the_event_dates_are_the_events_own_columns(staff, two_events):
    first, _second = two_events
    with event_scope(first.id):
        await SystemConfigService.set_raw(KEY_EVENT_START_DATE, '2027-10-21', staff)
        assert await SystemConfigService.get_date(KEY_EVENT_START_DATE) == date(2027, 10, 21)
        await SystemConfigService.set_raw(KEY_EVENT_START_DATE, '', staff)
    await first.refresh_from_db()
    assert first.starts_on is None


async def test_a_community_setting_stays_one_row(staff, two_events):
    first, second = two_events
    with event_scope(first.id):
        await SystemConfigService.set_raw(KEY_DISCORD_INVITE_URL, 'https://discord.gg/abc', staff)
    with event_scope(second.id):
        assert await SystemConfigService.get_raw(KEY_DISCORD_INVITE_URL) == 'https://discord.gg/abc'


async def test_an_event_timezone_overrides_the_communitys(staff, two_events):
    first, second = two_events
    with event_scope(second.id):
        await TimezoneService.set_event_override(staff, MODE_PINNED, 'Europe/Berlin')
        assert await TimezoneService.get_settings() == {'mode': MODE_PINNED, 'name': 'Europe/Berlin'}
        assert await TimezoneService.tenant_timezone_name() == 'Europe/Berlin'
    with event_scope(first.id):
        assert (await TimezoneService.get_settings())['name'] != 'Europe/Berlin'
    with event_scope(second.id):
        await TimezoneService.set_event_override(staff, None)
        assert await TimezoneService.event_override() is None


async def test_a_bad_event_timezone_is_refused(staff, two_events):
    _first, second = two_events
    with event_scope(second.id), pytest.raises(ValueError, match='recognised'):
        await TimezoneService.set_event_override(staff, MODE_PINNED, 'Mars/Olympus')
