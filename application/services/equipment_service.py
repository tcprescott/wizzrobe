"""
Equipment Service - Business Logic Layer

Manages lending assets (create/edit/delete, bulk creation, auto-assigned asset
numbers) and the checkout/check-in workflow with full loan history.
"""

from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional, TypeVar

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from application.errors import NotFoundError, require_found
from application.feature_flags import requires_feature
from application.repositories.equipment_repository import EquipmentRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_membership_service import TenantMembershipService
from models import (
    SYSTEM_USER_DISCORD_ID,
    Equipment,
    EquipmentLoan,
    EquipmentStatus,
    FeatureFlag,
    User,
)

MAX_BULK_COUNT = 200

_NUMBER_TAKEN = "Someone else added an asset at the same moment. Save again and it'll get the next number."

T = TypeVar('T')


class EquipmentService:
    """Service for equipment lending operations."""

    def __init__(self) -> None:
        self.repository = EquipmentRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _resolve_owner(owner_user_id: Optional[int], actor: User) -> Optional[User]:
        """Resolve an owner id to a User; ``None`` means the community owns it."""
        if not owner_user_id:
            return None
        try:
            await TenantMembershipService.require_community_users([owner_user_id], actor)
        except NotFoundError:
            owner = None
        else:
            owner = await User.get_or_none(id=owner_user_id)
        if owner is None:
            raise ValueError("That owner doesn't exist. Pick someone from the list.")
        return owner

    async def _with_next_number(self, create: Callable[[int], Awaitable[T]]) -> tuple[int, T]:
        """Run ``create`` with the next free asset number, retrying once on a clash.

        The number is read as max+1, so two managers saving at once can both
        read the same one; the (tenant, asset_number) unique constraint refuses
        the second insert. One retry re-reads past the winner.
        """
        for attempt in range(2):
            start = await self.repository.next_asset_number()
            try:
                return start, await create(start)
            except IntegrityError:
                if attempt:
                    raise ValueError(_NUMBER_TAKEN) from None
        raise AssertionError('unreachable')

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def create_asset(
        self,
        actor: User,
        name: str,
        description: Optional[str] = None,
        private_notes: Optional[str] = None,
        owner_user_id: Optional[int] = None,
    ) -> Equipment:
        await AuthService.ensure(
            await AuthService.can_manage_equipment(actor),
            "You do not have permission to manage equipment.",
        )
        name = (name or '').strip()
        if not name:
            raise ValueError("Asset name is required.")

        owner = await self._resolve_owner(owner_user_id, actor)
        description = (description or '').strip() or None
        private_notes = (private_notes or '').strip() or None
        asset_number, equipment = await self._with_next_number(
            lambda number: self.repository.create(
                asset_number=number,
                name=name,
                description=description,
                private_notes=private_notes,
                owner_user=owner,
            )
        )

        await self.audit_service.write_log(
            actor,
            AuditActions.EQUIPMENT_CREATED,
            {'equipment_id': equipment.id, 'asset_number': asset_number},
        )
        return equipment

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def bulk_create_assets(
        self,
        actor: User,
        name: str,
        count: int,
        description: Optional[str] = None,
        private_notes: Optional[str] = None,
        owner_user_id: Optional[int] = None,
    ) -> List[Equipment]:
        await AuthService.ensure(
            await AuthService.can_manage_equipment(actor),
            "You do not have permission to manage equipment.",
        )
        name = (name or '').strip()
        if not name:
            raise ValueError("Asset name is required.")
        if not 1 <= count <= MAX_BULK_COUNT:
            raise ValueError(f"Count must be between 1 and {MAX_BULK_COUNT}.")

        owner = await self._resolve_owner(owner_user_id, actor)
        description = (description or '').strip() or None
        private_notes = (private_notes or '').strip() or None

        async def create_block(first: int) -> List[Equipment]:
            block = [
                Equipment(
                    asset_number=first + offset,
                    name=name,
                    description=description,
                    private_notes=private_notes,
                    owner_user=owner,
                )
                for offset in range(count)
            ]
            async with in_transaction():
                await self.repository.bulk_create(block)
            return block

        start, assets = await self._with_next_number(create_block)

        await self.audit_service.write_log(
            actor,
            AuditActions.EQUIPMENT_CREATED,
            {'count': count, 'asset_number_start': start, 'asset_number_end': start + count - 1},
        )
        return assets

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def update_asset(
        self,
        actor: User,
        equipment_id: int,
        name: str,
        description: Optional[str],
        private_notes: Optional[str],
        owner_user_id: Optional[int],
        status: Optional[EquipmentStatus] = None,
    ) -> Equipment:
        await AuthService.ensure(
            await AuthService.can_manage_equipment(actor),
            "You do not have permission to manage equipment.",
        )
        equipment = require_found(await self.repository.get_by_id(equipment_id), "Asset")
        name = (name or '').strip()
        if not name:
            raise ValueError("Asset name is required.")

        equipment.name = name
        equipment.description = (description or '').strip() or None
        equipment.private_notes = (private_notes or '').strip() or None
        equipment.owner_user = await self._resolve_owner(owner_user_id, actor)
        fields = ['name', 'description', 'private_notes', 'owner_user_id', 'updated_at']

        async with in_transaction():
            if status is not None and status != equipment.status:
                if equipment.status == EquipmentStatus.CHECKED_OUT or status == EquipmentStatus.CHECKED_OUT:
                    raise ValueError("Use check out / check in to change a checked-out status.")
                # Conditional, like checkout's claim: must not overwrite a CHECKED_OUT written a moment ago.
                if not await self.repository.transition_status(equipment.id, equipment.status, status):
                    raise ValueError("This asset was just checked out. Check it in before changing its status.")
                equipment.status = status
            await self.repository.update(equipment, fields)
        await self.audit_service.write_log(
            actor,
            AuditActions.EQUIPMENT_UPDATED,
            {'equipment_id': equipment.id, 'asset_number': equipment.asset_number},
        )
        return equipment

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def delete_asset(self, actor: User, equipment_id: int) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_equipment(actor),
            "You do not have permission to manage equipment.",
        )
        equipment = require_found(await self.repository.get_by_id(equipment_id), "Asset")
        if await self.repository.get_open_loan(equipment) is not None:
            raise ValueError("Cannot delete an asset that is currently checked out.")

        asset_number = equipment.asset_number
        await self.repository.delete(equipment)
        await self.audit_service.write_log(
            actor,
            AuditActions.EQUIPMENT_DELETED,
            {'equipment_id': equipment_id, 'asset_number': asset_number},
        )

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def checkout(
        self,
        actor: User,
        equipment_id: int,
        borrower_id: Optional[int] = None,
    ) -> EquipmentLoan:
        await AuthService.ensure(
            await AuthService.can_checkout_equipment(actor),
            "You do not have permission to check out equipment.",
        )
        if borrower_id is not None and borrower_id != actor.id:
            await AuthService.ensure(
                await AuthService.can_manage_equipment(actor),
                "You can only check equipment out to yourself.",
            )
            borrower = await User.get_or_none(id=borrower_id)
            if borrower is None:
                raise ValueError("That borrower doesn't exist. Pick someone from the list.")
            # Deliberately no membership check on the borrower: lending to someone who just walked in is the point.
            if str(borrower.discord_id) == str(SYSTEM_USER_DISCORD_ID) or not borrower.is_active:
                raise ValueError("That account cannot borrow equipment.")
        else:
            borrower = actor

        equipment = require_found(await self.repository.get_by_id(equipment_id), "Asset")
        if equipment.status == EquipmentStatus.RETIRED:
            raise ValueError("This asset is retired and cannot be checked out.")
        if await self.repository.get_open_loan(equipment) is not None:
            raise ValueError("This asset is already checked out.")

        # The conditional status flip is the lock: two scans both pass the reads above; only one moves the row.
        async with in_transaction():
            if not await self.repository.transition_status(
                equipment.id, EquipmentStatus.AVAILABLE, EquipmentStatus.CHECKED_OUT,
            ):
                raise ValueError("This asset is already checked out.")
            loan = await self.repository.create_loan(equipment, borrower, actor)
        equipment.status = EquipmentStatus.CHECKED_OUT

        await self.audit_service.write_log(
            actor,
            AuditActions.EQUIPMENT_CHECKED_OUT,
            {'equipment_id': equipment.id, 'asset_number': equipment.asset_number, 'borrower_id': borrower.id},
        )
        return loan

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def checkin(self, actor: User, equipment_id: int) -> Equipment:
        await AuthService.ensure(
            await AuthService.can_checkin_equipment(actor),
            "You do not have permission to check in equipment.",
        )
        equipment = require_found(await self.repository.get_by_id(equipment_id), "Asset")
        loan = await self.repository.get_open_loan(equipment)
        if loan is None:
            raise ValueError("This asset is not currently checked out.")

        # Conditional on the loan still being open, so a double-tap records one return.
        async with in_transaction():
            if not await self.repository.close_loan(loan, actor, datetime.now(timezone.utc)):
                raise ValueError("This asset is not currently checked out.")
            if not await self.repository.has_open_loan(equipment.id):
                await self.repository.transition_status(
                    equipment.id, EquipmentStatus.CHECKED_OUT, EquipmentStatus.AVAILABLE,
                )
                equipment.status = EquipmentStatus.AVAILABLE

        await self.audit_service.write_log(
            actor,
            AuditActions.EQUIPMENT_CHECKED_IN,
            {'equipment_id': equipment.id, 'asset_number': equipment.asset_number},
        )
        return equipment

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def list_assets(self) -> List[Equipment]:
        return await self.repository.list_all()

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def list_register(self, actor: Optional[User]) -> List[Equipment]:
        """The whole register, for the people the admin Equipment tab admits.
        Everyone else reaches one asset at a time, from its QR page."""
        await AuthService.ensure(
            await AuthService.can_manage_equipment(actor),
            "Only staff and equipment managers can list the whole equipment register.",
        )
        return await self.repository.list_all()

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def get_assets_by_ids(self, ids: List[int]) -> List[Equipment]:
        """Fetch the given assets (tenant-scoped) for bulk QR-label printing.

        A pure read ordered by asset number. The tenant-scoped query silently
        drops any id that is unknown or belongs to another community, so a
        crafted id list can never surface another tenant's assets.
        """
        return await self.repository.list_by_ids(ids)

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def get_asset(self, equipment_id: int) -> Optional[Equipment]:
        return await self.repository.get_by_id(equipment_id)

    async def current_loan(self, equipment: Equipment) -> Optional[EquipmentLoan]:
        return await self.repository.get_open_loan(equipment)

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def open_loans_by_equipment_id(self) -> dict[int, EquipmentLoan]:
        return await self.repository.open_loans_by_equipment_id()

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def loan_history(
        self, equipment: Equipment, *, limit: Optional[int] = None,
    ) -> List[EquipmentLoan]:
        """Loan history, newest first, optionally bounded to ``limit`` rows."""
        return await self.repository.list_loans_for_equipment(equipment, limit=limit)

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def loan_count(self, equipment: Equipment) -> int:
        """Total loans against this asset — the ``N`` in a bounded view's "5 of N"."""
        return await self.repository.count_loans_for_equipment(equipment)

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def my_checkouts(self, user: User) -> List[EquipmentLoan]:
        return await self.repository.list_open_loans_for_user(user)
