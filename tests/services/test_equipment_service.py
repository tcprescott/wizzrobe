import asyncio

import pytest

from application.services.audit_service import AuditActions
from application.services.equipment_service import EquipmentService
from application.tenant_context import tenant_scope
from models import AuditLog, Equipment, EquipmentLoan, EquipmentStatus, Role, User, UserRole
from tests.api_helpers import create_community_member


async def _user(discord_id: int, username: str, *roles: Role) -> User:
    user = await create_community_member(discord_id=discord_id, username=username)
    for role in roles:
        await UserRole.create(user=user, role=role)
    return user


@pytest.fixture
def service():
    return EquipmentService()


class TestAssetNumbering:
    async def test_assigns_sequential_numbers(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        a = await service.create_asset(manager, name='Console')
        b = await service.create_asset(manager, name='Capture card')
        assert a.asset_number == 1
        assert b.asset_number == 2

    async def test_bulk_create_contiguous_range(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        await service.create_asset(manager, name='Cable')  # number 1
        created = await service.bulk_create_assets(manager, name='HDMI', count=5)
        numbers = sorted(a.asset_number for a in created)
        assert numbers == [2, 3, 4, 5, 6]
        assert await Equipment.all().count() == 6

    async def test_bulk_count_bounds(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        with pytest.raises(ValueError):
            await service.bulk_create_assets(manager, name='X', count=0)
        with pytest.raises(ValueError):
            await service.bulk_create_assets(manager, name='X', count=201)


class TestPermissions:
    async def test_non_manager_cannot_create(self, db, service):
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        with pytest.raises(PermissionError):
            await service.create_asset(volunteer, name='Console')

    async def test_staff_can_manage(self, db, service):
        staff = await _user(3, 'staff', Role.STAFF)
        asset = await service.create_asset(staff, name='Console')
        assert asset.asset_number == 1


class TestCheckoutCheckin:
    async def test_a_non_manager_checking_out_to_someone_else_is_refused(self, db, service):
        """Refused, not silently rewritten to the actor. A loan booked against
        the wrong person is the failure that survives the event."""
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        other = await _user(3, 'other', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')

        with pytest.raises(PermissionError):
            await service.checkout(volunteer, asset.id, borrower_id=other.id)

        assert (await service.get_asset(asset.id)).status == EquipmentStatus.AVAILABLE

    async def test_volunteer_checks_out_to_themselves(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')

        loan = await service.checkout(volunteer, asset.id)
        assert loan.borrower_id == volunteer.id

        refreshed = await service.get_asset(asset.id)
        assert refreshed.status == EquipmentStatus.CHECKED_OUT

    async def test_a_member_with_no_roles_can_borrow(self, db, service):
        """The QR-code case: a player scans a loaner and checks it out to
        themselves, holding no roles at all."""
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        player = await _user(4, 'player')
        asset = await service.create_asset(manager, name='RetroTINK 2X')

        loan = await service.checkout(player, asset.id)
        assert loan.borrower_id == player.id
        assert (await service.get_asset(asset.id)).status == EquipmentStatus.CHECKED_OUT

    async def test_a_member_with_no_roles_still_cannot_check_in(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        player = await _user(4, 'player')
        asset = await service.create_asset(manager, name='RetroTINK 2X')
        await service.checkout(player, asset.id)

        with pytest.raises(PermissionError):
            await service.checkin(player, asset.id)

    async def test_manager_can_checkout_on_behalf(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        borrower = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')

        loan = await service.checkout(manager, asset.id, borrower_id=borrower.id)
        assert loan.borrower_id == borrower.id

    async def test_cannot_checkout_when_already_out(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(manager, asset.id, borrower_id=volunteer.id)

        with pytest.raises(ValueError):
            await service.checkout(volunteer, asset.id)

    async def test_volunteer_cannot_checkin(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)

        with pytest.raises(PermissionError):
            await service.checkin(volunteer, asset.id)

    async def test_checkin_closes_loan_and_frees_asset(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)

        await service.checkin(manager, asset.id)

        refreshed = await service.get_asset(asset.id)
        assert refreshed.status == EquipmentStatus.AVAILABLE
        loan = await EquipmentLoan.get(equipment=refreshed, borrower=volunteer)
        assert loan.checked_in_at is not None
        assert loan.checked_in_by_id == manager.id
        assert await service.current_loan(refreshed) is None


class TestDelete:
    async def test_delete_blocked_while_on_loan(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)

        with pytest.raises(ValueError):
            await service.delete_asset(manager, asset.id)

    async def test_delete_after_checkin(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)
        await service.checkin(manager, asset.id)

        await service.delete_asset(manager, asset.id)
        assert await Equipment.all().count() == 0


class TestGetAssetsByIds:
    async def test_returns_selected_ordered_by_number(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        a = await service.create_asset(manager, name='A')  # number 1
        b = await service.create_asset(manager, name='B')  # number 2
        c = await service.create_asset(manager, name='C')  # number 3

        got = await service.get_assets_by_ids([c.id, a.id])
        # Selection honored, and ordered by asset_number regardless of input order.
        assert [x.asset_number for x in got] == [a.asset_number, c.asset_number]
        assert b.id not in {x.id for x in got}

    async def test_empty_and_unknown_ids(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='A')
        assert await service.get_assets_by_ids([]) == []
        # Unknown ids are silently dropped; known ones still come back.
        got = await service.get_assets_by_ids([asset.id, 999_999])
        assert [x.id for x in got] == [asset.id]

    async def test_does_not_leak_across_tenants(self, service, two_tenants):
        tenant_a, tenant_b = two_tenants
        # Create assets directly per tenant (asset_number is unique per tenant,
        # so both can be #1) — this exercises the repo's tenant scoping, not the
        # per-tenant role gate.
        a_asset = await Equipment.create(tenant_id=tenant_a.id, asset_number=1, name='A-asset')
        b_asset = await Equipment.create(tenant_id=tenant_b.id, asset_number=1, name='B-asset')

        # Asking for both ids from within tenant A returns only tenant A's asset.
        with tenant_scope(tenant_a.id):
            got = await service.get_assets_by_ids([a_asset.id, b_asset.id])
        assert [x.id for x in got] == [a_asset.id]


class TestOwnerLabel:
    async def test_owner_label_house_vs_user(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        owner = await _user(2, 'owner', Role.VOLUNTEER)

        house = await service.create_asset(manager, name='House asset')
        # An un-owned asset falls back to the owning community's name.
        assert house.owner_label('Acme Community') == 'Acme Community'

        owned = await service.create_asset(manager, name='Owned asset', owner_user_id=owner.id)
        fetched = await service.get_asset(owned.id)
        assert fetched.owner_label('Acme Community') == owner.preferred_name


class TestOwnerMustBelong:
    async def test_an_owner_from_outside_the_community_is_refused(self, db, service):
        """``User`` is global; an unchecked owner id put any account's name on
        an asset every member can list."""
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        stranger = await User.create(discord_id=77, username='elsewhere')
        with pytest.raises(ValueError, match="doesn't exist"):
            await service.create_asset(manager, name='Borrowed', owner_user_id=stranger.id)


class TestLoanHistoryBound:
    """The history is bounded at the read, not just at the render.

    An asset lent two hundred times used to fetch two hundred rows with three
    prefetched users each, and paint every one, on the phone where scrolling
    costs most.
    """

    @staticmethod
    async def _asset_with_loans(service, count: int):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        borrower = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Well-travelled cable')
        for _ in range(count):
            await service.checkout(borrower, asset.id)
            await service.checkin(manager, asset.id)
        return asset, manager, borrower

    async def test_limit_is_honoured(self, db, service):
        asset, _, _ = await self._asset_with_loans(service, 12)
        assert len(await service.loan_history(asset, limit=5)) == 5

    async def test_no_limit_returns_everything(self, db, service):
        asset, _, _ = await self._asset_with_loans(service, 12)
        assert len(await service.loan_history(asset)) == 12

    async def test_count_is_the_total_not_the_page(self, db, service):
        asset, _, _ = await self._asset_with_loans(service, 12)
        await service.loan_history(asset, limit=5)
        assert await service.loan_count(asset) == 12

    async def test_the_bounded_page_is_the_most_recent(self, db, service):
        asset, _, _ = await self._asset_with_loans(service, 12)
        every = await service.loan_history(asset)
        page = await service.loan_history(asset, limit=3)
        assert [loan.id for loan in page] == [loan.id for loan in every[:3]]
        # Newest first, so the page's oldest row is no older than the rest.
        assert page[0].checked_out_at >= page[-1].checked_out_at

    async def test_an_asset_with_no_loans_is_clean(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Never lent')
        assert await service.loan_history(asset, limit=5) == []
        assert await service.loan_count(asset) == 0

    async def test_a_limit_larger_than_the_history_is_fine(self, db, service):
        asset, _, _ = await self._asset_with_loans(service, 2)
        assert len(await service.loan_history(asset, limit=50)) == 2


class TestBorrowerRules:
    """What the *service* refuses, versus what the picker merely narrows.

    Two exclusions are rules and belong here, because this is the only place
    every caller passes through. Community membership is **not** one of them:
    the picker defaults to this community and offers an opt-in to widen,
    precisely so an asset can be lent to someone who just walked into the venue.
    """

    async def test_the_system_account_cannot_borrow(self, db, service):
        from models import SYSTEM_USER_DISCORD_ID
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        system = await User.create(discord_id=SYSTEM_USER_DISCORD_ID, username='System')
        asset = await service.create_asset(manager, name='Console')

        with pytest.raises(ValueError, match='cannot borrow'):
            await service.checkout(manager, asset.id, borrower_id=system.id)

    async def test_a_deactivated_account_cannot_borrow(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        gone = await _user(2, 'gone', Role.VOLUNTEER)
        gone.is_active = False
        await gone.save()
        asset = await service.create_asset(manager, name='Console')

        with pytest.raises(ValueError, match='cannot borrow'):
            await service.checkout(manager, asset.id, borrower_id=gone.id)

    async def test_a_refused_borrower_leaves_the_asset_available(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        gone = await _user(2, 'gone', Role.VOLUNTEER)
        gone.is_active = False
        await gone.save()
        asset = await service.create_asset(manager, name='Console')

        with pytest.raises(ValueError):
            await service.checkout(manager, asset.id, borrower_id=gone.id)

        refreshed = await service.get_asset(asset.id)
        assert refreshed.status == EquipmentStatus.AVAILABLE
        assert await service.current_loan(refreshed) is None

    async def test_someone_outside_the_community_can_still_be_lent_to(self, db, service):
        # This test documents the decision, not an accident: the picker narrows
        # by default and can be widened, and the service must honour the widened
        # choice. Hard-rejecting a non-member would break the venue case the
        # whole feature exists for.
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        walk_in = await User.create(discord_id=99, username='walk_in')  # no role, no tournament
        asset = await service.create_asset(manager, name='Console')

        loan = await service.checkout(manager, asset.id, borrower_id=walk_in.id)

        assert loan.borrower_id == walk_in.id
        refreshed = await service.get_asset(asset.id)
        assert refreshed.status == EquipmentStatus.CHECKED_OUT


class TestConcurrentLending:
    """A double-tap or two phones on one label both pass the service's reads;
    the conditional writes are what keep it to one loan and one return."""

    async def test_simultaneous_checkouts_make_one_loan(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')

        results = await asyncio.gather(
            service.checkout(volunteer, asset.id),
            service.checkout(volunteer, asset.id),
            return_exceptions=True,
        )

        assert sum(isinstance(r, ValueError) for r in results) == 1
        assert await EquipmentLoan.filter(equipment_id=asset.id).count() == 1

    async def test_a_checkout_that_loses_the_claim_is_refused(self, db, service, monkeypatch):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        first = await _user(2, 'first', Role.VOLUNTEER)
        second = await _user(3, 'second', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(first, asset.id)

        # The losing request read "no open loan" before the winner committed.
        async def stale_read(_equipment):
            return None

        monkeypatch.setattr(service.repository, 'get_open_loan', stale_read)
        monkeypatch.setattr(
            service.repository, 'get_by_id',
            _stale_asset_reader(service.repository.get_by_id, EquipmentStatus.AVAILABLE),
        )

        with pytest.raises(ValueError, match='already checked out'):
            await service.checkout(second, asset.id)

        loans = await EquipmentLoan.filter(equipment_id=asset.id)
        assert [loan.borrower_id for loan in loans] == [first.id]

    async def test_a_double_checkin_records_one_return(self, db, service, monkeypatch):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)
        open_loan = await service.repository.get_open_loan(await service.get_asset(asset.id))

        await service.checkin(manager, asset.id)

        async def stale_read(_equipment):
            return open_loan

        monkeypatch.setattr(service.repository, 'get_open_loan', stale_read)
        with pytest.raises(ValueError, match='not currently checked out'):
            await service.checkin(manager, asset.id)

        assert await AuditLog.filter(action=AuditActions.EQUIPMENT_CHECKED_IN).count() == 1
        assert (await service.get_asset(asset.id)).status == EquipmentStatus.AVAILABLE

    async def test_retiring_an_asset_checked_out_meanwhile_is_refused(self, db, service, monkeypatch):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        volunteer = await _user(2, 'vol', Role.VOLUNTEER)
        asset = await service.create_asset(manager, name='Console')
        await service.checkout(volunteer, asset.id)
        monkeypatch.setattr(
            service.repository, 'get_by_id',
            _stale_asset_reader(service.repository.get_by_id, EquipmentStatus.AVAILABLE),
        )

        with pytest.raises(ValueError, match='just checked out'):
            await service.update_asset(
                manager, asset.id, name='Console', description=None, private_notes=None,
                owner_user_id=None, status=EquipmentStatus.RETIRED,
            )

        assert (await Equipment.get(id=asset.id)).status == EquipmentStatus.CHECKED_OUT

    async def test_retire_and_unretire(self, db, service):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        asset = await service.create_asset(manager, name='Console')

        await service.update_asset(
            manager, asset.id, name='Console', description=None, private_notes=None,
            owner_user_id=None, status=EquipmentStatus.RETIRED,
        )
        assert (await Equipment.get(id=asset.id)).status == EquipmentStatus.RETIRED
        await service.update_asset(
            manager, asset.id, name='Console', description=None, private_notes=None,
            owner_user_id=None, status=EquipmentStatus.AVAILABLE,
        )
        assert (await Equipment.get(id=asset.id)).status == EquipmentStatus.AVAILABLE


def _stale_asset_reader(real_get_by_id, status):
    """``get_by_id`` as a request that read the row before another one changed it."""
    async def read(equipment_id):
        asset = await real_get_by_id(equipment_id)
        asset.status = status
        return asset
    return read


class TestAssetNumberClash:
    """Two managers saving at once both read the same max+1."""

    async def test_create_retries_past_a_taken_number(self, db, service, monkeypatch):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        await service.create_asset(manager, name='Console')  # takes #1
        real = service.repository.next_asset_number
        reads = iter([1])

        async def racing_read():
            return next(reads, None) or await real()

        monkeypatch.setattr(service.repository, 'next_asset_number', racing_read)
        asset = await service.create_asset(manager, name='Capture card')

        assert asset.asset_number == 2

    async def test_create_gives_up_with_a_retry_message(self, db, service, monkeypatch):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        await service.create_asset(manager, name='Console')

        async def always_taken():
            return 1

        monkeypatch.setattr(service.repository, 'next_asset_number', always_taken)
        with pytest.raises(ValueError, match='Save again'):
            await service.create_asset(manager, name='Capture card')

    async def test_bulk_create_retries_without_leaving_a_partial_block(self, db, service, monkeypatch):
        manager = await _user(1, 'manager', Role.EQUIPMENT_MANAGER)
        await service.create_asset(manager, name='Console')  # takes #1
        real = service.repository.next_asset_number
        reads = iter([1])

        async def racing_read():
            return next(reads, None) or await real()

        monkeypatch.setattr(service.repository, 'next_asset_number', racing_read)
        created = await service.bulk_create_assets(manager, name='HDMI', count=3)

        assert sorted(a.asset_number for a in created) == [2, 3, 4]
        assert await Equipment.all().count() == 4
