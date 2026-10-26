"""Tests for VolunteerPositionService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from tests.factories import make_audit_double

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.usefixtures("bypass_auth")
@pytest.fixture
def service():
    svc = object.__new__(VolunteerPositionService)
    svc.repository = MagicMock()
    svc.repository.list_all = AsyncMock(return_value=[])
    svc.repository.list_active = AsyncMock(return_value=[])
    svc.repository.get_by_id = AsyncMock(return_value=None)
    svc.repository.create = AsyncMock()
    svc.repository.update = AsyncMock()
    svc.repository.delete = AsyncMock()
    svc.audit_service = make_audit_double()
    svc.role_mapping_service = MagicMock(reconcile_all=AsyncMock(return_value={}))
    return svc


def make_position(**overrides):
    from types import SimpleNamespace
    defaults = dict(id=1, name='Check-in', shift_length_minutes=None, stagger_minutes=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _validate_stagger (static, no DB)
# ---------------------------------------------------------------------------


class TestValidateStagger:
    def test_both_none_is_valid(self):
        VolunteerPositionService._validate_stagger(None, None)

    def test_both_set_is_valid(self):
        VolunteerPositionService._validate_stagger(240, 120)

    def test_back_to_back_is_valid(self):
        VolunteerPositionService._validate_stagger(240, 240)

    def test_only_shift_length_raises(self):
        with pytest.raises(ValueError, match='both'):
            VolunteerPositionService._validate_stagger(240, None)

    def test_only_stagger_raises(self):
        with pytest.raises(ValueError, match='both'):
            VolunteerPositionService._validate_stagger(None, 120)

    def test_zero_stagger_raises(self):
        with pytest.raises(ValueError, match='positive'):
            VolunteerPositionService._validate_stagger(240, 0)

    def test_zero_shift_length_raises(self):
        with pytest.raises(ValueError, match='positive'):
            VolunteerPositionService._validate_stagger(0, 120)

    def test_stagger_exceeds_length_raises(self):
        with pytest.raises(ValueError, match='exceed'):
            VolunteerPositionService._validate_stagger(120, 240)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreate:
    async def test_raises_when_name_empty(self, service):
        with pytest.raises(ValueError, match='required'):
            service.repository.name_taken = AsyncMock(return_value=False)
            await service.create(actor=MagicMock(), name='')

    async def test_raises_when_name_whitespace(self, service):
        with pytest.raises(ValueError, match='required'):
            service.repository.name_taken = AsyncMock(return_value=False)
            await service.create(actor=MagicMock(), name='   ')

    async def test_raises_when_name_already_exists(self, service):
        service.repository.name_taken = AsyncMock(return_value=True)
        with pytest.raises(ValueError, match="already exists"):
            await service.create(actor=MagicMock(), name='Check-in')

    async def test_creates_and_audits_on_success(self, service):
        position = make_position(id=5, name='Check-in')
        service.repository.create = AsyncMock(return_value=position)
        service.repository.name_taken = AsyncMock(return_value=False)
        result = await service.create(actor=MagicMock(), name='Check-in')
        assert result is position
        service.audit_service.write_log.assert_awaited_once()

    async def test_raises_on_invalid_stagger_config(self, service):
        service.repository.name_taken = AsyncMock(return_value=False)
        with pytest.raises(ValueError):
            await service.create(
                actor=MagicMock(), name='Tech',
                shift_length_minutes=240, stagger_minutes=None,
            )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdate:
    async def test_raises_when_new_name_empty(self, service):
        position = make_position()
        service.repository.update = AsyncMock(return_value=position)
        service.repository.name_taken = AsyncMock(return_value=False)
        with pytest.raises(ValueError, match='required'):
            await service.update(actor=MagicMock(), position=position, name='')

    async def test_raises_when_name_taken_by_other(self, service):
        position = make_position()
        service.repository.name_taken = AsyncMock(return_value=True)
        with pytest.raises(ValueError, match="already exists"):
            await service.update(actor=MagicMock(), position=position, name='Other Name')

    async def test_updates_and_audits_on_success(self, service):
        position = make_position()
        updated = make_position(name='New Name')
        service.repository.update = AsyncMock(return_value=updated)
        service.repository.name_taken = AsyncMock(return_value=False)
        result = await service.update(actor=MagicMock(), position=position, name='New Name')
        assert result is updated
        service.audit_service.write_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    async def test_deletes_and_audits(self, service):
        position = make_position(id=3)
        service.repository.delete = AsyncMock()
        await service.delete(actor=MagicMock(), position=position)
        service.repository.delete.assert_awaited_once_with(position)
        service.audit_service.write_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# list_all / list_active / get
# ---------------------------------------------------------------------------


class TestQueries:
    async def test_list_all_delegates(self, service):
        service.repository.list_all = AsyncMock(return_value=['a', 'b'])
        result = await service.list_all()
        assert result == ['a', 'b']

    async def test_list_active_delegates(self, service):
        service.repository.list_active = AsyncMock(return_value=['a'])
        result = await service.list_active()
        assert result == ['a']

    async def test_get_delegates(self, service):
        pos = make_position()
        service.repository.get_by_id = AsyncMock(return_value=pos)
        result = await service.get(1)
        assert result is pos
