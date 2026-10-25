"""Member details schemas (Admin → Members' member sheet, as data)."""

from datetime import datetime
from typing import List, Optional, Self

from pydantic import BaseModel, Field

from api.schemas.sent_messages import SentMessageEntry
from application.services import MemberDetails


class LinkedAccountInfo(BaseModel):
    service: str = Field(..., description="The service, e.g. `Challonge` or `racetime.gg`.")
    name: str = Field(..., description="Their name on it.")


class ChannelsInfo(BaseModel):
    has_discord: bool = Field(..., description="Whether the account has a Discord account to DM.")
    discord_dms: bool = Field(..., description="Whether they left Discord DMs on in their Profile.")
    push_devices: int = Field(..., description="Phones and browsers signed up for device notifications.")
    email: Optional[str] = Field(
        None,
        description=(
            "`on`, `off`, `unverified` or `none` (no address). Null when this community doesn't offer email. "
            "The address itself is never returned."
        ),
    )
    telegram: Optional[str] = Field(
        None, description="`on`, `off` or `none` (not linked). Null when this community doesn't offer Telegram.",
    )
    last_delivered_at: Optional[datetime] = Field(None, description="When Discord last delivered them a DM here (UTC).")


class NoticeInfo(BaseModel):
    key: str = Field(..., description="The message's catalogue key, as in the notification settings.")
    recipient: str = Field(..., description="Which recipient column it is, e.g. `players` or `crew`.")
    title: str = Field(..., description="The message's name.")
    section: str = Field(..., description="Where it sits on their Profile → Notifications list.")
    community: str = Field(..., description="The community's setting: `off`, `silent` or `ping`.")
    theirs: str = Field(..., description="Their own choice (their preset or a per-message change).")
    effective: str = Field(..., description="What they actually get: the quieter of the two.")
    reason: Optional[str] = Field(
        None,
        description=(
            "Why they get it this way when the two differ: `Community setting`, `Their choice`, "
            "`Their preset`, or that it's an opt-in message they haven't turned on."
        ),
    )


class UpcomingMatchInfo(BaseModel):
    match_id: int
    tournament: str
    title: Optional[str] = None
    scheduled_at: datetime = Field(..., description="UTC.")
    opponents: List[str]


class UpcomingShiftInfo(BaseModel):
    position: str
    starts_at: datetime = Field(..., description="UTC.")
    ends_at: datetime = Field(..., description="UTC.")
    checked_in: bool
    called_off: bool = Field(False, description="Staff called this shift off; it still counts as worked.")


class MemberDetailsResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None
    pronouns: Optional[str] = None
    discord_id: Optional[int] = Field(None, description="Their Discord ID.")
    timezone: Optional[str] = Field(None, description="The time zone they chose, if any.")
    is_active: bool
    joined_at: Optional[datetime] = Field(None, description="When they joined this community (UTC).")
    joined_via: Optional[str] = Field(None, description="How they joined, e.g. `discord_auto_join` or `staff`.")
    roles: List[str] = Field(..., description="Roles they hold in this community.")
    tournament_admin_of: List[str]
    crew_coordinator_of: List[str]
    entered: List[str] = Field(..., description="Tournaments they're entered in here.")
    linked_accounts: List[LinkedAccountInfo]
    channels: ChannelsInfo
    preset: str = Field(..., description="Their notification preset: `everything`, `needs_me` or `quiet`.")
    notifications: List[NoticeInfo] = Field(..., description="Every message that can reach them here.")
    # Built from dicts so the MCP twin can swap in its own message model.
    recent_messages: List[SentMessageEntry] = Field(..., description="The last DMs sent to them here, newest first.")
    upcoming_matches: List[UpcomingMatchInfo]
    upcoming_shifts: Optional[List[UpcomingShiftInfo]] = Field(
        None, description="Null when this community has volunteers turned off.",
    )
    equipment_out: Optional[List[str]] = Field(
        None, description="Equipment they have checked out. Null when equipment is turned off.",
    )
    check_in: Optional[str] = Field(
        None, description="Their event check-in status. Null when check-in is turned off.",
    )

    @classmethod
    def build(cls, d: MemberDetails) -> Self:
        u = d.user
        return cls(
            id=u.id, username=u.username, display_name=u.display_name, pronouns=u.pronouns,
            discord_id=u.discord_id, timezone=u.timezone, is_active=u.is_active,
            joined_at=d.joined_at, joined_via=d.joined_via.value if d.joined_via else None,
            roles=[r.value for r in d.roles],
            tournament_admin_of=d.tournament_admin_of, crew_coordinator_of=d.crew_coordinator_of,
            entered=d.entered,
            linked_accounts=[LinkedAccountInfo(service=a.service, name=a.name) for a in d.linked_accounts],
            channels=ChannelsInfo(**vars(d.channels)),
            preset=d.preset.value,
            notifications=[
                NoticeInfo(
                    key=n.key, recipient=n.recipient, title=n.title, section=n.section,
                    community=n.community.value, theirs=n.theirs.value, effective=n.effective.value,
                    reason=n.reason,
                )
                for n in d.notices
            ],
            recent_messages=[
                dict(  # type: ignore[misc]
                    id=m.id, recipient_id=m.recipient_id, recipient_name=u.preferred_name,
                    discord_id=int(m.discord_id), title=m.title, body=m.body,
                    link_label=m.link_label, link_url=m.link_url, silent=m.silent,
                    notice_key=m.notice_key, audience=m.audience, status=m.status.value,
                    error=m.error, push_devices=m.push_devices, email_sent=m.email_sent,
                    telegram_sent=m.telegram_sent, created_at=m.created_at,
                )
                for m in d.recent_messages
            ],
            upcoming_matches=[UpcomingMatchInfo(**vars(m)) for m in d.upcoming_matches],
            upcoming_shifts=(
                [UpcomingShiftInfo(**vars(s)) for s in d.upcoming_shifts]
                if d.upcoming_shifts is not None else None
            ),
            equipment_out=d.equipment_out,
            check_in=d.check_in,
        )
