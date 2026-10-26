"""Unit tests for AuditService.

The repository delegate methods (list_logs, count_logs) are tested by
mocking the AuditRepository. The pure ``_encode_details`` JSON helper and
the actor-required guard in ``write_log`` are tested directly.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.audit_service import (
    AuditActions,
    AuditService,
    _encode_details,
)


def make_user(user_id: int = 1):
    return SimpleNamespace(id=user_id, username='alice', discord_id=555000111)


@pytest.fixture
def service():
    svc = object.__new__(AuditService)
    svc.repository = MagicMock()
    svc.repository.list = AsyncMock(return_value=[])
    svc.repository.count = AsyncMock(return_value=0)
    return svc


# ---------------------------------------------------------------------------
# _encode_details
# ---------------------------------------------------------------------------


class TestEncodeDetails:
    def test_none_returns_none(self):
        assert _encode_details(None) is None

    def test_simple_dict_round_trip(self):
        import json
        encoded = _encode_details({'a': 1, 'b': 'two'})
        assert json.loads(encoded) == {'a': 1, 'b': 'two'}

    def test_keys_are_sorted(self):
        # Sort order matters for deterministic queryability.
        encoded = _encode_details({'z': 1, 'a': 2, 'm': 3})
        assert encoded.index('"a"') < encoded.index('"m"') < encoded.index('"z"')

    def test_datetime_falls_back_to_str(self):
        dt = datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)
        encoded = _encode_details({'when': dt})
        # default=str renders datetimes as their str() representation.
        assert str(dt) in encoded

    def test_arbitrary_object_falls_back_to_str(self):
        class Custom:
            def __str__(self):
                return 'custom-repr'

        encoded = _encode_details({'obj': Custom()})
        assert 'custom-repr' in encoded

    def test_nested_dict_is_serialized(self):
        import json
        encoded = _encode_details({'outer': {'inner': [1, 2, 3]}})
        decoded = json.loads(encoded)
        assert decoded == {'outer': {'inner': [1, 2, 3]}}

    def test_accepts_any_mapping(self):
        # Mapping protocol, not just dict.
        from collections import OrderedDict
        encoded = _encode_details(OrderedDict([('b', 1), ('a', 2)]))
        # Still sorted
        assert encoded.index('"a"') < encoded.index('"b"')


# ---------------------------------------------------------------------------
# list_logs / count_logs — delegation to repository
# ---------------------------------------------------------------------------


class TestListLogs:
    async def test_passes_filters_through(self, service):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 2, 1, tzinfo=timezone.utc)
        await service.list_logs(
            start=start, end=end, user_id=7, action_contains='match.', limit=50, offset=10,
        )
        service.repository.list.assert_awaited_once_with(
            start=start, end=end, user_id=7, event_id=None, action_contains='match.', limit=50, offset=10,
        )

    async def test_defaults_when_no_filters(self, service):
        await service.list_logs()
        kwargs = service.repository.list.await_args.kwargs
        assert kwargs['limit'] == 100
        assert kwargs['offset'] == 0
        assert kwargs['start'] is None
        assert kwargs['end'] is None
        assert kwargs['user_id'] is None
        assert kwargs['action_contains'] is None

    async def test_returns_repository_result(self, service):
        sentinel = [object(), object()]
        service.repository.list = AsyncMock(return_value=sentinel)
        result = await service.list_logs()
        assert result is sentinel


class TestCountLogs:
    async def test_passes_filters_through(self, service):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        await service.count_logs(start=start, user_id=3, action_contains='user.')
        service.repository.count.assert_awaited_once_with(
            start=start, end=None, user_id=3, event_id=None, action_contains='user.',
        )

    async def test_returns_repository_count(self, service):
        service.repository.count = AsyncMock(return_value=42)
        assert await service.count_logs() == 42


# ---------------------------------------------------------------------------
# write_log — actor required, calls AuditLog.create
# ---------------------------------------------------------------------------


class TestWriteLog:
    async def test_none_actor_raises_value_error(self, service):
        with pytest.raises(ValueError, match='requires an actor'):
            await service.write_log(None, AuditActions.MATCH_CREATED)

    async def test_creates_audit_log_with_actor_action_details(self, service, monkeypatch):
        created = SimpleNamespace(id=1)
        create_mock = AsyncMock(return_value=created)
        monkeypatch.setattr('application.services.audit_service.AuditLog.create', create_mock)

        user = make_user()
        result = await service.write_log(
            user, AuditActions.MATCH_CREATED, {'match_id': 99},
        )

        assert result is created
        create_mock.assert_awaited_once()
        kwargs = create_mock.await_args.kwargs
        assert kwargs['user'] is user
        assert kwargs['action'] == 'match.created'
        # details should be JSON-encoded, with the actor identity snapshotted
        # in so attribution survives a later user deletion (FK is SET_NULL).
        import json
        assert json.loads(kwargs['details']) == {
            'match_id': 99,
            'actor_username': 'alice',
            'actor_discord_id': '555000111',
        }

    async def test_no_details_still_snapshots_actor_identity(self, service, monkeypatch):
        create_mock = AsyncMock(return_value=SimpleNamespace(id=1))
        monkeypatch.setattr('application.services.audit_service.AuditLog.create', create_mock)

        await service.write_log(make_user(), AuditActions.USER_CREATED)
        import json
        assert json.loads(create_mock.await_args.kwargs['details']) == {
            'actor_username': 'alice',
            'actor_discord_id': '555000111',
        }


# ---------------------------------------------------------------------------
# AuditActions — sanity checks on the constants
# ---------------------------------------------------------------------------


class TestAuditActionsConstants:
    def test_namespaced_format(self):
        # Each action should be 'verb.object' lowercase with a dot.
        for name in dir(AuditActions):
            if name.startswith('_'):
                continue
            value = getattr(AuditActions, name)
            assert isinstance(value, str)
            assert '.' in value, f'{name}={value} is not namespaced'
            assert value == value.lower(), f'{name}={value} should be lowercase'

    def test_known_actions_exist(self):
        # Spot-check action names that callers across the codebase reference.
        assert AuditActions.MATCH_CREATED == 'match.created'
        assert AuditActions.USER_ROLE_GRANTED == 'user.role_granted'
        assert AuditActions.SYSTEM_CONFIG_UPDATED == 'system_config.updated'
        assert AuditActions.DISCORD_ROLE_MAPPING_ADDED == 'discord_role.mapping_added'
        assert AuditActions.DISCORD_ROLE_MAPPING_REMOVED == 'discord_role.mapping_removed'
        assert AuditActions.ROLE_DISCORD_SYNC_GRANTED == 'role.discord_sync_granted'
        assert AuditActions.ROLE_DISCORD_SYNC_REVOKED == 'role.discord_sync_revoked'
