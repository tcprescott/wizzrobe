"""
Data Access Layer (Repository Pattern)

Repositories handle all database queries and return domain objects.
They should NOT contain business logic - only data fetching/persistence.
"""

from .accommodation_repository import AccommodationRepository
from .api_token_repository import ApiTokenRepository
from .async_qualifier_repository import (
    AsyncQualifierLiveRaceRepository,
    AsyncQualifierPermalinkRepository,
    AsyncQualifierPoolRepository,
    AsyncQualifierRepository,
    AsyncQualifierReviewNoteRepository,
    AsyncQualifierRunRepository,
    PlayedPermalinks,
)
from .audit_repository import AuditRepository
from .bracket_repository import BracketRepository
from .browser_monitoring_repository import BrowserMonitoringRepository
from .challonge_repository import ChallongeRepository
from .check_in_repository import (
    CheckInEntrantRepository,
    CheckInEventRepository,
    CheckInPassRepository,
    CheckInTierRepository,
    CheckInUserLookupRepository,
)
from .commentator_repository import CommentatorRepository
from .community_event_repository import (
    CommunityEventRepository,
    CommunityEventSeenRepository,
    CommunityEventSubscriptionRepository,
)
from .discord_role_mapping_repository import DiscordRoleMappingRepository
from .discord_role_sync_repository import DiscordRoleSyncRepository
from .discord_scheduled_event_repository import DiscordScheduledEventRepository
from .discord_tournament_grant_repository import DiscordTournamentGrantRepository
from .emergency_contact_repository import EmergencyContactRepository
from .equipment_repository import EquipmentRepository
from .feature_flag_group_repository import FeatureFlagGroupRepository
from .feature_flag_repository import TenantFeatureFlagRepository
from .feedback_repository import FeedbackRepository
from .log_level_override_repository import LogLevelOverrideRepository
from .match_acknowledgment_repository import MatchAcknowledgmentRepository
from .match_comment_repository import MatchCommentRepository
from .match_hard_preset_repository import MatchHardPresetRepository
from .match_repository import MatchRepository
from .match_stream_volunteer_repository import MatchStreamVolunteerRepository
from .match_watcher_repository import MatchWatcherRepository
from .matcherino_login_repository import MatcherinoLoginRepository
from .mcp_auth_repository import McpAuthRepository
from .notification_channel_repository import NotificationChannelRepository
from .notification_preference_repository import NotificationPreferenceRepository
from .notification_setting_repository import NotificationSettingRepository
from .player_availability_repository import PlayerAvailabilityRepository
from .preset_repository import PresetRepository
from .profile_nudge_repository import ProfileNudgeRepository
from .provider_task_repository import ProviderTaskRepository
from .race_room_profile_repository import RaceRoomProfileRepository
from .racetime_bot_repository import RacetimeBotRepository
from .racetime_room_repository import RacetimeRoomRepository
from .randomizer_credential_repository import RandomizerCredentialRepository
from .reschedule_request_repository import RescheduleRequestRepository
from .room_token_repository import RoomTokenRepository
from .sent_message_repository import SentMessageRepository
from .speedgaming_episode_repository import SpeedGamingEpisodeRepository
from .speedgaming_event_link_repository import SpeedGamingEventLinkRepository
from .staff_request_dm_repository import StaffRequestDMRepository
from .stage_repository import StageRepository
from .startgg_repository import StartggRepository
from .station_repository import StationRepository
from .table_preference_repository import TablePreferenceRepository
from .telemetry_repository import TelemetryRepository
from .tenant_join_request_repository import TenantJoinRequestRepository
from .tenant_logo_repository import TenantLogoRepository
from .tenant_membership_repository import TenantMembershipRepository
from .tenant_repository import TenantRepository
from .tournament_notification_repository import TournamentNotificationRepository
from .tournament_payout_repository import TournamentPayoutRepository
from .tournament_repository import TournamentRepository
from .tracker_repository import TrackerRepository
from .triforce_text_repository import TriforceTextRepository
from .user_repository import UserRepository
from .user_role_repository import UserRoleRepository
from .volunteer_assignment_repository import VolunteerAssignmentRepository
from .volunteer_availability_repository import VolunteerAvailabilityRepository
from .volunteer_position_repository import VolunteerPositionRepository
from .volunteer_qualification_repository import VolunteerQualificationRepository
from .volunteer_role_mapping_repository import VolunteerRoleMappingRepository
from .volunteer_shift_repository import VolunteerShiftRepository
from .web_push_repository import WebPushRepository
from .webhook_delivery_repository import WebhookDeliveryRepository
from .webhook_repository import WebhookRepository

__all__ = [
    'AccommodationRepository',
    'ApiTokenRepository',
    'AsyncQualifierLiveRaceRepository',
    'AsyncQualifierPermalinkRepository',
    'AsyncQualifierPoolRepository',
    'AsyncQualifierRepository',
    'AsyncQualifierReviewNoteRepository',
    'AsyncQualifierRunRepository',
    'AuditRepository',
    'BracketRepository',
    'BrowserMonitoringRepository',
    'ChallongeRepository',
    'CheckInEntrantRepository',
    'CheckInEventRepository',
    'CheckInPassRepository',
    'CheckInTierRepository',
    'CheckInUserLookupRepository',
    'CommentatorRepository',
    'CommunityEventRepository',
    'CommunityEventSeenRepository',
    'CommunityEventSubscriptionRepository',
    'DiscordRoleMappingRepository',
    'DiscordRoleSyncRepository',
    'DiscordScheduledEventRepository',
    'DiscordTournamentGrantRepository',
    'EmergencyContactRepository',
    'EquipmentRepository',
    'FeatureFlagGroupRepository',
    'FeedbackRepository',
    'LogLevelOverrideRepository',
    'MatchAcknowledgmentRepository',
    'MatchCommentRepository',
    'MatchHardPresetRepository',
    'MatchRepository',
    'MatchStreamVolunteerRepository',
    'MatchWatcherRepository',
    'MatcherinoLoginRepository',
    'McpAuthRepository',
    'NotificationChannelRepository',
    'NotificationPreferenceRepository',
    'NotificationSettingRepository',
    'PlayedPermalinks',
    'PlayerAvailabilityRepository',
    'PresetRepository',
    'ProfileNudgeRepository',
    'ProviderTaskRepository',
    'RaceRoomProfileRepository',
    'RacetimeBotRepository',
    'RacetimeRoomRepository',
    'RandomizerCredentialRepository',
    'RescheduleRequestRepository',
    'RoomTokenRepository',
    'SentMessageRepository',
    'SpeedGamingEpisodeRepository',
    'SpeedGamingEventLinkRepository',
    'StaffRequestDMRepository',
    'StageRepository',
    'StartggRepository',
    'StationRepository',
    'TablePreferenceRepository',
    'TelemetryRepository',
    'TenantFeatureFlagRepository',
    'TenantJoinRequestRepository',
    'TenantLogoRepository',
    'TenantMembershipRepository',
    'TenantRepository',
    'TournamentNotificationRepository',
    'TournamentPayoutRepository',
    'TournamentRepository',
    'TrackerRepository',
    'TriforceTextRepository',
    'UserRepository',
    'UserRoleRepository',
    'VolunteerAssignmentRepository',
    'VolunteerAvailabilityRepository',
    'VolunteerPositionRepository',
    'VolunteerQualificationRepository',
    'VolunteerRoleMappingRepository',
    'VolunteerShiftRepository',
    'WebPushRepository',
    'WebhookDeliveryRepository',
    'WebhookRepository',
]
