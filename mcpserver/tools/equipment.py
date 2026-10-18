"""Equipment inventory reads.

Gated at **actor** behind the ``EQUIPMENT`` flag, mirroring
``api/routers/equipment.py``. A member reaches one asset at a time, as on its
QR page (``pages/equipment.py``), which shows the holder and loan history to
anyone; listing the whole register is the admin Equipment tab's job, so
``list_equipment`` goes through ``EquipmentService.list_register``, which admits
staff and equipment managers only. ``private_notes`` — the staff-only part of
an asset — stays out of both shapes.

``EquipmentService`` carries its own ``@requires_feature`` on every read, so the
flag is enforced twice — once at the registry gate, once in the owning service.
That is the intended shape, not redundancy: the registry hides the tool, the
service is what makes the hiding true for every other caller.
"""

from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.equipment import MyCheckoutResponse
from application.errors import require_found
from application.services import EquipmentService
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import EquipmentDetail, EquipmentSummary, LoanRecord, TenantArg
from models import EquipmentStatus, FeatureFlag

# A busy controller collects hundreds of loans over an event, and every equipment
# write tool hands back this detail; REST caps its history at 200 for the same reason.
MAX_LOANS = 25


def _name(user) -> Optional[str]:
    return user.preferred_name if user else None


def _loan(loan) -> LoanRecord:
    return LoanRecord(
        id=loan.id,
        borrower=_name(getattr(loan, 'borrower', None)),
        checked_out_at=loan.checked_out_at,
        checked_out_by=_name(getattr(loan, 'checked_out_by', None)),
        checked_in_at=loan.checked_in_at,
        checked_in_by=_name(getattr(loan, 'checked_in_by', None)),
    )


async def list_equipment(
    tenant: TenantArg = None,
    status: Optional[str] = None,
) -> List[EquipmentSummary]:
    """List the community's equipment inventory and who currently holds each item.

    For staff and equipment managers. `status` filters on the asset's state: `available`, `checked_out`, or
    `retired`.
    """
    wanted: Optional[str] = None
    if status:
        try:
            wanted = EquipmentStatus(status.strip().lower()).value
        except ValueError as exc:
            valid = ', '.join(s.value for s in EquipmentStatus)
            raise ValueError(
                f"Unknown status '{status}'. Valid: {valid}"
            ) from exc

    service = EquipmentService()
    assets = await service.list_register(current_actor().user)
    open_loans = await service.open_loans_by_equipment_id()
    return [
        EquipmentSummary(
            id=asset.id,
            asset_number=asset.asset_number,
            name=asset.name,
            status=getattr(asset.status, 'value', asset.status),
            owner=_name(getattr(asset, 'owner_user', None)),
            borrower=_name(getattr(open_loans.get(asset.id), 'borrower', None)),
        )
        for asset in assets
        if wanted is None or getattr(asset.status, 'value', asset.status) == wanted
    ]


async def get_equipment(
    equipment_id: int,
    tenant: TenantArg = None,
) -> EquipmentDetail:
    """Get one asset with its most recent checkouts, newest first, and the total count."""
    service = EquipmentService()
    asset = require_found(await service.get_asset(equipment_id), 'Equipment')
    # Newest first, so an open loan is always inside the window.
    loans = await service.loan_history(asset, limit=MAX_LOANS)
    open_loan = next((loan for loan in loans if loan.checked_in_at is None), None)
    return EquipmentDetail(
        id=asset.id,
        asset_number=asset.asset_number,
        name=asset.name,
        status=getattr(asset.status, 'value', asset.status),
        owner=_name(getattr(asset, 'owner_user', None)),
        borrower=_name(getattr(open_loan, 'borrower', None)),
        description=asset.description,
        loans=[_loan(loan) for loan in loans],
        loan_count=await service.loan_count(asset),
    )


async def my_equipment_checkouts(tenant: TenantArg = None) -> List[MyCheckoutResponse]:
    """The equipment you currently have checked out. Mirrors `GET /api/equipment/me/checkouts`."""
    return [
        MyCheckoutResponse(
            loan_id=loan.id,
            equipment_id=loan.equipment_id,
            asset_number=loan.equipment.asset_number,
            name=loan.equipment.name,
            checked_out_at=loan.checked_out_at,
        )
        for loan in await EquipmentService().my_checkouts(current_actor().user)
    ]


def register_tools(mcp: MCPServer) -> None:
    register(
        mcp, my_equipment_checkouts, gate=Gate.ACTOR,
        feature=FeatureFlag.EQUIPMENT, title='My equipment checkouts',
    )
    register(
        mcp, list_equipment, gate=Gate.ACTOR,
        feature=FeatureFlag.EQUIPMENT, title='List equipment',
    )
    register(
        mcp, get_equipment, gate=Gate.ACTOR,
        feature=FeatureFlag.EQUIPMENT, title='Get equipment',
    )
