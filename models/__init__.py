"""Tortoise ORM models for Wizzrobe.

Historically a single ``models.py``; split into cohesive per-domain submodules
once it outgrew the file-length budget. Every model and enum is re-exported here
so ``from models import X`` keeps working and Tortoise/aerich still discover the
whole schema through the app's single ``"models"`` entry (discovery iterates this
package's namespace). Cross-model foreign keys use string references
(``'models.User'``), so submodules have no import-order dependencies on each
other — only the shared enums in :mod:`models.enums` are imported directly.
"""

from .accommodation import AccommodationRequest
from .async_qualifier import (
    AsyncQualifier,
    AsyncQualifierLiveRace,
    AsyncQualifierPermalink,
    AsyncQualifierPool,
    AsyncQualifierReviewNote,
    AsyncQualifierRun,
)
from .audit import AuditLog, TelemetryEvent
from .bracket import (
    Bracket,
    BracketEntrant,
    BracketEntry,
    BracketMatch,
    BracketMatchGame,
)
from .challonge import (
    ChallongeApiUsage,
    ChallongeConnection,
    ChallongeMatch,
    ChallongeParticipant,
)
from .check_in import CheckInEntrant, CheckInEvent, CheckInPass, CheckInTier, MatcherinoLogin
from .column_guards import FieldValueError, install_column_guards
from .community_event import CommunityEvent, CommunityEventSeen, CommunityEventSubscription
from .data_export import DataExport
from .discord_events import DiscordScheduledEvent, SentMessage, StaffRequestDM
from .discord_role_sync import DiscordRoleGrant, DiscordRoleRule, DiscordRoleSyncState
from .emergency_contact import EmergencyContact
from .enums import (
    STATION_REGEXES,
    SYSTEM_USER_DISCORD_ID,
    AccommodationStatus,
    ApiTokenOrigin,
    AsyncQualifierLiveRaceStatus,
    AsyncQualifierReviewStatus,
    AsyncQualifierRunStatus,
    BotStatus,
    BracketEntrantStatus,
    BracketEntryStatus,
    BracketFormat,
    BracketMatchGameState,
    BracketMatchState,
    BracketState,
    ChallongeMatchState,
    CheckInDmStatus,
    CheckInEntrantSource,
    CheckInEventStatus,
    CheckInLinkMethod,
    CheckInTierLanyard,
    CommentaryInviteStatus,
    CommunityEventStatus,
    DataExportKind,
    DataExportStatus,
    DiscordEventSource,
    DiscordRoleRuleKind,
    EquipmentStatus,
    EventStatus,
    FeatureFlag,
    FeedbackCategory,
    FeedbackStatus,
    ForfeitReason,
    JoinRequestStatus,
    MatchCommentKind,
    MatchNotificationLevel,
    MembershipSource,
    NotificationMode,
    NotificationPreset,
    NotificationRecipient,
    PresetOverride,
    ProviderTaskStatus,
    RaceRoomHoldReason,
    RaceRoomStatus,
    RescheduleRequestKind,
    RescheduleRequestStatus,
    Role,
    RoleSource,
    SentMessageStatus,
    StaffRequestKind,
    StartggSetState,
    StationFormat,
    StationSide,
    SyncStatus,
    TournamentGrant,
    VolunteerAvailabilityStatus,
)
from .equipment import Equipment, EquipmentLoan
from .event import Event
from .feature_flag import FeatureFlagGroup, TenantFeatureFlag
from .feedback import Feedback
from .item_tracker import ItemTrackerBoard, ItemTrackerChange
from .match import (
    CommentaryInvite,
    Commentator,
    Match,
    MatchAcknowledgment,
    MatchComment,
    MatchHardPresetOptIn,
    MatchPlayers,
    MatchRescheduleRequest,
    MatchStreamVolunteer,
    MatchWatcher,
    Stage,
    Station,
    Tracker,
)
from .mcp import McpAuthorizationCode, McpOAuthClient
from .notification_preference import NotificationPreference
from .notification_setting import NotificationSetting
from .preferences import UserTablePreference
from .racetime import RaceRoomProfile, RacetimeBot, RacetimeBotTenant, RacetimeRoom
from .speedgaming import SpeedGamingEpisode, SpeedGamingEventLink
from .startgg import StartggConnection, StartggEntrant, StartggPhase, StartggPool, StartggSet
from .stream_layout import StageOverlay, StreamLayout, StreamLayoutArt
from .system import (
    BrowserErrorIgnoreRule,
    BrowserMonitoringSettings,
    LogLevelOverride,
    RoomToken,
    SystemConfiguration,
)
from .tenant import (
    ProfileNudgeDismissal,
    Tenant,
    TenantJoinRequest,
    TenantLogo,
    TenantMemberBlock,
    TenantMembership,
    WatchLayoutVote,
)
from .tournament import (
    GeneratedSeeds,
    Preset,
    ProviderTask,
    RandomizerCredential,
    Tournament,
    TournamentNotificationPreference,
    TournamentPayout,
    TournamentPlayers,
    TriforceText,
)
from .user import (
    ApiToken,
    ContactVerification,
    DiscordRoleMapping,
    DiscordTournamentGrant,
    User,
    UserRole,
    WebPushSubscription,
)
from .volunteer import (
    PlayerAvailability,
    VolunteerAssignment,
    VolunteerAvailability,
    VolunteerPosition,
    VolunteerQualification,
    VolunteerRoleMapping,
    VolunteerShift,
)
from .webhook import PersonalWebhook, Webhook, WebhookDelivery

# Every model is imported by now, so the column-fit guards can be attached to
# their fields. Must stay last: it walks this module's namespace for models.
install_column_guards(globals())

__all__ = [
    'STATION_REGEXES',
    # constants
    'SYSTEM_USER_DISCORD_ID',
    # accommodation
    'AccommodationRequest',
    'AccommodationStatus',
    # user / auth
    'ApiToken',
    # enums
    'ApiTokenOrigin',
    # async qualifier
    'AsyncQualifier',
    'AsyncQualifierLiveRace',
    'AsyncQualifierLiveRaceStatus',
    'AsyncQualifierPermalink',
    'AsyncQualifierPool',
    'AsyncQualifierReviewNote',
    'AsyncQualifierReviewStatus',
    'AsyncQualifierRun',
    'AsyncQualifierRunStatus',
    # audit / telemetry
    'AuditLog',
    'BotStatus',
    # brackets
    'Bracket',
    'BracketEntrant',
    'BracketEntrantStatus',
    'BracketEntry',
    'BracketEntryStatus',
    'BracketFormat',
    'BracketMatch',
    'BracketMatchGame',
    'BracketMatchGameState',
    'BracketMatchState',
    'BracketState',
    'BrowserErrorIgnoreRule',
    'BrowserMonitoringSettings',
    # challonge
    'ChallongeApiUsage',
    'ChallongeConnection',
    'ChallongeMatch',
    'ChallongeMatchState',
    'ChallongeParticipant',
    # check-in
    'CheckInDmStatus',
    'CheckInEntrant',
    'CheckInEntrantSource',
    'CheckInEvent',
    'CheckInEventStatus',
    'CheckInLinkMethod',
    'CheckInPass',
    'CheckInTier',
    'CheckInTierLanyard',
    # match / crew
    'CommentaryInvite',
    'CommentaryInviteStatus',
    'Commentator',
    'CommunityEvent',
    'CommunityEventSeen',
    'CommunityEventStatus',
    'CommunityEventSubscription',
    'ContactVerification',
    'DataExport',
    'DataExportKind',
    'DataExportStatus',
    'DiscordEventSource',
    'DiscordRoleGrant',
    'DiscordRoleMapping',
    'DiscordRoleRule',
    'DiscordRoleRuleKind',
    'DiscordRoleSyncState',
    # discord events
    'DiscordScheduledEvent',
    'DiscordTournamentGrant',
    'EmergencyContact',
    # equipment
    'Equipment',
    'EquipmentLoan',
    'EquipmentStatus',
    'Event',
    'EventStatus',
    'FeatureFlag',
    'FeatureFlagGroup',
    # feedback
    'Feedback',
    'FeedbackCategory',
    'FeedbackStatus',
    # column guards
    'FieldValueError',
    'ForfeitReason',
    # tournament
    'GeneratedSeeds',
    'ItemTrackerBoard',
    'ItemTrackerChange',
    'JoinRequestStatus',
    'LogLevelOverride',
    'Match',
    'MatchAcknowledgment',
    'MatchComment',
    'MatchCommentKind',
    'MatchHardPresetOptIn',
    'MatchNotificationLevel',
    'MatchPlayers',
    'MatchRescheduleRequest',
    'MatchStreamVolunteer',
    'MatchWatcher',
    'MatcherinoLogin',
    # mcp oauth
    'McpAuthorizationCode',
    'McpOAuthClient',
    'MembershipSource',
    'NotificationMode',
    'NotificationPreference',
    'NotificationPreset',
    'NotificationRecipient',
    'NotificationSetting',
    # webhooks
    'PersonalWebhook',
    # volunteer
    'PlayerAvailability',
    'Preset',
    'PresetOverride',
    'ProfileNudgeDismissal',
    'ProviderTask',
    'ProviderTaskStatus',
    # racetime
    'RaceRoomProfile',
    'RaceRoomHoldReason',
    'RaceRoomStatus',
    'RacetimeBot',
    'RacetimeBotTenant',
    'RacetimeRoom',
    'RandomizerCredential',
    'RescheduleRequestKind',
    'RescheduleRequestStatus',
    'Role',
    'RoleSource',
    'RoomToken',
    'SentMessage',
    'SentMessageStatus',
    # speedgaming
    'SpeedGamingEpisode',
    'SpeedGamingEventLink',
    'StaffRequestDM',
    'StaffRequestKind',
    'Stage',
    'StageOverlay',
    'StartggConnection',
    'StartggEntrant',
    'StartggPhase',
    'StartggPool',
    'StartggSet',
    'StartggSetState',
    'Station',
    'StationFormat',
    'StationSide',
    'StreamLayout',
    'StreamLayoutArt',
    'SyncStatus',
    # system
    'SystemConfiguration',
    'TelemetryEvent',
    # tenant
    'Tenant',
    'TenantFeatureFlag',
    'TenantJoinRequest',
    'TenantLogo',
    'TenantMemberBlock',
    'TenantMembership',
    'Tournament',
    'TournamentGrant',
    'TournamentNotificationPreference',
    'TournamentPayout',
    'TournamentPlayers',
    'Tracker',
    'TriforceText',
    'User',
    'UserRole',
    # per-user UI preferences
    'UserTablePreference',
    'VolunteerAssignment',
    'VolunteerAvailability',
    'VolunteerAvailabilityStatus',
    'VolunteerPosition',
    'VolunteerQualification',
    'VolunteerRoleMapping',
    'VolunteerShift',
    'WatchLayoutVote',
    'WebPushSubscription',
    'Webhook',
    'WebhookDelivery',
]
