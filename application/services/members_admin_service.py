"""The admin Members screen as the app draws it: one row per member, plus the
counts on its header (join requests, ADA requests) and the community's
switches that decide which columns and menu items exist.

Writes stay with the services that own them (roles, membership, join requests,
accommodations, emergency contacts); this only shapes the reads that no other
service returns in one piece.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from application.errors import NotFoundError
from application.event_context import get_current_event_id
from application.repositories.user_repository import UserRepository
from application.services.accommodation_service import AccommodationService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.user_service import UserService
from application.tenant_context import require_tenant_id
from application.utils.discord_avatar import avatar_url
from models import AccommodationStatus, FeatureFlag, MembershipSource, User

RECENT_JOIN_WINDOW = timedelta(days=7)

JOIN_SOURCE_LABELS: Dict[MembershipSource, str] = {
    MembershipSource.STAFF: 'Added by staff',
    MembershipSource.JOIN_REQUEST: 'Join request',
    MembershipSource.DISCORD_AUTO_JOIN: 'Joined via Discord',
    MembershipSource.OPEN: 'Joined openly',
    MembershipSource.ROLE_GRANT: 'Role grant',
    MembershipSource.DISCORD_ROLE: 'Discord role sync',
    MembershipSource.IMPORT: 'SpeedGaming import',
    MembershipSource.DISCORD_IMPORT: 'Discord import',
}

ADA_STATUS_LABELS: Dict[AccommodationStatus, str] = {
    AccommodationStatus.NEW: 'New',
    AccommodationStatus.ACKNOWLEDGED: 'Acknowledged',
    AccommodationStatus.ARRANGED: 'Arranged',
    AccommodationStatus.WITHDRAWN: 'Withdrawn',
}

_STAFF_ONLY = 'Only staff can manage members.'


@dataclass(frozen=True)
class MemberRole:
    role: str
    label: str
    event_name: Optional[str] = None


@dataclass(frozen=True)
class MemberAda:
    request_id: int
    status: str
    label: str
    changed_since_arranged: bool


@dataclass(frozen=True)
class MemberRow:
    id: int
    username: str
    display_name: Optional[str]
    preferred_name: str
    pronouns: Optional[str]
    avatar_url: Optional[str]
    is_active: bool
    challonge: Optional[str]
    roles: List[MemberRole]
    tournaments_admin: int
    tournaments_crew_coordinator: int
    joined_at: Optional[datetime]
    joined_via: Optional[str]
    joined_recently: bool
    ada: Optional[MemberAda] = None


@dataclass(frozen=True)
class MembersSwitches:
    accommodations: bool
    emergency_contacts: bool
    challonge: bool


@dataclass(frozen=True)
class MembersBoard:
    members: List[MemberRow]
    join_requests: int
    ada_action_needed: int
    switches: MembersSwitches


@dataclass(frozen=True)
class AddableAccount:
    id: int
    username: str
    preferred_name: str
    avatar_url: Optional[str]


@dataclass(frozen=True)
class AdaRequestRow:
    id: int
    user_id: int
    preferred_name: str
    username: str
    status: str
    label: str
    details: Optional[str]
    staff_notes: Optional[str]
    changed_since_arranged: bool
    arranged_details: Optional[str]
    created_at: datetime
    updated_at: datetime


class MembersAdminService:
    """Staff-only reads behind the app's Admin → Members screen."""

    async def board(self, actor: User) -> MembersBoard:
        await AuthService.ensure(await AuthService.is_staff(actor), _STAFF_ONLY)
        live = await FeatureFlagService().enabled_flags()
        accommodations = FeatureFlag.ADA_ACCOMMODATIONS in live
        tenant_id = require_tenant_id()

        values = await UserRepository.member_table_values()
        memberships = await TenantMembershipService().memberships_by_user()
        roles, admins, coordinators = await UserService().member_role_summary(
            tenant_id, get_current_event_id(),
        )
        ada_by_user = await AccommodationService().open_requests_by_user(actor) if accommodations else {}
        cutoff = datetime.now(timezone.utc) - RECENT_JOIN_WINDOW

        rows: List[MemberRow] = []
        for u in values:
            membership = memberships.get(u['id'])
            joined_at = membership.created_at if membership else None
            source = membership.source if membership else None
            request = ada_by_user.get(u['id'])
            rows.append(MemberRow(
                id=u['id'],
                username=u['username'],
                display_name=u['display_name'],
                preferred_name=u['display_name'] or u['username'] or '',
                pronouns=u['pronouns'],
                avatar_url=avatar_url(u['discord_id'], u['discord_avatar']),
                is_active=u['is_active'],
                challonge=u['challonge_username'] or u['challonge_user_id'] or None,
                roles=list({
                    (role.value, event): MemberRole(role=role.value, label=role.label, event_name=event)
                    for role, event in roles.get(u['id'], [])
                }.values()),
                tournaments_admin=admins[u['id']],
                tournaments_crew_coordinator=coordinators[u['id']],
                joined_at=joined_at,
                joined_via=JOIN_SOURCE_LABELS.get(source) if source else None,
                joined_recently=bool(joined_at and joined_at >= cutoff),
                ada=MemberAda(
                    request_id=request.id, status=request.status.value,
                    label=ADA_STATUS_LABELS[request.status],
                    changed_since_arranged=request.changed_since_arranged,
                ) if request is not None else None,
            ))

        return MembersBoard(
            members=rows,
            join_requests=len(await TenantMembershipService().list_pending_for(actor)),
            ada_action_needed=await AccommodationService().action_needed_count(actor) if accommodations else 0,
            switches=MembersSwitches(
                accommodations=accommodations,
                emergency_contacts=FeatureFlag.EMERGENCY_CONTACTS in live,
                challonge=FeatureFlag.CHALLONGE in live,
            ),
        )

    async def addable(self, actor: User) -> List[AddableAccount]:
        """Accounts on the platform that aren't members here yet, for Add a member."""
        await AuthService.ensure(await AuthService.can_grant_roles(actor), _STAFF_ONLY)
        return [
            AddableAccount(
                id=u.id, username=u.username, preferred_name=u.display_name or u.username,
                avatar_url=avatar_url(u.discord_id, u.discord_avatar),
            )
            for u in await TenantMembershipService().addable_users()
        ]

    async def ada_requests(self, actor: User, *, include_withdrawn: bool = False) -> List[AdaRequestRow]:
        """The ADA requests tab: every open request, with what the member asked for."""
        requests = await AccommodationService().list_requests(actor, include_withdrawn=include_withdrawn)
        return [self._ada_row(r) for r in requests]

    async def ada_request(self, actor: User, request_id: int) -> AdaRequestRow:
        request = await AccommodationService().get_request(actor, request_id)
        if request is None:
            raise NotFoundError('Accommodation request not found.')
        return self._ada_row(request)

    @staticmethod
    def _ada_row(request) -> AdaRequestRow:
        user = request.user
        return AdaRequestRow(
            id=request.id,
            user_id=request.user_id,
            preferred_name=user.display_name or user.username,
            username=user.username,
            status=request.status.value,
            label=ADA_STATUS_LABELS[request.status],
            details=request.details,
            staff_notes=request.staff_notes,
            changed_since_arranged=request.changed_since_arranged,
            arranged_details=request.arranged_details,
            created_at=request.created_at,
            updated_at=request.updated_at,
        )
