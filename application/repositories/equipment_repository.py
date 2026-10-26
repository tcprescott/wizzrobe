"""
Equipment Repository - Data Access Layer

Handles database operations for lending assets and their loan history.
"""

from datetime import datetime, timezone
from typing import List, Optional

from tortoise.functions import Max

from application.repositories._event import tagged_event_id
from application.repositories._tenant import current_tenant_id, scoped
from models import Equipment, EquipmentLoan, EquipmentStatus, User


class EquipmentRepository:
    """Repository for equipment and loan data access."""

    # --- Assets ---

    @staticmethod
    async def create(
        asset_number: int,
        name: str,
        description: Optional[str],
        private_notes: Optional[str],
        owner_user: Optional[User],
    ) -> Equipment:
        return await Equipment.create(
            tenant_id=current_tenant_id(),
            asset_number=asset_number,
            name=name,
            description=description,
            private_notes=private_notes,
            owner_user=owner_user,
        )

    @staticmethod
    async def get_by_id(equipment_id: int) -> Optional[Equipment]:
        return await Equipment.get_or_none(
            id=equipment_id, tenant_id=current_tenant_id()
        ).prefetch_related('owner_user')

    @staticmethod
    async def list_all() -> List[Equipment]:
        """All assets, lowest asset number first, owner prefetched."""
        return await scoped(Equipment.all()).order_by('asset_number').prefetch_related('owner_user')

    @staticmethod
    async def list_by_ids(ids: List[int]) -> List[Equipment]:
        """Assets whose ids are in ``ids``, lowest asset number first, owner prefetched.

        Tenant-scoped, so ids belonging to another community are silently
        dropped rather than fetched. Empty ``ids`` short-circuits to no query.
        """
        if not ids:
            return []
        return await scoped(
            Equipment.filter(id__in=ids)
        ).order_by('asset_number').prefetch_related('owner_user')

    @staticmethod
    async def next_asset_number() -> int:
        row = await scoped(Equipment.annotate(m=Max('asset_number'))).values('m')
        current = row[0]['m'] if row else None
        return (current or 0) + 1

    @staticmethod
    async def bulk_create(assets: List[Equipment]) -> List[Equipment]:
        for asset in assets:
            asset.tenant_id = current_tenant_id()
        return await Equipment.bulk_create(assets)

    @staticmethod
    async def update(equipment: Equipment, fields: List[str]) -> None:
        await equipment.save(update_fields=fields)

    @staticmethod
    async def transition_status(
        equipment_id: int, from_status: EquipmentStatus, to_status: EquipmentStatus,
    ) -> bool:
        """Move an asset from ``from_status`` to ``to_status`` in one conditional UPDATE.

        Returns ``False`` when the row was not in ``from_status`` — the caller
        lost a race (or the asset moved on), so nothing was written.
        """
        updated = await scoped(
            Equipment.filter(id=equipment_id, status=from_status)
        ).update(status=to_status, updated_at=datetime.now(timezone.utc))
        return updated > 0

    @staticmethod
    async def delete(equipment: Equipment) -> None:
        await equipment.delete()

    # --- Loans ---

    @staticmethod
    async def create_loan(
        equipment: Equipment,
        borrower: User,
        checked_out_by: User,
    ) -> EquipmentLoan:
        return await EquipmentLoan.create(
            tenant_id=current_tenant_id(),
            event_id=tagged_event_id(),
            equipment=equipment,
            borrower=borrower,
            checked_out_by=checked_out_by,
        )

    @staticmethod
    async def get_open_loan(equipment: Equipment) -> Optional[EquipmentLoan]:
        return await scoped(EquipmentLoan.filter(
            equipment=equipment, checked_in_at__isnull=True
        )).prefetch_related('borrower').first()

    @staticmethod
    async def close_loan(loan: EquipmentLoan, checked_in_by: User, checked_in_at: datetime) -> bool:
        """Close ``loan`` if it is still open. ``False`` means someone else already did."""
        updated = await scoped(
            EquipmentLoan.filter(id=loan.id, checked_in_at__isnull=True)
        ).update(checked_in_at=checked_in_at, checked_in_by_id=checked_in_by.id)
        if not updated:
            return False
        loan.checked_in_at = checked_in_at
        loan.checked_in_by = checked_in_by
        return True

    @staticmethod
    async def has_open_loan(equipment_id: int) -> bool:
        return await scoped(EquipmentLoan.filter(
            equipment_id=equipment_id, checked_in_at__isnull=True
        )).exists()

    @staticmethod
    async def list_open_loans_for_user(user: User) -> List[EquipmentLoan]:
        return await scoped(EquipmentLoan.filter(
            borrower=user, checked_in_at__isnull=True
        )).order_by('-checked_out_at').prefetch_related('equipment')

    @staticmethod
    async def list_loans_for_equipment(
        equipment: Equipment, *, limit: Optional[int] = None,
    ) -> List[EquipmentLoan]:
        """Loans against one asset, newest first. ``limit`` bounds the fetch.

        Each row prefetches three users, so an asset lent two hundred times is
        two hundred rows and six hundred joins — on the device where scrolling
        costs most. The page asks for the handful it paints; use
        :meth:`count_loans_for_equipment` for the total it reports alongside.
        """
        query = scoped(EquipmentLoan.filter(equipment=equipment)).order_by('-checked_out_at')
        if limit is not None:
            query = query.limit(limit)
        return await query.prefetch_related('borrower', 'checked_out_by', 'checked_in_by')

    @staticmethod
    async def count_loans_for_equipment(equipment: Equipment) -> int:
        return await scoped(EquipmentLoan.filter(equipment=equipment)).count()

    @staticmethod
    async def open_loans_by_equipment_id() -> dict[int, EquipmentLoan]:
        """Map of equipment_id -> its open loan (borrower prefetched), for list views."""
        loans = await scoped(EquipmentLoan.filter(checked_in_at__isnull=True)).prefetch_related('borrower')
        return {loan.equipment_id: loan for loan in loans}
