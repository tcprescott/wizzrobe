"""The app's Admin → Equipment screen, beyond the asset routes under
``/equipment``: the owner and borrower picker and the QR label sheet."""

from typing import List

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, require_api_actor
from api.schemas.admin_equipment import LabelAssetResponse, LabelSheetResponse, PersonPickResponse
from application.services import EquipmentAdminService
from models import User

router = APIRouter(prefix='/admin/equipment', tags=['Admin: equipment'], route_class=ServiceErrorRoute)


@router.get(
    '/people',
    response_model=List[PersonPickResponse],
    summary='People an asset can belong to or be lent to',
    description=(
        "This community's people, by name. `everyone` widens it to every account on the "
        "platform, for lending to someone who isn't a member yet. Deactivated accounts are "
        "left out.\n\n"
        "**Who can call this:** Staff or Equipment manager."
    ),
)
async def people(
    everyone: bool = Query(False, description="Include people outside this community."),
    actor: User = Depends(require_api_actor),
) -> List[PersonPickResponse]:
    return [
        PersonPickResponse(id=p.id, name=p.name, username=p.username)
        for p in await EquipmentAdminService().people(actor, everyone=everyone)
    ]


@router.get(
    '/labels',
    response_model=LabelSheetResponse,
    summary='QR labels to print',
    description=(
        "The named assets, by asset number, with the community name and logo the labels "
        "carry. Ids from another community are dropped. Each label's code links to "
        "`/equipment/{id}` on the website.\n\n"
        "**Who can call this:** Staff or Equipment manager."
    ),
)
async def labels(
    ids: List[int] = Query(..., description="Asset ids, repeated: `?ids=1&ids=2`."),
    logo: bool = Query(True, description="Put the community's logo in the middle of each code."),
    actor: User = Depends(require_api_actor),
) -> LabelSheetResponse:
    sheet = await EquipmentAdminService().labels(actor, ids, logo=logo)
    return LabelSheetResponse(
        community=sheet.community, qr_logo=sheet.qr_logo,
        assets=[LabelAssetResponse(id=a.id, asset_number=a.asset_number, name=a.name) for a in sheet.assets],
    )
