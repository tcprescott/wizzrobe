"""The reads the app's Admin → Equipment screen needs beyond the asset routes:
the owner and borrower picker, and the sheet of QR labels."""

from dataclasses import dataclass
from typing import Dict, List, Optional

from application.feature_flags import requires_feature
from application.services.auth_service import AuthService
from application.services.equipment_service import EquipmentService
from application.services.player_home_service import PlayerHomeService
from application.services.tenant_logo_service import TenantLogoService
from application.services.tenant_service import TenantService
from application.services.user_service import UserService
from application.tenant_context import get_current_tenant_id
from application.utils.environment import get_base_url
from application.utils.qrcode_util import asset_qr_svg_data_uri
from application.utils.tenant_urls import tenant_url
from models import SYSTEM_USER_DISCORD_ID, Equipment, FeatureFlag, User

_MANAGERS_ONLY = 'Only staff and equipment managers can do that.'


@dataclass(frozen=True)
class PersonPick:
    id: int
    name: str
    username: str


@dataclass(frozen=True)
class LabelSheet:
    community: str
    qr_logo: Optional[str]
    assets: List[Equipment]
    codes: Dict[int, str]


@dataclass(frozen=True)
class EquipmentBoard:
    assets: List[Equipment]
    borrowers: Dict[int, User]
    zone: Optional[str]


class EquipmentAdminService:
    @requires_feature(FeatureFlag.EQUIPMENT)
    async def board(self, actor: User) -> EquipmentBoard:
        """The whole register with who holds each asset now, and the zone to print times in."""
        service = EquipmentService()
        assets = await service.list_register(actor)
        loans = await service.open_loans_by_equipment_id()
        return EquipmentBoard(
            assets=assets,
            borrowers={eid: loan.borrower for eid, loan in loans.items() if getattr(loan, 'borrower', None)},
            zone=await PlayerHomeService.viewer_zone(actor),
        )

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def people(self, actor: User, *, everyone: bool = False) -> List[PersonPick]:
        """Who an asset can belong to or be lent to: this community's people, or with
        ``everyone`` every account (the checkout dialog's "outside this community" box).
        Deactivated and system accounts are never offered."""
        await AuthService.ensure(await AuthService.can_manage_equipment(actor), _MANAGERS_ONLY)
        users = UserService()
        people = (
            await users.get_all_users() if everyone
            else await users.get_community_people_summaries(include_user_ids=[actor.id])
        )
        return sorted(
            (
                PersonPick(id=p.id, name=p.display_name or p.username, username=p.username)
                for p in people
                if p.is_active and str(p.discord_id) != str(SYSTEM_USER_DISCORD_ID)
            ),
            key=lambda p: p.name.lower(),
        )

    @requires_feature(FeatureFlag.EQUIPMENT)
    async def labels(self, actor: User, ids: List[int], *, logo: bool = True) -> LabelSheet:
        """The assets to print QR labels for, with the community name and logo the labels carry."""
        await AuthService.ensure(await AuthService.can_manage_equipment(actor), _MANAGERS_ONLY)
        qr_logo = await TenantLogoService.qr_logo_data_uri() if logo else None
        assets = await EquipmentService().get_assets_by_ids(ids)
        tid = get_current_tenant_id()
        tenant = await TenantService.get_by_id(tid) if tid is not None else None

        def link(asset: Equipment) -> str:
            path = f'/equipment/{asset.id}'
            return tenant_url(tenant, path) if tenant is not None else f'{get_base_url()}{path}'

        return LabelSheet(
            community=await TenantService.current_community_name() or 'Wizzrobe',
            qr_logo=qr_logo,
            assets=assets,
            codes={a.id: asset_qr_svg_data_uri(link(a), qr_logo) for a in assets},
        )
