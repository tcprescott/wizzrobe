"""The app's Admin → Check-in Roster screen in one read: the check-in events,
the Matcherino login card, and what the event form offers (comp roles, the
volunteer comp threshold). Writes stay on ``CheckInService`` and
``MatcherinoLoginService``.
"""

from dataclasses import dataclass
from typing import List, Optional

from application.errors import require_found
from application.feature_flags import requires_feature
from application.services.auth_service import AuthService
from application.services.check_in_rules import LANYARD_LABELS, comp_label
from application.services.check_in_service import CheckInService
from application.services.matcherino_login_service import MatcherinoLoginService, MatcherinoLoginStatus
from application.services.system_config_service import SystemConfigService
from models import CheckInEvent, CheckInTier, CheckInTierLanyard, FeatureFlag, Role, User

_STAFF_ONLY = 'Only staff can set up check-in.'


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class CheckInBoard:
    events: List[CheckInEvent]
    login: MatcherinoLoginStatus
    comp_role_options: List[Option]
    lanyard_options: List[Option]
    volunteer_comp_hours: Optional[float]


class CheckInAdminService:
    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def board(self, actor: User) -> CheckInBoard:
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_ONLY)
        tiers = await SystemConfigService.get_volunteer_comp_tiers()
        return CheckInBoard(
            events=await CheckInService().list_events(),
            login=await MatcherinoLoginService().status(actor),
            comp_role_options=[Option(r.value, comp_label(r.value)) for r in Role.tenant_grantable()],
            lanyard_options=[Option(v.value, LANYARD_LABELS[v.value]) for v in CheckInTierLanyard],
            volunteer_comp_hours=tiers[0] if tiers else None,
        )

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def tiers(self, actor: User, event_id: int) -> List[CheckInTier]:
        """An event's badge types with their lanyards, for the event form."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_ONLY)
        service = CheckInService()
        event = require_found(await service.get_event(event_id), 'Check-in event')
        return await service.tiers_for(event)
