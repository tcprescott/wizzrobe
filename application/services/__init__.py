"""
Service Layer - Business Logic

Services orchestrate business operations, validate data, and coordinate
between repositories. They should NOT know about UI components.
"""

from application.errors import NotFoundError, require_found

from . import (
    active_users_metrics,
    availability_windows,
    bracket_sources,
    challonge_sync_worker,
    check_in_accounts,
    check_in_comps,
    check_in_dm,
    check_in_rules,
    check_in_sales,
    check_in_sync_worker,
    community_event_reminder,
    notification_catalog,
    notification_links,
    oauth_handoff_service,
    opt_in_notifications,
    race_room_worker,
    reporting_shared,
    result_alerts,
    round_windows,
    seed_roll_worker,
    service_health_worker,
    sglive_pool_worker,
    speedgaming_sync_worker,
    staff_request_dms,
    startgg_sync_worker,
    telegram_bot_worker,
    telemetry_retention,
)
from .accommodation_service import AccommodationService
from .analytics_service import AnalyticsService
from .api_token_service import ApiTokenService
from .async_qualifier import (
    AsyncQualifierConfig,
    AsyncQualifierDraw,
    AsyncQualifierLiveRaceService,
    AsyncQualifierService,
    async_qualifier_access,
    async_qualifier_scoring,
    validate_async_qualifier_config,
    validate_counts,
    validate_window,
)
from .audit_service import AuditService
from .auth_service import AuthService, get_user_from_discord_id, get_user_from_session
from .bracket_config import BracketConfig, validate_bracket_config
from .bracket_overview_service import BracketOverviewService
from .bracket_service import BracketService
from .bracket_set_service import BracketSetService
from .browser_monitoring_service import BrowserMonitoringService
from .challonge_service import ChallongeService
from .check_in_desk_service import CheckInDeskService
from .check_in_service import CheckInService
from .commentary_invite_service import CommentaryInviteService
from .community_event_service import CommunityEventService, EventDraft
from .crew_hold_service import CrewHoldService
from .crew_service import CrewService
from .discord import (
    DiscordEmailService,
    DiscordEventReconcilerService,
    DiscordEventSyncService,
    DiscordLinkService,
    DiscordLoginService,
    DiscordMemberImportService,
    DiscordRoleMappingService,
    DiscordRoleSyncService,
    DiscordService,
    discord_event_worker,
    discord_queue,
    discord_role_sync_worker,
)
from .emergency_contact_service import EmergencyContactService
from .equipment_service import EquipmentService
from .event_info_service import EventGuide, EventInfoService
from .exports import DataExportService, ExportFile, data_export_worker
from .feature_flag_service import FeatureFlagService
from .feedback_service import FeedbackService
from .help_service import HelpService
from .identity_link_service import IdentityLinkProvider, IdentityLinkService
from .log_level_service import LoggerLevelRow, LogLevelService
from .match import (
    CancellationMixin,
    HardPresetState,
    MatchCommentService,
    MatchDisplayService,
    MatchHardPresetService,
    MatchParticipants,
    MatchScheduleService,
    MatchService,
    MatchStreamVolunteerService,
    MatchSuggestionService,
    MatchWatcherService,
    PlayerLateService,
    assert_sg_fields_unchanged,
)
from .match_reschedule_service import MatchRescheduleService
from .matcherino_login_service import MatcherinoLoginService, MatcherinoLoginStatus
from .mcp_auth_service import McpAuthService
from .member_details_service import MemberDetails, MemberDetailsService
from .my_timeline_service import MyTimelineService
from .notification_channel_service import NotificationChannelService
from .notification_preference_service import NotificationPreferenceService
from .notification_settings_service import NotificationSettingsService
from .password_auth_service import PasswordAuthService
from .payout_service import PayoutService
from .personal_webhook_service import PersonalWebhookService
from .platform_admin_service import AccountOverview, CommunityOverview, PlatformAdminService
from .player_availability_service import PlayerAvailabilityService
from .preset_service import PresetService
from .profile_nudge_service import ProfileNudgeService
from .provider_task_service import ProviderTaskService
from .race_room_profile_service import RaceRoomProfileService
from .race_room_service import RaceRoomService
from .racetime_bot_service import RacetimeBotService
from .racetime_room_service import RacetimeRoomService
from .racetime_service import RacetimeService
from .randomizer_credential_service import RandomizerCredentialService
from .reports_service import ReportsService
from .room_token_service import RoomTokenService
from .round_schedule_service import RoundScheduleService, RoundWindowError, ScheduleStage
from .runtime_stats_service import RuntimeStatsService
from .seed_roll_service import SeedRollService
from .seedgen_service import SeedGenerationService
from .sent_message_service import SentMessageRef, SentMessageService
from .service_health_service import ProbeResult, ServiceHealthService, ServiceStatus
from .speedgaming_etl_service import SpeedGamingETLService
from .speedgaming_sync_service import SpeedGamingSyncService
from .staff_alert_service import StaffAlertService
from .stage_service import StageService
from .startgg_connection_service import StartggConnectionService
from .startgg_link_service import StartggLinkService
from .startgg_service import StartggService
from .station_service import StationService
from .system_config_service import SystemConfigService
from .table_preference_service import TablePreferenceService
from .telemetry_service import TelemetryService
from .tenant_logo_service import TenantLogoService
from .tenant_membership_service import TenantMembershipService
from .tenant_service import TenantService
from .tenant_setup_service import SetupStep, TenantSetupService
from .tenant_theme_service import TenantThemeService
from .timezone_service import TimezoneService
from .tournament_config import TournamentConfig, validate_tournament_config
from .tournament_notification_service import TournamentNotificationService
from .tournament_service import TournamentService
from .triforce_text_service import TriforceTextService
from .twitch_service import TwitchService
from .user_service import UserService
from .volunteer import (
    DraftPolicy,
    HoursSummary,
    VolunteerArrivalService,
    VolunteerAutoscheduleService,
    VolunteerAvailabilityService,
    VolunteerCallOffService,
    VolunteerExportService,
    VolunteerHoursService,
    VolunteerPoolService,
    VolunteerPositionService,
    VolunteerQualificationService,
    VolunteerRoleMappingService,
    VolunteerScheduleService,
    volunteer_reminder,
)
from .web_push_service import WebPushService
from .webhook_service import WebhookService

__all__ = [
    'AccommodationService',
    'AccountOverview',
    'AnalyticsService',
    'ApiTokenService',
    'AsyncQualifierConfig',
    'AsyncQualifierDraw',
    'AsyncQualifierLiveRaceService',
    'AsyncQualifierService',
    'AuditService',
    'AuthService',
    'BracketConfig',
    'BracketOverviewService',
    'BracketService',
    'BracketSetService',
    'BrowserMonitoringService',
    'CancellationMixin',
    'ChallongeService',
    'CheckInDeskService',
    'CheckInService',
    'CommentaryInviteService',
    'CommunityEventService',
    'CommunityOverview',
    'CrewHoldService',
    'CrewService',
    'DataExportService',
    'DiscordEmailService',
    'DiscordEventReconcilerService',
    'DiscordEventSyncService',
    'DiscordLinkService',
    'DiscordLoginService',
    'DiscordMemberImportService',
    'DiscordRoleMappingService',
    'DiscordRoleSyncService',
    'DiscordService',
    'DraftPolicy',
    'EmergencyContactService',
    'EquipmentService',
    'EventDraft',
    'EventGuide',
    'EventInfoService',
    'ExportFile',
    'FeatureFlagService',
    'FeedbackService',
    'HardPresetState',
    'HelpService',
    'HoursSummary',
    'IdentityLinkProvider',
    'IdentityLinkService',
    'LogLevelService',
    'LoggerLevelRow',
    'MatchCommentService',
    'MatchDisplayService',
    'MatchHardPresetService',
    'MatchParticipants',
    'MatchRescheduleService',
    'MatchScheduleService',
    'MatchService',
    'MatchStreamVolunteerService',
    'MatchSuggestionService',
    'MatchWatcherService',
    'MatcherinoLoginService',
    'MatcherinoLoginStatus',
    'McpAuthService',
    'MemberDetails',
    'MemberDetailsService',
    'MyTimelineService',
    'NotFoundError',
    'NotificationChannelService',
    'NotificationPreferenceService',
    'NotificationSettingsService',
    'PasswordAuthService',
    'PayoutService',
    'PersonalWebhookService',
    'PlatformAdminService',
    'PlayerAvailabilityService',
    'PlayerLateService',
    'PresetService',
    'ProbeResult',
    'ProfileNudgeService',
    'ProviderTaskService',
    'RaceRoomProfileService',
    'RaceRoomService',
    'RacetimeBotService',
    'RacetimeRoomService',
    'RacetimeService',
    'RandomizerCredentialService',
    'ReportsService',
    'RoomTokenService',
    'RoundScheduleService',
    'RoundWindowError',
    'RuntimeStatsService',
    'ScheduleStage',
    'SeedGenerationService',
    'SeedRollService',
    'SentMessageRef',
    'SentMessageService',
    'ServiceHealthService',
    'ServiceStatus',
    'SetupStep',
    'SpeedGamingETLService',
    'SpeedGamingSyncService',
    'StaffAlertService',
    'StageService',
    'StartggConnectionService',
    'StartggLinkService',
    'StartggService',
    'StationService',
    'SystemConfigService',
    'TablePreferenceService',
    'TelemetryService',
    'TenantLogoService',
    'TenantMembershipService',
    'TenantService',
    'TenantSetupService',
    'TenantThemeService',
    'TimezoneService',
    'TournamentConfig',
    'TournamentNotificationService',
    'TournamentService',
    'TriforceTextService',
    'TwitchService',
    'UserService',
    'VolunteerArrivalService',
    'VolunteerAutoscheduleService',
    'VolunteerAvailabilityService',
    'VolunteerCallOffService',
    'VolunteerExportService',
    'VolunteerHoursService',
    'VolunteerPoolService',
    'VolunteerPositionService',
    'VolunteerQualificationService',
    'VolunteerRoleMappingService',
    'VolunteerScheduleService',
    'WebPushService',
    'WebhookService',
    'active_users_metrics',
    'assert_sg_fields_unchanged',
    'async_qualifier_access',
    'async_qualifier_scoring',
    'availability_windows',
    'bracket_sources',
    'challonge_sync_worker',
    'check_in_accounts',
    'check_in_comps',
    'check_in_dm',
    'check_in_rules',
    'check_in_sales',
    'check_in_sync_worker',
    'community_event_reminder',
    'data_export_worker',
    'discord_event_worker',
    'discord_queue',
    'discord_role_sync_worker',
    'get_user_from_discord_id',
    'get_user_from_session',
    'notification_catalog',
    'notification_links',
    'oauth_handoff_service',
    'opt_in_notifications',
    'race_room_worker',
    'reporting_shared',
    'require_found',
    'result_alerts',
    'round_windows',
    'seed_roll_worker',
    'service_health_worker',
    'sglive_pool_worker',
    'speedgaming_sync_worker',
    'staff_request_dms',
    'startgg_sync_worker',
    'telegram_bot_worker',
    'telemetry_retention',
    'validate_async_qualifier_config',
    'validate_bracket_config',
    'validate_counts',
    'validate_tournament_config',
    'validate_window',
    'volunteer_reminder',
]
