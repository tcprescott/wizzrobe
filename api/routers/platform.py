"""Platform administration endpoints — the ``/platform`` page's tabs as REST.

Communities, accounts, feature groups and log levels, all super-admin only.
Racetime bots (``/racetime-bots``) and the health board (``/service-health/board``)
were already here and keep their own routers.

Every route depends on ``require_super_admin`` (reads) or
``require_super_admin_write`` (writes), and every service it calls checks the
role again. A super admin's token is bound to one community like any other, but
nothing here reads that binding: these routes name their community by id.
Passwords and creating accounts are deliberately absent; they stay on the page.
"""

from enum import Enum
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, require_super_admin, require_super_admin_write
from api.schemas.platform import (
    CommunityCreateRequest,
    CommunityDetailResponse,
    CommunityFeatureResponse,
    CommunityResponse,
    CommunityUpdateRequest,
    FeatureGroupAssignRequest,
    FeatureGroupCreateRequest,
    FeatureGroupResponse,
    FeatureGroupUpdateRequest,
    FeatureOverrideRequest,
    LogLevelResponse,
    LogLevelSetRequest,
    PlatformUserResponse,
    SetupStepResponse,
    StaffGrantRequest,
    StaffMemberResponse,
    UserActivationRequest,
)
from application.errors import require_found
from application.services import (
    FeatureFlagService,
    LogLevelService,
    PlatformAdminService,
    TenantService,
)
from models import FeatureFlag, User

router = APIRouter(prefix="/platform", route_class=ServiceErrorRoute)

_WHO = "\n\n**Who can call this:** Super admins only (platform)."
_COMMUNITIES: List[Union[str, Enum]] = ["Communities"]
_USERS: List[Union[str, Enum]] = ["Platform users"]
_GROUPS: List[Union[str, Enum]] = ["Feature groups"]
_LOGGING: List[Union[str, Enum]] = ["Log levels"]


async def _detail(actor: User, tenant_id: int) -> CommunityDetailResponse:
    overview = await PlatformAdminService().get_community(actor, tenant_id)
    staff = await TenantService.list_staff(actor, tenant_id)
    return CommunityDetailResponse(
        **CommunityResponse.from_overview(overview).model_dump(),
        setup=[SetupStepResponse.from_step(step) for step in overview.setup],
        staff=[StaffMemberResponse.model_validate(u) for u in staff],
    )


def _flag(value: str) -> FeatureFlag:
    try:
        return FeatureFlag(value)
    except ValueError as exc:
        raise ValueError(
            f"Unknown feature '{value}'. Valid: {', '.join(f.value for f in FeatureFlag)}"
        ) from exc


@router.get(
    "/communities",
    tags=_COMMUNITIES,
    response_model=List[CommunityResponse],
    summary="List every community",
    description="Every community on the platform, switched off ones included, with its feature group and setup readiness." + _WHO,
)
async def list_communities(actor: User = Depends(require_super_admin)):
    return [
        CommunityResponse.from_overview(o)
        for o in await PlatformAdminService().list_communities(actor)
    ]


@router.get(
    "/communities/{tenant_id}",
    tags=_COMMUNITIES,
    response_model=CommunityDetailResponse,
    summary="Get a community",
    description="One community with its whole setup checklist and its Staff." + _WHO,
)
async def get_community(tenant_id: int, actor: User = Depends(require_super_admin)):
    return await _detail(actor, tenant_id)


@router.post(
    "/communities",
    tags=_COMMUNITIES,
    response_model=CommunityDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a community",
    description=(
        "Creates a switched-on community with no Staff; grant its first with "
        "`POST /platform/communities/{tenant_id}/staff`. Fails with 400 when the name is blank, the "
        "slug is malformed, reserved or taken, or the domain is malformed, the platform's own host or "
        "taken." + _WHO
    ),
)
async def create_community(payload: CommunityCreateRequest, actor: User = Depends(require_super_admin_write)):
    tenant = await TenantService.create_tenant(actor, **payload.model_dump())
    return await _detail(actor, tenant.id)


@router.patch(
    "/communities/{tenant_id}",
    tags=_COMMUNITIES,
    response_model=CommunityDetailResponse,
    summary="Update a community",
    description=(
        "Edits a community; omitted fields keep their value and `null` clears the domain or Discord "
        "server. Setting `is_active` to false hides the community from everyone, its own Staff "
        "included. Fails with 400 on the same checks as creating one." + _WHO
    ),
)
async def update_community(
    tenant_id: int, payload: CommunityUpdateRequest, actor: User = Depends(require_super_admin_write),
):
    tenant = await PlatformAdminService().require_community(actor, tenant_id)
    fields = payload.model_dump(exclude_unset=True)
    for required in ('name', 'slug', 'is_active'):
        if required in fields and fields[required] is None:
            raise ValueError(f'{required} cannot be null.')
    if fields:
        await TenantService.update_tenant(actor, tenant, **fields)
    return await _detail(actor, tenant_id)


@router.post(
    "/communities/{tenant_id}/staff",
    tags=_COMMUNITIES,
    response_model=List[StaffMemberResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Make someone Staff of a community",
    description=(
        "Makes the account a member and Staff of the community, the way a community gets its first "
        "admin. Returns the community's Staff afterwards. Doing it twice changes nothing." + _WHO
    ),
)
async def grant_staff(
    tenant_id: int, payload: StaffGrantRequest, actor: User = Depends(require_super_admin_write),
):
    return await PlatformAdminService().grant_community_staff(actor, tenant_id, payload.user_id)


@router.get(
    "/communities/{tenant_id}/features",
    tags=_COMMUNITIES,
    response_model=List[CommunityFeatureResponse],
    summary="List a community's features",
    description=(
        "Every feature with what the community's group gives it, the platform override, and whether "
        "it's live there." + _WHO
    ),
)
async def list_community_features(tenant_id: int, actor: User = Depends(require_super_admin)):
    await PlatformAdminService().require_community(actor, tenant_id)
    return await FeatureFlagService().list_for_tenant(actor, tenant_id)


@router.put(
    "/communities/{tenant_id}/feature-group",
    tags=_COMMUNITIES,
    response_model=List[CommunityFeatureResponse],
    summary="Put a community on a feature group",
    description=(
        "Sets the community's feature group (tier), or puts it back on the default group with `null`. "
        "Features can switch on or off for it straight away. Returns its features afterwards." + _WHO
    ),
)
async def assign_feature_group(
    tenant_id: int, payload: FeatureGroupAssignRequest, actor: User = Depends(require_super_admin_write),
):
    await PlatformAdminService().require_community(actor, tenant_id)
    service = FeatureFlagService()
    await service.assign_tenant_group(actor, tenant_id, payload.group_id)
    return await service.list_for_tenant(actor, tenant_id)


@router.put(
    "/communities/{tenant_id}/features/{flag}",
    tags=_COMMUNITIES,
    response_model=List[CommunityFeatureResponse],
    summary="Override one feature for a community",
    description=(
        "Forces one feature available (`true`) or off (`false`) for this community whatever its group "
        "says, or follows the group again (`null`). The community's own on/off choice is left alone. "
        "Fails with 400 for an unknown feature key. Returns its features afterwards." + _WHO
    ),
)
async def override_feature(
    tenant_id: int, flag: str, payload: FeatureOverrideRequest,
    actor: User = Depends(require_super_admin_write),
):
    await PlatformAdminService().require_community(actor, tenant_id)
    service = FeatureFlagService()
    await service.set_availability(actor, tenant_id, _flag(flag), payload.available)
    return await service.list_for_tenant(actor, tenant_id)


@router.get(
    "/users",
    tags=_USERS,
    response_model=List[PlatformUserResponse],
    summary="List every account",
    description=(
        "Every real account on the platform, across all communities. `q` matches name, Discord "
        "handle, Discord id and sign-in name." + _WHO
    ),
)
async def list_platform_users(
    q: Optional[str] = Query(None, description="Search text."),
    has_password: Optional[bool] = Query(None, description="Only accounts with (true) or without (false) a password."),
    is_active: Optional[bool] = Query(None, description="Only active (true) or deactivated (false) accounts."),
    limit: int = Query(200, ge=1, le=1000, description="Most rows to return."),
    actor: User = Depends(require_super_admin),
):
    rows = await PlatformAdminService().list_accounts(
        actor, search=q, has_password=has_password, is_active=is_active,
    )
    return [PlatformUserResponse.from_overview(o) for o in rows[:limit]]


@router.get(
    "/users/{user_id}",
    tags=_USERS,
    response_model=PlatformUserResponse,
    summary="Get an account",
    description="One account with its communities and sign-in methods." + _WHO,
)
async def get_platform_user(user_id: int, actor: User = Depends(require_super_admin)):
    return PlatformUserResponse.from_overview(await PlatformAdminService().get_account(actor, user_id))


@router.put(
    "/users/{user_id}/active",
    tags=_USERS,
    response_model=PlatformUserResponse,
    summary="Deactivate or reactivate an account",
    description=(
        "Deactivating signs the account out everywhere and stops it signing in to any community until "
        "it's reactivated. Fails with 400 for your own account." + _WHO
    ),
)
async def set_platform_user_active(
    user_id: int, payload: UserActivationRequest, actor: User = Depends(require_super_admin_write),
):
    return PlatformUserResponse.from_overview(
        await PlatformAdminService().set_account_active(actor, user_id, payload.is_active)
    )


async def _group(actor: User, group_id: int) -> FeatureGroupResponse:
    rows = await FeatureFlagService().list_groups_with_counts(actor)
    row = require_found(next((r for r in rows if r['id'] == group_id), None), 'Feature group')
    return FeatureGroupResponse(**row, community_count=row['tenant_count'])


@router.get(
    "/feature-groups",
    tags=_GROUPS,
    response_model=List[FeatureGroupResponse],
    summary="List feature groups",
    description="Every feature group (tier) with its features and how many communities are on it." + _WHO,
)
async def list_feature_groups(actor: User = Depends(require_super_admin)):
    return [
        FeatureGroupResponse(**row, community_count=row['tenant_count'])
        for row in await FeatureFlagService().list_groups_with_counts(actor)
    ]


@router.post(
    "/feature-groups",
    tags=_GROUPS,
    response_model=FeatureGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a feature group",
    description="Creates a feature group. Fails with 400 when the name is blank or taken." + _WHO,
)
async def create_feature_group(payload: FeatureGroupCreateRequest, actor: User = Depends(require_super_admin_write)):
    group = await FeatureFlagService().create_group(actor, **payload.model_dump())
    return await _group(actor, group.id)


@router.patch(
    "/feature-groups/{group_id}",
    tags=_GROUPS,
    response_model=FeatureGroupResponse,
    summary="Update a feature group",
    description=(
        "Edits a group; omitted fields keep their value. Communities on it gain or lose features "
        "straight away. Fails with 400 when the name is blank or taken, or when it would leave the "
        "platform with no default group." + _WHO
    ),
)
async def update_feature_group(
    group_id: int, payload: FeatureGroupUpdateRequest, actor: User = Depends(require_super_admin_write),
):
    await FeatureFlagService().update_group(actor, group_id, **payload.model_dump(exclude_unset=True))
    return await _group(actor, group_id)


@router.delete(
    "/feature-groups/{group_id}",
    tags=_GROUPS,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a feature group",
    description=(
        "Deletes a group. Its communities fall back to the default group straight away, which can "
        "switch features off for them. Fails with 400 for the only default group." + _WHO
    ),
)
async def delete_feature_group(group_id: int, actor: User = Depends(require_super_admin_write)):
    await FeatureFlagService().delete_group(actor, group_id)


@router.get(
    "/log-levels",
    tags=_LOGGING,
    response_model=List[LogLevelResponse],
    summary="List logger levels",
    description="The loggers worth tuning plus any set by hand, with the level each is running at." + _WHO,
)
async def list_log_levels(actor: User = Depends(require_super_admin)):
    return [LogLevelResponse.from_row(r) for r in await LogLevelService().list_rows(actor)]


@router.put(
    "/log-levels",
    tags=_LOGGING,
    response_model=List[LogLevelResponse],
    summary="Set a logger's level",
    description=(
        "Changes how much one logger writes to the container log, straight away and across restarts "
        "until reset. Sentry still gets INFO and up whatever you pick. Fails with 400 for a malformed "
        "logger name or unknown level. Returns every logger afterwards." + _WHO
    ),
)
async def set_log_level(payload: LogLevelSetRequest, actor: User = Depends(require_super_admin_write)):
    service = LogLevelService()
    await service.set_level(actor, payload.logger, payload.level)
    return [LogLevelResponse.from_row(r) for r in await service.list_rows(actor)]


@router.delete(
    "/log-levels",
    tags=_LOGGING,
    response_model=List[LogLevelResponse],
    summary="Reset a logger's level",
    description=(
        "Drops the level set on one logger so it inherits its parent's again. Does nothing when none "
        "was set. Returns every logger afterwards." + _WHO
    ),
)
async def reset_log_level(
    logger: str = Query(..., description="Logger name. Empty string or `root` is the root logger."),
    actor: User = Depends(require_super_admin_write),
):
    service = LogLevelService()
    await service.clear(actor, logger)
    return [LogLevelResponse.from_row(r) for r in await service.list_rows(actor)]
