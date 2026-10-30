"""Unit tests for StageService.

Validation, permission gating, and audit-log fields are exercised here. The
repository is mocked so the tests do not depend on a database.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.stage_service import StageService
from tests.factories import make_audit_double

pytestmark = pytest.mark.usefixtures("bypass_auth")
@pytest.fixture
def service():
    svc = object.__new__(StageService)
    svc.repository = MagicMock()
    svc.repository.create = AsyncMock()
    svc.repository.update = AsyncMock(side_effect=lambda stage, **f: stage)
    svc.repository.get_by_id = AsyncMock()
    svc.repository.count_matches = AsyncMock(return_value=0)
    svc.repository.name_taken = AsyncMock(return_value=False)
    svc.audit_service = make_audit_double()
    return svc


def make_user(user_id=1):
    return SimpleNamespace(id=user_id, username='staff')


def make_stage(stage_id=10, **overrides):
    defaults = dict(
        id=stage_id,
        name='Stage 1',
        stream_url='https://twitch.tv/x',
        is_active=True,
        delete=AsyncMock(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCreateStage:
    async def test_happy_path_creates_stage_and_audits(self, service):
        created = make_stage(stage_id=1, name='Stage A')
        service.repository.create = AsyncMock(return_value=created)

        result = await service.create_stage(
            name=' Stage A ',
            stream_url=' https://x.tv ',
            actor=make_user(),
        )

        assert result is created
        kwargs = service.repository.create.await_args.kwargs
        assert kwargs['name'] == 'Stage A'
        assert kwargs['stream_url'] == 'https://x.tv'
        assert kwargs['is_active'] is True

        service.audit_service.write_log.assert_awaited_once()
        details = service.audit_service.write_log.await_args.args[2]
        assert details == {'stage_id': 1, 'name': 'Stage A'}

    async def test_empty_name_raises_value_error(self, service):
        with pytest.raises(ValueError, match='name is required'):
            await service.create_stage(name='   ', actor=make_user())
        service.repository.create.assert_not_called()

    async def test_blank_name_raises_value_error(self, service):
        with pytest.raises(ValueError, match='name is required'):
            await service.create_stage(name='', actor=make_user())

    async def test_url_none_stored_as_none(self, service):
        service.repository.create = AsyncMock(return_value=make_stage())
        await service.create_stage(name='Stage 1', stream_url=None, actor=make_user())
        assert service.repository.create.await_args.kwargs['stream_url'] is None

    async def test_unauthorized_actor_denied(self, service, monkeypatch):
        from application.services import auth_service

        async def deny(*_a, **_kw):
            return False

        async def real_ensure(allowed, message=None):
            if not allowed:
                raise PermissionError(message or 'denied')

        monkeypatch.setattr(auth_service.AuthService, 'can_manage_stages', deny)
        monkeypatch.setattr(auth_service.AuthService, 'ensure', real_ensure)

        with pytest.raises(PermissionError):
            await service.create_stage(name='Stage 1', actor=make_user())


class TestUpdateStage:
    async def test_only_changed_fields_are_written(self, service):
        stage = make_stage()
        await service.update_stage(stage, name='Stage New', actor=make_user())

        kwargs = service.repository.update.await_args.kwargs
        assert kwargs == {'name': 'Stage New'}

    async def test_passing_none_for_field_does_not_update_it(self, service):
        stage = make_stage()
        await service.update_stage(
            stage, name='X', stream_url=None, is_active=None, actor=make_user(),
        )
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs == {'name': 'X'}

    async def test_empty_name_raises(self, service):
        stage = make_stage()
        with pytest.raises(ValueError, match='name cannot be empty'):
            await service.update_stage(stage, name='   ', actor=make_user())

    async def test_audit_log_records_changed_field_names(self, service):
        stage = make_stage()
        await service.update_stage(
            stage, name='New', is_active=False, actor=make_user(),
        )
        details = service.audit_service.write_log.await_args.args[2]
        assert details['stage_id'] == stage.id
        assert set(details['changed_fields']) == {'name', 'is_active'}

    async def test_stream_url_is_stripped(self, service):
        stage = make_stage()
        await service.update_stage(
            stage, stream_url='  https://x.tv  ', actor=make_user(),
        )
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs['stream_url'] == 'https://x.tv'

    async def test_empty_stream_url_becomes_none(self, service):
        stage = make_stage()
        await service.update_stage(stage, stream_url='', actor=make_user())
        kwargs = service.repository.update.await_args.kwargs
        assert kwargs['stream_url'] is None


class TestDeleteStage:
    async def test_calls_delete_and_audits(self, service):
        stage = make_stage(stage_id=99)
        await service.delete_stage(stage, actor=make_user())
        stage.delete.assert_awaited_once()
        details = service.audit_service.write_log.await_args.args[2]
        assert details == {'stage_id': 99}

    async def test_audit_uses_id_before_delete(self, service):
        # Even if the orm clears the id after delete(), audit_log should still have it.
        stage = make_stage(stage_id=42)
        await service.delete_stage(stage, actor=make_user())
        assert service.audit_service.write_log.await_args.args[2]['stage_id'] == 42

    async def test_refuses_delete_when_matches_assigned(self, service):
        stage = make_stage(stage_id=7)
        service.repository.count_matches = AsyncMock(return_value=3)
        with pytest.raises(ValueError, match='inactive'):
            await service.delete_stage(stage, actor=make_user())
        stage.delete.assert_not_called()
        service.audit_service.write_log.assert_not_called()
