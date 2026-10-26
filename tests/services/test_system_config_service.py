"""Unit tests for SystemConfigService.

These tests patch the model-layer methods (SystemConfiguration.get_or_none,
Match.all, Stage.filter) so the typed accessors and the
``get_event_window`` fallback chain are exercised without a database.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.services.system_config_service import (
    KEY_EVENT_END_DATE,
    KEY_EVENT_START_DATE,
    KEY_STATION_FORMAT,
    SystemConfigService,
)
from models import StationFormat
from tests.factories import make_audit_double


@pytest.fixture
def stub_storage(monkeypatch):
    """Fixture providing a fake SystemConfiguration key/value store + helpers
    to patch the model layer the service depends on.
    """

    storage: dict[str, str] = {}

    async def fake_get_or_none(**kwargs):
        name = kwargs.get('name')
        if name in storage:
            return SimpleNamespace(
                name=name, value=storage[name], save=AsyncMock(), _meta=SimpleNamespace(fields_map={}),
            )
        return None

    async def fake_create(**kwargs):
        storage[kwargs['name']] = kwargs['value']
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(
        'application.services.system_config_service.SystemConfiguration.get_or_none',
        fake_get_or_none,
    )
    monkeypatch.setattr(
        'application.services.system_config_service.SystemConfiguration.create',
        fake_create,
    )

    # The event settings live on the event; the same store stands in for it.
    async def fake_get_event_raw(key):
        return storage.get(key)

    async def fake_set_event_raw(key, value):
        old = storage.get(key)
        storage[key] = value
        return old

    monkeypatch.setattr(SystemConfigService, '_get_event_raw', staticmethod(fake_get_event_raw))
    monkeypatch.setattr(SystemConfigService, '_set_event_raw', staticmethod(fake_set_event_raw))
    return storage


pytestmark = pytest.mark.usefixtures("bypass_auth")
@pytest.fixture
def bypass_audit(monkeypatch):
    """Patch the AuditService class so set_raw doesn't try to write a log."""
    mock_audit = make_audit_double()
    monkeypatch.setattr(
        'application.services.system_config_service.AuditService',
        lambda: mock_audit,
    )
    return mock_audit


def make_user(user_id=1):
    return SimpleNamespace(id=user_id, username='staff')


# ---------------------------------------------------------------------------
# get_raw / get_int / get_date
# ---------------------------------------------------------------------------


class TestGetRaw:
    async def test_returns_value_when_present(self, stub_storage):
        stub_storage['k'] = 'v'
        assert await SystemConfigService.get_raw('k') == 'v'

    async def test_returns_none_when_missing(self, stub_storage):
        assert await SystemConfigService.get_raw('missing') is None


class TestGetInt:
    async def test_returns_int_when_value_parses(self, stub_storage):
        stub_storage['n'] = '42'
        assert await SystemConfigService.get_int('n') == 42

    async def test_returns_default_when_missing(self, stub_storage):
        assert await SystemConfigService.get_int('absent', default=7) == 7

    async def test_returns_default_when_empty_string(self, stub_storage):
        stub_storage['n'] = ''
        assert await SystemConfigService.get_int('n', default=9) == 9

    async def test_returns_default_when_unparseable(self, stub_storage):
        stub_storage['n'] = 'not-a-number'
        assert await SystemConfigService.get_int('n', default=5) == 5

    async def test_returns_negative_int(self, stub_storage):
        stub_storage['n'] = '-3'
        assert await SystemConfigService.get_int('n') == -3


class TestGetDate:
    async def test_returns_date_when_valid_iso(self, stub_storage):
        stub_storage['d'] = '2025-10-23'
        assert await SystemConfigService.get_date('d') == date(2025, 10, 23)

    async def test_returns_default_when_missing(self, stub_storage):
        default = date(2025, 1, 1)
        assert await SystemConfigService.get_date('absent', default=default) == default

    async def test_returns_default_when_blank(self, stub_storage):
        stub_storage['d'] = ''
        assert await SystemConfigService.get_date('d', default=date(2030, 6, 1)) == date(2030, 6, 1)

    async def test_returns_default_when_unparseable(self, stub_storage):
        stub_storage['d'] = '10/23/2025'
        assert await SystemConfigService.get_date('d', default=None) is None


# ---------------------------------------------------------------------------
# set_raw — permission gate, upsert, audit
# ---------------------------------------------------------------------------


class TestSetRaw:
    async def test_creates_new_when_missing(self, stub_storage, bypass_audit):
        await SystemConfigService.set_raw('new_key', 'new_value', make_user())
        assert stub_storage['new_key'] == 'new_value'

    async def test_updates_existing(self, stub_storage, bypass_audit, monkeypatch):
        # Use a stronger stub that lets save() mutate the storage dict.
        stub_storage['k'] = 'old'

        async def fake_get_or_none(**kwargs):
            name = kwargs.get('name')
            if name not in stub_storage:
                return None
            obj = SimpleNamespace(name=name, value=stub_storage[name], _meta=SimpleNamespace(fields_map={}))
            async def _save(update_fields=None):
                stub_storage[name] = obj.value
            obj.save = _save
            return obj

        monkeypatch.setattr(
            'application.services.system_config_service.SystemConfiguration.get_or_none',
            fake_get_or_none,
        )
        await SystemConfigService.set_raw('k', 'new', make_user())
        assert stub_storage['k'] == 'new'

    async def test_writes_audit_log_with_old_and_new_values(self, stub_storage, bypass_audit):
        stub_storage['k'] = 'before'

        # Replace get_or_none to return a settable namespace.
        async def fake_get_or_none(**kwargs):
            name = kwargs.get('name')
            if name not in stub_storage:
                return None
            ns = SimpleNamespace(name=name, value=stub_storage[name], _meta=SimpleNamespace(fields_map={}))
            async def _save(update_fields=None):
                stub_storage[name] = ns.value
            ns.save = _save
            return ns

        from unittest.mock import patch
        with patch(
            'application.services.system_config_service.SystemConfiguration.get_or_none',
            fake_get_or_none,
        ):
            await SystemConfigService.set_raw('k', 'after', make_user())

        bypass_audit.write_log.assert_awaited_once()
        args = bypass_audit.write_log.await_args.args
        details = args[2]
        assert details == {'key': 'k', 'old_value': 'before', 'new_value': 'after'}

    async def test_non_staff_is_denied(self, stub_storage, bypass_audit, monkeypatch):
        from application.services import auth_service

        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(auth_service.AuthService, 'is_staff', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', real_ensure)

        with pytest.raises(PermissionError):
            await SystemConfigService.set_raw('k', 'v', make_user())


# ---------------------------------------------------------------------------
# get_event_window — fallback chain
# ---------------------------------------------------------------------------


def _patch_match_minmax(monkeypatch, first_at=None, last_at=None):
    """Patch Match.all().order_by('scheduled_at').first() and the reverse."""
    first_match = SimpleNamespace(scheduled_at=first_at) if first_at else None
    last_match = SimpleNamespace(scheduled_at=last_at) if last_at else None

    class FakeQS:
        def __init__(self, key):
            self._key = key

        def order_by(self, ordering):
            self._ordering = ordering
            return self

        async def first(self):
            if self._ordering == 'scheduled_at':
                return first_match
            if self._ordering == '-scheduled_at':
                return last_match
            return None

    # The service scopes by event: event_scoped(Match.all(), ...).
    monkeypatch.setattr(
        'application.services.system_config_service.Match.all', lambda: FakeQS('match_qs'),
    )
    monkeypatch.setattr(
        'application.services.system_config_service.event_scoped', lambda qs, *_a: qs,
    )


class TestGetEventWindow:
    async def test_uses_system_config_when_both_set(self, stub_storage, monkeypatch):
        stub_storage[KEY_EVENT_START_DATE] = '2025-10-20'
        stub_storage[KEY_EVENT_END_DATE] = '2025-10-23'
        _patch_match_minmax(monkeypatch)
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 10, 20)
        assert end == date(2025, 10, 23)

    async def test_falls_back_to_match_min_max_when_config_missing(
        self, stub_storage, monkeypatch,
    ):
        _patch_match_minmax(
            monkeypatch,
            # UTC 19:30 on Jan 15 = 14:30 Eastern Jan 15
            first_at=datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc),
            last_at=datetime(2025, 1, 18, 19, 30, tzinfo=timezone.utc),
        )
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 1, 15)
        assert end == date(2025, 1, 18)

    async def test_partial_config_uses_match_for_missing_side(
        self, stub_storage, monkeypatch,
    ):
        stub_storage[KEY_EVENT_START_DATE] = '2025-10-20'
        _patch_match_minmax(
            monkeypatch,
            last_at=datetime(2025, 10, 25, 19, 30, tzinfo=timezone.utc),
        )
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 10, 20)
        assert end == date(2025, 10, 25)

    async def test_falls_back_to_today_when_no_config_or_matches(
        self, stub_storage, monkeypatch,
    ):
        _patch_match_minmax(monkeypatch)  # no matches
        start, end = await SystemConfigService.get_event_window()
        # end falls back to start when only one date is derived.
        assert start == end

    async def test_end_never_before_start(self, stub_storage, monkeypatch):
        stub_storage[KEY_EVENT_START_DATE] = '2025-10-20'
        stub_storage[KEY_EVENT_END_DATE] = '2025-10-15'  # before start
        _patch_match_minmax(monkeypatch)
        start, end = await SystemConfigService.get_event_window()
        assert end >= start
        assert start == date(2025, 10, 20)
        assert end == date(2025, 10, 20)


# ---------------------------------------------------------------------------
# get_max_concurrent_players / get_max_concurrent_stages
# ---------------------------------------------------------------------------


class TestGetMaxConcurrentPlayers:
    async def test_returns_default_when_unset(self, stub_storage):
        assert await SystemConfigService.get_max_concurrent_players(default=60) == 60

    async def test_returns_configured_value(self, stub_storage):
        stub_storage['max_concurrent_players'] = '8'
        assert await SystemConfigService.get_max_concurrent_players() == 8

    async def test_zero_or_negative_falls_back_to_default(self, stub_storage):
        stub_storage['max_concurrent_players'] = '0'
        assert await SystemConfigService.get_max_concurrent_players(default=42) == 42
        stub_storage['max_concurrent_players'] = '-1'
        assert await SystemConfigService.get_max_concurrent_players(default=42) == 42


class TestGetMaxConcurrentStages:
    async def test_returns_configured_value(self, stub_storage, monkeypatch):
        stub_storage['max_concurrent_stages'] = '3'
        # Stage.filter shouldn't be called when config has a positive value.
        assert await SystemConfigService.get_max_concurrent_stages() == 3

    async def test_falls_back_to_default(self, stub_storage):
        assert await SystemConfigService.get_max_concurrent_stages(default=4) == 4

    async def test_falls_back_to_active_stage_count(self, stub_storage, monkeypatch):
        # No config, no default -> count of active stages.
        class FakeFilter:
            def __init__(self, count):
                self._count = count

            async def count(self):
                return self._count

        monkeypatch.setattr(
            'application.services.system_config_service.Stage.filter',
            lambda **_kw: FakeFilter(5),
        )
        monkeypatch.setattr(
            'application.services.system_config_service.event_scoped', lambda qs, *_a: qs,
        )
        assert await SystemConfigService.get_max_concurrent_stages() == 5


# ---------------------------------------------------------------------------
# get_station_format
# ---------------------------------------------------------------------------


class TestGetStationFormat:
    async def test_returns_enum_when_valid_string_stored(self, stub_storage):
        stub_storage[KEY_STATION_FORMAT] = 'numeric'
        assert await SystemConfigService.get_station_format() == StationFormat.NUMERIC

    async def test_returns_default_when_key_missing(self, stub_storage):
        assert await SystemConfigService.get_station_format() == StationFormat.FREE

    async def test_returns_default_when_invalid_value_stored(self, stub_storage):
        stub_storage[KEY_STATION_FORMAT] = 'not_a_valid_format'
        assert await SystemConfigService.get_station_format() == StationFormat.FREE

    async def test_returns_custom_default_when_missing(self, stub_storage):
        result = await SystemConfigService.get_station_format(default=StationFormat.STRUCTURED)
        assert result == StationFormat.STRUCTURED

    async def test_returns_all_valid_enum_values(self, stub_storage):
        for fmt in StationFormat:
            stub_storage[KEY_STATION_FORMAT] = fmt.value
            assert await SystemConfigService.get_station_format() == fmt
