"""Schemas for the platform administration endpoints (super-admin only).

The ``/platform`` page's tabs as REST: communities, accounts, feature groups and
log levels. Racetime bots and the service-health board predate these and keep
their own modules.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from application.services import AccountOverview, CommunityOverview, LoggerLevelRow
from application.services.tenant_setup_service import SetupStep
from models import User

_SLUG = "URL slug, as in `/t/<slug>`: lowercase letters, digits and hyphens."
_DOMAIN = "Custom domain the community is served on, such as `example.com`. No scheme or path."
_GUILD = "Discord server id. One server may back several communities."


class SetupStepResponse(BaseModel):
    key: str = Field(..., description="Stable id of the step, such as `staff` or `tournament`.")
    label: str = Field(..., description="What the step asks for.")
    done: bool = Field(..., description="Whether the community has done it.")
    required: bool = Field(..., description="Whether matches can't be scheduled until it's done.")
    hint: str = Field(..., description="Why the step matters.")

    @classmethod
    def from_step(cls, step: SetupStep) -> 'SetupStepResponse':
        return cls(key=step.key, label=step.label, done=step.done, required=step.required, hint=step.hint)


class StaffMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="User id.")
    username: str = Field(..., description="Discord username, or sign-in name for a password-only account.")
    display_name: Optional[str] = Field(None, description="Display name, when set.")


class CommunityResponse(BaseModel):
    id: int = Field(..., description="Community id.")
    name: str = Field(..., description="Display name.")
    slug: str = Field(..., description=_SLUG)
    domain: Optional[str] = Field(None, description=_DOMAIN + " Null when unset.")
    discord_guild_id: Optional[int] = Field(None, description=_GUILD + " Null when unlinked.")
    is_active: bool = Field(..., description="Whether the community is switched on. An inactive one is hidden everywhere.")
    feature_group_id: Optional[int] = Field(
        None, description="Feature group (tier) the community is on. Null means the default group."
    )
    feature_group: Optional[str] = Field(None, description="Name of that group. Null means the default group.")
    setup_ready: bool = Field(..., description="Whether every required setup step is done.")
    setup_missing: List[str] = Field(..., description="Setup steps still to do, by label.")
    created_at: datetime = Field(..., description="When the community was created (UTC).")

    @classmethod
    def from_overview(cls, overview: CommunityOverview) -> 'CommunityResponse':
        tenant = overview.tenant
        return cls(
            id=tenant.id, name=tenant.name, slug=tenant.slug, domain=tenant.domain,
            discord_guild_id=tenant.discord_guild_id, is_active=tenant.is_active,
            feature_group_id=tenant.feature_group_id,  # type: ignore[attr-defined]
            feature_group=overview.feature_group,
            setup_ready=overview.setup_ready, setup_missing=overview.setup_missing,
            created_at=tenant.created_at,
        )


class CommunityDetailResponse(CommunityResponse):
    setup: List[SetupStepResponse] = Field(..., description="The whole setup checklist.")
    staff: List[StaffMemberResponse] = Field(..., description="Everyone holding Staff in the community.")


class CommunityCreateRequest(BaseModel):
    name: str = Field(..., description="Display name.")
    slug: str = Field(..., description=_SLUG + " Must be unused.")
    domain: Optional[str] = Field(None, description=_DOMAIN)
    discord_guild_id: Optional[int] = Field(None, description=_GUILD)


class CommunityUpdateRequest(BaseModel):
    """A partial edit. Omitted fields keep their value; `null` clears `domain` or `discord_guild_id`."""

    name: Optional[str] = Field(None, description="Display name.")
    slug: Optional[str] = Field(None, description=_SLUG + " Changing it breaks every saved link to the community.")
    domain: Optional[str] = Field(None, description=_DOMAIN)
    discord_guild_id: Optional[int] = Field(None, description=_GUILD)
    is_active: Optional[bool] = Field(None, description="Switch the community on or off.")


class StaffGrantRequest(BaseModel):
    user_id: int = Field(..., description="Id of the account to make Staff, from `GET /platform/users`.")


class CommunityFeatureResponse(BaseModel):
    flag: str = Field(..., description="Feature key, such as `volunteers`.")
    label: str = Field(..., description="Human-readable name.")
    description: str = Field(..., description="What the feature does.")
    category: str = Field(..., description="Group the feature is listed under.")
    group_available: bool = Field(..., description="Whether the community's feature group includes it.")
    override: Optional[bool] = Field(
        None, description="Platform override: `true` forces it available, `false` forces it off, null follows the group."
    )
    available: bool = Field(..., description="Whether the community may use it, after the override.")
    enabled: bool = Field(..., description="Whether the community's staff have it switched on.")
    live: bool = Field(..., description="Available and enabled: the feature is on for this community.")


class FeatureGroupAssignRequest(BaseModel):
    group_id: Optional[int] = Field(
        None, description="Feature group to put the community on. Null puts it back on the default group."
    )


class FeatureOverrideRequest(BaseModel):
    available: Optional[bool] = Field(
        None, description="`true` forces the feature available, `false` forces it off, null drops the override."
    )


class FeatureGroupResponse(BaseModel):
    id: int = Field(..., description="Group id.")
    name: str = Field(..., description="Group name.")
    description: str = Field(..., description="Free-text note. Empty string when unset.")
    flags: List[str] = Field(..., description="Feature keys the group makes available.")
    is_default: bool = Field(..., description="Whether communities with no group fall back to this one.")
    community_count: int = Field(..., description="How many communities are on this group.")


class FeatureGroupCreateRequest(BaseModel):
    name: str = Field(..., description="Group name. Must be unused.")
    flags: List[str] = Field(default_factory=list, description="Feature keys the group makes available. Unknown keys are dropped.")
    description: Optional[str] = Field(None, description="Free-text note.")
    is_default: bool = Field(False, description="Make this the default group. The old default stops being one.")


class FeatureGroupUpdateRequest(BaseModel):
    """A partial edit. Omitted fields keep their value."""

    name: Optional[str] = Field(None, description="Group name. Must be unused.")
    flags: Optional[List[str]] = Field(None, description="Replaces the group's feature keys. Unknown keys are dropped.")
    description: Optional[str] = Field(None, description="Free-text note.")
    is_default: Optional[bool] = Field(
        None, description="Make this the default group. Can't be turned off while it's the only default."
    )


class PlatformUserResponse(BaseModel):
    id: int = Field(..., description="User id.")
    username: str = Field(..., description="Discord username, or sign-in name for a password-only account.")
    display_name: Optional[str] = Field(None, description="Display name, when set.")
    discord_id: Optional[int] = Field(None, description="Discord user id. Null for a password-only account.")
    login_name: Optional[str] = Field(None, description="Password sign-in name. Null when the account has no password.")
    is_active: bool = Field(..., description="Whether the account can sign in.")
    is_super_admin: bool = Field(..., description="Whether the account holds the platform super-admin role.")
    communities: List[str] = Field(..., description="Names of the communities the account belongs to.")

    @classmethod
    def from_overview(cls, overview: AccountOverview) -> 'PlatformUserResponse':
        user: User = overview.user
        return cls(
            id=user.id, username=user.username, display_name=user.display_name,
            discord_id=user.discord_id, login_name=user.login_name or None,
            is_active=user.is_active, is_super_admin=overview.is_super_admin,
            communities=overview.communities,
        )


class UserActivationRequest(BaseModel):
    is_active: bool = Field(..., description="`false` signs the account out everywhere and blocks sign-in; `true` restores it.")


class LogLevelResponse(BaseModel):
    logger: str = Field(..., description="Logger name, as in `logging.getLogger(__name__)`. Empty string is the root logger.")
    description: str = Field(..., description="What the logger covers. `Custom` for one added by hand.")
    override: Optional[str] = Field(None, description="Level set here. Null when it inherits from its parent.")
    effective: str = Field(..., description="Level the logger is running at right now.")
    updated_by: Optional[str] = Field(None, description="Who set the override.")
    updated_at: Optional[datetime] = Field(None, description="When the override was set (UTC).")

    @classmethod
    def from_row(cls, row: LoggerLevelRow) -> 'LogLevelResponse':
        return cls(
            logger=row.name, description=row.description, override=row.override,
            effective=row.effective, updated_by=row.updated_by, updated_at=row.updated_at,
        )


class LogLevelSetRequest(BaseModel):
    logger: str = Field(..., description="Logger name. Empty string or `root` is the root logger.")
    level: str = Field(..., description="`TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`.")
