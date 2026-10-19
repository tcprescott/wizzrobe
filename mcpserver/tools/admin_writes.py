"""Event-day administration: stages, volunteer schedule, members and roles, equipment, payouts.

The fourth write module, held to the same rule as the others: each tool
mirrors one REST endpoint, from ``api/routers/stage_actions.py``,
``api/routers/volunteers.py``, ``api/routers/users.py``,
``api/routers/equipment.py``, ``api/routers/payouts.py`` and
``api/routers/notification_settings.py``. A write that
belongs here belongs in the REST router too, and vice versa.

Every tool is registered at ``Gate.ACTOR`` with ``write=True``, mirroring
``require_write_actor``; the owning service makes the real permission check
(stage managers, volunteer coordinators, equipment managers, staff, a
tournament's own admins for its payouts). Each declares its feature flag so a
community with the feature off is never offered it. Every row is loaded
through a tenant-scoped repository, so an id from another community is simply
not found.
"""

from decimal import Decimal
from typing import Callable, List, Optional

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from api.schemas.notification_settings import NotificationSettingResponse
from api.schemas.payouts import PayoutLineRequest
from api.schemas.volunteers import (
    AssignResponse,
    VolunteerAssignmentResponse,
    VolunteerPositionCreate,
    VolunteerPositionResponse,
    VolunteerPositionUpdate,
    VolunteerShiftResponse,
)
from application.errors import require_found
from application.services import (
    EquipmentService,
    NotificationSettingsService,
    PayoutService,
    StageService,
    TenantMembershipService,
    TenantService,
    UserService,
)
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.tenant_context import require_tenant_id
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import EquipmentDetail, OperationResult, StageInfo, TenantArg, TournamentPayouts
from mcpserver.tools._args import parse_ts, window
from mcpserver.tools.equipment import get_equipment
from mcpserver.tools.tournaments import get_tournament_payouts
from models import EquipmentStatus, FeatureFlag, Role, User


async def _user(user_id: int) -> User:
    return require_found(await UserService().get_user_by_id(user_id), 'User')


async def _community_user(user_id: int) -> User:
    """A member of this community, else not found, like ``load_community_user_or_404``."""
    user = await _user(user_id)
    is_member = await TenantService.is_member(user.id, require_tenant_id())
    return require_found(user if is_member else None, 'User')


def _role(value: str) -> Role:
    try:
        return Role(value.strip().lower())
    except ValueError as exc:
        valid = ', '.join(r.value for r in Role if r is not Role.SUPER_ADMIN)
        raise ValueError(f"Unknown role '{value}'. Valid roles: {valid}") from exc


# --- Stages ---------------------------------------------------------------------


async def create_stage(
    name: str,
    tenant: TenantArg = None,
    stream_url: Optional[str] = None,
    is_active: bool = True,
) -> StageInfo:
    """Add a stage (a stream or play area matches are assigned to). Staff or broadcast techs."""
    stage = await StageService().create_stage(
        name=name, stream_url=stream_url, is_active=is_active, actor=current_actor().user,
    )
    return StageInfo.model_validate(stage, from_attributes=True)


async def update_stage(
    stage_id: int,
    tenant: TenantArg = None,
    name: Optional[str] = None,
    stream_url: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> StageInfo:
    """Change a stage. Only what you pass changes; an empty `stream_url` clears it.

    Set `is_active` false to retire a stage that has matches on it. Staff or
    broadcast techs.
    """
    service = StageService()
    stage = require_found(await service.get_stage_by_id(stage_id), 'Stage')
    await service.update_stage(
        stage, name=name, stream_url=stream_url, is_active=is_active, actor=current_actor().user,
    )
    return StageInfo.model_validate(stage, from_attributes=True)


async def delete_stage(
    stage_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Permanently delete a stage with no matches on it. Staff or broadcast techs."""
    service = StageService()
    stage = require_found(await service.get_stage_by_id(stage_id), 'Stage')
    await service.delete_stage(stage, actor=current_actor().user)
    return OperationResult(detail=f'Stage {stage_id} deleted.')


# --- Volunteer positions ----------------------------------------------------------


async def create_volunteer_position(
    name: str,
    tenant: TenantArg = None,
    description: Optional[str] = None,
    color: Optional[str] = None,
    display_order: int = 0,
    is_active: bool = True,
    shift_length_minutes: Optional[int] = None,
    stagger_minutes: Optional[int] = None,
    staff_alerts: bool = False,
) -> VolunteerPositionResponse:
    """Add a volunteer position, such as "Runner" or "Info desk". Staff or volunteer coordinators.

    Set `shift_length_minutes` and `stagger_minutes` together to have generated
    shifts overlap; leave both out for back-to-back blocks. Set `staff_alerts`
    on the admin-desk position: whoever is on its shift gets match alerts
    (player comments, reschedule requests) instead of every staff member.
    """
    args = dict(locals())
    args.pop('tenant')
    body = VolunteerPositionCreate.model_validate(args)
    position = await VolunteerPositionService().create(current_actor().user, **body.model_dump())
    return VolunteerPositionResponse.model_validate(position, from_attributes=True)


async def update_volunteer_position(
    position_id: int,
    tenant: TenantArg = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    color: Optional[str] = None,
    display_order: Optional[int] = None,
    is_active: Optional[bool] = None,
    shift_length_minutes: Optional[int] = None,
    stagger_minutes: Optional[int] = None,
    staff_alerts: Optional[bool] = None,
) -> VolunteerPositionResponse:
    """Change a volunteer position. Only what you pass changes. Staff or volunteer coordinators."""
    args = dict(locals())
    fields = {
        k: v for k, v in args.items() if k not in ('position_id', 'tenant') and v is not None
    }
    body = VolunteerPositionUpdate.model_validate(fields)
    service = VolunteerPositionService()
    position = require_found(await service.get(position_id), 'Position')
    changes = body.model_dump(exclude_unset=True)
    if changes:
        position = await service.update(current_actor().user, position, **changes)
    return VolunteerPositionResponse.model_validate(position, from_attributes=True)


async def delete_volunteer_position(
    position_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Delete a position with all its shifts and assignments. Nobody is notified. Staff or volunteer coordinators."""
    service = VolunteerPositionService()
    position = require_found(await service.get(position_id), 'Position')
    await service.delete(current_actor().user, position)
    return OperationResult(detail=f'Position {position_id} deleted with its shifts.')


# --- Volunteer shifts and assignments ---------------------------------------------


async def create_volunteer_shift(
    position_id: int,
    starts_at: str,
    ends_at: str,
    tenant: TenantArg = None,
    slots_needed: int = 1,
    label: Optional[str] = None,
    notes: Optional[str] = None,
) -> VolunteerShiftResponse:
    """Add an empty shift under a position. Times are ISO 8601 (UTC). Staff or volunteer coordinators."""
    service = VolunteerScheduleService()
    start, end = window(starts_at, ends_at)
    shift = await service.create_shift(
        current_actor().user,
        position_id=position_id,
        starts_at=start,
        ends_at=end,
        label=label, slots_needed=slots_needed, notes=notes,
    )
    return VolunteerShiftResponse.from_row(
        require_found(await service.get_shift(shift.id), 'Shift'), include_drafts=True,
    )


async def update_volunteer_shift(
    shift_id: int,
    tenant: TenantArg = None,
    position_id: Optional[int] = None,
    starts_at: Optional[str] = None,
    ends_at: Optional[str] = None,
    slots_needed: Optional[int] = None,
    label: Optional[str] = None,
    notes: Optional[str] = None,
) -> VolunteerShiftResponse:
    """Change a shift. Only what you pass changes; an empty `label` or `notes` clears it.

    Moving the start or end clears everyone's acknowledgment and DMs them the
    new time. Staff or volunteer coordinators.
    """
    fields: dict = {}
    if position_id is not None:
        fields['position_id'] = position_id
    if starts_at:
        fields['starts_at'] = parse_ts('starts_at', starts_at)
    if ends_at:
        fields['ends_at'] = parse_ts('ends_at', ends_at)
    if slots_needed is not None:
        fields['slots_needed'] = slots_needed
    for key, value in (('label', label), ('notes', notes)):
        if value is not None:
            fields[key] = value.strip() or None
    service = VolunteerScheduleService()
    shift = require_found(await service.get_shift(shift_id), 'Shift')
    if fields:
        await service.update_shift(current_actor().user, shift, **fields)
    return VolunteerShiftResponse.from_row(
        require_found(await service.get_shift(shift_id), 'Shift'), include_drafts=True,
    )


async def delete_volunteer_shift(
    shift_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Delete a shift and its assignments. Everyone on it gets a DM. Staff or volunteer coordinators."""
    service = VolunteerScheduleService()
    shift = require_found(await service.get_shift(shift_id), 'Shift')
    await service.delete_shift(current_actor().user, shift)
    return OperationResult(detail=f'Shift {shift_id} deleted.')


async def assign_volunteer(
    shift_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> AssignResponse:
    """Put a member on a shift. They get a DM to acknowledge it. Staff or volunteer coordinators.

    `warnings` flags soft problems that don't block it: the shift is already
    full, or the person marked the time unavailable. Refused when they are
    already on an overlapping shift.
    """
    service = VolunteerScheduleService()
    shift = require_found(await service.get_shift(shift_id), 'Shift')
    target = await _community_user(user_id)
    assignment, warnings = await service.assign(current_actor().user, shift, target)
    assignment.user = target
    return AssignResponse(assignment=VolunteerAssignmentResponse.from_row(assignment), warnings=warnings)


async def unassign_volunteer(
    assignment_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take a volunteer off a shift. `assignment_id` is the assignment row, not the shift.

    A published assignment's volunteer gets a DM; removing a draft is silent.
    Staff or volunteer coordinators.
    """
    service = VolunteerScheduleService()
    assignment = require_found(await service.get_assignment(assignment_id), 'Assignment')
    await service.unassign(current_actor().user, assignment)
    return OperationResult(detail=f'Assignment {assignment_id} removed.')


# --- Members and roles ----------------------------------------------------------------


async def add_community_member(
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Make someone a member of this community. Staff only. They get a DM saying they're in."""
    await TenantMembershipService().add_member(current_actor().user, await _user(user_id))
    return OperationResult(detail=f'User {user_id} is a member.')


async def remove_community_member(
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take someone out of this community. Staff only.

    Refused while they hold a role here or admin or coordinate a tournament;
    revoke those first.
    """
    await TenantMembershipService().remove_member(current_actor().user, await _community_user(user_id))
    return OperationResult(detail=f'User {user_id} removed from the community.')


async def grant_community_role(
    user_id: int,
    role: str,
    tenant: TenantArg = None,
) -> OperationResult:
    """Give someone a role in this community, making them a member if needed. Staff only.

    `role` is a name such as `proctor`, `volunteer` or `check_in_desk`.
    """
    parsed = _role(role)
    await UserService().grant_role(await _user(user_id), parsed, actor=current_actor().user)
    return OperationResult(detail=f'Granted {parsed.value} to user {user_id}.')


async def revoke_community_role(
    user_id: int,
    role: str,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take a role away in this community. They stay a member. Staff only."""
    parsed = _role(role)
    await UserService().revoke_role(await _user(user_id), parsed, actor=current_actor().user)
    return OperationResult(detail=f'Revoked {parsed.value} from user {user_id}.')


# --- Notification settings --------------------------------------------------------------


async def set_notification_setting(
    notice: str,
    recipient: str,
    mode: str,
    tenant: TenantArg = None,
) -> NotificationSettingResponse:
    """Set how one message reaches one kind of recipient. Staff only.

    `notice` and `recipient` come from `list_notification_settings`; `mode` is
    `off`, `silent` or `ping`. Choosing the default clears the override.
    Mirrors `PUT /api/notification-settings/{notice}/{recipient}`.
    """
    return NotificationSettingResponse.from_row(await NotificationSettingsService.set_mode(current_actor().user, notice, recipient, mode))


async def reset_notification_settings(
    tenant: TenantArg = None,
) -> OperationResult:
    """Put every notification back to its default. Staff only.

    Mirrors `POST /api/notification-settings/reset`.
    """
    count = await NotificationSettingsService.reset_all(current_actor().user)
    return OperationResult(detail=f'{count} setting(s) back to default.')


# --- Equipment ------------------------------------------------------------------------


async def create_equipment(
    name: str,
    tenant: TenantArg = None,
    count: int = 1,
    description: Optional[str] = None,
    private_notes: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> List[EquipmentDetail]:
    """Add one or more identical assets, numbered from the next free asset number. Staff or equipment managers.

    `owner_user_id` is a member who lent the item, if it isn't the community's.
    """
    service = EquipmentService()
    actor = current_actor().user
    if count == 1:
        assets = [await service.create_asset(
            actor, name=name, description=description,
            private_notes=private_notes, owner_user_id=owner_user_id,
        )]
    else:
        created = await service.bulk_create_assets(
            actor, name=name, count=count, description=description,
            private_notes=private_notes, owner_user_id=owner_user_id,
        )
        numbers = {asset.asset_number for asset in created}
        assets = [a for a in await service.list_assets() if a.asset_number in numbers]
    return [await get_equipment(asset.id) for asset in assets]


async def update_equipment(
    equipment_id: int,
    tenant: TenantArg = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    private_notes: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    clear_owner: bool = False,
    status: Optional[str] = None,
) -> EquipmentDetail:
    """Change an asset. Only what you pass changes; an empty string clears a text field.

    `status` is `available` or `retired`; use the checkout tools to lend it.
    Staff or equipment managers.
    """
    service = EquipmentService()
    asset = require_found(await service.get_asset(equipment_id), 'Equipment')
    parsed_status = None
    if status:
        try:
            parsed_status = EquipmentStatus(status.strip().lower())
        except ValueError as exc:
            valid = ', '.join(s.value for s in EquipmentStatus)
            raise ValueError(f"Unknown status '{status}'. Valid: {valid}") from exc
    if clear_owner and owner_user_id is not None:
        raise ValueError('owner_user_id is both set and cleared; pick one.')
    await service.update_asset(
        current_actor().user,
        equipment_id,
        name=asset.name if name is None else name,
        description=asset.description if description is None else description,
        private_notes=asset.private_notes if private_notes is None else private_notes,
        owner_user_id=None if clear_owner else (
            asset.owner_user_id if owner_user_id is None else owner_user_id
        ),
        status=parsed_status,
    )
    return await get_equipment(equipment_id)


async def delete_equipment(
    equipment_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Delete an asset and its loan history. Refused while it's checked out. Staff or equipment managers."""
    await EquipmentService().delete_asset(current_actor().user, equipment_id)
    return OperationResult(detail=f'Equipment {equipment_id} deleted.')


async def check_out_equipment(
    equipment_id: int,
    tenant: TenantArg = None,
    borrower_id: Optional[int] = None,
) -> EquipmentDetail:
    """Lend an asset. Without `borrower_id` it goes to you; naming someone else needs staff or an equipment manager."""
    await EquipmentService().checkout(current_actor().user, equipment_id, borrower_id=borrower_id)
    return await get_equipment(equipment_id)


async def check_in_equipment(
    equipment_id: int,
    tenant: TenantArg = None,
) -> EquipmentDetail:
    """Record an asset coming back. Staff or equipment managers."""
    await EquipmentService().checkin(current_actor().user, equipment_id)
    return await get_equipment(equipment_id)


# --- Payouts --------------------------------------------------------------------------


class PayoutLine(BaseModel):
    place: int = Field(..., ge=1, description='Finishing place this share pays, starting at 1.')
    percentage: Decimal = Field(..., gt=0, description='Share of the total, as a percentage ("25" is 25%).')
    entrant_id: Optional[int] = Field(None, description='User ID of the person paid; leave out while drafting.')
    note: Optional[str] = None


async def set_tournament_prize_pool(
    tournament_id: int,
    tenant: TenantArg = None,
    prize_pool: Optional[Decimal] = None,
    prize_bonus: Optional[Decimal] = None,
) -> TournamentPayouts:
    """Set a tournament's prize pool and bonus together; leaving one out clears it.

    Every place's amount moves with the new total. Staff or that tournament's admins.
    """
    await PayoutService().set_pool(tournament_id, prize_pool, prize_bonus, current_actor().user)
    return await get_tournament_payouts(tournament_id)


async def set_tournament_payout_split(
    tournament_id: int,
    lines: List[PayoutLine],
    tenant: TenantArg = None,
) -> TournamentPayouts:
    """Replace a tournament's whole prize split. An empty list clears it.

    Percentages may add up to less than 100 but not more, and every entrant must
    be a member. Staff or that tournament's admins.
    """
    rows = [PayoutLineRequest.model_validate(line.model_dump()).model_dump() for line in lines]
    await PayoutService().set_split(tournament_id, rows, current_actor().user)
    return await get_tournament_payouts(tournament_id)


def register_tools(mcp: MCPServer) -> None:
    def write_tool(
        fn: Callable, title: str, feature: Optional[FeatureFlag] = None, *, destructive: bool = False,
    ) -> None:
        register(
            mcp, fn, gate=Gate.ACTOR, feature=feature, write=True, title=title,
            destructive=destructive,
        )

    write_tool(create_stage, 'Create stage')
    write_tool(update_stage, 'Update stage')
    write_tool(delete_stage, 'Delete stage', destructive=True)

    volunteers = FeatureFlag.VOLUNTEERS
    write_tool(create_volunteer_position, 'Create volunteer position', volunteers)
    write_tool(update_volunteer_position, 'Update volunteer position', volunteers)
    write_tool(delete_volunteer_position, 'Delete volunteer position', volunteers, destructive=True)
    write_tool(create_volunteer_shift, 'Create volunteer shift', volunteers)
    write_tool(update_volunteer_shift, 'Update volunteer shift', volunteers)
    write_tool(delete_volunteer_shift, 'Delete volunteer shift', volunteers, destructive=True)
    write_tool(assign_volunteer, 'Assign volunteer', volunteers)
    write_tool(unassign_volunteer, 'Unassign volunteer', volunteers, destructive=True)

    write_tool(add_community_member, 'Add community member')
    write_tool(remove_community_member, 'Remove community member', destructive=True)
    write_tool(grant_community_role, 'Grant community role')
    write_tool(revoke_community_role, 'Revoke community role', destructive=True)

    write_tool(set_notification_setting, 'Set notification setting')
    write_tool(reset_notification_settings, 'Reset notification settings', destructive=True)

    equipment = FeatureFlag.EQUIPMENT
    write_tool(create_equipment, 'Create equipment', equipment)
    write_tool(update_equipment, 'Update equipment', equipment)
    write_tool(delete_equipment, 'Delete equipment', equipment, destructive=True)
    write_tool(check_out_equipment, 'Check out equipment', equipment)
    write_tool(check_in_equipment, 'Check in equipment', equipment)

    payouts = FeatureFlag.PAYOUTS
    write_tool(set_tournament_prize_pool, 'Set prize pool', payouts)
    write_tool(set_tournament_payout_split, 'Set payout split', payouts)
