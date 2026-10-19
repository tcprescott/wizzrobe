"""
Member Details Service - Business Logic Layer

Everything this community knows about one member, in one read, for Admin →
Members' member sheet and its REST and MCP twins: roles, what they're entered
in and booked for, how messages reach them, what each message does for them
once the community's setting and their own choice are combined, and what was
actually sent.

Staff only, and only for members of the current community: someone outside it
reads as not found, so the sheet can't be used to probe other communities.
Account-level details stay coarse: an email address is reported as on, off or
unverified, never the address. The emergency contact isn't here at all; it is
opened on its own through :meth:`EmergencyContactService.open_for`, which
audits the look.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from application.errors import NotFoundError
from application.repositories import MemberDetailsRepository, UserRepository
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.notification_preference_service import (
    STAFF_RECIPIENTS,
    MyNotificationRow,
    NotificationPreferenceService,
)
from application.utils.discord_avatar import avatar_url
from models import (
    FeatureFlag,
    MembershipSource,
    NotificationMode,
    NotificationPreset,
    Role,
    SentMessage,
    User,
)

PRESET_LABELS = {
    NotificationPreset.EVERYTHING: 'Everything',
    NotificationPreset.NEEDS_ME: 'Only what needs me',
    NotificationPreset.QUIET: 'Quiet',
}

_RANK = {NotificationMode.OFF: 0, NotificationMode.SILENT: 1, NotificationMode.PING: 2}

UPCOMING_LIMIT = 3
RECENT_MESSAGES = 10


@dataclass(frozen=True)
class LinkedAccount:
    service: str
    name: str


@dataclass(frozen=True)
class Channels:
    """How a DM reaches them. ``email`` and ``telegram`` are ``None`` when the
    community doesn't offer them; otherwise a short state, never an address."""

    has_discord: bool
    discord_dms: bool
    push_devices: int
    email: Optional[str]
    telegram: Optional[str]
    last_delivered_at: Optional[datetime]


@dataclass(frozen=True)
class NoticeDetail:
    key: str
    recipient: str
    title: str
    when: str
    section: str
    community: NotificationMode
    theirs: NotificationMode
    effective: NotificationMode
    # Why they get it this way when it isn't simply what both sides ask for.
    reason: Optional[str]

    @property
    def quieter_than_community(self) -> bool:
        return self.effective != self.community


@dataclass(frozen=True)
class UpcomingMatch:
    match_id: int
    tournament: str
    title: Optional[str]
    scheduled_at: datetime
    opponents: List[str]


@dataclass(frozen=True)
class UpcomingShift:
    position: str
    starts_at: datetime
    ends_at: datetime
    checked_in: bool


@dataclass(frozen=True)
class MemberDetails:
    user: User
    avatar_url: Optional[str]
    joined_at: Optional[datetime]
    joined_via: Optional[MembershipSource]
    roles: List[Role]
    tournament_admin_of: List[str]
    crew_coordinator_of: List[str]
    entered: List[str]
    linked_accounts: List[LinkedAccount]
    channels: Channels
    preset: NotificationPreset
    notices: List[NoticeDetail]
    recent_messages: List[SentMessage]
    upcoming_matches: List[UpcomingMatch]
    # ``None`` where the community has the feature off, so a caller can tell
    # "nothing" from "not applicable".
    upcoming_shifts: Optional[List[UpcomingShift]]
    equipment_out: Optional[List[str]]
    check_in: Optional[str]
    emergency_contacts: bool = field(default=False)

    @property
    def preset_label(self) -> str:
        return PRESET_LABELS[self.preset]

    @property
    def changed_from_preset(self) -> int:
        return sum(1 for n in self.notices if n.reason == 'Their choice')

    @property
    def quieter_than_community(self) -> List[NoticeDetail]:
        return [n for n in self.notices if n.quieter_than_community]


def _reason(row: MyNotificationRow) -> Optional[str]:
    if row.effective == row.community and row.effective == row.mine:
        return None
    if _RANK[row.community] < _RANK[row.mine]:
        return 'Community setting'
    if row.opt_in and row.override is None:
        return "Opt-in, they haven't turned it on"
    return 'Their choice' if row.override is not None else 'Their preset'


def _notice(row: MyNotificationRow) -> NoticeDetail:
    recipient = row.target.recipient
    title = row.notice.title
    if recipient in STAFF_RECIPIENTS:
        title = f'{title} ({recipient.label.lower()})'
    return NoticeDetail(
        key=row.target.key, recipient=recipient.value, title=title,
        when=row.notice.when, section=row.section, community=row.community,
        theirs=row.mine, effective=row.effective, reason=_reason(row),
    )


def _linked_accounts(user: User) -> List[LinkedAccount]:
    pairs = (
        ('Challonge', user.challonge_username),
        ('start.gg', user.startgg_username),
        ('racetime.gg', user.racetime_username),
        ('Twitch', user.twitch_username),
        ('Matcherino', user.matcherino_username),
    )
    return [LinkedAccount(service, name) for service, name in pairs if name]


def _email_state(user: User) -> str:
    if not user.email:
        return 'none'
    if user.email_verified_at is None:
        return 'unverified'
    return 'on' if user.email_notifications else 'off'


def _telegram_state(user: User) -> str:
    if not user.telegram_chat_id:
        return 'none'
    return 'on' if user.telegram_notifications else 'off'


class MemberDetailsService:
    def __init__(self) -> None:
        self.repository = MemberDetailsRepository()

    async def get(self, member_id: int, *, actor: Optional[User]) -> MemberDetails:
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can look up a member.',
        )
        member = await UserRepository.get_by_id(member_id)
        membership = await self.repository.membership(member_id) if member else None
        if member is None or membership is None or member.is_system:
            raise NotFoundError('That person is not a member of this community.')

        live = await FeatureFlagService().enabled_flags()
        now = datetime.now(timezone.utc)
        mine = await NotificationPreferenceService.get_mine(member)

        matches = await self.repository.upcoming_matches(member.id, now, UPCOMING_LIMIT)
        shifts = None
        if FeatureFlag.VOLUNTEERS in live:
            shifts = [
                UpcomingShift(
                    a.shift.position.name, a.shift.starts_at, a.shift.ends_at, a.checked_in_at is not None,
                )
                for a in await self.repository.upcoming_shifts(member.id, now, UPCOMING_LIMIT)
            ]
        equipment = None
        if FeatureFlag.EQUIPMENT in live:
            equipment = [
                f'#{loan.equipment.asset_number} {loan.equipment.name}'
                for loan in await self.repository.open_loans(member.id)
            ]
        check_in = None
        if FeatureFlag.EVENT_CHECK_IN in live:
            entries = await self.repository.check_in_entries(member.id)
            check_in = 'Not on a check-in roster'
            if entries:
                entry = entries[0]
                check_in = (
                    f'Checked in at {entry.event.name}' if entry.checked_in_at
                    else f'Not checked in yet at {entry.event.name}'
                )

        channels_live = FeatureFlag.NOTIFICATION_CHANNELS in live
        return MemberDetails(
            user=member,
            avatar_url=avatar_url(member.discord_id, member.discord_avatar),
            joined_at=membership.created_at,
            joined_via=membership.source,
            roles=sorted(await AuthService.get_roles(member), key=lambda r: r.value),
            tournament_admin_of=await self.repository.admin_tournament_names(member.id),
            crew_coordinator_of=await self.repository.coordinated_tournament_names(member.id),
            entered=await self.repository.entered_tournament_names(member.id),
            linked_accounts=_linked_accounts(member),
            channels=Channels(
                has_discord=bool(member.discord_id),
                discord_dms=member.dm_notifications,
                push_devices=await self.repository.push_device_count(member.id),
                email=_email_state(member) if channels_live else None,
                telegram=_telegram_state(member) if channels_live else None,
                last_delivered_at=await self.repository.last_delivered_at(member.id),
            ),
            preset=mine.preset,
            notices=[_notice(row) for row in mine.rows],
            recent_messages=await self.repository.recent_messages(member.id, RECENT_MESSAGES),
            upcoming_matches=[
                UpcomingMatch(
                    m.id, m.tournament.name, m.title, m.scheduled_at,
                    [p.user.preferred_name for p in m.players if p.user_id != member.id],  # type: ignore[attr-defined]
                )
                for m in matches
            ],
            upcoming_shifts=shifts,
            equipment_out=equipment,
            check_in=check_in,
            emergency_contacts=FeatureFlag.EMERGENCY_CONTACTS in live,
        )
