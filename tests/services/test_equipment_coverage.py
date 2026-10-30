"""Coverage-focused tests for EquipmentService.

Complements tests/services/test_equipment_service.py by exercising the
update_asset method in full plus the scattered error/branch/read paths that
the primary suite leaves uncovered.
"""

import pytest

from application.services.equipment_service import EquipmentService
from models import AuditLog, EquipmentLoan, EquipmentStatus, Role, User, UserRole
from tests.api_helpers import create_community_member


async def _user(discord_id: int, username: str, *roles: Role) -> User:
    user = await create_community_member(discord_id=discord_id, username=username)
    for role in roles:
        await UserRole.create(user=user, role=role)
    return user


@pytest.fixture
def service():
    return EquipmentService()


class TestResolveOwnerErrors:
    async def test_create_asset_unknown_owner_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="owner"):
            await service.create_asset(manager, name='Console', owner_user_id=9999)

    async def test_update_asset_unknown_owner_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(ValueError, match="owner"):
            await service.update_asset(
                manager, asset.id, name='Console', description=None,
                private_notes=None, owner_user_id=9999,
            )


class TestNameValidation:
    async def test_create_asset_blank_name_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="name is required"):
            await service.create_asset(manager, name='   ')

    async def test_bulk_create_blank_name_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="name is required"):
            await service.bulk_create_assets(manager, name='', count=3)

    async def test_update_asset_blank_name_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(ValueError, match="name is required"):
            await service.update_asset(
                manager, asset.id, name='  ', description=None,
                private_notes=None, owner_user_id=None,
            )


class TestUpdateAsset:
    async def test_non_manager_cannot_update(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(PermissionError):
            await service.update_asset(
                volunteer, asset.id, name='Renamed', description=None,
                private_notes=None, owner_user_id=None,
            )

    async def test_update_missing_asset_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="Asset not found"):
            await service.update_asset(
                manager, 4242, name='Renamed', description=None,
                private_notes=None, owner_user_id=None,
            )

    async def test_update_persists_all_fields_and_owner(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        owner = await _user(2, 'owner', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console', description='old')

        updated = await service.update_asset(
            manager, asset.id, name='  Renamed console  ',
            description='  a shiny box  ', private_notes='  serial 42  ',
            owner_user_id=owner.id,
        )

        assert updated.name == 'Renamed console'
        assert updated.description == 'a shiny box'
        assert updated.private_notes == 'serial 42'
        assert updated.owner_user_id == owner.id

        refreshed = await service.get_asset(asset.id)
        assert refreshed.name == 'Renamed console'
        assert refreshed.owner_label('Acme Community') == owner.preferred_name

    async def test_update_clears_optional_fields_to_none(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        owner = await _user(2, 'owner', Role.VOLUNTEER)
        asset = await service.create_asset(
            manager, name='Console', description='old', private_notes='n',
            owner_user_id=owner.id,
        )

        updated = await service.update_asset(
            manager, asset.id, name='Console', description='   ',
            private_notes='', owner_user_id=None,
        )

        assert updated.description is None
        assert updated.private_notes is None
        assert updated.owner_user is None
        assert updated.owner_label('Acme Community') == 'Acme Community'

    async def test_update_writes_audit_log(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')

        await service.update_asset(
            manager, asset.id, name='Renamed', description=None,
            private_notes=None, owner_user_id=None,
        )

        assert await AuditLog.filter(action='equipment.updated').count() == 1

    async def test_update_status_available_to_retired(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')

        updated = await service.update_asset(
            manager, asset.id, name='Console', description=None,
            private_notes=None, owner_user_id=None, status=EquipmentStatus.RETIRED,
        )

        assert updated.status == EquipmentStatus.RETIRED
        refreshed = await service.get_asset(asset.id)
        assert refreshed.status == EquipmentStatus.RETIRED

    async def test_update_same_status_is_noop_branch(self, db, service):
        """status provided but equal to current -> no status change, no error."""
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')

        updated = await service.update_asset(
            manager, asset.id, name='Console', description=None,
            private_notes=None, owner_user_id=None, status=EquipmentStatus.AVAILABLE,
        )
        assert updated.status == EquipmentStatus.AVAILABLE

    async def test_update_status_to_checked_out_rejected(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')

        with pytest.raises(ValueError, match="check out / check in"):
            await service.update_asset(
                manager, asset.id, name='Console', description=None,
                private_notes=None, owner_user_id=None, status=EquipmentStatus.CHECKED_OUT,
            )

    async def test_update_status_from_checked_out_rejected(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)

        with pytest.raises(ValueError, match="check out / check in"):
            await service.update_asset(
                manager, asset.id, name='Console', description=None,
                private_notes=None, owner_user_id=None, status=EquipmentStatus.RETIRED,
            )


class TestDeleteErrors:
    async def test_delete_missing_asset_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="Asset not found"):
            await service.delete_asset(manager, 555)

    async def test_non_manager_cannot_delete(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(PermissionError):
            await service.delete_asset(volunteer, asset.id)


class TestCheckoutErrors:
    async def test_a_proctor_borrows_to_themselves(self, db, service):
        """Borrowing carries no role gate — a proctor holding nothing else gets
        the same self-checkout a player scanning the QR code does."""
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        proctor = await _user(2, 'proc', Role.PROCTOR)
        asset = await service.create_asset(manager, name='Console')
        loan = await service.checkout(proctor, asset.id)
        assert loan.borrower_id == proctor.id

    async def test_deactivated_actor_cannot_borrow(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        gone = await _user(2, 'gone')
        gone.is_active = False
        await gone.save()
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(PermissionError):
            await service.checkout(gone, asset.id)

    async def test_manager_checkout_unknown_borrower_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(ValueError, match="borrower"):
            await service.checkout(manager, asset.id, borrower_id=9999)

    async def test_checkout_missing_asset_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="Asset not found"):
            await service.checkout(manager, 8888)

    async def test_checkout_retired_asset_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.update_asset(
            manager, asset.id, name='Console', description=None,
            private_notes=None, owner_user_id=None, status=EquipmentStatus.RETIRED,
        )
        with pytest.raises(ValueError, match="retired"):
            await service.checkout(volunteer, asset.id)


class TestCheckinErrors:
    async def test_checkin_missing_asset_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError, match="Asset not found"):
            await service.checkin(manager, 7777)

    async def test_checkin_not_checked_out_raises(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')
        with pytest.raises(ValueError, match="not currently checked out"):
            await service.checkin(manager, asset.id)


class TestReads:
    async def test_list_assets_ordered_by_number(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        await service.create_asset(manager, name='First')
        await service.create_asset(manager, name='Second')
        assets = await service.list_assets()
        assert [a.asset_number for a in assets] == [1, 2]

    async def test_open_loans_by_equipment_id(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        checked = await service.create_asset(manager, name='Checked out')
        idle = await service.create_asset(manager, name='Idle')
        await service.checkout(volunteer, checked.id)

        mapping = await service.open_loans_by_equipment_id()
        assert set(mapping.keys()) == {checked.id}
        assert idle.id not in mapping
        assert mapping[checked.id].borrower_id == volunteer.id

    async def test_loan_history_returns_all_loans_newest_first(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)
        await service.checkin(manager, asset.id)
        await service.checkout(volunteer, asset.id)

        history = await service.loan_history(asset)
        assert len(history) == 2
        assert isinstance(history[0], EquipmentLoan)
        assert history[0].checked_in_at is None
        assert history[1].checked_in_at is not None

    async def test_my_checkouts_returns_only_open_for_user(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        other = await _user(3, 'other', Role.VOLUNTEER)
        held = await service.create_asset(manager, name='Held')
        returned = await service.create_asset(manager, name='Returned')
        others = await service.create_asset(manager, name='Others')

        await service.checkout(volunteer, held.id)
        await service.checkout(volunteer, returned.id)
        await service.checkin(manager, returned.id)
        await service.checkout(other, others.id)

        mine = await service.my_checkouts(volunteer)
        assert len(mine) == 1
        assert mine[0].equipment_id == held.id
