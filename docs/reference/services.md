# Service Layer Reference

_Method-level reference for `application/services/` and `application/utils/`. Part of the [documentation index](../README.md)._

## Package layout

`application/services/` is mostly flat — one `<domain>_service.py` per service — with a subpackage per domain that outgrew a single module:

| Subpackage | Holds |
|---|---|
| `match/` | The scheduling and lifecycle core: `MatchService`, `MatchScheduleService`, plus participants, display formatting, cancellation, review flag, stations and the seating draw, watchers, stream volunteering, the hard-preset opt-in, the request/SpeedGaming/bracket-result guards, time suggestion, and the `stage_reminder` loop |
| `discord/` | Outbound sends (`DiscordService`, `discord_queue`) and guild/event sync (`discord_event_*`, role mapping, account link) |
| `volunteer/` | Profiles, positions, qualifications, availability, shift scheduling + autoschedule, and the reminder sweep |
| `async_qualifier/` | Qualifier lifecycle, pool/permalink authoring, seed draw, review queue, run expiry + its worker, live racetime races, scoring, access rules, runner DMs, and config validation |
| `bracket_engines/` | Pure pairing/progression engines behind the `bracket_format` strategy kind (auto-registered on import) |
| `tournament_strategies/` | The strategy registry the engines register into |
| `_bracket/` | Private mixins composed into `BracketService` (see [BracketService](#bracket_servicepy--bracketservice)) |

A few private top-level modules hold the halves of a service that would push it past the length budget: `_tournament_signup.py` (`TournamentSignupMixin`), `_reschedule_notifications.py` (`RescheduleNotificationMixin`), and `_seedgen_dk64r.py` / `_seedgen_avianart.py` / `_seedgen_samus.py` / `_seedgen_varia.py` / `_seedgen_types.py` (the DK64R, AVIANART, samus.link and VARIA backends and the seed value objects).

Each public subpackage re-exports its names and `application/services/__init__.py` re-exports those in turn, so **`from application.services import MatchService` works regardless of which module a service lives in** — that is the import form callers should use. `bracket_engines`/`tournament_strategies` are the deliberate exception: they are imported by path. `application/utils/` follows the same shape: helpers sit at the top level, with `clients/` for the third-party HTTP clients (Challonge, Matcherino, racetime, SpeedGaming, Twitch, OAuth identity) and `mocks/` for the `MOCK_*` flags and their offline stand-ins.

## Pattern & conventions

Services are the business-logic layer of the [three-layer architecture](../refactoring-guide.md): pages and dialogs call services, services call repositories, repositories touch the ORM. Layer boundaries, `ValueError`-for-user-errors, and audit conventions are codebase-wide — see [CLAUDE.md](../../CLAUDE.md), [refactoring-guide.md](../refactoring-guide.md) and [audit-logging.md](../features/audit-logging.md). What follows is specific to this layer.

- **`PermissionError` for authorization failures.** Permission gates run through `AuthService.ensure(allowed, message)`, which raises `PermissionError`. UI handlers that call gate-protected services catch both it and `ValueError` (see `pages/admin_tabs/admin_schedule.py`).
- **The `actor` parameter.** Mutating methods take the acting `User` as a trailing `actor` parameter (or `user` when the action is inherently self-service, e.g. `acknowledge_match`, `signup_crew`, `watch`). The same object feeds both the permission gate and the audit entry; callers resolve it once via `get_user_from_session(app.storage.user)`.
- **Stateless instances.** Services hold only repository/service references; instantiate per request (`MatchService()`) or call static methods on the class (`AuthService`, `SystemConfigService`). The intentional pieces of module/class state are `MatchScheduleService._seed_locks` (per-match seed-generation locks) and `match_hard_preset_service._agreement_locks`, the `discord_queue` module's queue/worker, each worker module's background loop (`volunteer_reminder`, `stage_reminder`, the `*_worker` modules), the bot singleton and handler registries in `discord_service`, the in-process single-worker caches and stores (`TenantService`'s resolution caches, the `oauth_handoff_service` and `McpAuthService` pending stores, `ServiceHealthService`'s probe cache), and `FeatureFlagService`'s per-request flag cache (a contextvar).
- **`(ok, message)` tuple returns** are the exception, not the rule, and exist only where failure is routine and must not raise:
  - `DiscordService.send_dm*`, `add_role_to_user`, `remove_role_from_user` → `(bool, str)`
  - `DiscordService.list_guilds`, `list_guild_roles` → `(bool, data_or_error)`
  - `MatchScheduleService.generate_seed` → `(bool, message, seed_url | None)`

  Everything else returns models/values and raises on failure.
- **Fire-and-forget Discord work** is wrapped in a coroutine and handed to `discord_queue.enqueue(...)` rather than awaited inline, so DB transactions never block on Discord rate limits.
- **Call sites are deliberately not listed.** Who calls a service changes on every UI change; `grep` the service name for an always-current answer. Sections list `Collaborators:` (what a service depends on) instead, which is stable.

## Service catalog

| Service | Module | Responsibility | Feature doc |
|---|---|---|---|
| `AccommodationService` | [accommodation_service.py](../../application/services/accommodation_service.py) | ADA accommodation requests: member self-service, STAFF queue | [ada-accommodations.md](../features/ada-accommodations.md) |
| `EmergencyContactService` | [emergency_contact_service.py](../../application/services/emergency_contact_service.py) | Members' emergency contacts: self-service, STAFF open-one-at-a-time with an audit row per look | — |
| `CommunityEventService` | [community_event_service.py](../../application/services/community_event_service.py) | Member-submitted community events: submit, staff review, edits, cancellation, subscriptions | [community-events.md](../features/community-events.md) |
| `AnalyticsService` | [analytics_service.py](../../application/services/analytics_service.py) | Longitudinal trends: crew participation, volunteer hours, tournament health | — |
| `ApiTokenService` | [api_token_service.py](../../application/services/api_token_service.py) | Bearer credential issue/revoke/authenticate (PATs and OAuth tokens) | [rest-api.md](rest-api.md) |
| `McpAuthService` | [mcp_auth_service.py](../../application/services/mcp_auth_service.py) | The MCP OAuth 2.1 authorization server: dynamic client registration, authorization codes, access/refresh token issue and rotation | [features/mcp-server.md](../features/mcp-server.md) |
| `AsyncQualifierService` | [async_qualifier_service.py](../../application/services/async_qualifier/async_qualifier_service.py) | Self-paced permalink-pool qualifiers: management, draw, run lifecycle, review, scoring | [online-tournaments.md](../features/online-tournaments.md) |
| `AsyncQualifierLiveRaceService` | [async_qualifier_live_race_service.py](../../application/services/async_qualifier/async_qualifier_live_race_service.py) | Synchronous racetime qualifier races feeding `AsyncQualifierRun`s | [online-tournaments.md](../features/online-tournaments.md) |
| `AuditService` / `AuditActions` | [audit_service.py](../../application/services/audit_service.py) | Write and query the audit trail | [audit-logging.md](../features/audit-logging.md) |
| `AuthService` / `get_user_from_session` / `get_user_from_discord_id` | [auth_service.py](../../application/services/auth_service.py) | Role checks, permission policy, resolving the signed-in user from the session | [authentication.md](authentication.md), [role-based-auth.md](authentication.md#roles) |
| `PlatformAdminService` / `CommunityOverview` / `AccountOverview` | [platform_admin_service.py](../../application/services/platform_admin_service.py) | Super-admin composite reads for the REST `/platform` routes and MCP platform tools: every community (inactive included) with tier and setup readiness, every account with its communities; resolves ids/slugs for the owning services' writes | [mcp-server.md](../features/mcp-server.md#platform-super-admin) |
| `PasswordAuthService` | [password_auth_service.py](../../application/services/password_auth_service.py) | Password sign-in check plus the super-admin account tools on Platform → Users: set/remove password, create a password-only account, (de)activate | [authentication.md](authentication.md#password-sign-in) |
| `BracketService` | [bracket_service.py](../../application/services/bracket_service.py) | Native bracket lifecycle: author stages, roster/enroll/seed, start (generate + persist), report results + advance, complete, multi-stage advancement, scheduling seam, best-of-N series (`SeriesMixin`: game numbering, clinch, the racetime auto-open hold) | [brackets.md](../features/brackets.md) |
| `ChallongeService` | [challonge_service.py](../../application/services/challonge_service.py) | Challonge OAuth, bracket sync, scheduling, result push | — |
| `StartggService` | [startgg_service.py](../../application/services/startgg_service.py) | start.gg event mirror, entrant mapping, booking, opt-in result writeback | — |
| `StartggConnectionService` | [startgg_connection_service.py](../../application/services/startgg_connection_service.py) | A community's start.gg sign-in: connect, token refresh, platform-token fallback, per-tournament reporting access | — |
| `StartggLinkService` | [startgg_link_service.py](../../application/services/startgg_link_service.py) | Player start.gg account linking (`IdentityLinkService`) | — |
| `bracket_sources` | [bracket_sources.py](../../application/services/bracket_sources.py) | Shared contract for linked bracket sites: result hook, My Schedule bookables, sync | — |
| `startgg_sync_worker` | [startgg_sync_worker.py](../../application/services/startgg_sync_worker.py) | Re-syncs linked start.gg events every 2 minutes | — |
| `CheckInService` | [check_in_service.py](../../application/services/check_in_service.py) | Matcherino-backed event check-in: roster sync, auto-linking, the desk's check-in/link actions, walk-ups | [event-check-in.md](../features/event-check-in.md), `EVENT_CHECK_IN` |
| `CheckInDeskService` | [check_in_desk_service.py](../../application/services/check_in_desk_service.py) | The desk's gated reads (events you can work, an event's roster) for the REST and MCP surfaces | [event-check-in.md](../features/event-check-in.md), `EVENT_CHECK_IN` |
| `check_in_dm` (module) | [check_in_dm.py](../../application/services/check_in_dm.py) | The "you're checked in" test DM the desk sends, and its result on the roster row | [event-check-in.md](../features/event-check-in.md), `EVENT_CHECK_IN` |
| `MatcherinoLoginService` | [matcherino_login_service.py](../../application/services/matcherino_login_service.py) | The per-community Matcherino login check-in syncs with: status, verified save, remove, sync-time resolve | [event-check-in.md](../features/event-check-in.md#the-stored-login), `EVENT_CHECK_IN` |
| `CrewService` | [crew_service.py](../../application/services/crew_service.py) | Crew signup/undo, approval, and acknowledgment | [match-participation.md](../features/match-participation.md) |
| `MyTimelineService` | [my_timeline_service.py](../../application/services/my_timeline_service.py) | My Schedule's Timeline view: one person's played matches, crew slots, volunteer shifts and community events (via `CommunityEventService.list_attending`) merged into time ranges, their can't-play windows cut per event day, plus the pure lane and 06:00 event-day rules | [frontend.md](frontend.md) |
| `DiscordEventReconcilerService` | [discord_event_reconciler_service.py](../../application/services/discord/discord_event_reconciler_service.py) | Idempotent mirror of the schedule into a guild's Discord Scheduled Events | [discord.md](../features/discord.md) |
| `DiscordEventSyncService` | [discord_event_sync_service.py](../../application/services/discord/discord_event_sync_service.py) | Admin surface over the reconciler: per-tournament opt-in + "reconcile now" | [discord.md](../features/discord.md) |
| `DiscordLinkService` | [discord_link_service.py](../../application/services/discord/discord_link_service.py) | Verified tenant↔Discord-server link (bot-authorization OAuth + authority re-check) | [multitenancy.md](../features/multitenancy.md) |
| `DiscordLoginService` | [discord_login_service.py](../../application/services/discord/discord_login_service.py) | One-time login links the bot hands out (`/login`, the login panel) and the panel's STAFF gate | [discord.md](../features/discord.md#log-in-from-discord) |
| `DiscordMemberImportService` | [discord_member_import_service.py](../../application/services/discord/discord_member_import_service.py) | Admin → Users › Import from Discord: an account and a membership for everyone in the linked server | [discord.md](../features/discord.md) |
| `DiscordRoleMappingService` | [discord_role_mapping_service.py](../../application/services/discord/discord_role_mapping_service.py) | Discord-role→app-role mapping CRUD and login-time role sync | [discord.md](../features/discord.md) |
| `DiscordRoleSyncService` | [discord_role_sync_service.py](../../application/services/discord/discord_role_sync_service.py) | The Discord roles Wizzrobe hands out (players, live match, per tournament, badges): rules, bot-permission checks, and the sync; `discord_role_sync_worker` runs it | [discord.md](../features/discord.md#app--guild-role-sync-discord-roles-from-wizzrobe) |
| `discord_member_events` (module) | [discord_member_events.py](../../application/services/discord/discord_member_events.py) | The bot's `GUILD_MEMBER_*` listeners: app-role re-sync and the cached avatar hash | [discord-integration.md](discord-integration.md) |
| `discord_queue` (module) | [discord_queue.py](../../application/services/discord/discord_queue.py) | Serialized background Discord sends | [discord-integration.md](discord-integration.md) |
| `DiscordService` / `MockDiscordService` | [discord_service.py](../../application/services/discord/discord_service.py) | Bot DMs, button views, guild roles | [discord-integration.md](discord-integration.md) |
| `EquipmentService` | [equipment_service.py](../../application/services/equipment_service.py) | Lending-asset CRUD and checkout/check-in workflow | — |
| `FeatureFlagService` | [feature_flag_service.py](../../application/services/feature_flag_service.py) | Per-tenant feature gating: effective-state resolution (override → group → default), group (tier) CRUD + assignment, availability/enable writes | [feature-flags.md](../features/feature-flags.md) |
| `EventInfoService` | [event_info_service.py](../../application/services/event_info_service.py) | Feature- and role-aware reads of the community's event handbook articles | [event-information.md](../features/event-information.md) |
| `FeedbackService` | [feedback_service.py](../../application/services/feedback_service.py) | In-app feedback submission and review | — |
| `HelpService` | [help_service.py](../../application/services/help_service.py) | Feature-aware reads and search of the shipped help articles | [help.md](../features/help.md) |
| `MatchDisplayService` | [match_display_service.py](../../application/services/match/match_display_service.py) | Read + format matches into table-row dicts (filters, display shape) | [frontend.md](frontend.md) |
| `MatchScheduleService` | [match_schedule_service.py](../../application/services/match/match_schedule_service.py) | Match lifecycle transitions, seed rolls, DM fan-out | [match-participation.md](../features/match-participation.md) |
| `MatchService` | [match_service.py](../../application/services/match/match_service.py) | Match CRUD, results, station/stage, plus the mixins it composes: reads (`MatchReadsMixin`), acknowledgments (`MatchAcknowledgmentMixin`), cancellation (`CancellationMixin`), the dispute flag (`MatchReviewMixin`) | [match-participation.md](../features/match-participation.md) |
| `MatchSuggestionService` | [match_suggestion_service.py](../../application/services/match/match_suggestion_service.py) | Suggest match start times that minimise venue occupancy | — |
| `MatchWatcherService` | [match_watcher_service.py](../../application/services/match/match_watcher_service.py) | Watch/unwatch matches for DM updates | [match-participation.md](../features/match-participation.md) |
| `MatchRescheduleService` | [match_reschedule_service.py](../../application/services/match_reschedule_service.py) | Players asking staff to move or call off their match; approving performs the change | [match-participation.md](../features/match-participation.md#reschedule-requests) |
| `MatchHardPresetService` | [match_hard_preset_service.py](../../application/services/match/match_hard_preset_service.py) | Players privately opting into a match's harder preset; resolves which preset a roll uses | [match-participation.md](../features/match-participation.md#harder-settings-opt-in) |
| `MatchCommentService` | [match_comment_service.py](../../application/services/match/match_comment_service.py) | Signed comments on a match: player → staff, private staff notes, staff messages to players | [match-participation.md](../features/match-participation.md#match-comments) |
| `MatchStreamVolunteerService` | [match_stream_volunteer_service.py](../../application/services/match/match_stream_volunteer_service.py) | Players offering their own match for stream (advisory) | [match-participation.md](../features/match-participation.md#stream-volunteering) |
| `PlayerAvailabilityService` | [player_availability_service.py](../../application/services/player_availability_service.py) | Player-declared availability windows | — |
| `availability_windows` (module) | [availability_windows.py](../../application/services/availability_windows.py) | Pure window algorithms (`covers`, `effective_segments`, `group_by_user`) shared by the player + volunteer availability services; the `default` argument is what makes one opt-out and the other opt-in | — |
| `PresetService` | [preset_service.py](../../application/services/preset_service.py) | Tenant-authored seed-rolling presets (CRUD + built-in and upstream-API import) | [seed-generation.md](seed-generation.md#presets-db-backed) |
| `ProviderTaskService` | [provider_task_service.py](../../application/services/provider_task_service.py) | Lifecycle of a persisted task-queue provider call (`ProviderTask`) | [seed-generation.md](seed-generation.md#asynchronous-rolls) |
| `RandomizerCredentialService` | [randomizer_credential_service.py](../../application/services/randomizer_credential_service.py) | Per-tenant randomizer API credentials | [seed-generation.md](seed-generation.md#per-tenant-credentials) |
| `ReportsService` | [reports_service.py](../../application/services/reports_service.py) | Capacity, operations, crew, and stage reports | [admin-reports.md](../features/admin-reports.md) |
| `reporting_shared` (module) | [reporting_shared.py](../../application/services/reporting_shared.py) | Shared reporting constants (`DEFAULT_MATCH_DURATION_MIN`, `ON_TIME_THRESHOLD_MIN`) + `to_display`/`window_hours`/`crew_requirement`/`is_crew_covered` helpers used by both Reports and Insights (so on-time % and crew coverage can't drift) | — |
| `RoomTokenService` | [room_token_service.py](../../application/services/room_token_service.py) | Unlisted kiosk tokens for the tournament room's read-only seeds board | [frontend.md](frontend.md#the-tournament-room-seeds-board-pagesroom_seedspy) |
| `SeedRollService` | [seed_roll_service.py](../../application/services/seed_roll_service.py) | Submit/poll/complete a task-queue seed roll (driven by `seed_roll_worker`) | [seed-generation.md](seed-generation.md#asynchronous-rolls) |
| `SeedGenerationService` | [seedgen_service.py](../../application/services/seedgen_service.py) | Randomizer seed generation | [seed-generation.md](seed-generation.md) |
| `ServiceHealthService` | [service_health_service.py](../../application/services/service_health_service.py) | Platform external-service health probes (computed + cached, no model) | — |
| `StationService` | [station_service.py](../../application/services/station_service.py) | Venue station pool CRUD | [match-participation.md](../features/match-participation.md#the-station-pool) |
| `StageService` | [stage_service.py](../../application/services/stage_service.py) | Stage (stage) CRUD | — |
| `SystemConfigService` | [system_config_service.py](../../application/services/system_config_service.py) | Typed access to `SystemConfiguration` keys | [admin-reports.md](../features/admin-reports.md) |
| `ProfileNudgeService` | [profile_nudge_service.py](../../application/services/profile_nudge_service.py) | A community's recommended profile items and which ones a member still owes | [profile-reminders.md](../features/profile-reminders.md) |
| `TablePreferenceService` | [table_preference_service.py](../../application/services/table_preference_service.py) | Per-user table layouts (columns, order, widths, page size, density, wrap) | [frontend.md](frontend.md#data-tables) |
| `PayoutService` | [payout_service.py](../../application/services/payout_service.py) | Prize pool, placement splits, and the Matcherino export | [payouts.md](../features/payouts.md), `PAYOUTS` |
| `TelemetryService` / `TelemetryCategory` / `TelemetryEventType` | [telemetry_service.py](../../application/services/telemetry_service.py) | Engagement telemetry capture + Staff-gated engagement report | [telemetry.md](../features/telemetry.md) |
| `TenantService` | [tenant_service.py](../../application/services/tenant_service.py) | Tenant resolution (cached slug/guild/domain lookup), tenant CRUD, membership, super-admin grant | [multitenancy.md](../features/multitenancy.md) |
| `TenantMembershipService` | [tenant_membership_service.py](../../application/services/tenant_membership_service.py) | Community membership: list, add, remove, and the role-implies-membership hook | [multitenancy.md](../features/multitenancy.md#identity-roles-and-membership) |
| `TenantSetupService` | [tenant_setup_service.py](../../application/services/tenant_setup_service.py) | The derived new-community setup checklist (`SetupStep`); nothing stored | [multitenancy.md](../features/multitenancy.md#provisioning-a-community) |
| `TenantLogoService` | [tenant_logo_service.py](../../application/services/tenant_logo_service.py) | Per-tenant logo upload: re-encode a bitmap to WebP or sanitise an SVG, store in `TenantLogo`, serve by hash, feed the QR codes | [§ below](#tenant_logo_servicepy--tenantlogoservice) |
| `TenantThemeService` | [tenant_theme_service.py](../../application/services/tenant_theme_service.py) | Per-tenant brand palette (STAFF-editable colours on `Tenant.config['theme']`) | [frontend.md](../reference/frontend.md#per-tenant-theme-colours) |
| `TimezoneService` | [timezone_service.py](../../application/services/timezone_service.py) | Per-tenant display-timezone policy (pin vs. follow-the-viewer) and per-viewer resolution | [timezone-handling.md](../timezone-handling.md) |
| `StaffAlertService` | [staff_alert_service.py](../../application/services/staff_alert_service.py) | Match-alert recipients: the admin on duty (a published shift on a `staff_alerts` position) plus the tournament's opted-in admins, falling back to all STAFF; the same on-duty set for volunteer late/give-back alerts; a tournament admin's per-tournament opt-out | [match-participation.md](../features/match-participation.md) |
| `TournamentNotificationService` | [tournament_notification_service.py](../../application/services/tournament_notification_service.py) | Per-tournament notification preferences | [match-participation.md](../features/match-participation.md) |
| `TournamentService` | [tournament_service.py](../../application/services/tournament_service.py) | Tournament CRUD, TA/CC membership | — |
| `TriforceTextService` | [triforce_text_service.py](../../application/services/triforce_text_service.py) | Triforce text submission and moderation | [triforce-texts.md](../features/triforce-texts.md) |
| `TwitchService` | [twitch_service.py](../../application/services/twitch_service.py) | Twitch account-linking OAuth (verified identity capture) | — |
| `RacetimeService` | [racetime_service.py](../../application/services/racetime_service.py) | racetime.gg account-linking OAuth (verified identity capture) | — |
| `IdentityLinkService` / `IdentityLinkProvider` | [identity_link_service.py](../../application/services/identity_link_service.py) | Provider-parameterized account-linking engine (redirect/authorize/exchange/record/unlink + duplicate-account guard) that `TwitchService` and `RacetimeService` both delegate to | — |
| `RacetimeBotService` | [racetime_bot_service.py](../../application/services/racetime_bot_service.py) | Platform CRUD + tenant authorization grants for shared racetime bots | — |
| `RaceRoomProfileService` | [race_room_profile_service.py](../../application/services/race_room_profile_service.py) | SYNC_ADMIN CRUD for reusable racetime room settings | — |
| `RacetimeRoomService` | [racetime_room_service.py](../../application/services/racetime_room_service.py) | Race-room record lookup (unscoped by-slug routing) + status writes | — |
| `RaceRoomService` | [race_room_service.py](../../application/services/race_room_service.py) | Racetime room lifecycle mapped onto a `Match` (create/open/seed/finish/cancel + result capture) | — |
| `SpeedGamingETLService` | [speedgaming_etl_service.py](../../application/services/speedgaming_etl_service.py) | One-way SpeedGaming→Wizzrobe schedule ETL (extract/transform/load into `Match`) | — |
| `SpeedGamingSyncService` | [speedgaming_sync_service.py](../../application/services/speedgaming_sync_service.py) | Tenant-facing SpeedGaming event-link CRUD + on-demand "sync now" | — |
| `UserService` | [user_service.py](../../application/services/user_service.py) | User CRUD, profiles, roles, enrollments | [role-based-auth.md](authentication.md#roles) |
| `WebhookService` | [webhook_service.py](../../application/services/webhook_service.py) | Staff-managed outbound webhooks: CRUD + signed delivery | [webhooks.md](../features/webhooks.md) |
| `WebPushService` | [web_push_service.py](../../application/services/web_push_service.py) | Per-device browser push subscriptions + encrypted delivery | [web-push.md](../features/web-push.md) |
| `NotificationChannelService` | [notification_channel_service.py](../../application/services/notification_channel_service.py) | Email and Telegram as extra notification channels: confirm/link, the DM copy, user-chosen test notifications | `NOTIFICATION_CHANNELS` · [notification-channels.md](../features/notification-channels.md) |
| `NotificationPreferenceService` | [notification_preference_service.py](../../application/services/notification_preference_service.py) | A person's own preset and per-message choices (Profile → Notifications), capped by the community setting: `mode_for` for `send_dm`, own get/set/reset | [notification-settings.md](../features/notification-settings.md#personal-choices) |
| `NotificationSettingsService` | [notification_settings_service.py](../../application/services/notification_settings_service.py) | A community's overrides of the notification catalogue (Admin → Notifications): `mode_for` for `send_dm`, staff list/set/reset | [notification-settings.md](../features/notification-settings.md) |
| `notification_catalog` (module) | [notification_catalog.py](../../application/services/notification_catalog.py) | Every DM Wizzrobe sends, as a `Notice` per message with its default mode per recipient kind; the source of truth for the settings grid | [notification-settings.md](../features/notification-settings.md) |
| `DiscordEmailService` | [discord_email_service.py](../../application/services/discord/discord_email_service.py) | "Use my Discord email": one extra OAuth with `identify email`, accepts only a verified address | [notification-channels.md](../features/notification-channels.md) |
| `VolunteerAutoscheduleService` | [volunteer_autoschedule_service.py](../../application/services/volunteer/volunteer_autoschedule_service.py) | Greedy draft generator for the volunteer schedule | — |
| `VolunteerAvailabilityService` | [volunteer_availability_service.py](../../application/services/volunteer/volunteer_availability_service.py) | Volunteer-declared availability windows | — |
| `VolunteerExportService` | [volunteer_export_service.py](../../application/services/volunteer/volunteer_export_service.py) | Flattens roster, preferences, positions, shifts, assignments and a slot grid into spreadsheet-ready tables | — |
| `VolunteerHoursService` | [volunteer_hours.py](../../application/services/volunteer/volunteer_hours.py) | Hours served against the per-tenant comp tiers | `VOLUNTEERS` |
| `VolunteerPositionService` | [volunteer_position_service.py](../../application/services/volunteer/volunteer_position_service.py) | Coordinator-defined volunteer position CRUD | — |
| `VolunteerRoleMappingService` | [volunteer_role_mapping_service.py](../../application/services/volunteer/volunteer_role_mapping_service.py) | Volunteer-position→app-role mappings and the assignment-driven role sync | — |
| `VolunteerQualificationService` | [volunteer_qualification_service.py](../../application/services/volunteer/volunteer_qualification_service.py) | Read and set which positions a volunteer is qualified to fill | — |
| `VolunteerPoolService` | [volunteer_pool_service.py](../../application/services/volunteer/volunteer_pool_service.py) | The assignable pool: VOLUNTEER or STAFF role holders | — |
| `stage_reminder` (module) | [stage_reminder.py](../../application/services/match/stage_reminder.py) | Background loop DMing a stage match's players and crew shortly before it starts | — |
| `volunteer_reminder` (module) | [volunteer_reminder.py](../../application/services/volunteer/volunteer_reminder.py) | Background loop sending shift-reminder DMs | — |
| `VolunteerScheduleService` | [volunteer_schedule_service.py](../../application/services/volunteer/volunteer_schedule_service.py) | Volunteer shifts, assignments, acknowledgment, coverage | — |
| `VolunteerShiftMoveService` | [volunteer_shift_move_service.py](../../application/services/volunteer/volunteer_shift_move_service.py) | Moving a volunteer to another shift and swapping one person for another (Admin → Shift Board), checking first so a refusal changes nothing | `VOLUNTEERS` |

Every service **class** is re-exported from [`application/services/__init__.py`](../../application/services/__init__.py), along with `get_user_from_discord_id` and `NotFoundError` / `require_found` (from [`application/errors.py`](../../application/errors.py)). The helper and worker modules — `active_users_metrics`, `availability_windows`, `async_qualifier_access`, `async_qualifier_scoring`, `check_in_dm`, `check_in_sync_worker`, `discord_queue`, `discord_event_worker`, `notification_catalog`, `notification_links`, `oauth_handoff_service`, `race_room_worker`, `reporting_shared`, `seed_roll_worker`, `service_health_worker`, `sglive_pool_worker`, `speedgaming_sync_worker`, `telemetry_retention`, `volunteer_reminder` — are exported as modules (`from application.services import discord_queue`); `stage_reminder` is exported the same way from its own package (`from application.services.match import stage_reminder`), as are the remaining `async_qualifier_*` modules from `application.services.async_qualifier`. A few value types and validators ride along: `BracketConfig`/`validate_bracket_config`, `TournamentConfig`/`validate_tournament_config`, `AsyncQualifierConfig`/`validate_async_qualifier_config`/`validate_counts`/`validate_window`, `AsyncQualifierDraw`, `DraftPolicy`, `HoursSummary`, `HardPresetState`, `MatchParticipants`, `CancellationMixin`, `assert_sg_fields_unchanged`, `IdentityLinkProvider`, `ProbeResult`/`ServiceStatus`, `SetupStep`. The exceptions to "every class" are `MockDiscordService` (reached through the `DiscordService` rebind under `MOCK_DISCORD`) and the internal mixins. Non-class members (`AuditActions`, `MatchStatus`, `TelemetryCategory`) import from their own module (`MatchStatus` also from `application.services.match`).

### api_token_service.py — ApiTokenService

Issues, lists, revokes, and authenticates personal API access tokens. Only the SHA-256 hash of a token is persisted; the plaintext (prefixed `wizzrobe_pat_`) is returned exactly once, at creation. A token authenticates as its owning `User` and inherits that user's permissions; a `read_only` token is restricted to read (GET) endpoints by the API auth layer. Used by the REST API; see [rest-api.md](rest-api.md).

| Method | Returns | Description |
|---|---|---|
| `create_token(actor, name, read_only=False, expires_at=None)` | `(ApiToken, str)` | Create a token for `actor`; returns the record and the **raw token** (only chance to capture it). Non-empty name required; `expires_at` must be in the future (else `ValueError`). Audits `apitoken.created`. |
| `list_tokens(actor)` | `list[ApiToken]` | The actor's own active (non-revoked) tokens. |
| `revoke_token(actor, token_id)` | `None` | Revoke; `ValueError` for unknown/already-revoked tokens, `PermissionError` when the token belongs to another user. Audits `apitoken.revoked`. |
| `authenticate(raw_token)` | `(User, ApiToken) \| None` | Resolve a raw bearer token to its owner. Returns `None` for unknown/revoked/expired tokens (logged as warnings); on success updates `last_used_at`. |
| `resolve_actor(raw_token)` | `(User, ApiToken) \| None` | `authenticate` plus the deactivated-owner check. **The shared front half of bearer authentication for both entry surfaces** — the REST API and the MCP server call this so their notion of "who is this token" cannot drift. Raises `PermissionError` when the token is valid but its owner is deactivated (401 vs 403 at the caller). Binds no tenant: which community a request acts in is the caller's decision. |

Collaborators: `ApiTokenRepository`, `AuditService`.

### room_token_service.py — RoomTokenService

Issues, lists, revokes and resolves the unlisted keys that open the tournament
room's seeds board (`/t/<slug>/room/<token>/seeds`). Only the SHA-256 hash is
stored; the URL (token prefixed `wizzrobe_room_`) is shown once, at issue. A room
token carries **no user identity** and authorizes exactly one read-only page —
deliberately not an `ApiToken`, which acts with its owner's full permissions.

| Method | Returns | Description |
|---|---|---|
| `issue(actor, label)` | `(RoomToken, str)` | Create a token for the actor's community; returns the record and the **raw token** (only chance to capture it). STAFF only; non-empty label required. Audits `roomtoken.created`. |
| `list_tokens(actor)` | `list[RoomToken]` | Every token this community has issued, live ones first then newest-first. Revoked rows stay listed so staff can see which machine's URL stopped working. STAFF only. |
| `revoke(actor, token_id)` | `RoomToken` | Revoke; `NotFoundError` for unknown or already-revoked tokens. STAFF only. Audits `roomtoken.revoked`. |
| `resolve(raw_token)` | `RoomToken \| None` | The live token this string opens. Unknown, revoked, malformed and wrong-community all return `None` — the page renders the same not-found page an unknown route does (HTTP 404, same copy) for each, so guessing reveals nothing. Stamps `last_used_at` on success. |

Both audit actions are deliberately event-less (`test_event_audit_parity`): a
credential a venue machine holds is a security fact, not a domain one.

Module helpers: `hash_token(raw)` (the stored SHA-256 form; public so the dev seed can plant a token) and `room_seeds_path(raw)` (the tenant-relative `/room/<token>/seeds` path).

Collaborators: `RoomTokenRepository`, `AuditService`, `AuthService`.

### mcp_auth_service.py — McpAuthService

The OAuth 2.1 authorization server behind the MCP endpoint: dynamic client registration, PKCE authorization codes, and access/refresh token issue and rotation. Tokens are `ApiToken` rows, so `ApiTokenService.resolve_actor` remains the one bearer-authentication front half for both entry surfaces. Only hashes are stored — raw codes and tokens are returned once. Full flow: [mcp-server.md](../features/mcp-server.md).

| Method | Returns | Description |
|---|---|---|
| `register_client(...)` / `get_client(client_id)` | `McpOAuthClient` / `\| None` | Dynamic client registration (RFC 7591, open by design — registering grants nothing, since every code still needs interactive approval) and lookup. At least one redirect URI is required (`ValueError`). |
| `begin_authorization(pending)` / `get_pending(txn_id)` / `discard_pending(txn_id)` | `str` / `PendingAuthorization` / `None` | Park an in-flight authorization request in the bounded in-process store while the user consents; `get_pending` raises on an unknown or expired transaction. Expired entries are swept on access. |
| `approve(txn_id, user, *, allow_write=False)` | `(code, redirect_uri, state)` | The user consented: mint a single-use authorization code bound to the client and the PKCE challenge. `allow_write` is the consent screen's checkbox and the only thing that decides the granted scope — what the client requested is ignored. It is a keyword with a safe default so a caller that forgets it grants read-only. |
| `load_code(client_id, raw_code)` | `McpAuthorizationCode \| None` | Resolve a raw code for its owning client (`None` when unknown, consumed, or expired). |
| `exchange_code(...)` | `(access, refresh, expires_in)` | Verify the PKCE verifier and consume the code for a token pair. |
| `load_refresh_token(client_id, raw_refresh)` / `exchange_refresh(token)` | `ApiToken \| None` / `(access, refresh, expires_in)` | Refresh-token rotation — the old token is replaced, not reused. |
| `load_access_token(raw_token)` / `revoke(token)` | `ApiToken \| None` / `None` | Resolve a bearer token for the MCP request path; revoke a token (RFC 7009). |

`PendingAuthorization` (same module) is the parked-request dataclass. Module helpers `granted_scope(*, allow_write)` (the scope a consent decision grants — read always, write only when ticked) and `scope_allows_write(scope)` (whether a stored scope string carries write). Collaborators: `McpAuthRepository`, `ApiTokenRepository`, `AuditService`.

### audit_service.py — AuditService

Writes and queries `AuditLog` rows. Every mutating service action records who did what, with JSON-encoded details. Canonical doc: [audit-logging.md](../features/audit-logging.md).

**`AuditActions`** is a plain constants class holding every namespaced `verb.object` action string in the codebase — one place to grep when reviewing audit coverage. Add a constant here when introducing a new action; never pass a literal string. Namespaces mirror the domains one-for-one (`match.*`, `crew.*`, `bracket.*`, `volunteer.*`, …), so the class itself is the list.

| Method | Returns | Description |
|---|---|---|
| `write_and_publish(actor, action, details, event_type, *, event_extra=None, event_details=None)` | `AuditLog` | Write the audit row **and** publish the mirror event. The default for any audited change; a hand-rolled `write_log` + `event_bus.publish` pair is blocked by `check_dry_regressions.py`. `event_extra` adds routing keys to the audit `details`; `event_details` replaces the payload wholesale (passing both raises). |
| `write_log(actor, action, details=None)` | `AuditLog` | Audit-only write (no event) — for changes with no paired event. JSON-encodes `details` (non-serializable values fall back to `str`). Raises `ValueError` if `actor` is `None`. |
| `list_logs(*, start=None, end=None, user_id=None, action_contains=None, limit=100, offset=0)` | `list[AuditLog]` | Filtered, paginated log listing (delegates to `AuditRepository`). |
| `count_logs(*, start=None, end=None, user_id=None, action_contains=None)` | `int` | Count matching the same filters as `list_logs`. |

Collaborators: `AuditRepository`, the [event bus](../features/event-system.md).

### auth_service.py — AuthService and get_user_from_discord_id

Stateless authorization policy: every check is a `@staticmethod async def` taking `User | None` and returning a bool (no exceptions for "not allowed" — that is `ensure`'s job). Deep dive: [authentication.md](authentication.md); role semantics: [role-based-auth.md](authentication.md#roles).

| Method | Returns | Description |
|---|---|---|
| `is_super_admin(user)` | `bool` | Holds the one global role (`UserRole` with `tenant=NULL`). |
| `get_roles(user)` | `set[Role]` | The user's roles **in the current tenant**; excludes `SUPER_ADMIN`; empty for `None` or when no tenant is in scope. |
| `has_role(user, role)` | `bool` | Holds `role` **in the current tenant**; False with no tenant in scope. |
| `is_staff(user)` | `bool` | Holds `Role.STAFF` in the current tenant, **or** is the global `SUPER_ADMIN` (staff-equivalence — the one hook that gives a platform admin full authority in every tenant). |
| `is_proctor` / `is_stream_manager` / `is_volunteer_coordinator` / `is_equipment_manager` / `is_volunteer` / `is_triforce_submitter` | `bool` | Holds the matching per-tenant `Role`. |
| `is_tournament_admin(user, tournament_id)` | `bool` | Listed in `Tournament.admins`, filtered to this tenant. |
| `is_crew_coordinator_of(user, tournament_id)` | `bool` | Listed in `Tournament.crew_coordinators`, filtered to this tenant. |
| `tournament_scope(user)` | `(set[int], set[int])` | `(admin_ids, coordinated_ids)` — the tournaments the user is TA/CC of in this tenant, so a non-staff operator's surfaces can be scoped to them. Staff and `STREAM_MANAGER` authority is community-wide and not expressed here. |
| `can_view_schedule_board(user)` | `bool` | Whether the admin Schedule tab is reachable: Staff (incl. super-admin), `STREAM_MANAGER`, or TA/CC of any tournament in this tenant. Reports resolve drill-out links against it. |
| `can_view_volunteer(user)` | `bool` | Whether the Volunteer hub is reachable here: `FeatureFlag.VOLUNTEERS` live **and** (super-admin or one of `VOLUNTEER`/`PROCTOR`/`STAFF`). The nav's single source of truth, mirroring the `@protected_tab_page('/volunteer')` gate so the header link cannot dead-end. |
| `can_open_admin(user)` | `bool` | Whether the nav offers Admin: `/admin` has at least one tab for this viewer, flags included. Wider than `can_view_admin` by the online-play roles (`PRESET_MANAGER`, `SYNC_ADMIN`, `QUALIFIER_ADMIN`, per-qualifier admins) and narrower where a coordinator's or equipment manager's feature is off. Only the nav uses it. |
| `can_view_admin(user)` | `bool` | Staff (super-admin included), or TA/CC of a tournament in this tenant: the Reports tab's audience. No other role passes; each admin role is held to its own tab gates. |
| `can_view_volunteer_admin(user)` | `bool` | `can_view_admin` or `can_manage_volunteers`: the admin-side volunteer reads (positions, shifts, coverage, hours). |
| `can_edit_tournament(user, tournament)` | `bool` | Staff, or TA of that tournament. |
| `can_manage_payouts(user, tournament)` | `bool` | Staff, or TA of that tournament — gates reading and editing the prize split. |
| `can_crud_match(user, match)` | `bool` | Staff, or TA of the match's tournament. |
| `can_run_match(user, match)` | `bool` | Staff, Proctor, or TA — gates running a match on the floor: seat/start/finish, recording the result, seed rolls, station assignment. |
| `can_confirm_match(user, match)` | `bool` | Staff, or TA of the match's tournament. Excludes Proctor by design — confirming advances the bracket and pushes to Challonge, so it is the admin's verification step. |
| `can_approve_crew(user, match)` | `bool` | Staff, TA, or CC of the match's tournament. |
| `can_manage_stages(user)` | `bool` | Staff or Stream Manager — gates Stage CRUD. |
| `can_manage_volunteers(user)` | `bool` | Staff or Volunteer Coordinator — gates volunteer position/shift/assignment management and the auto-scheduler. |
| `can_manage_equipment(user)` | `bool` | Staff or Equipment Manager — gates asset CRUD and check-in. |
| `can_checkout_equipment(user)` | `bool` | Any signed-in, active member (self-checkout). Naming another borrower needs `can_manage_equipment`. |
| `can_checkin_equipment(user)` | `bool` | Staff or Equipment Manager. |
| `is_check_in_desk(user)` | `bool` | Holds `Role.CHECK_IN_DESK` in the current tenant. |
| `can_manage_check_in(user)` | `bool` | Staff — gates check-in event CRUD, bounty lookup, and adding/removing walk-ups. |
| `can_run_check_in_desk(user)` | `bool` | System actor, Staff, or `CHECK_IN_DESK` — gates check-in/undo, link/unlink, and sync. |
| `can_view_check_in_desk(user)` | `bool` | `FeatureFlag.EVENT_CHECK_IN` live **and** `can_run_check_in_desk`. Decides the Volunteer hub's **Check-in Desk** tab and, for a desk role with no volunteer role, the Volunteer menu link (`BaseLayout` additionally requires an open event). |
| `can_assign_match_stream(user, match)` | `bool` | Staff or Stream Manager globally, or TA of the match's tournament — gates stage assignment and the stream-candidate flag. |
| `can_grant_roles(user)` | `bool` | Staff only — gates role grants and TA/CC membership changes. |
| `is_system(user)` | `bool` | Field check (`User.is_system`) — the reserved automation actor. Sync (no DB). The `can_manage_*`/`can_admin_qualifier` gates short-circuit on it so workers/bots never hit a `PermissionError`. |
| `can_manage_presets(user)` | `bool` | System actor, super-admin, Staff, or `PRESET_MANAGER` — gates seed-rolling preset management (online tournaments). |
| `can_manage_sync(user)` | `bool` | System actor, super-admin, Staff, or `SYNC_ADMIN` — gates upstream sync config (SpeedGaming links, Discord events, racetime bot/room config). |
| `can_admin_qualifier(user, qualifier=None)` | `bool` | System actor, super-admin, Staff, `QUALIFIER_ADMIN`, or — when a `qualifier` is passed — a per-entity admin on its `admins` M2M. |
| `can_submit_triforce_text(user, tournament)` | `bool` | Staff or `TRIFORCE_SUBMITTER`, **and** the tournament is active with a generator that supports texts. |
| `ensure(allowed, message="Permission denied")` | `None` | Raises `PermissionError(message)` when `allowed` is falsy; the standard gate inside mutating services. |
| `ensure_super_admin` / `ensure_can_manage_presets` / `ensure_can_manage_sync` / `ensure_can_admin_qualifier` | `None` | Gate-and-raise wrappers over the matching `is_*`/`can_*` predicate. |

**Module-level helpers:** `get_user_from_session(app.storage.user) -> User | None` resolves the signed-in viewer (Discord or password session) to a `User` model (or `None` when logged out, deactivated, or the password changed); `get_user_from_discord_id(discord_id)` resolves a Discord id, for bot and Discord-side callers. Call it once at page entry and pass the result into the helpers above — don't re-resolve per check.

### password_auth_service.py — PasswordAuthService

| Method | Purpose |
|---|---|
| `authenticate(login_name, password)` | Returns the active, non-placeholder `User` or `None`. Unknown names verify against a dummy hash so timing is even. scrypt runs in `asyncio.to_thread`. |
| `list_accounts(actor)` | Every real account for the Users tab (placeholders and the system user excluded). |
| `create_account(actor, display_name, login_name, password)` | Password-only account with no Discord. Audits `user.created` and `user.password_set`. |
| `set_password(actor, user, login_name, password)` | Sets or changes the sign-in name and password; bumps `password_changed_at`, which ends that user's existing password sessions. |
| `remove_password(actor, user)` | Clears it; refuses an account with no Discord ("Deactivate the account instead"). |
| `set_active(actor, user, active)` | (De)activates; you can't deactivate yourself. |

Every method but `authenticate` is super-admin only. Module helpers: `hash_password`, `verify_password`, `generate_password`, `normalize_login_name`, `MIN_PASSWORD_LENGTH`. Failed-attempt limits live in `application/utils/login_throttle.py` (per name and per browser, never per IP).

### oauth_handoff_service.py — module functions

Cross-host handoff for custom tenant domains (`HOST_OAUTH_MODE=handoff`): OAuth always completes on the platform host, which **mints** a short-lived (30s TTL), single-use, host-bound token and redirects to the target domain's claim route, which **claims** it and completes the flow where the host-only session cookie lives. Only the nonce + host travel in the URL; the payload stays server-side in a bounded in-process store (a single-worker precondition, like the tenant caches), signed with `STORAGE_SECRET` via `itsdangerous`. Two payload shapes share the store: **login** (`mint`) and **secondary-provider identity links** (`mint_data`).

| Function | Returns | Description |
|---|---|---|
| `mint(*, discord_id, username, avatar, target_host, next_path, bind_commit=None)` | `str \| None` | Mint a **login** handoff token bound to `target_host` (must already be a validated active tenant domain); `None` if the host won't normalize. `bind_commit` ties the token to the initiating browser (login-CSRF guard). Claim returns the identity top-level (`payload['discord_id']`). |
| `mint_data(*, data, target_host, next_path, bind_commit=None)` | `str \| None` | Mint a **generic** handoff token carrying an arbitrary `data` dict (same TTL / single-use / host + browser binding). Used by the secondary-provider link handoff to carry `{key, user_id, name}`; claim returns it under `payload['data']`. |
| `claim(token, request_host)` | `dict \| None` | Validate + consume the token on `request_host`; returns the stored payload or `None` for invalid/expired/wrong-host/replayed tokens. Single-use — the nonce is popped regardless of outcome. |
| `reset()` | `None` | Clear the pending store (test isolation). |

The claim routes are `/session/claim` (login, in `pages/auth.py`) and `/oauth/link/claim` (provider links, in `pages/_oauth_link.py`). Detail: [multitenancy.md](../features/multitenancy.md).

### notification_links.py — module functions

Where each notification's call to action goes ([notification_links.py](../../application/services/notification_links.py)). Every DM that asks the recipient to *do* something gets a `DMLink` from here; `send_dm(..., link=...)` renders it as a Discord link button and hands the URL to the web-push mirror as its tap target.

One module rather than a private `_url` helper per service, because three mistakes kept recurring: a **relative** path (meaningless in a DM, which has no request context), a link to a page that merely *mentions* the thing, and a link to a surface the recipient **cannot act on** — the matchup-ready DM pointed entrants at `/brackets/<id>`, whose Schedule button is `is_staff`-gated.

| Function | Returns | Description |
|---|---|---|
| `link_for(label, path)` | `DMLink \| None` | `label` → the tenant-absolute form of `path`, resolving the tenant in scope. **Never raises** — callers are best-effort notifiers, so a failed lookup costs the button, not the DM. |
| `link_for_tenant(tenant, label, path)` | `DMLink \| None` | The same for a caller holding the tenant row already (the qualifier worker). Synchronous. |
| `player_schedule(bracket_match_id, *, label='Pick a time')` | `DMLink \| None` | `/home/player?schedule=<id>` — the schedule dialog, open. The entrant's only route to booking a bracket game. |
| `player_reschedule(match_id, *, label='Ask again')` | `DMLink \| None` | `/home/player?reschedule=<id>` — the reschedule-request form, open (the declined-request DM). |
| `player_hard_preset(match_id, *, label='Choose your settings')` | `DMLink \| None` | `/home/player?hard=<id>` — the hard-preset opt-in dialog, open. |
| `player_match(match_id, *, label='View your match')` | `DMLink \| None` | `/home/player?match=<id>` — the Player tab narrowed to one match (the stage DMs, whose recipients can't open the admin board). |
| `player_agree(request_id, *, label='Open the request')` | `DMLink \| None` | `/home/player?agree=<id>` — the opponent's reschedule request, open with its Agree button. |
| `crew_match(match_id, *, label='View your crew slot')` | `DMLink \| None` | `/home/my-crew?match=<id>` — a crew member's own card for that match. |
| `community_schedule(*, label='View the schedule')` | `DMLink \| None` | `/home/schedule`, for a DM about somebody *else's* match. |
| `home_tournaments(*, label='View tournaments')` | `DMLink \| None` | `/home/tournaments`, where signup and withdrawal happen (the signup confirmation). |
| `admin_match(match_id, *, label='Open the match')` | `DMLink \| None` | `/admin/match-schedule?match_id=<id>` — where crew gets refilled. |
| `admin_reschedule_request(request_id, *, label='Review the request')` | `DMLink \| None` | `/admin/match-schedule?reschedule_request=<id>` — one request's decision dialog, open. |
| `admin_volunteer_schedule(day=None, *, label='Open the shift')` | `DMLink \| None` | The volunteer roster on the shift's day (the community's clock, not the reader's). |
| `admin_users(*, label='Review the request')` | `DMLink \| None` | `/admin/members`, where join requests are decided. |
| `admin_ada_request(request_id, *, label='Open the request')` | `DMLink \| None` | `/admin/members?ada_request=<id>`, that ADA request's dialog open. |
| `admin_matcherino_login(*, label='Update the Matcherino login')` | `DMLink \| None` | `/admin/check-in`, whose first control is the Matcherino login card. |
| `admin_qualifier_queue(qualifier_id, *, label='Open the review queue')` | `DMLink \| None` | `/admin/qualifiers?qualifier=<id>&tab=queue` — one qualifier's review queue, open. |

Paths come from the pure [`app_links.py`](#additional-utilities), shared with the pages that render the same routes. Detail: [discord.md → Calls to action](../features/discord.md#calls-to-action).

### startgg_service.py — StartggService

A read-only mirror of a linked start.gg event (`StartggEntrant`/`Phase`/`Pool`/`Set`), gated by `FeatureFlag.STARTGG`. `link_event` (refuses a Challonge link or a native bracket), `unlink_event`, `sync_event(force)` (20 s throttle), `sync_linked_events` (worker, unaudited), `set_bookable_phase`, `set_report_results`; staff entrant mapping `list_entrants`, `count_unmatched_entrants(tournament_ids)`, `assign_entrant`, `confirm_all_suggestions`, `dismiss_suggestion`, `clear_entrant`; players `list_unbooked_sets_for_user`, `schedule_set` (through `MatchService.submit_match_request(from_bracket=True)`); the public page reads `get_event_view`. A sync DMs both players of each set that newly became bookable. `report_result_if_linked` is the soft-gated writeback: only when the tournament opted in, re-reading the set and skipping it when start.gg already has a result. Detail: [startgg.md](../features/startgg.md).

### bracket_set_service.py — BracketSetService

Best-of sets on a Challonge or start.gg matchup, one `Match` per game (`Match.set_game`, `set_best_of`, `set_first`). `standing(match)` reads the set's wins off its games' finish ranks; `call_off_unneeded(match)` cancels the unplayed games of a decided set as the system user; `book_followers` books games 2+ after `bracket_sources.schedule_set` booked game 1; `check_times` validates the game times; `label` renders "Game 2 of 3". The Challonge push and start.gg writeback send only the set's result. A stage's best-of comes from `RoundScheduleService.best_of_for`.

### round_schedule_service.py — RoundScheduleService

Per-stage booking windows for Challonge and start.gg tournaments (`Tournament.config['round_schedule']`), and enforcement for those plus native brackets (`Bracket.config['rounds']`). Staff editor: `list_stages` (group stage / pool phases, each elimination round keyed back from the final, placeholder rows before Challonge creates the elimination matches), `save_schedule` (audited `tournament.round_schedule_updated`). Lookups: `window_for(...)`, `window_for_challonge_match`, `window_for_startgg_set`, `window_for_bracket_match`, `window_for_match`, plus `describe_for_*` for the player-facing line. Enforcement: `check_time` / `check_booking` / `check_match_time` raise `RoundWindowError` (a `ValueError`) whose `can_override` is true for staff and tournament admins, who resubmit with `allow_outside_round=True`. Called from Challonge, start.gg and native booking, `MatchService.update_match`, reschedule requests and Suggest a time. The pure window maths and stage keys live in `round_windows.py`.

### round_windows.py — module functions

Pure: `RoundWindow` (opens, closes, daily hours on the community clock; `slot_fits`, `describe`), `elimination_keys`, `winners_final_round`, `stage_label`, `stage_sort_key`, `placeholder_keys`, `window_to_config`.

### bracket_sources.py — module functions

The shared contract for brackets that live on another site (`BracketSource` protocol; `StartggSource`, and `ChallongeSource` for sync and booking so far). `list_unbooked` (Match Schedule's Not booked yet view: open, unbooked matchups across Challonge, start.gg and native brackets, as `UnbookedMatch`), `on_match_confirmed` (called from match confirmation, isolates each site's failure), `list_bookable_for_user` / `schedule` (My Schedule), `sync_tournament` / `sync_all` (Admin → Tournaments), `unmatched_entrant_counts(tournaments)` (the Tournaments board's "N unmatched" chip; a failing site just shows no count), `live_sources`, `source_for`.

### challonge_service.py — ChallongeService

Coordinates the Challonge integration: one shared Wizzrobe service-account OAuth connection writes brackets; players link their own Challonge identity (scope `me`) only so they can be mapped to bracket participants (their tokens are not retained). The service mirrors a linked tournament's bracket into local `ChallongeParticipant`/`ChallongeMatch` rows, schedules open matchups through the existing match-request flow, and pushes recorded results back to Challonge. Module constants: internal token-refresh-buffer and sync-throttle windows; `get_connection_status` reports the month's request count with no cap. Mock mode is gated by [`MOCK_CHALLONGE`](#mock-switches).

| Method | Returns | Description |
|---|---|---|
| `is_configured()` (static) | `bool` | OAuth app credentials are present (or mock is on). |
| `service_authorize_url(state)` / `player_authorize_url(state)` (static) | `str` | Challonge OAuth authorize URL for the service-account / per-player flow. |
| `redirect_uri()` (static) | `str` | The single registered OAuth redirect URI (both flows). |
| `get_valid_access_token(force_refresh=False)` | `str` | Live service access token, refreshing when expiring/forced; `ValueError` when not connected or no refresh token is available. |
| `save_service_connection(token_payload, actor, community=None)` | `ChallongeConnection` | Staff-only; stores the service-account tokens and connected username, adds `community` to the remembered list; audits `challonge.connected`. |
| `remembered_communities()` | `list[ChallongeCommunity]` | `_challonge_communities.py`. The communities the connection should cover: those saved in `Tenant.config['challonge_communities']` (kept across a disconnect), then any a linked tournament's `challonge_community` names, each with its tournament count. |
| `communities_to_grant(extra=None)` | `list[str]` | The remembered slugs plus a typed one (`ValueError` if it isn't a subdomain). `/challonge/connect` asks Challonge for each in turn, one consent per community, because a grant names one `community_id`. |
| `remove_community(actor, community)` | `None` | Staff-only; drops a saved community. Refuses (`ValueError`) while linked tournaments use it. Audits `challonge.community_removed`. |
| `disconnect(actor)` | `None` | Staff-only; clears the connection; audits `challonge.disconnected`. |
| `get_connection_status()` | `dict` | `{connected, configured, request_usage, request_quota, ...}` for the admin UI. |
| `record_player_link(user, challonge_user_id, challonge_username, actor)` | `None` | Persist a player's linked Challonge identity (OAuth path); audits `challonge.player_linked`. Then maps them onto any already-mirrored entrant carrying that account (hand assignments untouched), refreshes the Players list and sends ready DMs, without waiting for the next sync. Best-effort. |
| `participant_tournament_ids(user)` | `set[int]` | Tournament ids whose mirrored bracket includes this user. |
| `link_player_manually(user, challonge_user_id, challonge_username, actor)` | `None` | Staff override linking a user by Challonge id; rejects ids already linked elsewhere (`ValueError`); audits `challonge.player_linked` with `manual: True`. |
| `set_player_username(user, challonge_username, actor)` | `None` | Staff override correcting the display username on an already-linked account; audits `challonge.player_username_updated`. |
| `unlink_player(user, actor)` | `None` | Clear a user's Challonge link; audits `challonge.player_unlinked`. |
| `exchange_player_code(code)` | `dict` | Exchange a player's auth code and return `{user_id, username}` (token used once, discarded). |
| `exchange_service_code(code)` | `dict` | Exchange the service-account auth code for a token payload. |
| `parse_tournament_identifier(id_or_url)` (static) | `tuple[str, str \| None]` | Extract a Challonge `(identifier, community)` from a raw id or URL; a `<community>.challonge.com` URL yields its subdomain as the community. |
| `link_tournament(tournament_id, id_or_url, actor)` | `Tournament` | Gated by `can_edit_tournament`; fetches the bracket, stores the link, and mirrors participants/matches in the same round-trip; audits `challonge.tournament_linked`. |
| `unlink_tournament(tournament_id, actor)` | `Tournament` | The migration path off Challonge: clears the link and deletes the local mirror (participants + `ChallongeMatch` rows), leaving the scheduled `Match` rows and the remote bracket untouched. The tournament becomes eligible for a native bracket. Audits `challonge.tournament_unlinked`. |
| `sync_bracket(tournament_id, actor, force=False)` | `dict[str, int]` | Gated by `can_edit_tournament`; re-fetch and mirror the bracket; non-forced calls inside the throttle window are a no-op (`{skipped: True}`). Audits `challonge.bracket_synced`. DMs both players of each match that became bookable in this sync (`_challonge_notifications.py`). |
| `list_bracket_participants(tournament_id, actor)` | `list[dict]` | Lives with the two below in `ChallongeParticipantAssignmentMixin` ([`_challonge_participants.py`](../../application/services/_challonge_participants.py)). Gated by `can_edit_tournament`; the linked bracket's entrants as `{id, name, challonge_user_id, user, status, verified_user}`. `status` is `manual` / `verified` / `unlinked`; `verified_user` is set only when a manual row's Challonge account belongs to someone else. |
| `count_unmatched_participants(tournament_ids)` | `dict[int, int]` | Entrants with no Wizzrobe user per linked tournament (tournaments with none left out); feeds `bracket_sources.unmatched_entrant_counts`. |
| `assign_participant_user(participant_id, user_id, actor)` | `ChallongeParticipant` | Gated by `can_edit_tournament`; maps an entrant to a community member by hand (`user_assigned_manually=True`, kept by every sync). Refuses a non-member or a user already mapped to another entrant in the bracket (`ValueError`). Audits `challonge.participant_assigned`. Sends the ready-to-schedule DM when it links a match's last entrant. |
| `sign_up_player(tournament, user)` | `ChallongeParticipant` | `_challonge_signup.py`. Called by `TournamentService.self_enroll` for a Challonge-linked tournament. Adds the player to the bracket by their linked `challonge_username`, with `misc = wizzrobe-user:<id>` so a sync maps the entrant before Challonge's invite is accepted. Refuses an unlinked player, a started bracket (`roster_locked`), a duplicate, a connection without `participants:write`, and more than `ROSTER_CHANGES_PER_HOUR` (6) sign-ups/withdrawals per player per hour (each spends quota). Audits `challonge.participant_signed_up` and publishes `tournament.enrolled`. |
| `withdraw_player(tournament, user)` | `None` | Deletes the player's own entry on Challonge (a 404 still withdraws locally) until the bracket starts. Audits `challonge.participant_withdrawn` and publishes `tournament.withdrawn`. |
| `can_add_participants()` | `bool` | Whether the connection holds `participants:write`; the Challonge card on Settings → Integrations warns staff to reconnect when it doesn't. |
| `clear_participant_user(participant_id, actor)` | `ChallongeParticipant` | Drops a hand assignment; the entrant falls back to whoever its Challonge account is linked to (or nobody). Audits `challonge.participant_unassigned`. |
| `advance_stage(tournament_id, action_key, actor)` | `Tournament` | Lives in `ChallongeStageMixin` ([`_challonge_stages.py`](../../application/services/_challonge_stages.py)). Gated by `can_edit_tournament`; runs one stage action on Challonge and force-syncs, so new matches (the bracket after the groups) are bookable at once. The action must be the one `stage_actions_for` offers from the cached `challonge_state`, and the connection must hold `tournaments:write` (`can_change_stages()`), else `ValueError`. **Finalize groups and start bracket** is two calls (`finalize_group_stage`, `start`); if the second fails the mirror is still re-synced and the error says which step stuck. A refused first step also re-syncs (best-effort), because the usual cause is a stage already moved on Challonge's own site; the error then says the page now shows the live state. Challonge's refusal text is surfaced as the `ValueError`. Audits `challonge.stage_advanced`. Reset is deliberately not offered. |
| `stage_actions_for(tournament)` / `stage_label_for(tournament)` (static) | `list[StageAction]` / `str \| None` | The next stage action(s) for the cached state (`pending` → Start tournament / Start group stage, `group_stages_underway` → Finalize groups and start bracket, `group_stages_finalized` → Start bracket, `awaiting_review` → Finalize tournament) and a readable state label. |
| `list_unscheduled_matches_for_user(user)` | `list[ChallongeMatch]` | Open bracket matchups for the user not yet scheduled locally. |
| `schedule_challonge_match(challonge_match_pk, scheduled_date, scheduled_time, actor, comment=None)` | `Match` | Schedule an open matchup via `MatchService.submit_match_request` and link it; `ValueError` for already-scheduled/not-ready matches or unlinked players. |
| `push_result_if_linked(match, actor)` | `bool` | Push the result only when the match mirrors a Challonge match; returns whether a push was attempted (used by the confirm flow). |
| `push_match_result(match, actor)` | `None` | Report the recorded winner/loser to Challonge, then force a re-sync to surface newly-opened next-round matches; audits `challonge.result_pushed`. |

Collaborators: `ChallongeRepository`, `TournamentRepository`, `MatchService`, `AuthService`, `AuditService`, [`challonge_client.py`](#challonge_clientpy) (`ChallongeClient`/`MockChallongeClient`), [`mock_challonge.py`](#mock-switches).

### bracket_service.py — BracketService

Owns the **native bracket** lifecycle ([brackets.md](../features/brackets.md)): authoring a stage while DRAFT, the tournament-level roster (entrants) and per-stage participation (entries), the generate-then-persist `start` that turns a seeded field into a persisted `BracketMatch` graph via the pure engines, result recording with pointer-following advancement, stage completion + ranking, and multi-stage advancement. Every write is Staff-gated (`AuthService.is_staff`), audits an `AuditActions.BRACKET_*` action, and publishes the mirror `EventType.BRACKET_*`. Native brackets and a Challonge link are mutually exclusive (`_ensure_no_challonge_link`). The engine runs only at `start` and per Swiss round; at all other times the persisted rows are the source of truth.

`BracketService` is one public class composed from per-concern mixins in the internal `application/services/_bracket/` subpackage (`generation.py`, `advancement.py`, `completion.py`, `multistage.py`, `scheduling.py`, `series.py`, `notifications.py`) so no single file exceeds the length budget; `bracket_service.py` keeps `__init__`, the shared helpers, and the roster/enrollment CRUD, and composes the mixins. The split is an implementation detail — importers still use `from application.services import BracketService`.

| Method | Returns | Description |
|---|---|---|
| `create_bracket(actor, tournament_id, name, format, stage_order=0, config=None)` | `Bracket` | Create a DRAFT stage; rejects a Challonge-linked tournament, a duplicate `stage_order`, and (via `validate_bracket_config`) a bad config. Audits/events `BRACKET_CREATED`. |
| `update_bracket(actor, bracket_id, name=None, stage_order=None, config=None, format=None)` | `Bracket` | Edit a DRAFT stage's name / order / config / **format** — the format is safe to change precisely because a DRAFT stage has no match graph and nothing but `start_bracket` reads it. The config is validated against the stage as it will be *after* the edit, so a format change and its format-specific keys are checked together. Audits `BRACKET_UPDATED`. |
| `set_round_metadata(actor, bracket_id, rounds)` | `Bracket` | Replace the per-round metadata (`{round: {best_of, scheduled_at, scheduled_end}}`), allowed in **any** state since round chrome never touches the graph. The `scheduled_at`/`scheduled_end` window bounds match-time suggestions for that round. Audits `BRACKET_UPDATED`. |
| `delete_bracket(actor, bracket_id)` | `None` | Delete a DRAFT stage. Audits `BRACKET_DELETED`. |
| `get_bracket / list_brackets / list_matches / list_entries / list_entrants` | reads | Stage, stage list (by `stage_order`), match graph, per-stage entries, tournament roster. Unfiltered: staff tooling and lifecycle code only. |
| `is_published(bracket)` · `list_visible_brackets(viewer, tournament_id)` · `list_all_visible_brackets(viewer)` · `get_visible_bracket(viewer, bracket_id)` · `get_visible_tournament(viewer, tournament_id)` | viewer-aware reads | What `viewer` (`None` = signed out) may see: DRAFT/CANCELLED stages and tournaments with nothing published are staff-only, and read as missing to anyone else. Every non-staff surface (pages, static views, join door, browse tab, REST, MCP) reads through these. |
| `get_match_with_games(match_id)` | `BracketMatch \| None` | One matchup with its series games loaded, for render/serialize. |
| `list_all_brackets()` | `list[Bracket]` | Every stage in the tenant with its tournament prefetched, active tournaments first then name then `stage_order` — the one read behind the anonymous home **Brackets** tab, which groups the rows rather than querying per tournament. |
| `add_entrant(actor, tournament_id, display_name, user_id=None)` | `BracketEntrant` | Add a roster entrant — placeholder (`user_id=None`) or linked. Audits/events `BRACKET_ENTRANT_ADDED`. |
| `set_entrant_user(actor, entrant_id, user_id)` | `BracketEntrant` | Link a roster entrant to a user account (`None` unlinks) — the only way to resolve a placeholder after creation, and what makes its matchups schedulable, DM-able and race-room-joinable. Allowed in **any** state: it names who the entrant *is*. Audits/events `BRACKET_ENTRANT_UPDATED`. |
| `import_entrants_from_roster(actor, tournament_id)` | `list[BracketEntrant]` | Create a linked entrant per enrolled player not already rostered (matched on `user_id`), returning only the new ones. Idempotent, so it can be re-run after late signups. Audits/events `BRACKET_ENTRANT_ADDED` per entrant, as `add_entrant` does. |
| `drop_entrant(actor, entrant_id)` | `BracketEntrant` | Mark an entrant `DROPPED`. Audits/events `BRACKET_ENTRANT_DROPPED`. |
| `enroll(actor, bracket_id, entrant_id, seed=None, group_number=None)` | `BracketEntry` | Enroll a roster entrant into a DRAFT stage (once per stage). |
| `preview_draw(bracket_id)` | `DrawPreview` | The read-only twin of `start_bracket`: same engine, same seeding rule, same bye materialization, in memory. Returns unsaved `BracketMatch` rows with synthetic negative ids (so the ordinary renderer can draw them), the projected `seed_of`, plain-language `summary` lines, and an `error` when the field could not start at all. Writes nothing. |
| `unenroll(actor, entry_id)` | `None` | Remove an entry from a DRAFT stage outright — the inverse of `enroll`, DRAFT-only for the same reason. Audits `BRACKET_ENTRY_REMOVED`. |
| `retire_entry(actor, entry_id)` | `BracketEntry` | Mark a stage entry `DROPPED` in **any** state: played results stand, Swiss stops pairing them, advancement skips them. Distinct from `drop_entrant`, which retires them from the tournament roster and does not cascade. Audits/events `BRACKET_ENTRY_RETIRED`. |
| `cancel_stage(actor, bracket_id)` | `Bracket` | Abandon a started stage: terminal, but writes no `final_rank` and records no champion. Rejected on a COMPLETE stage. A CANCELLED stage is hidden from the public views and can be deleted. Audits/events `BRACKET_CANCELLED`. |
| `advancement_plan(tournament_id, from_stage_order)` | `AdvancementPlan` | Everything `advance_stage` is about to do — source, destination, rule, and `(entry, resulting seed, source group)` per advancer — so the confirmation states the seeding that will actually be applied. `already_seeded` is *reported*, not raised. |
| `set_seeds(actor, bracket_id, seeds)` | `None` | Bulk-set per-entry seeds (`entry_id → seed`, `None` clears), DRAFT only. The whole resulting seeding is validated before any write, so a duplicate or `<1` seed changes nothing. Audits `BRACKET_UPDATED`. |
| `start_bracket(actor, bracket_id)` | `Bracket` | Fill missing seeds contiguously, generate the graph (elimination/round-robin) or pair Swiss round 1, auto-complete structural byes, and set `ACTIVE`. A non-first stage requires its predecessor `COMPLETE`. Audits/events `BRACKET_STARTED`. |
| `report_result(actor, match_id, winner_entry_id, *, entry1_score=None, entry2_score=None, forfeit=False)` | `BracketMatch` | Record an OPEN match's winner (optional set scores, all-or-nothing; the winner must hold the strictly-higher score unless `forfeit` marks a DQ/walkover), push winner/loser through `winner_to`/`loser_to`, settle walkovers, auto-complete an elimination final and generate the next Swiss round. Audits/events `BRACKET_MATCH_COMPLETED`. |
| `override_result(actor, match_id, winner_entry_id, *, entry1_score=None, entry2_score=None, forfeit=False)` | `BracketMatch` | Staff correction of a COMPLETE match, allowed only while nothing downstream is COMPLETE. |
| `get_open_matches(bracket_id)` | `List[BracketMatch]` | All OPEN (playable) matches. |
| `complete_stage(actor, bracket_id, tie_breaks=None)` | `Bracket` | Finalize: write every entry's `final_rank` (elimination depth / RR + Swiss standings, with optional staff tie-breaks) and set `COMPLETE`. Audits/events `BRACKET_COMPLETED`. |
| `standings(bracket_id)` | `List[StandingsGroup]` | Live standings of a round-robin (one group per pool) or Swiss (single group) stage — the same computation `complete_stage` ranks with, read-only and available mid-stage, with each row's entrant name/seed/status and the tiebreaker chain joined in. Rejects elimination formats, whose placement comes from the match graph. |
| `advance_stage(actor, tournament_id, from_stage_order)` | `Bracket` | Seed the next stage from the source's `final_rank` per its `advancement` rule (top `count`, per-group or overall; snake/preserve seeding), enrolling fresh entries on the same entrants. Audits/events `BRACKET_STAGE_ADVANCED`. |
| `get_advancing_preview(tournament_id, from_stage_order)` | `List[BracketEntry]` | Dry-run of the same selection for the confirm dialog. |
| `list_open_matches_for_user(user_id, tournament_id=None)` | `List[BracketMatch]` | OPEN, unscheduled matches whose both entrants are linked users — schedulable ones (peer of `ChallongeService.list_unscheduled_matches_for_user`). |
| `schedule_bracket_match(actor, bracket_match_id, **match_kwargs)` | `Match` | Schedule the next game of an OPEN matchup into a real `Match` and record it as a `BracketMatchGame` (peer of `schedule_challonge_match`). Gates for itself and routes by actor: Staff / tournament admin → `MatchService.create_match`; the matchup's own two entrants → `submit_match_request` with `from_bracket=True`. A non-privileged caller passing staff-only kwargs (`stage_id`, crew) raises `ValueError`. |
| `list_linkable_matches(tournament_id)` | `List[BracketMatch]` | OPEN matchups across the tournament's stages that a `Match` could still be linked to — the whole-tournament peer of `list_open_matches_for_user`. Backs the admin editor's picker. |
| `get_bracket_match_for_match(match_id)` | `BracketMatch \| None` | Read-only: the matchup a scheduled `Match` belongs to, so presentation never reaches through `service.repository`. |
| `link_match_to_bracket_match(actor, bracket_match_id, match_id)` | `BracketMatchGame` | Staff/TA: record an already-created `Match` as the matchup's next game. Validates same tournament, not already linked, a free slot, and that the match's **player set equals the two entrants' users** (a mismatch would leave `_winner_from_ranks` unable to resolve a winner). Audits/events `BRACKET_GAME_LINKED`. |
| `unlink_match(actor, match_id)` | `bool` | Staff/TA: detach a still-`SCHEDULED` game, leaving the `Match` in place; `False` when the match backs no game, `ValueError` once the game has been played. Audits/events `BRACKET_GAME_UNLINKED`. |
| `advance_if_linked(match, actor)` | `bool` | When a confirmed `Match` mirrors a bracket match, map its winner to the winning entry and `report_result` it (peer of `push_result_if_linked`). |
| `release_game_if_linked(match, actor, *, reason='')` | `bool` | The mirror of `advance_if_linked`: a cancelled or deleted `Match` hands its series slot back so the matchup is bookable again. Called from `MatchService._remove_match` **before** the row is deleted (`SET_NULL` makes the game unreachable afterwards). Acts only on a `SCHEDULED` game — which is what keeps it a no-op on a clinch teardown — and **deletes** the row rather than marking it `CANCELLED` (a cancelled number stays consumed). Audits/events `BRACKET_GAME_RELEASED` and DMs the entrants to rebook. |
| `get_game_for_match(match_id)` | `BracketMatchGame \| None` | Read-only: the series game a `Match` backs, one level below `get_bracket_match_for_match`. Backs the settled-result guard. |
| `matchup_live_state(bracket_matches)` | `Dict[int, dict]` | Per-matchup `{'status': MatchStatus, 'games': {game_id: MatchStatus}, 'watch_url': str}` for the bracket view. **One extra query for the whole field** — the games/matches arrive prefetched and the racetime rooms are read in a single batched lookup. |
| `round_name_for(bracket, round_number)` | `str` | "Semifinals" / "Losers Round 2" for one round, resolved from the stage's graph (one lean query). Elimination formats only: Swiss and round robin stay on "Round N", since their deepest round is the last one everyone plays, not a final. |
| `round_name_for_match(bracket, bracket_match)` | `str` | The round name a *matchup* carries — `round_name_for` plus a `Group N · ` prefix in a grouped stage, where every group runs the same round numbers at once. What the matchup-ready DM, the match DM context line and `matchup_summary` all use. |
| `match_dm_context(match_id)` | `(str, bool)` | `(bracket_line, frees_a_slot)` for a match's DMs — `"Semifinals · Game 2 of 3 · Series 1-0"` plus whether cancelling it would free a slot. Never raises; `('', False)` for a match no bracket scheduled. `bracket_line_for_match` is the line-only shorthand. |
| `matchup_summary(match_id)` | `dict \| None` | What a scheduled `Match` is *for* — round name, game number, best-of, series standing, both entrants with seeds, and the stakes ("Winner to Semifinals"). Backs the admin match editor's matchup panel. |
| `notify_matchup_ready(bracket_match, *, rebook=False)` | `None` | DM both entrants that they have a matchup to schedule. Fired on the `PENDING → OPEN` transition, at generation/Swiss pairing, and (with `rebook`) after a slot release. Best-effort, on `discord_queue`. |
| `notify_matchup_reopened(bracket_match, *, reason='')` / `notify_open_matchups(bracket)` | `None` | The released-game variant (a no-op once the series is decided or the matchup left `OPEN`), and the start-of-stage announcement of every matchup generation wrote straight to `OPEN`. |
| `set_best_of(actor, bracket_match_id, best_of)` | `BracketMatch` | Staff override of one matchup's series length (`None` clears); rejected once any game exists. From `SeriesMixin`. |
| `resolve_best_of(bracket, bracket_match)` / `wins_needed(best_of)` (static) | `int` | Series length (override → round → stage `default_best_of` → 1) and the wins that clinch it. |
| `series_standing(bracket_match)` / `is_decided(...)` / `next_game_number(bracket_match, best_of)` | `(int, int)` / `bool` / `int` | Series arithmetic; `next_game_number` raises when the series is full (a cancelled number stays taken). |
| `settle_game_if_linked(match, actor)` | `bool` | Record a finished `Match` as its series game, then clinch or release the next game. `False` with no actor or no linked game. |
| `cancel_remaining_games(actor, bracket_match_id, reason)` | `None` | Cancel every unplayed game and tear down its `Match` when a result is settled outside the per-game path (`report_result`/`override_result`). |
| `held_match_ids(match_ids)` / `series_matches_due(window_start, window_end)` / `release_next_game(bracket_match)` | `set[int]` / `list[Match]` / `None` | The racetime auto-open hold: later games wait until the earlier one's `Match.finished_at`; slipped games the poll window would miss; and the push that opens the next room when a game ends. |

Collaborators: `BracketRepository`, [`bracket_config.py`](#bracket_configpy--bracket-config-substrate) (`validate_bracket_config`, `AdvancementConfig`), [`bracket_engines/`](#bracket_engines--pairingprogression-engines) (`get_bracket_engine`, `compute_standings`), `MatchService` (scheduling seam), `AuthService`, `AuditService`, the event bus.

### crew_service.py — CrewService

Crew (commentator/tracker) self-signup, the approval workflow, and crew-side acknowledgment. Feature doc: [match-participation.md](../features/match-participation.md).

| Method | Returns | Description |
|---|---|---|
| `signup_crew(match_id, user, role)` | `None` | Self-signup as `'commentator'` or `'tracker'`, unapproved by default; rejects an invalid role, a **started or finished** match, a role the tournament does not use, a duplicate signup, and a user who is a player in the match; audits `crew.signup_created`. Seated-but-not-started stays open — that is when a stream discovers it needs a commentator. |
| `undo_crew_signup(match_id, user, role)` | `None` | Remove one's own crew signup; audits `crew.signup_removed` with `was_approved`. Withdrawing an **approved** commitment DMs the tournament's crew owners (admins + crew coordinators, plus the approver) with how long until the match starts; an unapproved one stays silent, because nobody was told it existed. Never gated on match state — a signup must always be removable. |
| `get_crew_member_by_id(crew_id, crew_type)` | `Commentator \| Tracker \| None` | Fetch by row id; `crew_type` must be `'commentator'` or `'tracker'` (else `ValueError`). |
| `update_crew_approval(crew_member, crew_type, approved, actor=None)` | `Commentator \| Tracker` | Approve/unapprove. Gated by `can_approve_crew`; refreshes from DB to narrow approval races; no-ops (no audit, no DM) when state already matches; unapproval clears `acknowledged_at`; approval enqueues an acknowledgment-request DM. |
| `approve_crew_member(crew_member, crew_type, actor=None)` | `Commentator \| Tracker` | Convenience wrapper for `update_crew_approval(..., approved=True)`. |
| `acknowledge_crew_assignment(crew_id, crew_type, user)` | `Commentator \| Tracker` | Crew member confirms their own approved assignment. Rejects other users and unapproved rows (`ValueError`); already-acknowledged rows are a silent no-op. |
| `list_my_commitments(user, upcoming_only=True)` | `list[dict]` | Both roles merged and sorted by when they happen — the query behind **My Crew** on Home. Returns plain dicts because the view is a join across two models, and merging ORM rows of different types in the page would put that join in the wrong layer. |
| `find_scheduling_conflicts(user, match, role)` | `list[str]` | What else this person is already committed to while `match` runs, one printable sentence each. One query across **all three** roles — someone racing at 19:00 cannot commentate at 19:00 either — over the tournament's `average_match_duration` (90 min default), widened both ways so a match that started earlier and is still running is caught. The approval confirmation prints what it finds; before this, no conflict check existed anywhere in the crew path. |

Collaborators: `CommentatorRepository`, `TrackerRepository`, `MatchRepository` (signup match lookup, `commitments_for_user`), `TournamentRepository`, `AuditService`, `DiscordService` (via `discord_queue`).

### discord_member_import_service.py — DiscordMemberImportService

Admin → Users › Import from Discord. Gives every human in the community's linked server an account (matched on `discord_id`, so nobody is duplicated) and a membership, so staff can enroll and map people who have never signed in. Staff-only (`can_grant_roles`); a missing server link or a Discord failure is a `ValueError`. Bots, people removed from the community (`TenantMemberBlock`), and inactive or system accounts are skipped. No DMs. Safe to rerun: a second run only adds people new to the server.

| Method | Returns | Description |
|---|---|---|
| `preview(actor)` | `DiscordImportPreview` | What a run would do now, without writing: `server_name`, `people` (humans), `new_accounts`, `new_members`, `already_members`, `bots`, `removed`. |
| `run(actor)` | `DiscordImportResult` | Bulk-inserts the missing accounts (`UserRepository.bulk_create_from_discord`, conflicts ignored), then one `TenantMembershipRepository.bulk_add` (`MembershipSource.DISCORD_IMPORT`) for the people not yet members, closing their pending join requests (`close_pending_request`); no per-person DM, audit row or event. One audit row for the run, `tenant.members_imported` + `EventType.TENANT_MEMBERS_IMPORTED`, carrying the counts and `created_user_ids` / `added_user_ids`. |

Collaborators: `DiscordService.list_members` / `.get_guild_summary`, `UserRepository`, `TenantMembershipRepository`, `TenantJoinRequestRepository`, `AuditService`.

### discord_role_mapping_service.py — DiscordRoleMappingService

CRUD for `DiscordRoleMapping` plus the login-time sync that maps a user's Discord guild roles onto application roles **and** onto per-tournament grants. Feature doc: [discord.md](../features/discord.md).

| Method | Returns | Description |
|---|---|---|
| `list_all_mappings()` / `list_mappings(guild_id)` | `List[DiscordRoleMapping]` | All mappings, or those scoped to one guild. |
| `add_mapping(guild_id, discord_role_id, discord_role_name, actor, app_role=None, tournament_grant=None, tournament_id=None)` | `DiscordRoleMapping` | Staff-only. Exactly one of `app_role` / `tournament_grant` — the latter requires a `tournament_id` in this tenant, the former forbids one. Rejects exact duplicates; every violation is a `ValueError`. Audits `discord_role.mapping_added`. |
| `add_mappings(guild_id, discord_roles, actor, app_roles=(), tournament_grants=(), tournament_id=None)` | `(created, already_mapped)` | Staff-only bulk add behind the admin dialog: every `(id, name)` in `discord_roles` × every grant. Validates every role, grant and the tournament before the first write; skips pairs that already exist instead of raising. One `discord_role.mapping_added` audit row per created mapping. |
| `remove_mapping(mapping_id, actor)` | `None` | Staff-only; `ValueError` if missing; audits `discord_role.mapping_removed`. |
| `remove_mappings(mapping_ids, actor)` | `int` | Staff-only bulk remove; skips ids already gone and returns how many it removed. Audits each. |
| `sync_all_users(actor)` | `dict` | Staff-only (`can_grant_roles`) bulk re-sync, applying current mappings immediately. First provisions an account for each member of the current tenant's guild who holds a mapped role and has none (`DiscordService.list_members_with_roles`; a Discord failure logs and creates nothing). Syncs **this community only** (`sync_user_roles_for_tenant`), over its members plus the guild's mapped-role holders. Returns `{users_processed, users_created, granted, revoked, skipped}` counts plus `created` (names) and `changed` (`[{name, granted, revoked}]`). Audits `role.discord_sync_bulk` with the counts and `created_user_ids` / `changed_user_ids`. |
| `provision_member(tenants, member)` | `User \| None` | The account for a `GuildMember` whose roles one of `tenants` maps (a grantable app role, or a tournament grant on an active tournament), created via `UserService.provision_from_discord_role_sync` if missing. `None` when no held role confers anything. Called by the live `sync_member_roles`. |
| `sync_user_roles_for_tenant(user, tenant)` | `dict` | The per-tenant half of `sync_user_roles`, inside `tenant_scope(tenant.id)` against that tenant's `discord_guild_id`. Never raises. |
| `sync_user_roles(user)` | `dict` | Full-syncs the user's Discord-sourced roles **and** tournament grants. **Never raises** — fails open on any error so login is never blocked. Grants mapped roles the user lacks (`source=discord`), revokes Discord-sourced roles no longer present, and never touches `source=manual` rows. Returns a `{'granted', 'revoked', 'tournament_granted', 'tournament_revoked', 'skipped'}` summary. |

The tournament half reconciles `Tournament.admins` / `.crew_coordinators` against
`DiscordTournamentGrant` provenance rows rather than the join tables, which is what
keeps a hand-made Tournament Admin safe: no provenance row, no revocation. A grant
the user already holds by hand is left unclaimed, and a mapping onto an **inactive**
tournament counts as absent, so ending an event takes back the authority its guild
role conferred. `TournamentService.add_admin` / `add_crew_coordinator` (and their
removes) delete the provenance row, pinning the grant as manual the way
`UserRoleRepository.add` promotes a row to `RoleSource.MANUAL`.

Collaborators: `DiscordRoleMappingRepository`, `UserRoleRepository`, `DiscordService.get_member_role_ids` / `.list_members_with_roles`, `UserService.provision_from_discord_role_sync`, `TenantService.list_tenants` (the per-tenant `discord_guild_id` is the routing key), `AuthService`, `AuditService`.

### discord_queue.py — module functions

Single-worker async queue (one instance of the shared `CoroutineQueue` from [`coroutine_queue.py`](#additional-utilities)) that serializes all outbound Discord work. Producers call `enqueue(coro)` from anywhere; one worker task awaits queued coroutines in FIFO order, so DM bursts cannot stampede the Discord API and callers never block on sends. Worker exceptions are logged (reaching Sentry) and swallowed — an individual failed DM never kills the worker. Deep dive: [discord-integration.md](discord-integration.md).

| Function | Returns | Description |
|---|---|---|
| `start()` | `None` | Create the worker task on the running event loop. Called once from `main.py` lifespan startup. |
| `stop()` (async) | `None` | Cancel the worker and await its exit; warns if items are still queued (they are dropped). Called from lifespan shutdown. Dropped items are closed (`discard_pending`), so none surfaces later as a never-awaited warning. |
| `discard_pending()` | `int` | Close every queued coroutine without running it, including the send an un-started `_run_in_tenant_scope` wrapper holds; returns the count. For a process with no worker: `scripts/seed_dev.py` calls it (and `dispatch_queue`'s) after seeding, because starting a bracket queues matchup DMs. |
| `enqueue(coro)` | `None` | Put a coroutine on the queue without awaiting it. Captures the caller's tenant and re-binds it (`tenant_scope`) around the coroutine when the worker runs it, since the worker starts with no tenant in scope; enqueued outside any tenant, the coroutine is queued unwrapped. |

Producers: the match services (`MatchService`, `MatchScheduleService`, cancellation, hard-preset, stage reminder), `CrewService`, `BracketService` notifications, reschedule and tournament-signup notifications, `TenantMembershipService`, and the volunteer schedule/reminder. Tests stub `enqueue` (see [Testing](#testing-the-service-layer)).

### discord_service.py — DiscordService, MockDiscordService, get_discord_bot

Thin wrapper around the shared discord.py bot: DM sending (plain and with interactive button views), guild/role management. All methods return tuples instead of raising, since Discord failures (DMs disabled, user missing, bot not ready) are routine. Deep dive: [discord-integration.md](discord-integration.md).

**Module-level:** `get_discord_bot() -> commands.Bot` lazily creates the singleton bot with the required intents and registers the `on_interaction` dispatcher, which routes a button's `custom_id` prefix (text before the first `:`) to a handler registered via `register_interaction_handler(prefix, handler)`. The DM senders build their button views through factories registered via `register_view_factory(kind, factory)`. Both registries are filled by `discordbot/__init__.py` (crew signup, match ack, crew ack, volunteer ack, unwatch, reschedule agree, hard preset), so this module never imports the bot package.

| Method | Returns | Description |
|---|---|---|
| `send_dm(user_id, message, view_factory=None, embed=None, link=None, on_sent=None, mirror=True, silent=False, notice=None)` | `(bool, str)` | `notice` is the catalogued [`NoticeTarget`](#notification_catalogpy--notice-catalog) (`MATCH_SCHEDULED.to(PLAYERS)`): the community's setting for it overrides `silent`, and `OFF` returns `(True, DM_NOTICE_OFF)` before anything is sent or mirrored to devices, email or Telegram. DM to a Discord user id. `on_sent(channel_id, message_id)` is awaited once Discord accepts the message (a failure inside it is logged, not reported as a failed send); [`staff_request_dms`](#staff_request_dmspy--module-functions) uses it to find the message again. `embed`, when given, is sent *instead of* `message` as the Discord content (the text then only feeds web push); `link` (a `DMLink` from [`notification_links`](#notification_linkspy--module-functions)) renders as a link button beside any `view_factory` buttons and is the web-push tap target. Also enqueues a fire-and-forget mirror of the message to the recipient's web-push devices (real service only; the mock skips it) — see [web-push.md](../features/web-push.md). | Every send in a community that isn't turned off is also logged to [`SentMessage`](data-model.md#sentmessage) via [`SentMessageService`](#sent_message_servicepy--sentmessageservice), with the notice's key and recipient.
| `edit_dm(channel_id, message_id, embed, link=None)` | `(bool, str)` | Replace a sent DM's card and buttons (`link` is the only button left). Silent: Discord doesn't notify on an edit, and the web-push copy can't change. The mock prints `[MOCK Discord DM edit]`. |
| `send_dm_with_crew_buttons(user_id, message, match_id)` | `(bool, str)` | DM with commentator/tracker signup buttons. |
| `send_dm_with_acknowledgment_button(user_id, message, match_id)` | `(bool, str)` | DM with a match Acknowledge button. |
| `send_dm_with_crew_acknowledgment_button(user_id, message, crew_type, crew_id)` | `(bool, str)` | DM with a crew-assignment Acknowledge button. |
| `send_dm_with_volunteer_acknowledgment_button(user_id, message, assignment_id)` | `(bool, str)` | DM with a volunteer-shift Acknowledge button. |
| `send_dm_with_reschedule_agree_button(user_id, message, request_id)` | `(bool, str)` | DM asking the opponent to agree to a reschedule request. |
| `send_dm_with_hard_preset_buttons(user_id, message, match_id, opted_in)` | `(bool, str)` | DM with the hard-preset opt-in/out buttons. |
| `send_dm_with_unwatch_button(user_id, message, match_id)` | `(bool, str)` | DM with an Unwatch button for match watchers. |

Every `send_dm_with_*` also takes the optional `embed=None, link=None, notice=None` and passes them through to `send_dm`.
| `get_bot()` | `commands.Bot \| None` | The underlying bot instance. |
| `list_guilds()` | `(bool, list[{id, name}] \| str)` | Guilds the bot is connected to. |
| `list_guild_roles(guild_id)` | `(bool, list[{id, name, color, mappable, position}] \| str)` | All roles in a guild (fetch with cached fallback). `color` is Discord's RGB int (0 = none); `mappable` is False for `@everyone` and bot/integration-managed roles; `position` is Discord's role order (higher outranks lower). |
| `bot_role_access(guild_id)` | `(bool, {manage_roles, top_position} \| str)` | Whether the bot holds Manage Roles (or Administrator) and its highest role's position. It can only hand out roles strictly below that. Feeds `DiscordRoleSyncService`. |
| `member_role_snapshot(guild_id, user_ids)` | `(bool, {user_id: frozenset[int]} \| str)` | The roles each of `user_ids` holds, one pass over the member cache (or one paged `fetch_members`); ids not in the guild are absent. Feeds `DiscordRoleSyncService`. |
| `add_role_to_user(guild_id, user_id, role_id, reason=None)` | `(bool, str)` | Grant a Discord role to a guild member. |
| `remove_role_from_user(guild_id, user_id, role_id, reason=None)` | `(bool, str)` | Remove a Discord role from a guild member. |
| `get_member_role_ids(guild_id, user_id)` | `(bool, set[int] \| str)` | The member's role ids (`@everyone` excluded); `(True, set())` for a non-member. Feeds `DiscordRoleMappingService.sync_user_roles`. |
| `list_members(guild_id)` | `(bool, list[GuildMember] \| str)` | Every member, bots included, as frozen `GuildMember(id, username, avatar, role_ids, bot)` records. Reads the member cache when the guild is chunked, otherwise pages `fetch_members`. Feeds `DiscordMemberImportService`. |
| `list_members_with_roles(guild_id, role_ids)` | `(bool, list[GuildMember] \| str)` | `list_members` narrowed to non-bot members holding at least one of `role_ids`. Feeds the provisioning step of `sync_all_users`. |
| `get_guild_summary(guild_id)` | `(bool, {id, name} \| str)` | Name of a guild the bot can see (renders the connected-server label; confirms bot presence). |
| `is_guild_member(guild_id, user_id)` | `(bool, bool \| str)` | Whether a user is in the guild. `ok=False` when the bot can't tell. Feeds `TenantMembershipService.join_via_discord`. |
| `member_can_manage_guild(guild_id, user_id)` | `(bool, bool \| str)` | Whether a user is owner / Administrator / has Manage Server. **Fails closed** (`ok=False`) if the bot can't determine it. The authority check behind `DiscordLinkService`. |
| `create_scheduled_event(guild_id, *, name, start_time, end_time, description=None, location='Stream')` | `(bool, int \| str)` | Create an external guild Scheduled Event; returns its id. |
| `edit_scheduled_event(guild_id, event_id, *, name, start_time, end_time, description=None, location='Stream')` / `delete_scheduled_event(guild_id, event_id)` | `(bool, str)` | Update / remove one. The three back `DiscordEventReconcilerService`. |

The methods from `list_guilds` down live in **`discord_guild_ops.py`** (`GuildOpsMixin`) and the scheduled-event wrappers in **`discord_scheduled_events.py`** (`ScheduledEventsMixin`), both composed into `DiscordService` the way `StationAssignmentMixin` is composed into `MatchService`; `discord_service.py` itself keeps the DM surface and the bot singleton. Each module carries its own mock mixin (`MockGuildOpsMixin`, `MockScheduledEventsMixin`) beside the real one — a mirror kept in another file drifts, and it drifts in the one mode nobody runs the tests in.

**`MockDiscordService`** composes those mock mixins and mirrors the full public surface, printing to stdout and returning success tuples. Its fixture data (guilds, roles, member roles, authority) comes from [`mock_discord_data.py`](../../application/utils/mocks/mock_discord_data.py), kept in sync with `scripts/seed_dev.py`. When [`MOCK_DISCORD`](../features/discord.md#mock-mode) is enabled, the module rebinds the name `DiscordService = MockDiscordService` at import time, so all callers get the stub transparently.

### staff_request_dms.py — module functions

The staff DMs about a request someone has to decide (a community event to review, an ADA request, a join request, a reschedule request, a crew signup), and the edit once someone has. Every staff member is DM'd, so each copy that lands is recorded in [`StaffRequestDM`](data-model.md#staffrequestdm) and rewritten when the request is decided, so the others needn't open it to check. Both halves go through `discord_queue`, whose single worker keeps an edit behind the sends it edits.

| Function | Description |
|---|---|
| `send(kind, object_id, recipients, message, *, embed, link)` (async) | Forgets the previous round's records for this request, then queues one DM per recipient with a `discord_id`, each recording itself through `send_dm(on_sent=...)`. |
| `mark_handled(kind, object_id, build, link, *, actor_id=None)` | Queues the edit of every recorded copy. `build(you: bool)` returns the card, so the decider's own copy reads "You". Called again on a later decision (approved, then taken down), it edits the same copies. |
| `carry_over(kind, old_id, new_id, build, link)` | Queues moving the records from `old_id` to `new_id`, then editing them. For a request withdrawn and made again before a second DM is due: a crew signup redone inside its 10-minute notice window turns the "withdrawn" copies back into the ask. |
| `card(title_and_description, *, color, community_name, fields=None)` | `notification_embed` from a `(title, description)` pair out of [`discord_messages_staff_requests.py`](../../application/utils/discord_messages_staff_requests.py). |

Callers: `_community_event_notifications` (approve, reject, a pending event cancelled), `AccommodationService` (moved off New, a change reviewed, withdrawn; a request that goes back to New gets its original card back), `TenantMembershipService` (approved, declined, let in another way) the reschedule notifications mixin (approved, declined, withdrawn, superseded) and `CrewService` (a pending signup withdrawn by the volunteer, approved, or removed by staff; one whose approval is taken back gets its original card back).

### discord_member_events.py — module functions

What the bot does with `GUILD_MEMBER_UPDATE` / `GUILD_MEMBER_REMOVE`, split out of `discord_service.py` so the bot singleton and the DM senders don't share a file with the listeners. Both log and swallow everything — an exception escaping a gateway listener takes the connection down with it.

| Function | Returns | Description |
|---|---|---|
| `sync_member_roles(guild_id, discord_user_id, member=None)` | `None` | Re-syncs the member's app roles across **every tenant sharing the guild**, each in its own `tenant_scope`. Unknown guild is a no-op. An unknown user gets an account via `DiscordRoleMappingService.provision_member` when `member` is given (the update event) and holds a mapped role; otherwise (including every removal) it's a no-op. |
| `sync_member_avatar(member)` | `None` | Stores `Member.avatar.key` — the **global** hash, matching what the OAuth login records — on `User.discord_avatar`, or `NULL` when the avatar was removed. Unknown users are a no-op; an unchanged hash writes nothing. The only avatar refresh someone who never signs in gets. |

### discord_link_service.py — DiscordLinkService

Verified linking of a `Tenant` to a Discord guild. Because `discord_guild_id` is no longer unique, a tenant may not merely *claim* a guild id — `DiscordLinkService` gates linking twice: **app** (`can_manage_link` → tenant STAFF or super-admin) and **Discord** (the actor must administer the guild). `authorize_url(state)` builds Discord's bot-authorization URL (`scope=bot`, adds the bot on consent); `complete_link(actor, tenant, code)` exchanges the code for an authoritative `guild` object then calls `link_guild(actor, tenant, guild_id)`, which re-checks `DiscordService.member_can_manage_guild` (fails closed) before stamping the guild via `TenantService.set_discord_guild_id` and auditing `discord.server_linked`. `disconnect(actor, tenant)` clears the link (leaving the bot, which other tenants may share) and audits `discord.server_unlinked`. Callback route: `/oauth/discord/connect/callback` in [`pages/auth.py`](../../pages/auth.py). Env: `DISCORD_BOT_PERMISSIONS`, `DISCORD_CONNECT_REDIRECT_URL`.


### discord_login_service.py — DiscordLoginService

The bot's one-time sign-in links. `mint_link(tenant, discord_id=…, username=…, avatar_hash=…, avatar_url=…)` provisions the account exactly as an OAuth login does (`UserService.provision_from_discord_login`), refuses a deactivated one with `ValueError`, and returns `tenant_url(tenant, '/login/discord?token=…')`. `claim(token, request_host, tenant_id)` consumes it: single-use (nonce popped from an in-process store, so single-worker like `oauth_handoff_service`), `LINK_TTL_SECONDS` (120), and bound to both the link's host and its tenant, since path-mode communities share a host. There's deliberately **no browser binding** — the person opens the link wherever Discord sends them — which is why the TTL is short and the reply is ephemeral. `communities_for_guild(guild_id)` lists the active tenants linked to a server; `community_for_panel(guild_id, tenant_id)` raises `NotFoundError` for a panel whose community is no longer linked there. `authorize_panel(discord_id, tenant)` is the STAFF (or super-admin) gate on posting a panel (`PermissionError`), and `record_panel_posted(actor, tenant, channel_id)` audits `discord.login_panel_posted`. Behaviour: [discord.md § Log in from Discord](../features/discord.md#log-in-from-discord).
### discord_event_reconciler_service.py — DiscordEventReconcilerService

Mirrors the Wizzrobe schedule into a tenant guild's **Discord Scheduled Events** as idempotent reconciliation. `reconcile_tenant(tenant, *, actor, now=None)` reads the tenant's opted-in tournaments' scheduled matches in a forward window, then per match creates / updates (on a content-hash change) / leaves unchanged its `DiscordScheduledEvent` link, and cancels links whose source match finished or vanished. **Shared-guild safety**: the working set is only the tenant's own link rows (the repository is tenant-scoped), so a sibling tenant's event in a shared guild is never enumerated or cancelled; the target guild is the verified `Tenant.discord_guild_id`. Titles/descriptions render from `{tournament}`/`{match}`/`{players}` templates. Audits + emits `discord_event.created`/`updated`/`cancelled` (the per-run summary is audit-only). Before touching Discord it asks `DiscordService.bot_event_problem(guild_id)` (Create Events, Manage Events or Administrator); when the bot can't, the pass is **paused**: one WARNING, `ReconcileResult.setup_error` set, no Discord calls and no audit row. A setup failure mid-pass (`SETUP_FAILURES`) stops the pass the same way instead of failing every match. Collaborators: `DiscordScheduledEventRepository`, `MatchRepository`, `DiscordService`, `AuditService`, event bus.

**discord_event_worker.py** — the reconcile background loop (peer of `speedgaming_sync_worker`). Every 5 min it scans (cross-tenant, unscoped) every tenant with a linked guild, then reconciles each inside `tenant_scope` as the system user. Idempotent, so an unchanged schedule is a cheap no-op. Started from the lifespan only when `DISCORD_EVENTS_SYNC_ENABLED` is on.

### discord_event_sync_service.py — DiscordEventSyncService

The human-driven surface over the reconciler, gated by `AuthService.can_manage_sync`. `list_tournaments` / `update_settings(actor, tournament_id, *, enabled, duration_minutes, title_template, description_template)` manage per-tournament opt-in (audits `discord_event.settings_updated`); `setup_problem(actor)` returns why events can't sync (no linked server, or the bot can't create events), or `None`; while it is set `update_settings` raises `ValueError` (REST 400) and the admin pane shows a banner, locks every row and shows enabled tournaments as Paused; `list_events` feeds the admin observability table; `reconcile_now(actor)` runs the reconciler for the ambient tenant on demand (raises `ValueError` when no guild is linked).

### equipment_service.py — EquipmentService

Lending-asset management (create/edit/delete, bulk creation with auto-assigned asset numbers) and the checkout/check-in workflow with full loan history. Asset management is gated by `AuthService.can_manage_equipment` (Staff or Equipment Manager); checkout by `can_checkout_equipment` (any signed-in member, to themselves — a loaner's QR code is scanned by whoever holds it; only a manager may pass `borrower_id`, and a non-manager who does is refused rather than silently self-assigned); check-in by `can_checkin_equipment`. Audited under `equipment.*`. Module constant: `MAX_BULK_COUNT = 200`.

| Method | Returns | Description |
|---|---|---|
| `create_asset(actor, name, description=None, private_notes=None, owner_user_id=None)` | `Equipment` | Manager-only; auto-assigns the next asset number; non-empty name required; `owner_user_id=None` means the community (its tenant) owns it. Audits `equipment.created`. |
| `bulk_create_assets(actor, name, count, description=None, private_notes=None, owner_user_id=None)` | `list[Equipment]` | Manager-only; create `count` (1–200) consecutively numbered assets. |
| `update_asset(actor, equipment_id, name, description, private_notes, owner_user_id, status=None)` | `Equipment` | Manager-only edit; refuses to set/clear `CHECKED_OUT` status directly (use checkout/check-in). Audits `equipment.updated`. |
| `delete_asset(actor, equipment_id)` | `None` | Manager-only; refuses an asset with an open loan. Audits `equipment.deleted`. |
| `checkout(actor, equipment_id, borrower_id=None)` | `EquipmentLoan` | Open a loan; managers may set a `borrower_id`, otherwise the borrower is `actor`. Rejects retired/already-checked-out assets, and rejects the system account or a deactivated account as borrower (*"That account cannot borrow equipment."*). It deliberately does **not** require the borrower to belong to this community — the picker narrows to that by default and offers an opt-in to widen, because lending to someone who just walked into the venue is the case this serves. Flips status to `CHECKED_OUT`. Audits `equipment.checked_out`. |
| `checkin(actor, equipment_id)` | `Equipment` | Close the open loan and flip status to `AVAILABLE`; `ValueError` when not checked out. Audits `equipment.checked_in`. |
| `list_assets()` / `get_asset(equipment_id)` | reads | Every asset; one asset by id. |
| `get_assets_by_ids(ids)` | `list[Equipment]` | Tenant-scoped fetch of the given assets (ordered by asset number) for the bulk QR-label sheet; unknown/foreign ids are silently dropped. |
| `current_loan(equipment)` | `EquipmentLoan \| None` | The open loan for an asset, if any. |
| `open_loans_by_equipment_id()` | `dict[int, EquipmentLoan]` | All open loans keyed by equipment id (table batch-load). |
| `loan_count(equipment)` | `int` | Total loans against the asset — the `N` in a bounded history's "5 of N". |
| `my_checkouts(user)` | `list[EquipmentLoan]` | The user's open loans. |
| `loan_history(equipment, *, limit=None)` | `list[EquipmentLoan]` | Loan history, newest first; `limit` bounds the fetch. Each row prefetches three users, so an asset lent hundreds of times is worth bounding — the asset page asks for the five it paints. |
| `loan_count(equipment)` | `int` | Total loans against an asset — the `N` a bounded view reports as "5 of N". |
| `my_checkouts(user)` | `list[EquipmentLoan]` | A user's currently-open loans. |

Collaborators: `EquipmentRepository`, `AuthService`, `AuditService`. The equipment detail pages render QR codes via [`qrcode_util.py`](#qrcode_utilpy).

### check_in_service.py — CheckInService

The check-in desk for in-person events whose registration runs on Matcherino. Mirrors a ticketed venue's badge sales into `CheckInTier` / `CheckInPass` rows and one `CheckInEntrant` per buyer, auto-links each buyer to a `User` on exact identifiers, and records arrivals. Every public method carries `@requires_feature(FeatureFlag.EVENT_CHECK_IN)`. Event setup and walk-ups need `AuthService.can_manage_check_in` (Staff); the desk actions need `can_run_check_in_desk` (Staff, `CHECK_IN_DESK`, or the system actor). Both raise `PermissionError`. Constructor takes an optional `client: MatcherinoClient`; without one, each Matcherino call builds `get_matcherino_client(<this community's saved login>)` (so `MOCK_MATCHERINO` applies), never caching it on the service, because the sync worker shares one service across tenants. Constants: `MIN_SYNC_INTERVAL_MINUTES = 1`, `MAX_SYNC_INTERVAL_MINUTES = 120`, `SUGGESTION_LIMIT = 3`. Feature doc: [event-check-in.md](../features/event-check-in.md).

| Method | Returns | Description |
|---|---|---|
| `create_event(actor, name, venue_id=None, status=DRAFT, sync_interval_minutes=5, *, comp_roles=None, comp_volunteers=False)` / `update_event(..., *, comp_roles=None, comp_volunteers=None)` (`None` keeps the current rules) / `delete_event(actor, event_id)` | `CheckInEvent` / `None` | Staff. One event per venue per community. Audits `check_in_event.created` / `.updated` / `.deleted`; publishes no event (tenant-internal config). |
| `preview_venue(actor, venue_id)` | `VenuePreview(venue, tiers)` | Staff. The venue's title and badge types, so the admin dialog can confirm an ID before saving. Reading badge types needs the stored Matcherino login, so a venue that previews is one the sync can read. |
| `list_events()` / `list_open_events()` / `get_event(event_id)` / `has_open_event()` | reads | `has_open_event` drives the nav link. |
| `sync_event(actor, event_id, *, audit=True)` | `SyncResult` | Desk. Needs a venue or comp rules. Works out comps first (`check_in_comps.comped_users`), then upserts buyers by `matcherino_user_id`, withdraws those holding no badge and restores those who hold one again, auto-links, then upserts badge types and badges (`check_in_sales`). A failure, or an empty response while live rows exist, records `last_sync_error` and changes nothing. Publishes `check_in_event.synced` every time; audits it only when `audit=True` (the worker passes `False`). One `ROSTER` signal on `check_in_live`. |
| `roster(event)` / `get_entrant(entrant_id)` | reads | Each entrant's `passes__tier` prefetched. |
| `tiers_for(event)` | `list[CheckInTier]` | Badge types, dearest first, for the event dialog's lanyard pickers. |
| `set_tier_lanyard(actor, tier_id, lanyard)` | `CheckInTier` | Staff. Sets the lanyard a badge type's holders get (VIP / Base / Day Pass); the sync never overwrites it. Audits `check_in_tier.updated`. |
| `volunteer_user_ids()` | `set[int]` | Users with a published (non-draft) `VolunteerAssignment` in this tenant, for the desk's **Volunteer** chip. A soft cross-subsystem read: empty, not raising, when `VOLUNTEERS` is off. |
| `suggest_users(entrant, limit=3)` / `search_members(event, query, limit=20)` | `list[User]` | Candidates for the link and walk-up dialogs: community members not already on the roster. `suggest_users` ranks by `difflib` name similarity; it only ever suggests. |
| `check_in(actor, entrant_id)` | `CheckInOutcome(entrant, already)` | Desk. Checking in twice is not an error; `already` says someone got there first. |
| `undo_check_in(actor, entrant_id)` | `CheckInEntrant` | Desk. |
| `link(actor, entrant_id, user_id)` / `unlink(actor, entrant_id)` | `CheckInEntrant` | Desk. Manual matching. A link fills `User.matcherino_user_id` / `matcherino_username` only when empty (audited as `user.profile_updated`); an unlink leaves `link_method = MANUAL` so the sync never re-links the row, and clears the id (and the handle, if still the one check-in filled) from the account. |
| `add_walk_up(actor, event_id, *, user_id=None, name=None, check_in=True)` | `CheckInEntrant` | Staff. A member or a bare name. |
| `remove_entrant(actor, entrant_id)` | `None` | Staff. Walk-ups only; a Matcherino row would come back on the next sync (refund the badge instead), and a comp row comes from the comp rules. `link`/`unlink` also refuse a comp row. |

The roster mutations audit and publish `check_in_entrant.checked_in` / `.check_in_undone` / `.linked` / `.unlinked` / `.walk_up_added` / `.removed` in one `write_and_publish` call, then nudge open desks on [`check_in_live`](../features/event-system.md) (`application/events/check_in_live.py`). `check_in_comps.py` decides and applies comps: `comped_users(event)` (role holders and volunteers at the lowest comp tier, via `VolunteerHoursService.roster`), `apply_comps` (comp reasons on every row, `COMP` rows created and withdrawn), `absorb_comp_row` (a comp row folded into the purchase row that takes its account). `check_in_accounts.py` holds `build_lookups` and `remember_matcherino_account` / `forget_matcherino_account`. `check_in_sales.py` is the data half of the sync: `buyers_of(sales)`, `buyer_fields`, `apply_tiers` and `apply_badges` (badges that leave the feed get `removed_at`). The pure rules live in `check_in_rules.py` (no I/O): `entrant_filters(entrant, badges)` / `ROSTER_FILTERS` / `tier_filter` (the desk's filter chips, one per badge type held), `active_badges` (unrefunded and unremoved, dearest first), `summarize(entrants) -> RosterCounts` (withdrawn rows count only as withdrawn), `handle_id`, `resolve_link` (auto-link precedence over an `IdentityLookups`), and the value types `SyncResult`, `RosterCounts`, `CheckInOutcome`.

Collaborators: `CheckInEventRepository`, `CheckInEntrantRepository`, `CheckInUserLookupRepository` (global identity lookups), `UserRepository` (community-scoped candidates), `AuditService`, `AuthService`, `MatcherinoClient`.

**sglive_pool_worker.py** — every 15 min, reads the sglive.speedgaming.org data feed once (only when some tournament has a `sglive_slug`) and hands it to `PayoutService.apply_sglive_games` per tenant as the system user, skipping tenants with `PAYOUTS` off. A failed read keeps every pool; first failure ERROR, repeats WARNING. No env switch. One tick by hand: `scripts/run_worker_tick.py sglive_pool`.

**check_in_sync_worker.py** — the roster poll (peer of `speedgaming_sync_worker`). Every 60 s it loads every open event with a bounty (cross-tenant, unscoped), then per event, inside `tenant_scope` via `for_each_tenant_scoped` and as the system user, skips one not yet due on its `sync_interval_minutes` or whose tenant has `EVENT_CHECK_IN` off, and otherwise runs `sync_event(..., audit=False)`. After a failure it backs off from the failure time rather than retrying every tick. No environment switch: it only touches events staff have opened. Started/stopped in `main.py`; one tick by hand with `scripts/run_worker_tick.py check_in_sync`.

### help_service.py — HelpService

Feature-aware, read-only access to the shipped help articles (`application/help/content/*.md`). Stateless, no repository — there is no stored help. An article documenting an optional subsystem is withheld from a community that has it off, and a gated article gives the **same** answer as a missing one so a stale link reveals nothing. See [help.md](../features/help.md).

| Method | Returns | Description |
|---|---|---|
| `list_articles()` | `list[HelpArticle]` | Every article whose `feature:` (if any) is live here, in catalogue order. |
| `get_article(slug)` | `HelpArticle \| None` | One article; `None` when missing *or* gated. |
| `get_snippet(name)` | `HelpSnippet \| None` | One popup region; `None` when its article is not live here, so `help_icon` renders nothing. |
| `search(query)` | `list[HelpArticle]` | AND over whitespace-separated terms against title + summary + flattened body. Respects the gate — search must not route around it. |

Collaborators: `application.help` (catalogue), `FeatureFlagService`.

### event_info_service.py — EventInfoService

Feature- **and role**-aware access to the current community's event handbook (`application/event_info/content/<tenant-slug>/*.md`) — the per-community counterpart to help. Stateless; the only data access is the tenant lookup that resolves the slug. The whole section is behind `EVENT_INFO` (`@requires_feature` on every public method), an article may carry its own `feature:`, and an article may carry `roles:` — an OR, empty meaning public. All three withholdings give the same answer as "no such article". See [event-information.md](../features/event-information.md).

| Method | Returns | Description |
|---|---|---|
| `list_articles(user=None)` | `list[ContentArticle]` | This community's articles minus any gated off or not this reader's role. `user=None` is a signed-out reader: public articles only. |
| `get_article(slug, user=None)` | `ContentArticle \| None` | One article; `None` when missing, gated, or role-restricted. |
| `get_snippet(name, user=None)` | `ContentSnippet \| None` | One popup region, gated by its **owning article** — a snippet can never be more readable than the article it was cut from. |

Collaborators: `application.event_info` (per-tenant catalogue), `TenantRepository`, `FeatureFlagService`, `AuthService`.

### accommodation_service.py — AccommodationService

Per-community ADA accommodation requests. Every public method is gated by `FeatureFlag.ADA_ACCOMMODATIONS`; the staff methods also require `AuthService.is_staff` (`PermissionError`). Audited under `accommodation.*` with ids and status only (never `details` or `staff_notes`); no events are published. Constants: `DETAILS_MAX_LENGTH = 2000`, `STAFF_NOTES_MAX_LENGTH = 4000`, `OPEN_STATUSES`.

| Method | Returns | Description |
|---|---|---|
| `get_mine(actor)` | `AccommodationRequest \| None` | The actor's own request in this community, withdrawn or not. |
| `set_my_request(actor, requested, details=None)` | `AccommodationRequest \| None` | Create (members only), update details, withdraw (`requested=False`: clears details, keeps notes) or reopen. Changing details on an acknowledged request resets it to `NEW`; on an arranged one it stays `ARRANGED` and sets `changed_since_arranged`; identical details are a no-op. DMs staff (link to the request) on open/reopen and on the first change after arranging. Audits `accommodation.requested` / `.updated` / `.withdrawn`. |
| `close_for_removed_member(actor, member)` | `None` | Soft (`# feature-gate: exempt`). Withdraws a removed member's open request and clears its details; called by `TenantMembershipService.remove_member`. |
| `get_request(actor, request_id)` | `AccommodationRequest \| None` | STAFF. One request in this community, for the DM deep link. |
| `open_requests_by_user(actor)` | `dict[int, AccommodationRequest]` | STAFF. Open requests keyed by user id, for the Users-tab ADA column. |
| `action_needed_count(actor)` | `int` | STAFF. New + Acknowledged + Arranged-and-changed, for the sub-tab label. |
| `list_requests(actor, include_withdrawn=False)` | `list[AccommodationRequest]` | STAFF. Open requests oldest first, `user` prefetched. |
| `requesting_user_ids(actor)` | `set[int]` | STAFF. Users with an open request (the Users-tab filter and column). |
| `arranged_notes_for(actor, user_ids)` | `dict[int, str \| None]` | `ARRANGED` requests among `user_ids`, for the schedule boards' icon: staff notes for STAFF, `None` for a PROCTOR (they learn it's arranged, not what). **Soft-gated** (`# feature-gate: exempt`): returns `{}` when the flag is off or the viewer is neither STAFF nor PROCTOR, so ungated boards can call it unconditionally. Never returns `details`. |
| `update_request(actor, request_id, status, staff_notes)` | `AccommodationRequest` | STAFF. Sets status (not to/from `WITHDRAWN`) and notes in one save; `NotFoundError` for an unknown or other-tenant id. Saving a `changed_since_arranged` request clears the flag even with nothing else edited. Audits `accommodation.status_changed`, `.notes_updated` and/or `.change_reviewed`. |

Collaborators: `AccommodationRepository`, `AuthService`, `AuditService`, `TenantMembershipService`.

### emergency_contact_service.py — EmergencyContactService

Per-community emergency contacts. Every public method is gated by `FeatureFlag.EMERGENCY_CONTACTS`; `open_for` also requires `AuthService.is_staff` (`PermissionError`). Audited under `emergency_contact.saved` / `.removed` / `.viewed` with ids only; no events are published and there is no list, export, REST or MCP surface. Constants: `NAME_MAX_LENGTH = 100`, `RELATIONSHIP_MAX_LENGTH = 60`, `PHONE_MAX_LENGTH = 40`.

| Method | Returns | Description |
|---|---|---|
| `get_mine(actor)` | `EmergencyContact \| None` | The actor's own contact in this community, `last_viewed_by` prefetched. |
| `save_mine(actor, name, relationship, phone)` | `EmergencyContact` | Create (members only) or update; whitespace collapsed, name and phone required, phone validated. Identical values are a no-op. |
| `remove_mine(actor)` | `None` | Deletes the actor's contact. |
| `remove_for_removed_member(actor, member)` | `None` | Soft (`# feature-gate: exempt`). Deletes a removed member's contact; called by `TenantMembershipService.remove_member`. |
| `open_for(actor, member)` | `EmergencyContact \| None` | STAFF. Writes `emergency_contact.viewed` first (also when no contact exists), then stamps `last_viewed_at`/`last_viewed_by`. |

Collaborators: `EmergencyContactRepository`, `AuthService`, `AuditService`, `TenantMembershipService`.

### community_event_service.py — CommunityEventService

Meetups, panels and side events members run during the onsite event. Members only throughout (`PermissionError` for anyone else; a super-admin passes); gated by `FeatureFlag.COMMUNITY_EVENTS`. Forms pass an `EventDraft(title, description, starts_at, duration_minutes, location)` with `starts_at` already UTC. Every lifecycle write pairs an audit row with the same-named event via `write_and_publish` (`community_event.*`, payload adds `host_id`). DMs live in `_community_event_notifications.py`. See [community-events.md](../features/community-events.md).

| Method | Returns | Description |
|---|---|---|
| `list_upcoming(actor)` | `list[CommunityEvent]` | Approved events that haven't ended, soonest first. |
| `list_mine(actor)` | `list[CommunityEvent]` | The actor's own submissions, any status. |
| `get_event(actor, event_id)` | `CommunityEvent` | An approved event, or any the actor hosts, or any for staff; otherwise `NotFoundError` (pending submissions stay private). |
| `subscription_state(actor, event_ids)` | `(set[int], dict[int, int])` | Which events the actor follows, and subscriber counts. |
| `submit(actor, draft)` | `CommunityEvent` | Validates the draft (future start, 5 min–72 h). STAFF submissions are `APPROVED` (audits submitted + approved); a member's are `PENDING` and DM staff. |
| `update(actor, event_id, draft)` | `CommunityEvent` | Host or staff. An approved event stays approved; moving its start clears subscribers' reminders and DMs them. A host editing a `REJECTED` event resubmits it (`PENDING`, staff DM'd). Cancelled events refuse. |
| `cancel(actor, event_id)` | `CommunityEvent` | Host or staff; DMs subscribers if it was listed and not over. |
| `subscribe(actor, event_id)` / `unsubscribe(actor, event_id)` | `None` | Idempotent; subscribing needs a listed event that hasn't ended. |
| `rsvp_state(actor, event_ids)` | `(set[int], dict[int, int])` | Which events the actor RSVP'd to, and going counts. |
| `rsvp(actor, event_id)` / `cancel_rsvp(actor, event_id)` | `None` | Idempotent. RSVPing needs a listed event with RSVPs on that hasn't ended, refuses past `rsvp_capacity` (event row locked while counting), and subscribes the actor to the reminder; cancelling removes the subscription too. |
| `list_attendees(actor, event_id)` | `list[User]` | Host or staff: who RSVP'd, in order. |
| `list_for_review(actor, statuses=None)` | `list[CommunityEvent]` | STAFF. Every status when omitted. |
| `pending_count(actor)` | `int` | STAFF. |
| `approve(actor, event_id)` | `CommunityEvent` | STAFF. From `PENDING` or `REJECTED`; DMs the host. |
| `reject(actor, event_id, reason)` | `CommunityEvent` | STAFF. Reason required; DMs the host. On a listed event this is "take down" and also DMs subscribers. |

Collaborators: `CommunityEventRepository`, `CommunityEventSubscriptionRepository`, `AuthService`, `AuditService`, `TenantMembershipService`.

### active_users_metrics.py — module functions

Background worker (`run_worker_loop`, `TICK_SECONDS = 300`): sends Sentry Metrics gauges `users.active_15m` / `_1h` / `_24h` (distinct users with any `TelemetryEvent` row in the window, via the cross-tenant `TelemetryRepository.distinct_users_since`) plus a `…_by_community` gauge per tenant slug. Only started when Sentry metrics are on; a tick does nothing with telemetry off. `start()` / `stop()` from the `main.py` lifespan. See [observability.md](../features/observability.md#metrics).

### telemetry_retention.py — module functions

Background worker (`run_worker_loop`, `TICK_SECONDS` = 24 h, first tick at boot): `prune(now=None)` deletes every `TelemetryEvent` older than `RETENTION` (396 days, about 13 months) in every community via the cross-tenant `TelemetryRepository.delete_before`, 5,000 rows per batch, and returns the count. Runs whether or not capture is on. `start()` / `stop()` from the `main.py` lifespan. See [telemetry.md](../features/telemetry.md#retention).

### community_event_reminder.py — module functions

Background worker (`run_worker_loop`, `TICK_SECONDS = 60`): scans un-reminded subscriptions to approved events starting within `MAX_LEAD_MINUTES` (24 h), then per subscription, inside its tenant, skips tenants with `COMMUNITY_EVENTS` off, re-checks `get_community_event_reminder_lead_minutes`, stamps `reminder_sent_at` and enqueues the DM. `start()` / `stop()` from the `main.py` lifespan. Imported as a module (`from application.services import community_event_reminder`).

### feedback_service.py — FeedbackService

Records in-app feedback from logged-in attendees and lets admins review it. The submitted category is coerced to a valid `FeedbackCategory` (defaulting to `OTHER`); the page URL is truncated to `PAGE_URL_MAX_LENGTH = 512`. Audited under `feedback.*`; gated by `FeatureFlag.FEEDBACK`.

| Method | Returns | Description |
|---|---|---|
| `submit(actor, category, message, page_url)` | `Feedback` | Record a submission from `actor`; non-empty message required, and `actor` must be a **member** of this community or a super-admin (`PermissionError` otherwise, a REST 403 — the queue is the community's, and the drawer offers the dialog to members only). Audits `feedback.submitted`. |
| `list_recent(limit=200)` | `list[Feedback]` | Recent submissions for the admin review list. |
| `list_mine(actor, limit=25)` | `list[Feedback]` | The actor's own submissions and their status; no gate beyond being the actor. |
| `set_reviewed(actor, feedback_id, reviewed=True)` | `Feedback` | Admin-only (`can_view_admin`); sets status `REVIEWED`, or back to `NEW` with `reviewed=False` (reversible so a mis-click can't lose a submission); `NotFoundError` for unknown id. Audits `feedback.reviewed` / `feedback.reopened`. |
| `mark_reviewed(actor, feedback_id)` | `Feedback` | Back-compat alias for `set_reviewed(..., reviewed=True)`. |

Collaborators: `FeedbackRepository`, `AuthService`, `AuditService`.

### feature_flag_service.py — FeatureFlagService

Resolves and administers the per-tenant [feature flags](../features/feature-flags.md). A flag is **live** when it is *available* (from the tenant's `FeatureFlagGroup` tier, or the default group, with a tri-state per-tenant override on top) **and** *enabled* (the community's STAFF opt-out). Group CRUD and availability writes are super-admin gated; the enable toggle is STAFF gated.

| Method | Returns | Description |
|---|---|---|
| `is_enabled(flag)` | `bool` | Whether one flag is live for the ambient tenant. Reads the memoized set, so N checks in a request cost one pass. |
| `enabled_flags()` | `set[FeatureFlag]` | Every live flag in one query; the per-request cache the guards read (`reset_flag_cache()` clears it). |
| `ensure_enabled(flag)` | `None` | Raise `FeatureDisabledError` unless the flag is live. Prefer the `@requires_feature` decorator over calling this by hand. |
| `list_for_tenant_admin(actor)` / `set_tenant_enabled(...)` | `list[dict]` / — | STAFF-gated: every flag's effective state for the ambient tenant, and the enable toggle behind the admin **Features** tab. |
| `current_tenant_group_name()` | `str \| None` | The tier the ambient tenant sits in (shown alongside the toggles). |
| `list_for_tenant(actor, tenant_id)` / `set_availability(...)` | `list[dict]` / — | Super-admin: a named tenant's flag states and the availability override, from `/platform`. |
| `list_groups` / `list_groups_with_counts` / `get_group` / `create_group` / `update_group` / `delete_group` / `assign_tenant_group` | — | Super-admin group (tier) CRUD and per-tenant assignment. One group is always the default; deleting the last default is refused. |

`@requires_feature(FeatureFlag.X)` (from [`application/feature_flags.py`](../../application/feature_flags.py)) is the service-layer half of gating: it wraps a service's public entry methods and raises `FeatureDisabledError`, a `NotFoundError` — so REST answers 404 and a UI `except ValueError` notifies. Each flag's `FeatureFlagSpec` names the `service_modules` it must guard, and `check_feature_flag_gating.py` enforces that plus the entry-surface half. Placement rules and the two carve-outs (soft integration points return neutral; workers skip the tenant): [feature-flags.md](../features/feature-flags.md).

Collaborators: `TenantFeatureFlagRepository`, `FeatureFlagGroupRepository`, `TenantRepository`, `AuthService`, `AuditService`.

### match_display_service.py — MatchDisplayService

Read-only view-model assembly for the match tables: fetches matches (and their acknowledgments) via repositories and formats them into the plain dicts the NiceGUI slot templates consume. Extracted from `MatchService` so the latter keeps only lifecycle logic — no writes, audit, or notifications here. Feature doc: [frontend.md](frontend.md).

| Method | Returns | Description |
|---|---|---|
| `get_match_for_display(match_id)` | `dict \| None` | One match formatted for the UI (state, local-formatted times, players with rank/station, acknowledgment summary, crew with approval/ack state, seed URL, `is_racetime` — true when the tournament is racetime.gg-enabled, which hides on-site controls — plus `scheduled_ts`, an epoch sort key, and `is_overdue`, true when the aware-UTC scheduled time has passed with no check-in and no finish). |
| `get_matches_for_display(*, tournament_ids=None, stage_ids=None, only_upcoming=False, user_discord_id=None, exclude_racetime=False, match_ids=None)` | `list[dict]` | Filtered match list in the same display shape, with acknowledgments batch-loaded; `match_ids` narrows to specific rows (a live single-row refresh). `exclude_racetime` drops matches in racetime.gg tournaments at the query (the proctor board's rows are the ones an on-site proctor can act on). |
| `get_room_seed_rows()` | `list[dict]` | Rolled-but-unplayed matches (upcoming, seeded, not started) for the tournament room's kiosk seeds board. |
| `get_tournaments_for_filter()` | `dict[int, str]` | Tournament id → name for filter dropdowns. |
| `get_stages_for_filter()` | `dict[int, str]` | Stage id → name for filter dropdowns. |
| `_bracket_ref(match)` | `dict \| None` | The `{id, name, game, best_of, standing}` of the bracket stage a match is a game of (series context read off the prefetched games, no per-row query), for the schedule's link into the bracket view — `None` for an ordinary match, and `None` (not an exception) when the caller skipped `prefetch_relations`, since `bracket_match_game` is a reverse OneToOne. |

Module helpers: `rolling_elapsed_label(started_at)` ("Rolling… 1:24" for an in-flight task-queue seed roll) and `is_room_seed_row(row)` (the kiosk board's predicate, applied to full refreshes and single live rows alike).

**`crew_wanted`** sits on every row: whether the tournament uses the role at all (`required_* > 0`), which is what lets the board stop offering **Sign up** for a role nobody is needed in.

Collaborators: `MatchRepository`, `MatchAcknowledgmentRepository`, `MatchStreamVolunteerRepository`, `ProviderTaskRepository` (in-flight seed rolls), `TournamentRepository`, `StageRepository`.

### match_schedule_service.py — MatchScheduleService

Match lifecycle transitions (seat → start → finish → confirm), seed rolling, and all Discord DM fan-out for match events. Every transition is gated by `AuthService.can_run_match` — except `confirm_match`, which passes `authorize=AuthService.can_confirm_match` to `_transition` and so excludes Proctor — validates ordering (e.g. "Match must be checked in before starting" as `ValueError`), stamps the timestamp, writes the matching `match.*` audit action, and enqueues a participant DM. Feature docs: [match-participation.md](../features/match-participation.md).

| Method | Returns | Description |
|---|---|---|
| `seat_match(match, actor=None)` | `None` | Set `seated_at` (rejects double check-in; rejects a match with no players — a bracket match whose entrants are unresolved has nobody to check in; rejects racetime.gg tournaments — the race room owns the lifecycle); audits `match.seated`; DMs participants. |
| `start_match(match, actor=None)` | `None` | Set `started_at` (requires seated, rejects restart); audits `match.started`; silently DMs crew and watchers (not players). |
| `finish_match(match, actor=None)` | `None` | Set `finished_at` (requires started, rejects re-finish); audits `match.finished`; silently DMs crew and watchers (not players). |
| `confirm_match(match, actor=None)` | `None` | Set `confirmed_at` (requires finished, rejects re-confirm); audits `match.confirmed`; DMs participants. |
| `generate_seed(match_id, actor=None)` | `(bool, str, str \| None)` | Roll a seed via `SeedGenerationService`, using the preset `MatchHardPresetService.resolve_preset` picks (the tournament's `preset`, or its `hard_preset` when every player opted in / staff overrode) when set, else the legacy `seed_generator` string. Per-match `asyncio.Lock` (class-level `_seed_locks` dict) rejects concurrent rolls; also fails softly when a seed already exists, no generator is configured, the generator is unknown, the match has no players yet, or permission is denied. A task-queue randomizer (`ASYNC_RANDOMIZERS`) is handed to `ProviderTaskService` + `SeedRollService` and returns with no URL (audits/emits `match.seed_roll_queued`; the worker finishes it). Otherwise it calls `complete_seed_roll` inline. `MissingCredentialError` / `SeedProviderError` messages are returned as-is; anything else is logged and returned as a generic `(False, error, None)`. |
| `complete_seed_roll(match, randomizer, preset, actor, *, seed_url, settings, provider_meta)` | `str` | The single definition of "a seed roll completed", shared by the inline path and the seed-roll worker: creates the `GeneratedSeeds` row (settings snapshotted as sent), enqueues seed DMs to opted-in players (a link seed gets an "Open your seed" button; a pasted seed such as a WWR permalink gets a code block and a button to the match on the Player tab), audits `match.seed_rolled`. |
| `notify_match_participants(match, message, embed=None)` | `None` | DM opted-in players, approved crew, and watchers — one DM per person; watchers get the Unwatch-button variant. Never raises; per-DM failures are printed. |
| `notify_match_crew(match, message, embed=None)` | `None` | DM approved crew and watchers, excluding players (players get the acknowledgment DM instead). Never raises. |
| `notify_stage_changed(match)` / `notify_stage_reminder(match, *, stage_name)` | `None` | Tell players, approved crew and watchers the match's stage was set or cleared (read back off the match), or — driven by `stage_reminder` — that it is about to start. Both link the Player tab. Never raise. |
| `notify_match_cancelled(recipients, message, embed=None)` | `None` | DM a **pre-resolved** `{discord_id: is_watcher}` set that the match is off. Takes recipients rather than a `Match` because cancellation deletes the row (and its cascaded players/crew/watchers) before the queue worker runs. Never raises. |
| `notify_acknowledgment_request(match, *, rescheduled, community='', bracket_line='')` | `None` | DM each player whose acknowledgment is still pending with an Acknowledge button; message wording switches on `rescheduled`. `community`/`bracket_line` are resolved by the caller in request context, since the queue worker has none. Never raises. |
| `notify_tournament_subscribers_scheduled(match, message, exclude_discord_ids, embed=None)` | `None` | DM tournament-notification subscribers (filtered by whether the match has a stage) with crew signup buttons, skipping already-notified ids. Never raises. |
| `notify_stream_candidate_subscribers(match, exclude_discord_ids, community='')` | `None` | DM stream-candidate subscribers with crew signup buttons. Skipped entirely when the match already has a stage (those subscribers were already notified). Never raises. |
| `notify_match_scheduled(match, *, rescheduled=False, is_stream_candidate=False)` | `None` | The collapsed scheduled/rescheduled fan-out shared by `create_match`/`update_match`/`submit_match_request`: loads relations, computes the exclude list, then enqueues the ack request + crew DM + tournament-subscriber DMs (+ stream-candidate DMs when flagged). Awaited by the caller; the individual sub-notifications run on the queue. |
| `notify_stream_candidate(match)` | `None` | Standalone stream-candidate fan-out for `set_stream_candidate` (fetch + collect exclude + enqueue the subscriber DMs). |

The per-recipient `notify_*` methods are designed to run **inside** the `discord_queue` worker: callers enqueue them rather than awaiting. DM recipients need a `discord_id`; the `User.dm_notifications` (Discord DMs) switch is enforced inside `send_dm`, so devices still get the mirror. The module is split to stay under the file-length guideline, all parts mixed into `MatchScheduleService`: `match_schedule_service.py` holds the lifecycle transitions and `generate_seed`, `match/_schedule_notifications.py` the `notify_*` methods, `match/_match_recipients.py` the recipient resolution (`collect_match_recipients`), and `match/_dm_context.py` the lazy bracket-line / community-name lookups a DM's subtitle needs. `confirm_match` also refuses a match with no recorded result (`match_status.has_recorded_result`).

Collaborators: `MatchRepository`, `MatchAcknowledgmentRepository`, `TournamentNotificationRepository`, `DiscordService`, `SeedGenerationService`, `AuditService`, `discord_queue`.

### match_service.py — MatchService

Match CRUD with full notification fan-out, schedule queries, station/stage assignment, results, and player acknowledgments. The seat → start → finish → confirm transitions belong to [`MatchScheduleService`](#match_schedule_servicepy--matchscheduleservice) alone; table display formatting lives in [`MatchDisplayService`](#match_display_servicepy--matchdisplayservice) and crew signup/undo in [`CrewService`](#crew_servicepy--crewservice). The schedule-query methods below delegate to `MatchRepository`.

| Method | Returns | Description |
|---|---|---|
| `get_all_matches_for_schedule()` | `list[Match]` | Every match with relations prefetched, ordered by `scheduled_at` (public schedule tab). Delegates to `MatchRepository.get_all_for_schedule`. |
| `get_matches_for_date(target_date, exclude_finished=True, require_stage=True)` | `list[Match]` | A day's matches with players/crew prefetched (stage timeline). Delegates to `MatchRepository.get_for_date`. |
| `group_matches_by_stage(matches)` | `dict[int, (Stage, list[Match])]` | Group prefetched matches by stage. |
| `get_matches_for_player(discord_id)` | `list[Match]` | Matches where the Discord user is a player. Delegates to `MatchRepository.get_for_player`. |
| `viewer_relation(match_id, user)` | `str` | `RELATION_PLAYER` / `_CREW` (any signup) / `_WATCHER` / `_NONE` — what a deep-linked match is to the viewer, so `?match=` narrows only a player's board and explains itself to everyone else. |
| `create_match(tournament_id, scheduled_date, scheduled_time, player_ids, comment=None, stage_id=None, commentator_ids=None, tracker_ids=None, is_stream_candidate=False, title=None, actor=None)` | `Match` | Admin match creation — see flow below. Staff or TA of the target tournament. `title` replaces the Discord event title and names the racetime room, so it must be self-describing (bracket games pass one). |
| `update_match(match_id, *, tournament_id=None, scheduled_date=None, scheduled_time=None, scheduled_at=None, player_ids=None, commentator_ids=None, tracker_ids=None, comment=None, clear_seated/clear_started/clear_finished/clear_confirmed/clear_seed=False, actor=None)` | `Match` | Partial update; `scheduled_at` (a resolved UTC instant, used by approved reschedule requests) wins over the date/time strings; syncs player/crew lists; can clear lifecycle timestamps and the seed; audits `match.updated`; re-runs acknowledgment + notification fan-out when the time or player set changed. Gated by `can_crud_match`. |
| `submit_match_request(tournament_id, scheduled_date, scheduled_time, player_ids, actor, comment=None, *, title=None, from_bracket=False)` | `Match` | Player-initiated creation: the actor must be one of the players (`PermissionError` otherwise) — bypasses the TA/Staff gate without granting other powers. Refuses a tournament whose `allow_player_match_requests` is off (`assert_player_requests_allowed`), since a bracket-run tournament schedules only its own matchups; `from_bracket=True` skips that check and is set **only** by `BracketService.schedule_bracket_match` / `ChallongeService.schedule_challonge_match`, never from a request body. Audits `match.requested` and runs the same acknowledgment/notification fan-out as `create_match`. Lives in `match/match_request.py` (`MatchRequestMixin`). |
| `set_stream_candidate(match_id, flag, actor=None)` | `Match` | Toggle `is_stream_candidate`; gated by `can_assign_match_stream`; notifies stream-candidate subscribers on a false→true transition. |
| `assign_stage(match_id, stage_id, actor=None)` | `Match` | Assign or clear (`None`) the match's stage; gated by `can_assign_match_stream`; audits assigned/cleared variants. Enqueues the stage DM to the players, approved crew and watchers (both branches — the clear is a retraction), and nulls `stage_reminder_sent_at` so the pre-match reminder re-arms. |
| `assign_stations(match_id, assignments, actor=None)` | `Match` | Set `MatchPlayers.assigned_station` from a `{match_player_id: station}` mapping; gated by `can_run_match`; rejected for racetime.gg tournaments (on-site-only — players race remotely). Validation ladder, most specific first: **(1)** the same station twice in one match, **(2)** the `StationFormat` regex, **(3)** a label outside the tenant's `Station` pool *once that pool is non-empty*, **(4)** a station already in use by another seated-and-unfinished match, and **(5)** a key that is not one of this match's player-row ids — the near-miss is a `User` id, and applying only the keys that happened to match would report success for a write that changed nothing. Each raises `ValueError`. Lives in [match_stations.py](../../application/services/match/match_stations.py) (`StationAssignmentMixin`). |
| `suggest_stations(match_id, actor=None)` | `StationSuggestion` | Draw a free station for each of the match's two players — different `Station.side` where possible, preferring a station whose neighbours (same `side`+`section`, `position` ±1) are not in use, `secrets.choice` within the surviving tier. **Reads only**: the caller applies the result via `assign_stations`, so there is no audit row or event here. Returns `assignments` (`{match_player_id: label}`) plus `relaxations`, a list of user-facing sentences naming each rule it could not honour (one side full, no sides recorded, room too busy to avoid a neighbour). Raises `ValueError` for a match that does not have exactly two players, an empty station pool, fewer than two free stations, or a racetime.gg tournament. Gated by `can_run_match`. Lives in [match_station_draw.py](../../application/services/match/match_station_draw.py) (`StationDrawMixin`). |
| `occupied_stations_for_dialog(match_id)` | `dict[str, int]` | `{station label: match id}` for stations in play, excluding this match — the read-through the station picker uses so presentation never touches `MatchRepository`. Lives in `match_stations.py` alongside `assign_stations`. |
| `ensure_players_enrolled(tournament_id, player_ids, actor)` | `list[User]` | Enroll any of the given users not yet in the tournament (`ValueError` on unknown id); `actor` is required because each enrolment is audited and announced. Returns the users it enrolled so the caller can report the side effect. |
| `assert_within_tournament_hours(scheduled_at, tournament_id)` | `None` | `ValueError` when a UTC start falls outside that date's operating window (the tournament's own per-day hours, else the tenant-wide setting), evaluated on the community's clock. |
| `delete_match(match_id, actor=None)` | `None` | Delete; gated by `can_crud_match`; audits `match.deleted`. Notifies nobody — the silent "this shouldn't exist" path. |
| `cancel_match(match_id, actor=None, *, reason='')` | `None` | Call a real match off: DMs players + crew, cancels any live racetime room, audits/emits `match.cancelled`, then deletes (so `match.deleted` still fires). Gated by `can_crud_match`; the un-gated `_cancel_match` is what a clinched best-of-N series calls as the system user. Lives in [match_cancellation.py](../../application/services/match/match_cancellation.py) (`CancellationMixin`). |
| `get_match_by_id(match_id)` / `get_by_id(...)` / `get_match_players(match)` / `get_player_names(match_id)` / `list_acknowledgments(match)` | reads | Load-or-`None` fetches (`get_by_id` optionally skips `prefetch_relations`) and the participant/acknowledgment reads presentation and the entry surfaces use instead of reaching into repositories. The first four, plus the schedule queries and the stage grouping above, live in [match_reads.py](../../application/services/match/match_reads.py) (`MatchReadsMixin`). |
| `record_match_result(match_id, winner_id, actor)` | `Match` | Two-player results: winner gets `finish_rank` 1, the other 2. `winner_id` is a **`MatchPlayers` row id**, not a User id. Gated by `can_run_match`. |
| `rewind_match(match_id, to_step, *, actor)` / `clear_match_seed(match_id, *, actor)` | `Match` | The Edit Match ⋮ actions. `rewind_match` takes the match back to a step it has passed and clears every later timestamp together (step order and labels in [match_rewind.py](../../application/services/match/match_rewind.py)); `clear_match_seed` drops a rolled seed. Both delegate to `update_match`, so auth, audit and events are the same. |
| `acknowledge_match(match_id, user)` | `MatchAcknowledgment` | Player confirms they have seen their match. Only current players may acknowledge; double-acknowledge raises `ValueError`. Audits `match.acknowledged`. Lives in [match_acknowledgment.py](../../application/services/match/match_acknowledgment.py) (`MatchAcknowledgmentMixin`) with `list_acknowledgments` and the seeding path. |
| `flag_for_review(match_id, actor)` | `Match` | Raise the dispute flag on a recorded result — "an admin should look at this before confirming". Gated by `can_run_match` (the proctor's own call); `ValueError` unless the match is finished. Carries no note: what happened is a conversation, and the audit row is the record it happened. Audits + emits `match.flagged_for_review`. Lives in [match_review.py](../../application/services/match/match_review.py) (`MatchReviewMixin`). |
| `clear_review(match_id, actor)` | `Match` | Drop the flag **without** confirming. Gated by `can_confirm_match`, which excludes PROCTOR — resolving a dispute is the admin's. Audits + emits `match.review_cleared`. `MatchScheduleService.confirm_match` does the same clear implicitly. Same mixin. |

**Creation/reschedule flow.** `create_match` resolves every referenced user up front (so a bad id cannot leave an orphan `Match` row), creates the row, enrolls players in the tournament if needed, attaches commentators/trackers as pre-approved, audits `match.created`, then seeds per-player acknowledgment rows (the actor auto-acknowledges their own). Finally it hands the whole scheduled-notification fan-out to `MatchScheduleService.notify_match_scheduled` (ack request + crew DM + subscriber fan-out, plus stream-candidate when flagged). `update_match` re-seeds acknowledgments when `scheduled_at` or the player set changed; on a time change it calls `notify_match_scheduled(rescheduled=True)`, otherwise it enqueues just the acknowledgment request.

Collaborators: `MatchRepository`, `MatchAcknowledgmentRepository`, `TournamentRepository`, `UserRepository`, `CommentatorRepository`, `TrackerRepository`, `AuditService`, `MatchScheduleService` (lifecycle notify + `notify_match_scheduled`/`notify_stream_candidate`), `AuthService`, `discord_queue`.

**Guard modules.** Three pure, single-function modules hold the rules `MatchService` must not be able to skip. Each is the *enforcement*: the UI also hides or disables the thing, but the REST routers reach the service directly, so a gap here would ship a silently-wrong match.

| Module | Function | Raises | Called by |
|---|---|---|---|
| `match_request_guard.py` | `assert_player_requests_allowed(tournament)` | `PermissionError` when `Tournament.allow_player_match_requests` is off — the flag `BracketService.create_bracket` and `ChallongeService.link_tournament` turn off, because a bracket-run tournament schedules only the matchups its bracket produced | `submit_match_request` (skipped for `from_bracket=True`) |
| `match_source_guard.py` | `assert_sg_fields_unchanged(match, *, tournament_id, scheduled_date, scheduled_time, players_changed, scheduled_at=None)` | `ValueError` when a staff edit would change an ETL-owned field (schedule, players, tournament) on an SG-sourced match (`speedgaming_episode_id` set). Compared by *value*, so the dialog can resubmit disabled fields unchanged; a no-op for non-sourced matches | `update_match` |
| `bracket_result_guard.py` | `assert_bracket_result_editable(game)` | `ValueError` when the `Match` backs a `COMPLETE` `BracketMatchGame` — the bracket already advanced on that result, so rewriting the ranks would strand the series count and downstream slots. The message names the stage and points staff at **Results → Override**, which re-advances properly. A no-op with no game, or a `SCHEDULED` one | `record_match_result`, reading the game via `BracketService.get_game_for_match` |

**match_status.py** — the **one derived status vocabulary** shared by the schedule table, the bracket cards, the REST bracket payloads, and the Discord embeds. Pure and ORM-free. `MatchStatus` has nine members (`PENDING`, `UNSCHEDULED`, `SCHEDULED`, `CHECKED_IN`, `LIVE`, `AWAITING_RESULT`, `COMPLETE`, `CANCELLED`, `NEEDS_RESCHEDULE`); `resolve(*, match, game_state=None, bracket_match_state=None, room_status=None)` projects a `Match` onto it — a settled/cancelled **game** state outranks the timestamps, the timestamps are read newest-first, and the racetime room is a **tiebreaker only** (an `IN_PROGRESS` room with no `started_at` still reads `LIVE`). `resolve_matchup` folds a series' game statuses into one. **Derived, never stored** — the timestamps stay the source of truth. `label`/`tone` give a status's display label and semantic colour, `is_live` tests a set of statuses, `has_recorded_result(players)` is the one "is a result on the board" predicate (`confirm_match` and the display rows share it), and `legacy_label` adapts a status back to the schedule table's five historical strings; `STATUS_TONES` names the colour semantically once, mapped to Quasar in `theme/` and to `COLOR_*` ints in `discord_embeds.py`. Detail: [brackets.md](../features/brackets.md#presentation).

**match_participants.py** — `MatchParticipants`, the participant-row orchestration split out of `MatchService`. Pure repository plumbing, no audit or events: `resolve_users` (id list → `User`s in one query, `ValueError` on the first missing id), `ensure_enrolled`, `sync_players`/`sync_crew` (reconcile player and commentator/tracker rows to a target id set), and the ack-row pair: `seed_acknowledgments` (reset and re-create, auto-acking the actor — destructive by design, because an admin rewriting the roster is restarting the question) and `reconcile_acknowledgments` (create the missing, drop the stale, **keep the answers already given** — what the SpeedGaming sync needs, since it runs over the same match every poll). `MatchService` composes it lazily via a `participants` property, so create/update/request share one roster path.

A player with **no** ack row is invisible to the whole acknowledgment surface — no icon on the board, no Acknowledge button (gated on the row existing), and an admin dialog that reports nobody assigned — which is what a sourced match used to look like.

### match_suggestion_service.py — MatchSuggestionService

Offers the earliest 30-minute candidate slot that fits both the players and the room. A slot is ineligible if a player has **blocked it out** (availability is opt-out, so a slot no window covers is eligible and a player who entered nothing constrains nothing), if any of the players already has a match overlapping it, or if adding the match would take the room past `max_concurrent_players` (default 60) at any moment of the slot. Of the eligible slots, the earliest one that keeps the room within `_HEADROOM` (75%) of the cap wins; only when none does is the room filled up to the cap. A PREFERRED window can beat an earlier slot on the same day, never an earlier day. This fills the room early instead of spreading evenly: a bulk simulation against the SGL26 setup (Oct 2026) showed even spreading pushed group matches to the last night and left later rounds no slot. Searches the next 4 hours within the configured tournament hours first, then the full remaining event window. `exclude_match_id` drops the match being rescheduled from the busy set. Capacity is never surfaced to callers — the suggestion appears as a neutral "best time." A bracket matchup narrows both searches to its round's `scheduled_at`/`scheduled_end` window, as a **hard bound** — only slots the match fits inside end to end are offered, and an absent or half-configured window bounds only the side that is set. Module constants: `_SLOT_INTERVAL_MIN = 30`, `_PRIMARY_WINDOW_HOURS = 4`, `_HEADROOM = 0.75`, `_DEFAULT_CAPACITY = 60`.

| Method | Returns | Description |
|---|---|---|
| `suggest_match_time(tournament_id, player_ids, bracket_match_id=None)` | `datetime` | Best UTC start time for the match. `bracket_match_id` confines the search to that matchup's round window. Raises `ValueError` when no eligible slot exists within the event schedule (or, when given, the round window — the message names which). |
| `find_clashes(tournament_id, player_ids, scheduled_at, *, actor, exclude_match_id=None)` | `list[Clash]` | Why a chosen time might not work, each a `Clash(kind, message)` (`ClashKind`): a player already playing or on approved crew then, a player's published volunteer shift (when `VOLUNTEERS` is live) or blocked-out time, the room past `max_concurrent_players`, the match running past closing, a day with no hours, or a date outside the event. **Advisory**: `theme/dialog/clash_warning.py` shows them on a "Check this time" sheet before every booking dialog saves, and the person can save anyway. Shifts and blocked-out times are spelled out only to the player themselves, Staff and tournament admins; anyone else gets "isn't free then". Same permission gate as `suggest_match_time`. |

Collaborators: `PlayerAvailabilityRepository`, `BracketRepository` (`get_match_with_bracket`, for the round window), `VolunteerAssignmentRepository` and `UserRepository` (for `find_clashes`), `SystemConfigService` (`get_tournament_hours`, `get_event_window`), [`timezone.py`](#timezonepy), queries `Match`/`Tournament` directly.

### match_watcher_service.py — MatchWatcherService

Opt-in "watch this match" subscriptions: watchers receive lifecycle DMs without being players or crew. The DM fan-out itself happens in `MatchScheduleService`. Feature doc: [match-participation.md](../features/match-participation.md).

| Method | Returns | Description |
|---|---|---|
| `watch(match_id, user)` | `MatchWatcher` | Subscribe (idempotent via get-or-create). Rejects unknown and already-confirmed matches (`ValueError`); audits `match.watcher_added` only on creation. |
| `unwatch(match_id, user)` | `bool` | Unsubscribe; returns whether a row was removed; audits `match.watcher_removed` when it was. |
| `is_watching(match_id, user)` / `list_watched_match_ids(user)` | `bool` / `list[int]` | Whether the user watches one match; every match id they watch (marks rows in the schedule table). |

Collaborators: `MatchWatcherRepository`, `MatchRepository`, `AuditService`.

### match_comment_service.py — MatchCommentService

Signed comments on a match. The kind is the audience; "staff" is
`AuthService.can_run_match`. Feature doc:
[match-participation.md](../features/match-participation.md#match-comments).

| Method | Returns | Description |
|---|---|---|
| `add_player_comment(match_id, user, body)` | `MatchComment` | A player's comment to staff. `ValueError` for a non-player or an empty/over-long body. |
| `add_staff_comment(match_id, user, body, *, to_players)` | `MatchComment` | A private note, or a message the players read. `to_players` DMs each player with `dm_match_comments` on (never the author) via `_comment_notifications.notify_match_comment`. |
| `delete_comment(comment_id, user)` | `None` | Author or staff only. |
| `list_for_viewer(match_id, user)` | `list[dict]` | Display dicts (`id`, `kind`, `body`, `author`, `own`, `when`, `can_delete`): everything for staff; own player comments plus staff messages for a player; nothing for anyone else. |
| `counts_by_match(match_ids)` | `dict[int, dict]` | `{match_id: {'player': n, 'staff': n}}` for a staff board. Includes private notes, so callers gate it on `access.run`. |

Add and delete audit + emit `match.comment_added` / `match.comment_deleted` (payload carries `kind`; add also carries `body`) and nudge `match_live`.

Collaborators: `MatchCommentRepository`, `MatchRepository`, `AuditService`.

### match_stream_volunteer_service.py — MatchStreamVolunteerService

Players offering their **own** match for stream. Advisory throughout: it never
writes `Match.is_stream_candidate`, which stays the decision of whoever
`can_assign_match_stream` admits. Feature doc:
[match-participation.md](../features/match-participation.md#stream-volunteering).

| Method | Returns | Description |
|---|---|---|
| `volunteer(match_id, user)` | `MatchStreamVolunteer` | Offer the match (idempotent via get-or-create). `ValueError` for a non-player (`'Only the players in a match can offer it for stream.'`) or a match that has already started. Audits + emits `match.stream_volunteered` on creation. |
| `withdraw(match_id, user)` | `bool` | Take the offer back; returns whether a row was removed; audits + emits `match.stream_volunteer_withdrawn` when it was. Deliberately **allowed after the match starts** — a player who changes their mind must not be stuck with a request they cannot unsay. |
| `has_volunteered(match_id, user)` / `list_volunteered_match_ids(user)` | `bool` / `list[int]` | This viewer's own offer state; the id list marks rows in the schedule table. |
| `names_by_match(match_ids)` / `names_for_match(match)` | `dict[int, list[str]]` / `list[str]` | Who offered, for staff. The batched form is one query for a whole board — `MatchDisplayService` calls it once per refresh and puts `stream_volunteers` on each row. |

Collaborators: `MatchStreamVolunteerRepository`, `MatchRepository`, `AuditService`.

### match_hard_preset_service.py — MatchHardPresetService

Players privately opting into a tournament's harder preset. A match rolls
`Tournament.hard_preset` only when **every** current player has opted in, and
until then no player can learn whether anyone else did — the secrecy is the
feature, not a detail of it. Feature doc:
[match-participation.md](../features/match-participation.md#harder-settings-opt-in).

Three consequences shape the surface below:

- `my_state` / `board_states` are the **only** reads a page, router or bot may
  call, and both answer about the caller alone. `everyone_in` is computed only
  for a caller who opted in themselves, so it can never become a way to ask
  about the opponent.
- The individual opt-in is **audited but not published**. A webhook subscriber
  is an arbitrary outside listener; the audit log is staff reading their own
  community's history. Only the unanimous result reaches the event bus.
- Agreement is **derived**, never stored, so a roster change re-answers it.

| Method | Returns | Description |
|---|---|---|
| `opt_in(match_id, user)` / `withdraw(match_id, user)` | `HardPresetState` | The player's own choice (idempotent). `ValueError` for a non-player, a tournament with no hard preset, a rolled seed, or a match staff have overridden. Audits `match.hard_preset_opted_in` / `_withdrawn`; publishes nothing. |
| `my_state(match, user)` | `HardPresetState` | What this viewer may know about one match. A non-player (staff, spectator) gets the tournament's shape and the override, never anybody's opt-in. |
| `board_states(user, match_ids)` | `dict[int, HardPresetState]` | The same answer for a whole board in three queries. Omits matches this viewer does not play in, and matches whose tournament offers no hard preset. |
| `resolve_preset(match)` | `Preset \| None` | Which preset this match rolls: staff's override, else the hard preset when everyone opted in, else the tournament's standard one. Called by `generate_seed` at roll time — the one moment the answer matters, and the moment the window shuts. |
| `set_override(match_id, override, actor)` | `Match` | Staff choosing the match's preset themselves, or handing it back (`None`). Refused after the seed is rolled. Keeps existing opt-ins, so clearing restores what the players chose. Audits + emits `match.preset_override_set` / `_cleared`, and DMs the players. |
| `send_offer(match)` | `None` | The invitation DM, enqueued from the scheduling fan-out. Each recipient's button carries their own next move, so nothing about anyone else's answer can be read off it. Skipped once the seed exists. |
| `snapshot(match)` | `HardPresetSnapshot` | The opt-in situation as it stands, captured *before* an edit. Must be taken before the write: afterwards a swapped-in player has no row and a reassigned match no longer knows which tournament's preset was agreed to. |
| `reconcile_edit(match, before, actor)` | `None` | Re-answers the agreement after the write. A roster change drops departing players' rows and announces a crossing of the unanimity line in either direction; a move to another tournament (or a changed `hard_preset`) discards every opt-in, telling any agreement's players in the old tournament's words. |

`HardPresetState` is a frozen dataclass projection (`offered`, `opted_in`,
`everyone_in`, `locked`, `override`, `preset_name`, `standard_preset_name`),
deliberately not a model or queryset — a surface handed rows could render
somebody else's, and the leak would be one template edit away.

Collaborators: `MatchHardPresetRepository`, `MatchRepository`, `AuditService`,
`_hard_preset_notifications`.

### match_reschedule_service.py — MatchRescheduleService

Players asking staff to move or call off a match they are playing in. Two rules
hold the design together: **approving performs the change** (it calls the
existing `MatchService.update_match` / `cancel_match`, so the DM fan-out,
acknowledgment reseeding and `match.rescheduled` event all come with it), and
**no new authority** — deciding gates on `can_crud_match`, the same check that
guards `update_match`. Feature doc:
[match-participation.md](../features/match-participation.md#reschedule-requests).

| Method | Returns | Description |
|---|---|---|
| `submit(match_id, actor, *, reason, kind=RescheduleRequestKind.RESCHEDULE, proposed_at=None)` | `MatchRescheduleRequest` | Raise a request. `PermissionError` for a non-player, or a tournament with `allow_reschedule_requests` off. `ValueError` for a match already under way, one with no time, a SpeedGaming-sourced match (its time belongs to the next sync, so nobody could approve it), a blank reason, a past proposal, or a second open request by the same player. Validates the proposal against tournament hours *now*, so the player is told rather than staff discovering it at Approve. Audits + emits `match.reschedule_requested`; DMs staff and the opponent. |
| `record_opponent_agreement(request_id, actor)` | `MatchRescheduleRequest` | The match's *other* player agreeing. Advisory: stamped on the request, shown to staff, gates nothing. `PermissionError` for the requester themselves or an outsider; `ValueError` unless the match has exactly two players. Called by the Discord Agree button, the REST route and My Schedule. |
| `list_awaiting_agreement(actor)` | `list[MatchRescheduleRequest]` | Pending requests on two-player matches where `actor` is the other player, agreed or not — the web Agree card. |
| `agreement_link_state(request_id, actor)` | `str` | `AGREE_OPEN` / `AGREE_DONE` / `AGREE_DECIDED` / `AGREE_NOT_YOURS` (also for a match without exactly two players), for the `?agree=` deep link's stale notice. |
| `withdraw(request_id, actor)` | `MatchRescheduleRequest` | The requester taking it back. Distinct from a decline so staff can tell a request they refused from one that stopped mattering. |
| `approve(request_id, actor, *, scheduled_at=None, note=None)` | `MatchRescheduleRequest` | Grant it by making the change. `scheduled_at` lets staff counter with a different time; required when the request named none. Perform-then-record, so a move that fails never leaves a request marked approved over a schedule that did not budge. Other open requests on the match become `SUPERSEDED`. |
| `decline(request_id, actor, note)` | `MatchRescheduleRequest` | Refuse it. The note is **required** — a refusal with no reason is what this feature replaces. DMs the requester with an "Ask again" button. |
| `can_request(match, user)` / `list_requestable_match_ids(user)` | `bool` / `set[int]` | The UI's gate, so the control is hidden rather than shown and refused. The bulk form resolves a whole board in two queries; `can_request` per row would run three each. |
| `list_pending(actor=None, *, tournament_ids=None)` | `list[MatchRescheduleRequest]` | The staff queue. Gated on `can_view_admin` — the reason text is not schedule data — and narrowed by `tournament_ids` so a tournament admin is never shown a request they would be refused for deciding. |
| `list_mine(actor, limit=25)` / `list_for_match(match_id, actor=None)` | `list[MatchRescheduleRequest]` | A player's own history (no gate beyond being the actor), and one match's requests (whoever could decide them, or a player in the match). |
| `pending_count()` / `pending_match_ids()` / `pending_match_ids_for_user(user)` | `int` / `list[int]` | The admin strip's count and filter, and the board's per-row "Asked" mark. |
| `get_by_id(request_id)` | `MatchRescheduleRequest \| None` | Read-only lookup (the deep-linked decision dialog). |

Every mutation also publishes on `match_live` so open boards update in place —
`approve` reaches it through `update_match` / `cancel_match`, the other three
publish their own. That is the UI nudge channel, distinct from the `EventType`
domain events these methods emit for webhooks.

The DM fan-out lives in `application/services/_reschedule_notifications.py`
(`RescheduleNotificationMixin`), mixed into the service the way
`_schedule_notifications` is mixed into `MatchScheduleService`. Two audience
rules live there rather than at the call sites: the deciders are STAFF ∪ the
tournament's own admins (DMing only STAFF left a TA-run tournament with nobody
told), and an approval that *moved* the match sends nothing back, because
`update_match` has already fanned the reschedule out to both players.

The submission DM goes to **STAFF ∪ the tournament's admins**, matching the
`can_crud_match` set that will decide it — DMing only STAFF meant that in a
community whose tournament is run by a TA, nobody who owned it was told.

Only a **decline** and an approved **cancellation** DM the requester: an
approved reschedule already reached both players through `update_match`'s own
notification, and a second "your request was approved" would be the same news
twice.

Collaborators: `RescheduleRequestRepository`, `MatchRepository`,
`TournamentRepository`, `AuditService`, and `MatchService` service-to-service.

### player_availability_service.py — PlayerAvailabilityService

Self-service availability for any logged-in player — unlike volunteer availability there is no role gate. A user's windows are replaced wholesale on each save. Windows carry a `VolunteerAvailabilityStatus` (`AVAILABLE`/`PREFERRED`/`UNAVAILABLE`). Audited under `player_availability.*`.

**Read opt-out, and that is the one thing that differs from the volunteer service.** A player is available for any stretch no window covers, so the windows they save are the times they *cannot* play (`UNAVAILABLE`) or would rather (`PREFERRED`); an `AVAILABLE` window states the default and constrains nothing (the player editor does not offer it). Both static readers pass `DEFAULT_STATUS = AVAILABLE` as the `default` argument to `availability_windows`; the volunteer service passes nothing, keeping `None` for "not declared".

| Method | Returns | Description |
|---|---|---|
| `availability_for(user)` | `list[PlayerAvailability]` | The user's declared windows. |
| `set_windows(user, windows)` | `list[PlayerAvailability]` | Replace the user's availability with `(starts_at, ends_at, status, note)` tuples (each must end after it starts); transactional. Audits `player_availability.updated`. |
| `clear(user)` | `None` | Delete all of the user's windows; audits `player_availability.updated` with `window_count: 0`. |
| `availability_map(user_ids, start, end)` | `dict[int, list[PlayerAvailability]]` | user_id → windows overlapping `[start, end]` (batch lookup). |
| `covers(windows, start, end)` (static) | `VolunteerAvailabilityStatus` | Delegate to [`availability_windows`](#package-layout) with `default=AVAILABLE`: an overlapping `UNAVAILABLE` wins; else `PREFERRED` beats `AVAILABLE`; **`AVAILABLE` when nothing overlaps**. Never `None`. |
| `effective_segments(windows, start, end)` (static) | `list[(datetime, datetime, status)]` | Delegate to `availability_windows` with the same default: split `[start, end]` into maximal constant-availability segments (resolved by `covers` precedence, adjacent equal segments merged); an uncovered stretch is `AVAILABLE`, not `None`. |

Collaborators: `PlayerAvailabilityRepository`, `AuditService`, `availability_windows`.

### reports_service.py — ReportsService

Aggregation queries behind the admin Reports tabs. Day boundaries resolve on the viewer's display clock via [`application/utils/timezone.py`](../../application/utils/timezone.py); each match contributes an estimated active window (`seated_at` or scheduled−1h → `finished_at` or start+expected duration, default 90 min). On the capacity forecast and its "active at" drill-down, a late match with no check-in keeps sliding: its end is anchored at now, up to `LATE_PUSH_LIMIT` (2h) past schedule, and a late check-in anchors at `seated_at`. The duration and on-time thresholds are imported from `reporting_shared`. Feature doc: [admin-reports.md](../features/admin-reports.md).

| Method | Returns | Description |
|---|---|---|
| `generate_capacity_forecast(start, end, tournament_id=None)` | `dict` | Concurrent-player counts (total and on-stream) sampled at auto-sized intervals (15/30/60 min by span), plus active match ids per interval and the configured `max_capacity`. |
| `peak_times(intervals, counts, top_n=5)` (static) | `list[(datetime, int)]` | Top-N busiest sampled instants from a forecast. |
| `matches_active_at(instant, tournament_id=None)` | `list[Match]` | Matches whose estimated window covers the instant (drill-down for forecast points). |
| `match_operations(start, end, tournament_id=None)` | `dict` | Per-match rows (start delay, duration, confirmation lag) and per-tournament aggregates (averages, on-time % within ±5 min). |
| `crew_coverage(start, end, tournament_id=None, user_id=None, approved_only=False)` | `dict` | Per-match crew slot coverage (flagging stream candidates whose approved crew falls short of the tournament's `required_commentators`/`required_trackers`, via `reporting_shared.is_crew_covered`) and per-user contribution rows with covered hours. |
| `stage_utilization(start, end, tournament_id=None, stage_id=None)` | `dict` | Per-stage scheduled hours, back-to-back counts (<15 min gaps), idle gap hours, plus unplaced stream-candidate matches. |

**Module-level helper:** `event_day_bounds(d) -> (datetime, datetime)` — midnight-to-next-midnight UTC bounds for a date on the display clock.

Collaborators: `SystemConfigService` (capacity limit); queries `Match`/`Stage` ORM directly (read-only aggregation).

### analytics_service.py — AnalyticsService

Longitudinal / cross-event analytics behind the admin Reports → **Insights & Trends** page — the trend counterpart to `ReportsService`'s point-in-time snapshots. Events are bucketed by the local calendar date of when they happened (`week` = Monday-anchored, `month` = 1st-anchored) into contiguous buckets (capped at `MAX_BUCKETS = 240`). Module constants: `HEALTH_WEIGHTS` (completion 0.30, on-time 0.25, coverage 0.25, duration 0.20) and `MAX_BUCKETS = 240`; `ON_TIME_THRESHOLD_MIN` is imported from `reporting_shared`.

| Method | Returns | Description |
|---|---|---|
| `crew_participation_trends(start, end, bucket='week', tournament_id=None)` | `dict` | Per-bucket commentator/tracker signups & approvals and unique-people counts (bucketed by match `scheduled_at`), plus top-15 contributors and window totals. |
| `volunteer_hour_trends(start, end, bucket='week')` | `dict` | Per-bucket scheduled vs checked-in volunteer-hours, needed-hours and fill-rate %, a per-position breakdown, and top-15 volunteers (bucketed by shift `starts_at`). |
| `tournament_health(start, end, tournament_id=None)` | `dict` | Per-tournament scorecards (optionally one tournament): completion % (past matches only), on-time %, crew coverage % (against the tournament's own crew requirement — same `is_crew_covered` the crew report uses), avg duration vs expected, and a composite 0–100 `health_score`. |
| `activity_trends(start, end, bucket='week')` | `dict` | Per-bucket audit-log volume grouped by action namespace (the `verb.object` prefix). |
| `activity_extent(tournament_id=None)` | `(date \| None, date \| None)` | Earliest and latest **local dates** with any activity the four trends above read, so the page can open on a window that has data in it. `(None, None)` for a community with no history. A `tournament_id` narrows it to the tournament-scoped sources only — shifts and audit logs carry no tournament, so including them would widen the "narrowed" window back out. |

**Pure helpers (unit-tested without a DB):** `bucket_start`, `iter_bucket_starts`, `bucket_label`, `_bucket_index`, `health_score` (weighted average of present `(value, weight)` components, renormalized so missing dimensions don't dilute the score; `None` when no component has data), `_finalize_health`, `_duration_hours`.

Collaborators: queries `Match`/`Commentator`/`Tracker`/`VolunteerShift`/`VolunteerAssignment`/`AuditLog` ORM directly (read-only aggregation, same pattern as `ReportsService`); `now_local` for the past-match cutoff.

### seedgen_service.py — SeedGenerationService

Generates randomizer seeds from the presets in `presets/`. Deep dive (per-randomizer details, presets, per-tenant credentials): [seed-generation.md](seed-generation.md).

| Member | Returns | Description |
|---|---|---|
| `AVAILABLE_RANDOMIZERS` (class attr) | `list[str]` | Supported generator keys: `alttpr`, `avianart`, `ff1r`, `z1r`, `z2r`, `smb3r`, `aosr`, `sm`, `smz3`, `smmap`, `ootr`, `smdash`, `smvaria`, `dk64r`, `wwr`, `test`. Drives the tournament dialog dropdown and the `generate_seed` validity check. |
| `STUB_RANDOMIZERS` (class attr) | `set[str]` | Registered for selection but not wired to an upstream — rolling one raises `ValueError`. Empty today. |
| `PRESET_AWARE_RANDOMIZERS` (class attr) | `set[str]` | Generators that resolve the `Preset`'s settings (every key but `ootr`, `smmap` and `test`, which ignore it). |
| `MOCK_EXEMPT_RANDOMIZERS` (class attr) | `set[str]` | Randomizers `MOCK_SEEDGEN` leaves alone: `dk64r` (mocks its queue instead), `wwr`, `z1r`, `z2r`, `smb3r` and `aosr` (built locally, nothing upstream to fake). |
| `normalize_preset_settings(randomizer, settings)` (static) | `dict` | The per-randomizer check `PresetService` runs before saving. Passes most settings through; for `wwr` decodes the permalink, refuses one that isn't (`ValueError`), and returns `{"permalink": <trimmed>}`; FFR/AoSR links, flag strings, AVIANART and VARIA preset names and samus.link settings are checked the same way. |
| `describe_wwr_permalink(permalink)` (static) | `WWPermalinkInfo` | Build, exact version and headline settings summary (`None` for a build with no vendored tables) of a Wind Waker permalink, for the Presets tab. `ValueError` for a non-permalink. |
| `describe_ff1r_link(url)` (static) | `str` | The site version an FFR flags link pins (`4.9.7` from `4-9-7.finalfantasyrandomizer.com`, `latest` for the bare host), for the Presets tab. `ValueError` for a link `normalize_preset_settings` would refuse. |
| `example_flag_seed(randomizer, flags)` (static) / `rolls_from_flags(randomizer)` | `str` / `bool` | A sample `"<seed> - <flags>"` for the Presets tab (`z1r`, `z2r`, `smb3r`), and whether a randomizer rolls that way. `ValueError` for flags `normalize_preset_settings` would refuse. |
| `paste_instructions(randomizer, settings)` (static) | `str \| None` | The line above a pasted seed (WWR permalink, Z1R/Z2R/SMB3R seed and flags) in the seed DM; the WWR one names the build. `None` for link randomizers. |
| `TRIFORCE_TEXT_RANDOMIZERS` (class attr) / `supports_triforce_texts(generator)` | `set[str]` / `bool` | Generators that can embed community triforce texts (`alttpr`), and the predicate `AuthService.can_submit_triforce_text` gates on. |
| `ASYNC_RANDOMIZERS` (class attr) | `set[str]` | Task-queue backends (`dk64r`): submit now, collect minutes later. `MatchScheduleService.generate_seed` routes these through `ProviderTaskService` + `SeedRollService` instead of rolling inline. |
| `generate_seed_call(randomizer, preset=None, *, surface=None, triforce_text=None)` | `ProviderCall` | The provenance-carrying entry point (`triforce_text` becomes the end-game text of an ALTTPR roll; other backends ignore it): a `ProviderCall` (see [`seed_provider.py`](#additional-utilities)) wrapping a `RolledSeed` (URL + settings as sent) with attempt count and latency — what a `GeneratedSeeds` row records. Prefer it anywhere the roll is persisted. Provider failures raise a `SeedProviderError` subclass (a `ValueError`). |
| `submit_async_roll(randomizer, preset=None)` / `poll_async_roll(randomizer, provider_task_id, provider_params=None, settings=None)` | `AsyncRollSubmission` / `AsyncRollPoll` | The task-queue half: hand a roll upstream and return everything a later poll needs; check it **once** (`poll.seed` is `None` while still running, a failure raises `ValueError`). `ValueError` for a non-async randomizer. |
| `generate_seed(randomizer, preset=None)` | `str` | Thin wrapper over `generate_seed_call` returning just the URL. Dispatches to the named generator; returns the seed URL/string. ALTTPR uses `preset.settings` when a `Preset` is supplied (else the built-in `casualboots` settings); the other generators but these three are preset-aware; `ootr`, `smmap` and `test` ignore the preset and roll bundled settings. `ValueError` for unsupported keys. |
| `available_randomizers(configured)` (classmethod) | `list[str]` | `AVAILABLE_RANDOMIZERS` minus any randomizer whose declared credential this community has not set. Pure and DB-free; the caller passes `RandomizerCredentialService.configured_randomizers()`. |
| `REMOTE_PRESET_BRANCHES` (class attr) / `offers_remote_presets(randomizer)` / `remote_preset_branches(randomizer)` | `dict` / `bool` / `list[str]` | Randomizers that publish a preset catalogue over their API (`avianart`, and `dk64r` per branch), and the predicates the Presets tab's import dialog renders from. |
| `list_remote_presets(randomizer, *, branch=None)` | `list[RemotePreset]` | Fetch that catalogue, mapped into `RemotePreset(name, settings, description)` with settings already storable on a `Preset`. |

The DK64R backend (task queue, settings-string expansion, preset catalogue) lives in the private `_seedgen_dk64r.py` as the `DK64RBackend` mixin; AVIANART, samus.link (SM, SMZ3) and VARIA in `_seedgen_avianart.py`, `_seedgen_samus.py` and `_seedgen_varia.py`; the Wind Waker permalink codec (decode/encode/reseed, the seed-name port, settings decoding against the vendored `_wwr_data/<version>/` tables) in the private, pure-stdlib `_seedgen_wwr.py`; the value objects shared with everything that persists a roll (`RolledSeed`, `RemotePreset`, `AsyncRollSubmission`, `AsyncRollPoll`, `WWPermalinkInfo`) live in `_seedgen_types.py`.

Collaborators: `TriforceTextService` (text selection), `RandomizerCredentialService` (roll-time credential resolution — raises `MissingCredentialError` when this community has not set one), `pyz3r`/`aiohttp` for external randomizer APIs.

### matcherino_login_service.py — MatcherinoLoginService

The Matcherino account a community's check-in sync signs in as — the per-tenant successor to the `MATCHERINO_REFRESH_TOKEN` environment variable. Every public method carries `@requires_feature(FeatureFlag.EVENT_CHECK_IN)`; `status`/`set_login`/`clear_login` need `AuthService.can_manage_check_in` (Staff). Constructor takes an optional `client_factory` (defaults to `get_matcherino_client`) used to check a token before saving it.

| Member | Returns | Description |
|---|---|---|
| `status(actor)` | `MatcherinoLoginStatus` | `configured`, `matcherino_user_id`, `updated_at`, `updated_by` (display name). **Never the token.** |
| `set_login(actor, pasted)` | `MatcherinoLoginStatus` | Accepts the bare token or the whole `{"appName", "refreshToken"}` payload copied from DevTools (`normalize_refresh_token`). Rejects blank, whitespace or over-long input without calling Matcherino; otherwise signs in once (`MatcherinoClient.verify_login`) and saves only if Matcherino accepts it, recording the account id from the access token's `sub`. Audits `matcherino_login.set` with `replaced` and the account ids, never the token. |
| `clear_login(actor)` | `None` | Idempotent. Audits `matcherino_login.cleared`. |
| `alert_candidates(actor)` | `list[User]` | The community's Staff (system user excluded), by name: who may be picked to hear about a refused login. |
| `set_alert_user(actor, user_id)` | `MatcherinoLoginStatus` | Needs a saved login; `user_id` must be a candidate, or `None` for nobody. Leaves the "saved" time alone. Audits `matcherino_login.alerts_updated`. |
| `note_sync_result(error=None)` | `None` | The sync's report after each Matcherino read. `None` re-arms the alert; a `MatcherinoAuthError` claims it (`claim_alert`) and, if this call won and the recipient is still Staff with a Discord id, queues one DM (`discord_messages_check_in.matcherino_login_refused_dm`) with `notification_links.admin_matcherino_login()`. Other errors do nothing. Never raises. |
| `resolve()` | `str \| None` | The saved token, unmasked. Not role-gated: its only caller is `CheckInService`, whose boundary already authorized the actor. Never call it from anything that renders. |

No events, for the same reason as randomizer credentials. Collaborators: `MatcherinoLoginRepository`, `AuditService`, `AuthService`, `matcherino_client`.

### randomizer_credential_service.py — RandomizerCredentialService

A community's own credentials for the key-gated randomizer upstreams — the per-tenant successor to the `OOTR_API_KEY` / `SMMAP_SPOILER_TOKEN` / `DK64R_API_KEY` environment variables. Deep dive: [seed-generation.md](seed-generation.md#per-tenant-credentials).

| Member | Returns | Description |
|---|---|---|
| `list_status(actor)` | `list[dict]` | One row per declared `CredentialSpec` — `randomizer`, `key`, `label`, `help_text`, `configured`, `updated_at`. **Never the value.** |
| `set_credential(actor, randomizer, key, value)` | `None` | Validate against the registry, strip, reject blank, upsert. Audits `randomizer_credential.set` with the credential name only. |
| `clear_credential(actor, randomizer, key)` | `None` | Remove a stored credential; idempotent. Audits `randomizer_credential.cleared`. |
| `configured_randomizers()` | `set[str]` | Randomizers whose *every* declared credential this tenant has supplied — one query. Feeds `SeedGenerationService.available_randomizers`. |
| `resolve(randomizer, key)` | `str \| None` | The stored secret, unmasked. **Deliberately ungated** and the only path that returns a value: its sole caller is `SeedGenerationService` mid-roll, whose boundary already authorized the actor. Never call it from anything that renders. |

Mutations and `list_status` gated by `AuthService.can_manage_presets` (STAFF / `PRESET_MANAGER` / super-admin / system). No events — a community's key rotation is not for arbitrary webhook receivers. Collaborators: `RandomizerCredentialRepository`, `AuditService`, `AuthService`, `application.randomizer_credentials` (the spec registry).

### preset_service.py — PresetService

Tenant-authored seed-rolling presets (a named `randomizer` + `settings` blob). Deep dive: [seed-generation.md](seed-generation.md#presets-db-backed).

| Member | Returns | Description |
|---|---|---|
| `list_presets(actor)` / `list_selectable()` / `list_by_randomizer(randomizer)` | `list[Preset]` | The tenant's presets, gated by `AuthService.can_manage_presets`; and the ungated reads that populate the tournament dialog's Seed Preset select (all, or one randomizer's). |
| `get_preset(actor, preset_id)` | `Preset` | One preset, gated like `list_presets`; `NotFoundError` when missing. |
| `create_preset(actor, *, name, randomizer, settings, description=None)` | `Preset` | Validate (known randomizer, dict settings, unique `(randomizer, name)`) and insert. Audits `preset.created`. |
| `update_preset(actor, preset_id, *, name=None, randomizer=None, settings=None, description=None)` | `Preset` | Partial update with the same validation + uniqueness re-check. Audits `preset.updated`. |
| `delete_preset(actor, preset_id)` | `None` | Delete. Audits `preset.deleted`. Detaches linked tournaments (`SET_NULL`). |
| `test_roll(actor, preset_id)` | `PresetTestRoll` | Roll one seed from the preset through `SeedGenerationService.generate_seed_call(..., surface='preset_test')`, the same envelope a match roll uses, so staff can check the randomizer works. Stores no `GeneratedSeeds` row. A task-queue backend (DK64R) blocks until its seed is ready. Audits `preset.test_rolled` with `ok` plus `seed_url` or `error`, and re-raises a failure. `PresetTestRoll.used_preset_settings` is `False` for backends outside `PRESET_AWARE_RANDOMIZERS`, which roll their bundled settings. |
| `import_builtins(actor)` | `list[Preset]` | Import the committed `presets/` files as rows, idempotent by `(randomizer, name)`. Audits `preset.imported`. |
| `list_remote_presets(actor, randomizer, *, branch=None)` | `list[RemotePreset]` | Browse the presets the randomizer's own API publishes. Read-only; needs this tenant's credential for that randomizer. |
| `import_remote_presets(actor, randomizer, names, *, branch=None)` | `list[Preset]` | Save chosen upstream presets as rows. Takes names — the settings are re-fetched upstream, not taken from the caller. Idempotent by `(randomizer, name)`. Audits `preset.imported` with `source`/`branch`. |

All mutations gated by `AuthService.can_manage_presets` (STAFF / `PRESET_MANAGER` / super-admin / system). Collaborators: `PresetRepository`, `AuditService`, `AuthService`, `SeedGenerationService` (randomizer validity).

### station_service.py — StationService

CRUD for the venue's `Station` pool — the physical seats a proctor assigns
players to. Staff-only (`AuthService.is_staff`), audited under `station.*`, and
deliberately **not** feature-flagged: a community with no stations keeps the
historical free-text station field, so an empty pool is a valid steady state
rather than a setup step.

| Method | Returns | Description |
|---|---|---|
| `list_stations(active_only=False)` | `list[Station]` | The pool in `(sort_order, name)` order; `active_only` drops retired stations. |
| `get_station(station_id)` | `Station` | Load or raise `NotFoundError`. |
| `create_station(name, actor=None, *, section=None, side=None, position=None, sort_order=0)` | `Station` | Trims inputs; non-empty, tenant-unique name required and `position` must be ≥ 1 (`ValueError`). `side`/`section`/`position` are what the seating draw (`suggest_stations`) reads for opposite sides and neighbours. Audits `station.created`. |
| `update_station(station_id, actor=None, *, name=None, section=None, side=None, position=None, sort_order=None, is_active=None, clear_side=False, clear_position=False)` | `Station` | Partial update (`clear_side`/`clear_position` null those fields, since `None` means "unchanged"); rejects blanking or colliding the name. Audits `station.updated` with the changed field names. |
| `delete_station(station_id, actor=None)` | `None` | Delete and audit `station.deleted`. Historical `MatchPlayers.assigned_station` rows are untouched — the assignment stores the label, not an FK — so past matches keep showing where they were played. Prefer deactivating. |

Collaborators: `StationRepository`, `AuditService`, `AuthService`. Assigning a
player *to* a station is `MatchService.assign_stations`, not here; it validates
against this pool and against live occupancy
(`MatchService.occupied_stations_for_dialog` is the picker's read-through).

### stage_service.py — StageService

CRUD for `Stage` (stage) records. All mutations are gated by `AuthService.can_manage_stages` (Staff or Stream Manager) and audited under `stage.*`.

| Method | Returns | Description |
|---|---|---|
| `create_stage(name, stream_url=None, is_active=True, actor=None)` | `Stage` | Create; trims inputs; non-empty name required (`ValueError`). |
| `update_stage(stage, name=None, stream_url=None, is_active=None, actor=None)` | `Stage` | Partial update; rejects blanking the name. |
| `delete_stage(stage, actor=None)` | `None` | Delete and audit. |
| `get_all_stages(active_only=False)` / `get_stage_by_id(id)` | reads | All stages (optionally only active); one stage by id. |
| `require_in_tenant(stage_id)` | `None` | Raise unless the stage belongs to the in-scope tenant — the guard callers use before assigning a stage id that arrived from a request body. |

Collaborators: `StageRepository`, `AuditService`. Match↔stage assignment lives on `MatchService.assign_stage`, not here.

### system_config_service.py — SystemConfigService

Typed, static accessors over the `SystemConfiguration` key/value table. Module constants name the known keys: `KEY_EVENT_START_DATE`, `KEY_EVENT_END_DATE`, `KEY_MAX_CONCURRENT_PLAYERS`, `KEY_MAX_CONCURRENT_STAGES`, `KEY_VOLUNTEER_REMINDER_LEAD_MINUTES`, `KEY_VOLUNTEER_COMP_TIERS`, `KEY_TOURNAMENT_HOURS`, `KEY_STATION_FORMAT`, `KEY_JOIN_PREVIEW` (whether the join page shows non-members today's matches; off by default), `KEY_DISCORD_AUTO_JOIN` (linked-server members get in without a join request; off by default), `KEY_JOIN_REQUESTS` (whether the join page takes requests; read with `get_bool(..., default=True)`, so on unless staff turned it off), `KEY_OPEN_MEMBERSHIP` (anyone who signs in is let in; off by default; the three join keys are written together by `TenantMembershipService.set_join_mode`), `KEY_DISCORD_INVITE_URL` (read with `get_discord_invite_url()`, written with `set_discord_invite_url(raw, actor)`, which normalizes through `normalize_discord_invite_url` and raises `ValueError` for anything that is not a Discord invite).

| Method | Returns | Description |
|---|---|---|
| `get_raw(key)` / `get_int(key, default=None)` / `get_date(key, default=None)` / `get_bool(key, default=False)` | `str` / `int` / `date` / `bool`, or `None` | Typed reads of one key, falling back on a missing or unparseable value. |
| `set_raw(key, value, actor)` | `SystemConfiguration` | Upsert; Staff-only (`ensure`); audits `system_config.updated` with old/new values. |
| `get_event_window(tournament=None)` | `(date, date)` | Event start/end. A tournament's own `event_start_date`/`event_end_date` win per bound; otherwise the tenant setting. Falls back to min/max `Match.scheduled_at`, then to today; clamps end ≥ start. |
| `get_max_concurrent_players(default=60)` | `int` | Configured player capacity, or default when unset/non-positive. |
| `get_max_concurrent_stages(default=None)` | `int` | Configured stage capacity; falls back to the given default, then to the count of active stages. |
| `get_volunteer_reminder_lead_minutes(default=60)` | `int` | How far ahead of a shift the reminder loop fires; default when unset/non-positive. |
| `get_community_event_reminder_lead_minutes(default=30)` | `int` | How far ahead of a community event its subscribers are DM'd; default when unset/non-positive. |
| `community_events_require_approval()` | `bool` | Whether a member's community event waits for staff review (`community_event_require_approval`, default on). |
| `get_volunteer_comp_tiers(default=None)` | `list[float]` | Ascending hour thresholds that earn a volunteer something (`volunteer_comp_tiers`, comma-separated). `[8, 12, 16]` when unset; a lone `0` means no tiers; a malformed value falls back to the default rather than raising. |
| `get_tournament_hours(tournament=None)` | `dict[date, (time, time)]` | Per-day open/close windows (JSON-decoded; malformed entries skipped). A tournament's own `tournament_hours` blob fully replaces the tenant schedule. |
| `get_tournament_window_for_date(d, tournament=None)` | `(time, time) \| None` | Open/close window for one date, or `None` when unconfigured. |
| `set_tournament_hours(mapping, actor)` | `None` | Persist `{date: (open_HH:MM, close_HH:MM)}`; Staff-only. |
| `validate_hours_mapping(mapping)` | `dict[str, dict[str, str]]` | Pure HH:MM / close-after-open validation (`ValueError`) into a storable blob (blank days dropped), shared by `set_tournament_hours` and the per-tournament override. |
| `get_station_format(default=StationFormat.FREE)` | `StationFormat` | The venue's station-numbering mode. |

### table_preference_service.py — TablePreferenceService

One person's saved layout for one table, stored **globally** (no tenant — the
`User.timezone` call). `prime(user)` loads the whole set in a single query at page
build, and the caller binds it with `table_prefs_scope(...)` from
[`application/table_preferences_context.py`](../../application/table_preferences_context.py),
which synchronous table builds read back (unprimed means the shipped defaults);
`all_for_user` / `get` read it; `save` / `set_width` / `reset` / `reset_all` write it. `validate(config)` is
pure and is the entire security surface, since the blob arrives from a browser:
known keys only, `page_size` from `ALLOWED_PAGE_SIZES`, `density` from
`ALLOWED_DENSITIES`, bounded widths and column-list length, no duplicate names —
returning a normalised copy so storage is canonical. It deliberately never learns
column *names*: the authoritative list lives in presentation, and reconciliation
happens there (`theme/tables/preferences.effective_columns`). **No audit row and
no event** — hiding a column is not a change to a community's data; `save` and
`reset` fire a best-effort telemetry interaction instead. Collaborators:
`TablePreferenceRepository`, `TelemetryService`.

### telemetry_service.py — TelemetryService

Engagement telemetry — *how* people use the tool, as opposed to the deliberate actions `AuditService` records. Three best-effort capture points, all honoring the `TELEMETRY_ENABLED` kill-switch and swallowing their own errors so telemetry never breaks a render or a mutating call: `record_event(event)` (subscribed to the [event bus](../features/event-system.md) in `main.py`, mirroring every domain event into a `domain` row on the dispatch worker), `track_page_view(...)` (from the `protected_page` decorator — path, bounded query params, browser session id), and `track_interaction(...)` (curated UI actions). Reads are `AuthService.is_staff`-gated and aggregate in the database via `TelemetryRepository`: `engagement_summary`, `top_paths`, `top_event_types`, `top_users`, `list_events`, `count_events`. `TelemetryCategory` (`page`/`interaction`/`domain`) and `TelemetryEventType` (`page.view`/`report.viewed`/`report.exported`) name the buckets. Collaborators: `TelemetryRepository`, `AuthService`, `application.events`, `application.utils.environment.telemetry_enabled`. Feature doc: [telemetry.md](../features/telemetry.md).

### tenant_service.py — TenantService

The tenancy machinery: resolves a `Tenant` from a URL slug (`TenantMiddleware`), a custom domain, or a Discord guild id (bot routing), and backs the `/platform` super-admin surface. Resolution lookups are cached in-process (single worker) and the whole cache is dropped on any write. CRUD and grant operations are **super-admin gated** (`AuthService.is_super_admin` via `ensure`) and run with *no* tenant context, so they pass explicit ids to the repositories rather than relying on the ambient tenant — their audit rows are platform-level (`tenant=NULL`). Slugs are validated against `_SLUG_RE` and a `_RESERVED_SLUGS` set (the platform's own top-level paths). Feature doc: [multitenancy.md](../features/multitenancy.md).

| Method | Returns | Description |
|---|---|---|
| `get_by_id(tenant_id)` / `get_by_slug(slug)` / `get_by_domain(domain)` | `Tenant \| None` | Resolution lookups; `get_by_slug` is cached. |
| `community_name(tenant_id)` / `current_community_name()` | `str` | The community's display name ('' if unknown) for Discord embed footers; the `current_` form reads the ambient tenant and never raises. |
| `list_tenants_for_guild(guild_id)` | `list[Tenant]` | Every tenant linked to a Discord guild (a guild may back several); cached. The bot fans out over the result. |
| `list_tenants()` | `list[Tenant]` | All tenants (platform admin table + community picker). |
| `set_discord_guild_id(tenant, guild_id)` | `Tenant` | Set or clear the linked guild and drop the cache. **Ungated** — the caller (`DiscordLinkService`) has already verified guild authority. |
| `create_tenant(actor, *, name, slug, domain=None, discord_guild_id=None, is_active=True, config=None)` | `Tenant` | Super-admin create; validates slug/domain uniqueness and format; audited `tenant.created`. |
| `update_tenant(actor, tenant, **fields)` | `Tenant` | Super-admin partial update; re-validates slug/domain; audited `tenant.updated`. |
| `is_member(user_id, tenant_id)` | `bool` | Membership check by explicit ids, for tenancy machinery. Community-facing membership management lives in `TenantMembershipService`. |
| `grant_super_admin(actor, user)` / `revoke_super_admin(actor, user)` | `None` | Super-admin gated; grant/revoke the global `SUPER_ADMIN` role (`UserRole` with `tenant=NULL`); audited `super_admin.*`. |
| `bootstrap_staff(actor, tenant_id, user)` | `None` | Grant a tenant its first `STAFF` + membership. Wired to `/platform`'s **Admins** row action and offered at the end of tenant creation. |
| `list_staff(actor, tenant_id)` | `list[User]` | Who holds STAFF in a named tenant, read from `/platform`. Wraps `tenant_scope` because the role tables are scoped and the platform surface has no ambient tenant. |
| `slugify(name)` (module fn) | `str` | Best-effort URL-safe slug suggestion from a display name. |

Collaborators: `TenantRepository`, `TenantMembershipRepository`, `UserRoleRepository`, `AuthService`, `AuditService`.

### tenant_membership_service.py — TenantMembershipService

Who belongs to a community, and who may change that. Membership is the **wider set**: every role-holder is a member, not every member holds a role — the invariant enforced wherever roles are written (see [multitenancy.md](../features/multitenancy.md#identity-roles-and-membership)). `TenantMembership` is exempt from `check_tenant_scoping` (cross-tenant by nature: the row *is* the tenant linkage), so its queries pass tenant ids explicitly. Writes authorize on `AuthService.can_grant_roles` — the same gate the Users tab's role grants use, because member management and role management need identical authority.

| Method | Returns | Description |
|---|---|---|
| `list_members()` | `list[User]` | Everyone in the tenant in scope, by username. |
| `is_member(user)` | `bool` | Membership check for the tenant in scope. |
| `addable_users()` | `list[User]` | Accounts not in this community yet — the Add Member picker. Deliberately reads the *global* list (identity is shared), which is why it is a named service method rather than a bare `get_all_users()` in the page. |
| `add_member(actor, user)` | `None` | Idempotent; closes a pending join request (decider = actor, `closed_request_id` in the audit details) and DMs the person. Audited `tenant.member_added` + `EventType.TENANT_MEMBER_ADDED`. |
| `remove_member(actor, user)` | `None` | **Refuses** while the user holds any role here — silently cascading a revoke because staff clicked Remove is the invisible side effect this defends against. Withdraws their ADA request and DMs them. Audited `tenant.member_removed` + event. |
| `ensure_member(user, *, actor=None, source=None)` | `TenantJoinRequest \| None` | Idempotent, **unaudited** membership for the in-scope tenant, recording `source` on a new row. The invariant's hook, called from role grants that are audited in their own right. Returns the pending request it closed (decider = `actor`) so the caller's audit row can carry `closed_request_id`, and DMs the person in that case. |
| `memberships_by_user()` | `dict[int, TenantMembership]` | This community's rows keyed by user id: when (`created_at`) and how (`source`) each joined. |
| `get_join_mode(tenant_id=None)` / `set_join_mode(actor, mode)` | `JoinMode` / `None` | Static. The community's join rule as one choice, read from and written to the three join keys. Setting is staff-only (`set_raw`) and refuses a Discord mode with no guild linked. |
| `join_openly(user, tenant_id)` | `bool` | Open membership's door, called by `enforce_membership` before `join_via_discord`. With `open_membership` on, adds the user (`MembershipSource.OPEN`), closes a pending request, audits `tenant.member_added` with `source: open`. **Never raises.** |
| `admit(user, tenant_id=None)` | `bool` | Member already, or let in now by `join_openly` / `join_via_discord`. For entry points that never pass the page gate (crew signup's Discord button). |
| `auto_join_active(tenant_id)` | `bool` | Static. Auto-join switch on and a guild linked. Drives the removal/decline copy. Never raises. |
| `next_request_allowed_at(user, tenant_id)` | `datetime \| None` | When a declined requester may ask again (`JOIN_REQUEST_COOLDOWN`, 7 days after `decided_at`), or `None`. |
| `request_to_join(user, tenant_id, message=None)` | `TenantJoinRequest` | The door. Takes an **explicit** `tenant_id` — the caller is a page the requester is not a member of, so nothing membership-scoped is available. Refuses an existing member, and refuses everyone while `join_requests_enabled` is off (pending rows are left alone); re-opens a denied row rather than creating a second; bounds the message at 500 chars; refuses during the decline cooldown. Audits + publishes `tenant.join_requested` inside `tenant_scope(tenant_id)` with the **requester** as actor, and DMs the community's staff. |
| `join_via_discord(user, tenant_id)` | `bool` | The door's other way through, called by `enforce_membership`. With `discord_auto_join` on and a guild linked, asks the bot (`is_guild_member`) and adds a server member, closing any pending request as approved (no decider), auditing `tenant.member_added` with `source: discord_auto_join` (plus `closed_request_id`). No staff DM; the membership row records `DISCORD_AUTO_JOIN`. Explicit `tenant_id`; **never raises** — a bot error or exception is a `False`. |
| `get_request(user, tenant_id)` | `TenantJoinRequest \| None` | This user's request in a named tenant — what the join page reads to know it is already pending. |
| `list_pending()` | `list[TenantJoinRequest]` | The staff queue for the tenant in scope. |
| `approve_request(actor, request_id)` / `deny_request(actor, request_id)` | `TenantJoinRequest` | `can_grant_roles` gated. Approve creates the membership. Deny refuses for someone already a member. A request in another community is **not_found**, not forbidden — the message must not confirm it exists. The requester is DM'd on **both** outcomes; a decline says when they can ask again and links the Discord invite when one is set. |

### tenant_setup_service.py — TenantSetupService

Whether a community has what it needs to run, **derived on every read** — there is no `setup_complete` column, no dismissal flag and no per-step state, so a tenant that deletes its last tournament becomes un-set-up again and the checklist says so. Read-only: it takes no `actor` and writes no audit row. Five `.exists()` calls against `UserRoleRepository`, `TournamentRepository` and `StageRepository` plus the event-window config keys. Feature doc: [multitenancy.md § Provisioning a community](../features/multitenancy.md#provisioning-a-community).

| Method | Returns | Description |
|---|---|---|
| `status()` | `list[SetupStep]` | The checklist for the tenant in scope. |
| `status_for(tenant_id)` | `list[SetupStep]` | The same derivation inside `tenant_scope(tenant_id)` — the `/platform` caller, which has no ambient tenant. |
| `is_ready(steps)` | `bool` | Every **required** step done. The advisory ones never block. |
| `outstanding(steps)` | `list[SetupStep]` | The required steps still undone (the `/platform` tooltip). |

`SetupStep` is a frozen dataclass: `key`, `label`, `done`, `hint`, `tab` (which admin tab completes it) and `required`. Required: `staff`, `tournament`, `enrolment`. Advisory: `stage`, `event_window` — a match schedules without a stage, and Settings' own copy says the window is derivable from match times, so marking either required would put the UI at odds with the service.

Rendered by `theme/setup_checklist.py` (the `Setup` admin tab) and `pages/platform.py`'s Setup column.

### tenant_logo_service.py — TenantLogoService

A community's **logo**, shown in place of the brand-mark square (28×28 in the header), as the browser tab icon, and (when the Print QR labels dialog's "Logo in QR code" is ticked, the default) solid black in the middle of equipment QR codes. Uploads are never stored as sent. `prepare_logo` routes by content: anything whose first 4 KB is XML with an `<svg` root goes to `application/utils/svg_sanitize.sanitise_svg`, which rebuilds the document from an allowlist of drawing elements and presentation attributes (no `script`, `foreignObject`, `image`, `a`, animation or `on*`; references only to `#id`; CSS refused if it has `@import`, escapes or a non-local `url()`; entity declarations refused) and stores that. Everything else goes to `normalise_logo`, which accepts PNG, JPEG or WebP only (identified by decoding with Pillow, never by extension or declared type), up to 1 MB and 4096×4096 (checked from the header before decoding, which stops decompression bombs) and at least 64×64. It honours EXIF rotation, then fits the image onto a transparent 256×256 square and saves a fresh WebP, which drops EXIF/GPS, ICC payloads and any appended data. Decoding runs in `asyncio.to_thread`.

The stored bytes go in the `TenantLogo` table (`bytea`), never the filesystem; `Tenant.config['logo']` holds only their SHA-256 and `config['logo_format']` is `'svg'` for an SVG (absent for the WebP), which `TenantThemeService.get_theme` turns into a `logo` URL on the palette dict. `frontend.py` serves `/brand-logo/<tenant_id>/<sha256>.webp` and `.svg` publicly (each 404s unless the stored bytes are that format) with a year-long `immutable` cache, an `ETag` (304 on match), `nosniff` and a `default-src 'none'; style-src 'unsafe-inline'; sandbox` CSP, so an SVG opened directly in a tab can't run anything; a new upload is a new URL, so nothing needs invalidating. A 128-entry in-process LRU keyed by `(tenant_id, sha256)` saves the DB read on a cold browser.

Safari and iOS won't use an SVG or WebP as a tab or home-screen icon, so `render_icon` turns the stored bytes into a square PNG: 32×32 on transparent for the tab, 180×180 with the logo at 80% on the app's charcoal (`#17120D`) for the apple-touch icon. SVGs render with `resvg-py`, which needs no system libraries and never follows external references (the sanitiser leaves none anyway). `frontend.py` serves them at `/brand-logo/<tenant_id>/<sha256>-<32|180>.png` with the same immutable caching, from a second LRU; any other size, or a logo that won't render, is a 404. `get_theme` adds their URLs as `logo_icon` and `logo_touch`.

| Method | Returns | Description |
|---|---|---|
| `set_logo(actor, data)` | `str` | STAFF-gated; normalises, upserts the row and the config hash in one transaction, audits `theme.logo_updated`. Returns the URL. `ValueError` with an uploader-facing message for a rejected file |
| `remove_logo(actor)` | `None` | STAFF-gated; drops the row and the hash, audits `theme.logo_removed` |
| `qr_logo_data_uri()` | `str \| None` | The current community's stored logo as a `data:` URI for `qrcode_util.asset_qr_svg`, else `None` |
| `get_icon_png(tenant_id, sha256, side)` | `bytes \| None` | The logo as a `side` (32 or 180) PNG icon, rendered off the loop and cached; `None` for another size, a missing logo or a render failure |
| `get_logo_bytes(tenant_id, sha256)` | `bytes \| None` | For the image route (no tenant in scope); `None` unless that exact tenant and hash match |

### tenant_theme_service.py — TenantThemeService

The per-tenant **brand palette**. The app ships a fixed "Phoenix" palette (gold/ember); a community's STAFF may override four brand colours — `primary`, `secondary`, `accent`, and `header` (the brand mark beside the wordmark) — stored under `Tenant.config['theme']`, no dedicated columns. Semantic status colours (positive/negative/warning/info) are deliberately not editable. Reads merge overrides onto the defaults so callers always get a full palette; writes are STAFF-gated, validated to 6-digit hex, and audited `theme.updated`. `Tenant` is the tenancy discriminator (never tenant-scoped), so this service goes through the cross-tenant `TenantRepository`. Applied by `BaseLayout` — see [frontend.md § Per-tenant theme colours](../reference/frontend.md#per-tenant-theme-colours).

| Method | Returns | Description |
|---|---|---|
| `get_theme(tenant_id)` | `dict[str, str]` | Full palette for a tenant (overrides merged onto `DEFAULT_THEME`), plus a `logo` URL when the community has uploaded one (see `TenantLogoService`). Fresh read (`get_by_id` is uncached). |
| `get_current_theme()` | `dict[str, str]` | Best-effort palette for the in-scope tenant; the shipped defaults when there is no tenant (platform surface / error page). Non-raising. |
| `set_theme(actor, colors)` | `dict[str, str]` | STAFF-gated; validates hex, persists to `Tenant.config['theme']`, audits. A blank value clears that key; clearing all keys resets to defaults. |
| `is_customized(colors)` | `bool` | Whether a resolved palette differs from the shipped defaults. |
| `list_presets()` | `dict[str, dict]` | Curated, contrast-verified palettes (`THEME_PRESETS`) for the editor's preset picker. |
| `contrast_report(colors)` | `dict[str, list[dict]]` | Every WCAG-AA check per colour (`{use, mode, ratio, threshold, ok}`), modelled on where `styles.css` paints it: white button labels, light-mode links (15% toward black), dark-mode text, the brand mark (3:1). Pure; skips invalid/blank values. |
| `contrast_issues(colors)` | `dict[str, list[str]]` | One sentence per failing use, keyed by colour: what fails, in which mode, and the ratio. The Appearance editor shows these live under each field; they never block saving. |
| `contrast_warnings(colors)` | `list[str]` | `contrast_issues` flattened, each prefixed with its colour. |

Contrast math lives in [`application/utils/color_contrast.py`](../../application/utils/color_contrast.py) (pure WCAG relative-luminance / contrast-ratio). Presets are held to the same bar the shipped defaults meet — `tests/test_color_contrast.py` asserts every preset passes every check, so a preset can never ship failing AA — and `tests/theme/test_style_guide.py` asserts the surfaces and colour mixes the checks model still match `styles.css`. Collaborators: `TenantRepository`, `AuthService`, `AuditService`, `color_contrast`.

### timezone_service.py — TimezoneService

Which wall clock each viewer reads times on. Datetimes are stored UTC everywhere; this service owns the **policy** and the **resolution**, and [`application/timezone_context.py`](../../application/timezone_context.py) owns the request-scoped plumbing (contextvar + NiceGUI client stash, same two-tier shape as `tenant_context.py`).

Two tiers. `Tenant.config['timezone']` holds `{'mode': 'pinned'|'user', 'name': '<IANA>'}`: a **pinned** community renders one clock for everybody (right for an on-site event, where "14:30" means 14:30 at the venue); a **user** community defers to each viewer — their `User.timezone`, else the zone their browser reported via the `wiz_tz` cookie, else the community's own. `name` matters in both modes: pinned uses it as *the* clock, `user` mode as the floor everything falls back to. Writes are STAFF-gated and audited `timezone.updated`; the per-user preference is self-service and audited `user.timezone_updated`. `Tenant` is the tenancy discriminator (never tenant-scoped), so this goes through the cross-tenant `TenantRepository`. Full rules: [timezone-handling.md](../timezone-handling.md).

| Method | Returns | Description |
|---|---|---|
| `get_settings(tenant_id=None)` | `dict` | The community's `{mode, name}`, defaults applied. Defaults to the in-scope tenant. |
| `tenant_timezone_name(tenant_id=None)` | `str` | The community's **own** clock — the zone for anything not viewer-specific (cached spectator pages, tournament hours, workers, MCP/REST date params). Never raises. |
| `set_settings(actor, mode, name)` | `dict` | STAFF-gated; validates the mode and that the zone loads; persists to `Tenant.config['timezone']`; audits. |
| `set_user_timezone(actor, name)` | `str \| None` | Save or clear (`None`/blank) the actor's own preference. Self-service — the actor is the subject, so no role check. |
| `pick(settings, user=None, browser_timezone=None)` | `str` | Pure, sync resolution given already-loaded settings: pinned short-circuits, else profile → browser → community default. Lets the page builder re-resolve after the user loads without a second tenant read. |
| `resolve(user=None, browser_timezone=None, tenant_id=None)` | `str` | `get_settings` + `pick`. Never raises. |

Every read path is **total**: an unknown mode, a missing key, or a zone this system's tzdata cannot load degrades to the defaults rather than raising, because the settings are read on every page build and a hand-edited blob must not be able to take the app down. Module constants: `MODE_PINNED`/`MODE_USER`, `DEFAULT_MODE` (`user` — new communities follow the viewer; existing ones were migrated to an explicit Eastern pin), `COMMON_TIMEZONES` (the picker shortlist; both pickers still accept any IANA name), `CONFIG_KEY`.

Collaborators: `TenantRepository`, `UserRepository`, `AuthService`, `AuditService`, `timezone_context`. Bound at the edges by `middleware/auth.py` (`bind_display_timezone`, page build), `middleware/tenant.py` (the cookie), `api/dependencies.py`, `mcpserver/registry.py`, and `application/utils/background_loop.py` (worker ticks).

### profile_nudge_service.py — ProfileNudgeService

STAFF-picked profile items each member should have done, and the banner rows a member still owes. Rules live in `Tenant.config['profile_nudges']`; dismissals in `ProfileNudgeDismissal`. Full detail: [profile-reminders.md](../features/profile-reminders.md).

| Method | Returns | Description |
|---|---|---|
| `get_rules(tenant_id=None)` | `list[dict]` | `[{check, dismissible, note}]` in catalogue order; total over a bad blob. |
| `unavailable_reasons()` | `dict[ProfileCheck, str]` | Why each check a member couldn't complete here right now is out (flag off, integration not configured, timezone pinned); shown on the admin tab. |
| `set_rules(actor, rules)` | `list[dict]` | STAFF-gated; replaces the rules; audits `profile_nudges.updated`. Dismissals are kept. |
| `pending_for(user)` | `list[ProfileNudge]` | Unmet, applicable rules not dismissed (a mandatory rule ignores dismissals). |
| `dismiss(actor, check)` | `None` | Records an acknowledgement; refuses a rule that is off or mandatory; audits `profile_nudge.dismissed` once. |

Collaborators: `ProfileNudgeRepository`, `TenantRepository`, `AuthService`, `AuditService`, `FeatureFlagService`, `TimezoneService`, and the Challonge/Twitch/racetime services' `is_configured`.

### staff_alert_service.py — StaffAlertService

Who hears about a *match alert*: a staff-facing DM about one match that someone must pick up (a player's comment, a reschedule request). Community-wide requests (join, ADA, community events) are not match alerts and still go to every STAFF member.

| Method | Returns | Description |
|---|---|---|
| `on_duty_now(at=None)` | `List[User]` | Holders of a published shift at `at` on a position with `staff_alerts` on. |
| `match_alert_recipients(match, *, can_act=AuthService.can_run_match, exclude=(), at=None)` | `List[User]` | On-duty users, falling back to all STAFF when none who `can_act` is on duty, plus the tournament's admins minus opt-outs. Every recipient passes `can_act`, so nobody is linked to a control that refuses them. Reschedule requests pass `can_crud_match`. |
| `tagged_match_alert_recipients(...)` / `tagged_volunteer_alert_recipients(at=None)` | `List[(User, NotificationRecipient)]` | The same recipients, each tagged with the settings column that governs their copy: `ON_DUTY` for the on-shift set (even if they also admin the tournament), `ADMINS` for tournament admins or volunteer coordinators, `STAFF` only for the fallback when nobody is on duty. Senders pass the tag to `notice.to(...)`. |
| `tournament_alert_admins(tournament)` | `List[User]` | The tournament's active admins who haven't switched its alerts off; for alerts with no match (a player withdrawing). |
| `volunteer_alert_recipients(at=None)` | `List[User]` | Who hears a volunteer is running late or gave a shift back: on-duty users who `can_manage_volunteers` (all STAFF when none), plus every VOLUNTEER_COORDINATOR. |
| `admin_tournaments(user)` | `List[(Tournament, bool)]` | Active tournaments the user admins, with whether alerts are on. |
| `set_tournament_alerts(user, tournament_id, on)` | `None` | The opt-out. Raises `ValueError` unless the user admins that tournament. |

### notification_catalog.py — Notice catalog

Every notification Wizzrobe sends, as code. Each `Notice(key, group, title, when, defaults, who)` is declared once with `_notice(...)` as a module constant (`MATCH_SCHEDULED`, `MATCHUP_READY`, `CHECKED_IN`, …; 60 in all) and registered in `CATALOG: Dict[str, Notice]`. `defaults` maps each `NotificationRecipient` the message goes to onto its default `NotificationMode`; `who` explains a column where the label alone would mislead ("All staff" means the fallback only). `GROUPS` fixes the display order (Matches, Brackets, Reschedule requests, Crew, Volunteer shifts, Community, Event check-in, Async qualifiers); `notices()` returns the catalogue in it. `notice.to(recipient)` returns a `NoticeTarget(key, recipient)` (raising `KeyError` for a recipient the message doesn't have), which is what a sender passes to `send_dm(notice=...)`; `NoticeTarget.default` reads the catalogue default. Keys are stored in `NotificationSetting.notice`, so renaming one drops the overrides saved against it. Feature doc: [notification-settings.md](../features/notification-settings.md).

### notification_settings_service.py — NotificationSettingsService

A community's overrides of the catalogue defaults. Classmethods; staff-only apart from `mode_for`. No feature flag, and no event-bus event (tenant-internal config).

| Method | Returns | Description |
|---|---|---|
| `mode_for(target)` | `NotificationMode` | The community's choice for a `NoticeTarget`, else its default. Reads overrides through a per-tenant 30 s in-process cache (`_CACHE_SECONDS`), so a burst of DMs costs one query. **Never raises**: no tenant in scope, or a failed read, falls back to the default. Called by `send_dm` and by `check_in_dm` (which records nothing when the check-in DM is off). |
| `list_settings(actor)` | `list[NotificationSettingRow]` | Every catalogue cell (notice × recipient) with its current mode, in display order. `NotificationSettingRow(notice, recipient, mode)` adds `default` and `changed`. |
| `set_mode(actor, notice_key, recipient, mode)` | `NotificationSettingRow` | Set one cell; choosing the default deletes the override. `ValueError` for an unknown notice, recipient or mode. Drops the tenant's cache entry; audits `notification_setting.changed` (`notice`, `title`, `recipient`, `old`, `new`) only when the mode changed. |
| `reset_all(actor)` | `int` | Delete every override; returns the count. Audits `notification_settings.reset` when any were deleted. |

Collaborators: `NotificationSettingRepository`, `AuthService` (`is_staff`), `AuditService`.

### notification_preference_service.py — NotificationPreferenceService

A person's own choices, one row per (tenant, user). Classmethods; every write acts on the actor's own row only (`PermissionError` when signed out). Not audited, no event.

| Method | Returns | Description |
|---|---|---|
| `mode_for(discord_id, target)` | `NotificationMode` | The recipient's choice for a `NoticeTarget` (override, else what their preset asks), before the community ceiling; `send_dm` takes the quieter of this and `NotificationSettingsService.mode_for`. Per (tenant, Discord id) 30 s cache. **Never raises**: no tenant, account, row, or a failed read answers `PING`. |
| `get_mine(actor)` | `MyNotifications` | `preset` plus a `MyNotificationRow(target, community, preset, override)` for every target that can reach them (staff alerts only with `can_view_admin`), sorted by `SECTIONS`. Rows expose `effective`, `changed`, `section`; `counts(preset=None)` gives ping/silent/off totals for any preset. |
| `set_preset(actor, preset)` | `MyNotifications` | Change the preset; overrides stay. |
| `set_mode(actor, notice_key, recipient, mode)` | `MyNotificationRow` | One message; `None` or the preset's value removes the override. `staff_comment` → `players` also writes `User.dm_match_comments`. `ValueError` for an unknown notice, recipient or mode. |
| `reset(actor)` | `MyNotifications` | Clear overrides, keep the preset, turn `dm_match_comments` back on. |

`opted_in_user_ids(target)` returns who turned an opt-in message on (one query). Module helpers: `quieter(a, b)`, `preset_mode(preset, target)` (`off` for any opt-in target), `section_for(target)`. Collaborators: `NotificationPreferenceRepository`, `NotificationSettingsService`, `AuthService`.

### opt_in_notifications.py — module functions

Senders for the opt-in DMs (`notification_catalog._OPT_IN`), each a never-raising coroutine handed to `discord_queue.enqueue`: `opponent_acknowledged`, `stations_assigned`, `match_flagged`, `seed_failed`, `stream_offer`, `request_withdrawn`, `enrolment_changed` (staff add/remove → the player; a self-withdrawal → the tournament's admins), `match_result` (on confirm, to players and followers), `bracket_started`, `event_listed`, `volunteer_arrived`, `qualifier_window`. Each first asks `NotificationPreferenceService.opted_in_user_ids`, so nothing loads when nobody opted in. See [notification-settings.md](../features/notification-settings.md#opt-in-messages).

### check_in_dm.py — module functions

The "you're checked in" test DM. `start_dm_check(entrant, event_name)` records nothing when the entrant has no Discord account, marks `OPTED_OUT` without sending when they turned DMs off, returns quietly when `CHECKED_IN.to(MEMBERS)` is off in Admin → Notifications, and otherwise marks `PENDING` and queues the send on `discord_queue`. The queued `_deliver` sends with `notice=CHECKED_IN.to(MEMBERS)`, writes `SENT` / `BLOCKED` / `FAILED` to `CheckInEntrant.dm_status`, and nudges open desks on `check_in_live`. Feature doc: [event-check-in.md](../features/event-check-in.md).

### tournament_notification_service.py — TournamentNotificationService

Per-user, per-tournament match-notification preference management (`MatchNotificationLevel`). The subscriber queries that consume these preferences live in `TournamentNotificationRepository` and are called from `MatchScheduleService`. Feature doc: [match-participation.md](../features/match-participation.md).

| Method | Returns | Description |
|---|---|---|
| `get_preference(user, tournament_id)` / `get_user_preferences(user)` | reads | One tournament's preference (`None` when unknown or unset); all of a user's preferences. |
| `upsert_preference(user, tournament_id, match_notifications)` | `TournamentNotificationPreference` | Create/update; validates the level string against `MatchNotificationLevel` and the tournament's existence (`ValueError`). |
| `get_active_tournaments()` | `list[Tournament]` | Active tournaments, name-ordered (for the preference dialog). |

Collaborators: `TournamentNotificationRepository`, `TournamentRepository`.

### tournament_service.py — TournamentService

Tournament CRUD plus Tournament Admin / Crew Coordinator membership. Creation and deletion are Staff-only (deletion is permanent and additionally requires the tournament to be inactive plus a typed `permanently delete`); updates allow the tournament's TAs; membership grants are Staff-only (`can_grant_roles`). All mutations audited under `tournament.*`. The optional `config` blob is validated by `validate_tournament_config` (from [`tournament_config.py`](../../application/services/tournament_config.py)) before persistence — unknown keys raise `ValueError`.

| Method | Returns | Description |
|---|---|---|
| `create_tournament(name, description=None, seed_generator=None, ..., config=None, actor=None)` | `Tournament` | Create; trims strings; treats the literal string `"None"` as no seed generator; non-empty name required; validates `config`. The keyword set covers every tournament setting: links and format text (`bracket_url`, `rules_url`, `tournament_format`, `triforce_access_message`, `randomizer_notes`), durations, `is_active`, `players_per_match`, crew requirements (`required_commentators`/`required_trackers`, default 1), `allow_player_match_requests`, presets (`preset_id`, `hard_preset_id`), racetime (`racetime_bot_id`, `race_room_profile_id`, `racetime_auto_create_rooms`, `room_open_minutes_before=30`, `require_racetime_link`, `racetime_default_goal`), `stage_reminder_minutes=30`, the per-tournament event window and hours (`event_start_date`, `event_end_date`, `tournament_hours`, validated by `SystemConfigService.validate_hours_mapping`), and the signup window (`signups_open_at`, `signups_close_at`). |
| `update_tournament(tournament, ...same optional fields..., config=None, actor=None)` | `Tournament` | Partial update of any subset of the same fields; validates `config` when provided; audits the changed-field list. The nullable FK/date/hours fields default to an `_UNSET` sentinel so an explicit `None` clears them. |
| `delete_tournament(tournament, actor=None, confirmation=None)` | `None` | Staff-only **permanent** delete — the row and everything that cascades from it (matches, entrants, brackets, triforce texts). Two gates on top of the role check, both enforced here rather than in the dialog: `tournament.is_active` must already be `False`, and `confirmation` must equal `permanently delete` (stripped, case-insensitive). Either miss raises `ValueError` and writes nothing. Audits `tournament.deleted` with the name and the cascade counts. |
| `deletion_preview(tournament)` | `dict[str, int]` | The cascade counts (`matches`, `players`, `brackets`, `triforce_texts`) the confirmation dialog states before anyone types the phrase. Read-only, ungated. |
| `add_admin(tournament, target, actor=None)` | `None` | Grant Tournament Admin (M2M add). |
| `remove_admin(tournament, target, actor=None)` | `None` | Revoke Tournament Admin. |
| `add_crew_coordinator(tournament, target, actor=None)` | `None` | Grant Crew Coordinator. |
| `remove_crew_coordinator(tournament, target, actor=None)` | `None` | Revoke Crew Coordinator. |
| `get_all_tournaments(active_only=False)` / `get_tournament_by_id(id)` | reads | Tournament list (optionally active only); one tournament by id. |
| `list_schedulable(keep_id=None)` | `(tournaments, {id: entrants})` | The admin match dialog's Tournament options: active only (`keep_id` spares the one an edited match already points at, so editing does not blank its own chip) plus each tournament's entrant count, so an option whose player menu will open empty says so *before* the choice. |
| `list_player_requestable(user=None)` | `list[Tournament]` | What the request-a-match dialog may offer: bracket-run tournaments excluded; `user` narrows to their enrolled ones. |
| `get_tournaments_by_ids(ids)` / `get_enrolled_players(tournament)` / `get_enrolled_players_by_user(user)` / `get_enrolled_players_by_tournament_id(tournament_id)` | reads | Batch lookup and entrant reads, so presentation never reaches the repository. |
| `enroll_player(tournament, target, actor)` / `unenroll_player(tournament, target, actor)` | `None` | Entrant management from the tournament's own screen (`can_grant_roles` gated). `enroll_player` refuses a non-member of the community and a duplicate — the picker being member-scoped is a convenience, not the gate. |
| `list_signup_cards(user=None, now=None)` | `list[TournamentSignupCard]` | Every active tournament with this viewer's signup state on each: window, enrolment, entrant count and list, Challonge-managed flag, and the derived `can_sign_up` / `can_withdraw` / `needs_challonge_link`. A Challonge-linked tournament's enrolment and entrants come from its mirrored bracket (`ChallongeParticipant`), with unmapped entrants in `unmapped_entrant_names` and `roster_locked` once the bracket starts. The one shape the Tournaments tab, the REST payload and the MCP tool all read, so none of them can offer a signup the others would refuse. |
| `self_enroll(tournament_id, user)` | `Tournament` | Player signs *themselves* up. Refuses outside the window, for a non-member, and on a duplicate. A Challonge-linked tournament hands off to `ChallongeService.sign_up_player`, which adds them to the bracket instead of the player pool. Audits, publishes `tournament.enrolled`, and DMs a confirmation with a link button to the tab. |
| `self_withdraw(tournament_id, user)` | `Tournament` | Player withdraws themselves, **while the window is open**. Once it closes it is staff's call — a roster a bracket was seeded from should not lose an entrant silently. |

**Enrolment has four entry points** — the Tournaments tab's self-service signup
(`self_enroll` / `self_withdraw`, above), the per-user admin dialog
(`UserService.update_user_tournament_registrations` / `manage_tournament_enrollments`),
the tournament's own entrants dialog (`enroll_player` / `unenroll_player`), and
the match dialog's create path (`MatchService.ensure_players_enrolled`, which
returns the users it enrolled so the UI can report the side effect instead of
hiding it, and which takes a **required** `actor` because each row it creates is
audited). All four go through `record_enrolment_change`
(`application/services/_tournament_signup.py`), which writes the **same** audit
action, `user.tournament_enrollment_updated` — one fact reached from four
screens, and a parallel action per screen is how an audit log stops being
answerable — and publishes `tournament.enrolled` / `tournament.withdrawn`. The
event splits by direction because a subscriber mirroring a roster needs to know
which way it moved, and it fires on **one call per tournament** rather than one
batched call per screen: a staff roster edit is as much a roster change as a
player's own signup, so both reach a subscriber at the same granularity. All
four write through `TournamentRepository`, which stamps the non-null tenant FK.

**Only the self-service pair honours the signup window.** Staff enrolment
deliberately ignores it: closing signups stops players adding themselves, not
staff fixing a roster. Where the window stands is decided by the pure
`application/utils/tournament_signup.py` (`SignupWindow`, `signup_window_state`),
which both bounds read permissively — no open date means open now, no close date
means it stays open — so every tournament predating those columns behaves as it
always did.

Player self-service signup lives in the `TournamentSignupMixin`
(`application/services/_tournament_signup.py`), composed into `TournamentService`
so neither file exceeds the length budget. The split is an implementation
detail — importers still use `from application.services import TournamentService`.

Collaborators: `TournamentRepository`, `TenantMembershipService`, `AuditService`,
`FeatureFlagService` (the Challonge carve-out), `DiscordService`.

### tournament_config.py + tournament_strategies/ — hybrid-config substrate

Foundations for online-tournament user-definable logic ([online-tournaments.md](../features/online-tournaments.md)). `tournament_config.py` defines `TournamentConfig` (a Pydantic model with `extra='forbid'`) and `validate_tournament_config(config)`, which normalizes the blob and raises `ValueError` on any unknown key — the single entry point `TournamentService` calls before persisting `Tournament.config`. `tournament_strategies/` is the register/lookup registry for the finite set of named strategy primitives (`register_strategy(kind, name)`, `get_strategy(kind, name)`, `available_strategies(kind)`); config picks and parameterizes them — **never `eval`**. The **bracket engines** (below) are the concrete strategies populating it, under the `bracket_format` kind.

### bracket_config.py — bracket-config substrate

The schema-validated shape of `Bracket.config`, mirroring `tournament_config.py`. Two Pydantic models with `extra='forbid'`:

- **`BracketConfig`** — every field optional so a stage opts into only what it uses: `grand_final_reset` (double-elim reset toggle), `default_best_of` (series length for rounds without their own entry), `swiss_rounds`, `group_count`, the standings scoring points (`win_points`/`draw_points`/`loss_points`/`bye_points`), `tiebreakers` (ordered keys, checked against the known set), `omw_floor`, `advancement`, and `rounds`.
- **`AdvancementConfig`** — how a non-first stage draws its field from the prior stage's `final_rank`: `count` (≥1), `per_group` (top `count` per source group vs. overall), `seeding` (`'snake'` default / `'preserve'`).
- **`RoundConfig`** — one `rounds` entry (keyed by the round number as a string; negative for losers rounds): `best_of` and the `scheduled_at`/`scheduled_end` UTC ISO window that bounds match-time suggestions.

`validate_best_of(value)` is the shared positive-and-odd check used by `RoundConfig`, `default_best_of` and the per-matchup `BracketMatch.best_of` override. `validate_bracket_config(config, *, fmt=None, stage_order=None)` returns `None` unchanged or the normalized dict (unset keys dropped), raising `ValueError` on any unknown key or bad value. `fmt` and `stage_order` add the cross-field checks: a format-specific key on the wrong format, and an `advancement` rule on the first stage. `BracketService` always passes both before persisting.

### bracket_engines/ — pairing/progression engines

Pure structural code behind the `bracket_format` strategy kind ([brackets.md](../features/brackets.md)) — **no ORM, no NiceGUI, no async**; the service owns persistence. Importing the package auto-imports every sibling module, and each engine self-registers via `@register_strategy('bracket_format', …)`. Resolve one with `get_bracket_engine(fmt)` (`fmt` = a `BracketFormat` value); `available_bracket_formats()` lists the registered names.

Two engine shapes ([`base.py`](../../application/services/bracket_engines/base.py) defines the contract):

- **Generative** — `generate(num_entries, config) -> List[GeneratedMatch]` emits the *entire* graph up front with `winner_to`/`loser_to` pointers and seed placements. `single_elimination.py` (`SingleEliminationEngine`), `double_elimination.py` (`DoubleEliminationEngine` — winners + losers rounds via `standard_seeding`/`next_power_of_two`, grand final + conditional reset; structure ported in spirit from the MIT `smwa/python-tournaments`), and `round_robin.py` (`RoundRobinEngine` — snake-balanced groups scheduled by the circle method; no progression pointers).
- **Pairing** — `pair_round(players, config) -> List[(ref1, ref2|None)]` is a stateless per-round call. `swiss.py` (`SwissEngine`) is a thin adapter over the MIT **`swisspair`** library (min-cost no-rematch matching + one low-standing bye), encoding a deterministic rank tiebreak into the integer points so the pairing is reproducible.

Two shared, ORM-free passes sit beside the engines so a service, a Discord DM, or a REST payload can use them without the renderer:

- [`round_names.py`](../../application/services/bracket_engines/round_names.py) — `round_label(...)` for one round, `round_names(nodes, *, double_elim, elimination)` for a whole stage graph (`elimination=False` gives the flat formats plain "Round N"), and `detect_finals_ids` (the structural "which match is the grand final / reset" rule). `theme/brackets/layout.py` re-exports `round_label` and `render.detect_finals` adapts `detect_finals_ids`, so the renderer and the notification path cannot disagree.
- [`standings.py`](../../application/services/bracket_engines/standings.py) — `compute_standings(refs, results, config)` over opaque `int` refs and `ResultRow` records, computing match points and a configurable tiebreaker chain (`buchholz`, `omw`, `head_to_head`) into 1-based competition ranks (unresolved ties share a rank and list each other in `tied_with`). Round robin, Swiss re-pairing, stage-completion ranking, and the public bracket page's live standings all consume it.

### payout_service.py — PayoutService

Prize pool and placement splits. Behind `FeatureFlag.PAYOUTS` — every public method carries `@requires_feature`. A row stores a place and a percentage; the money is computed as `(prize_pool + prize_bonus) x percentage / 100`, quantized to the cent, at read time. Authorization is `AuthService.can_manage_payouts` (staff, super-admin, or that tournament's admin) and gates reads as well as writes. Feature doc: [payouts.md](../features/payouts.md).

| Method | Returns | Description |
|---|---|---|
| `list_overview(actor)` | `list[PayoutOverview]` | Every tournament this actor may manage, with pool, total, place count, allocated percentage and whether every place is named. |
| `get_split(tournament_id, actor)` | `PayoutSplit` | The rows with their computed amounts. Publishes nothing — it is a read. |
| `set_pool(tournament_id, pool, bonus, actor)` | `Tournament` | Sets the two columns; `None` clears rather than zeroes. Refuses a different pool while the tournament syncs from sglive. Audits/publishes `tournament.prize_pool_updated`. |
| `fetch_sglive_games` / `set_sglive_links` / `unlink_sglive` / `sync_sglive_pools` / `apply_sglive_games` | `Tournament` | The sglive.speedgaming.org pool sync; see [features/payouts.md](../features/payouts.md#syncing-from-sglivespeedgamingorg). |
| `set_split(tournament_id, rows, actor)` | `PayoutSplit` | Replaces the whole split in one transaction. Audits/publishes `tournament.payout_updated`. |
| `set_entrant(payout_id, user_id, actor)` | `TournamentPayout` | Names (or un-names) the winner on one drafted row. |
| `export_block(tournament_id, actor)` | `str` | The block pasted into the admin thread; an entrant with no `matcherino_username` is called out by name. |

Module helpers: `PayoutService.amount_for(total, percentage)` (static; rounds per row to the cent, so a 100% split can total a cent under the pool) and `format_percentage(value)` (`50.00` → `'50'`). `PayoutSplit` / `PayoutLine` / `PayoutOverview` are the read shapes.

Validation raises `ValueError`: shares summing above 100 (below is allowed), a place under 1, a share of 0 or less, and a negative pool or bonus.

Collaborators: `TournamentPayoutRepository`, `AuditService`, `AuthService`.

### triforce_text_service.py — TriforceTextService

Community-submitted ALTTP end-game triforce texts, scoped per tournament, with a moderation workflow (Staff or that tournament's TAs). Validation enforces exactly 3 lines, ≤19 characters each, restricted to the ALTTP text-engine character set, at least one non-blank line. Feature doc: [triforce-texts.md](../features/triforce-texts.md).

| Method | Returns | Description |
|---|---|---|
| `submit(tournament_id, lines, user)` | `TriforceText` | Validate and store a submission; rejects anonymous users, unknown or inactive tournaments; audits `triforce_text.submitted`. |
| `list_supporting_tournaments()` | `list[Tournament]` | Active tournaments whose generator can embed texts (`TRIFORCE_TEXT_RANDOMIZERS`). |
| `list_user_submissions(tournament_id, user)` | `list[TriforceText]` | A user's own submissions (empty list for unknown tournament/user). |
| `list_for_moderation(tournament_id, status=None)` | `list[TriforceText]` | Submissions filtered by `None`/`'pending'`/`'approved'`/`'rejected'`. |
| `moderate(text_id, approved, actor)` | `TriforceText` | Approve or reject; permission failure raises `ValueError` (not `PermissionError`); audits approved/rejected variants. |
| `delete(text_id, actor)` | `None` | Delete a submission under the same permission rule; audits `triforce_text.deleted`. |
| `get_balanced_text(tournament)` | `str \| None` | Random submitter first, then a random approved text from them — equal weight per submitter (deleted-user texts form their own bucket). |
| `get_random_text(tournament)` | `str \| None` | Uniformly random approved text. |

Collaborators: `TriforceTextRepository`, `AuditService`, `AuthService`.

### identity_link_service.py — IdentityLinkService, TwitchService, RacetimeService

The **provider-parameterized account-linking engine** behind both secondary-identity integrations. A logged-in user completes a one-time OAuth login with the provider so their verified identity is recorded on their `User`; the access token is used once during linking and discarded — identity only, never a stored credential. `TwitchService` and `RacetimeService` are thin public shells that construct an `IdentityLinkProvider` and delegate every call, so the two flows cannot drift.

| Method | Returns | Description |
|---|---|---|
| `is_configured()` | `bool` | OAuth app credentials are present (or the provider's mock is on). |
| `player_authorize_url(state)` | `str` | Provider OAuth authorize URL to redirect the browser to. |
| `redirect_uri()` | `str` | The registered OAuth redirect URI (provider env var, else derived from `BASE_URL`). |
| `build_oauth_client()` | client | The real or mock client for the provider (`_oauth_client()` on the shells). |
| `exchange_player_code(client, code)` | `dict` | Exchange a user's auth code (via the client from `build_oauth_client`; the shells' `exchange_player_code(code)` builds it for you) and return their identity (token used once, discarded). Twitch returns `{user_id, username, display_name}`; racetime `{user_id, username}`. |
| `record_player_link(user, provider_user_id, provider_username, actor)` | `None` | Persist the linked identity on the `User`; rejects ids already linked elsewhere (`ValueError`); audits the provider's `*.linked` action. |
| `unlink_player(user, actor)` | `None` | Clear the link; audits the provider's `*.unlinked` action. |

`IdentityLinkProvider` carries everything that differs between the two:

| | Twitch | racetime.gg |
|---|---|---|
| `field_prefix` on `User` | `twitch_*` | `racetime_*` |
| Scope | none (public identity needs none, keeping consent minimal) | `IDENTITY_SCOPE` (read) |
| Audit actions | `twitch.linked` / `twitch.unlinked` | `racetime.linked` / `racetime.unlinked` |
| Mock switch | `MOCK_TWITCH` | `MOCK_RACETIME` |
| Client | `TwitchClient` / `MockTwitchClient` | `RacetimeClient` / `MockRacetimeClient` |

These are the **identity-link** OAuth credentials only; the race-room bots use their own per-category credentials on `RacetimeBot` rows. Collaborators: `AuditService`, the [OAuth identity clients](#oauth-identity-clients), the [mock switches](#mock-switches).

### browser_monitoring_service.py — BrowserMonitoringService

Browser-side Sentry behind `/platform` → Monitoring ([observability.md](../features/observability.md#browser-errors-platform--monitoring)). SUPER_ADMIN-gated, no tenant context; audit rows are platform-level (`tenant=NULL`). Every change reloads the in-memory snapshot (`application/utils/browser_monitoring.py`) that page builds read; the page then pushes it to open tabs (`theme/browser_monitoring.push_to_open_tabs`).

| Method | Returns | Description |
|---|---|---|
| `load()` | `Snapshot` | Rebuild the snapshot from the database. Startup (`main.py` lifespan) and after each change. |
| `get_settings(actor)` | `SettingsView` | Send / replay / share, with who changed it and when. |
| `update_settings(actor, *, enabled, replay_on_error, sample_rate)` | `Snapshot` | `sample_rate` must be one of `SAMPLE_RATES`; audits `browser_monitoring.updated` with the previous values. |
| `list_rules(actor)` | `[IgnoreRuleRow]` | Added rules (oldest first), then the built-ins (`id=None`). |
| `add_rule(actor, pattern, match)` | `Snapshot` | 3–200 characters, `message` or `url`, not a built-in or existing rule; audits `browser_ignore_rule.added`. |
| `remove_rule(actor, rule_id)` | `Snapshot` | Audits `browser_ignore_rule.removed`. |

Collaborators: `AuditService`, `BrowserMonitoringRepository`.

### log_level_service.py — LogLevelService

Runtime per-logger levels behind `/platform` → Logging ([observability.md](../features/observability.md#runtime-log-levels-platform--logging)). SUPER_ADMIN-gated, no tenant context; audit rows are platform-level (`tenant=NULL`). A change is applied to the live logging tree and stored as a `LogLevelOverride`.

| Method | Returns | Description |
|---|---|---|
| `list_rows(actor)` | `[LoggerLevelRow]` | Every known logger (`log_levels.KNOWN_LOGGERS`, root first) plus any custom overridden one, with the stored level, the effective level, and who/when. |
| `set_level(actor, logger_name, level)` | `None` | Validate name and level, upsert, apply; audits `log_level.set` (with the previous level). |
| `clear(actor, logger_name)` | `None` | Delete the override; the logger inherits again (root returns to `LOG_LEVEL`); audits `log_level.cleared`. No-op if none. |
| `apply_persisted()` | `int` | Startup only (`main.py` lifespan, after `init_db`): reapply every row, skipping and logging any that no longer parses. |

Collaborators: `AuditService`, `LogLevelOverrideRepository`, [`log_levels.py`](#log_levelspy).

### runtime_stats_service.py — RuntimeStatsService

Live process load for the super-admin `runtime_stats` MCP tool ([observability.md](../features/observability.md#live-load-runtime_stats)). Stateless; no tenant context.

| Method | Returns | Description |
|---|---|---|
| `snapshot(actor)` (static) | `dict` | `ensure_super_admin`, then `application/utils/runtime_stats.collect()`: uptime, release, RSS, asyncio task count, NiceGUI clients, DB pool, the loop watchdog's last minute, every `BackgroundLoop`'s timing, and Watch-page audiences. |

### racetime_bot_service.py — RacetimeBotService

Platform administration of the shared, **global** racetime bots (one per game category, each holding that category's OAuth credentials). All mutations are SUPER_ADMIN-gated and run on `/platform` with no tenant context, so their audit rows are platform-level (`tenant=NULL`). The `client_secret` is a privileged secret: `serialize()` omits it, `update_bot` only rewrites it when a new value is supplied (blank = unchanged), and the audit log records only that the field changed — never its value.

| Method | Returns | Description |
|---|---|---|
| `serialize(bot)` (static) | `dict` | Secret-free view for admin tables (omits `client_secret`). |
| `list_bots(actor)` / `get_bot(actor, id)` | `[RacetimeBot]` / `RacetimeBot` | SUPER_ADMIN reads. |
| `create_bot(actor, *, category, client_id, client_secret, name, …)` | `RacetimeBot` | Create; unique category; audits `racetime_bot.created`. |
| `update_bot(actor, id, *, client_secret=None, …)` | `RacetimeBot` | Update; blank secret keeps the stored one; audits `racetime_bot.updated`. |
| `delete_bot(actor, id)` | `None` | Delete; audits `racetime_bot.deleted`. |
| `grant_tenant(actor, bot_id, tenant_id)` | `RacetimeBotTenant` | Authorize a tenant (idempotent; re-activates a suspended grant); audits `racetime_bot.granted`. |
| `revoke_tenant(actor, bot_id, tenant_id)` | `None` | Remove a grant; audits `racetime_bot.revoked`. |
| `restart_connection(actor, bot_id)` | `RacetimeBot` | SUPER_ADMIN; drops and respawns the bot's connection via `racetimebot.get_racetime_manager().restart` (which audits `racetime_bot.restarted`; `ValueError` when `RACETIME_BOT_ENABLED` is off). |
| `list_grants(actor, bot_id)` | `[RacetimeBotTenant]` | Grants for a bot (SUPER_ADMIN). |
| `list_authorized_for_tenant(tenant_id)` | `[RacetimeBot]` | Tenant-facing: active bots a tenant may select (no secret exposed; explicit tenant id). |
| `is_authorized_for_tenant(bot_id, tenant_id)` | `bool` | Whether a tenant may select a given bot. |

Runtime health methods are **not** SUPER_ADMIN-gated — the caller is the trusted in-process connection loop acting as the system user, writing platform-level (`tenant=NULL`) health rows. `client_secret` never appears in them.

| Method | Returns | Description |
|---|---|---|
| `list_active_bots()` | `[RacetimeBot]` | Active bots the runtime should hold a connection for. |
| `get_runtime_bot(id)` | `RacetimeBot?` | Load a bot with no permission gate (runtime/restart). |
| `record_connected(id, actor)` | `None` | `status=connected`, `last_connected_at`/`last_checked_at`; audits `racetime_bot.connected`. |
| `record_heartbeat(id)` | `None` | Refresh `last_checked_at` only (un-audited liveness tick). |
| `record_error(id, actor, msg, *, auth_failed=False)` | `None` | `status=error` + message; audits `racetime_bot.error` (with `auth_failed`). |
| `mark_disconnected(id, actor, msg=None)` | `None` | `status=disconnected`; audits `racetime_bot.disconnected`. |

Collaborators: `AuditService`, `RacetimeBotRepository`.

#### racetimebot/ — the racetime bot runtime

A peer of `discordbot/`, not part of `application/services/` — presentation layer: it calls services, never repositories. Started from the FastAPI lifespan when `RACETIME_BOT_ENABLED` is on; a no-op otherwise. Tenant resolution here is 1:1 (slug → room → tenant), the opposite of `discordbot/`'s fan-out — see [online-tournaments.md](../features/online-tournaments.md#two-bot-runtimes-opposite-tenant-resolution-models).

| Module | Holds |
|---|---|
| `manager.py` | `RacetimeBotManager` singleton — one `CategoryConnection` task per active bot, re-adopts open rooms on boot, `restart(actor, id)` |
| `connection.py` | `CategoryConnection` — the supervised loop: authenticate → `record_connected` → run with heartbeats. Auth error → `record_error(auth_failed=True)` and stop; transient → `record_error` + exponential backoff capped at 5 min; graceful stop → `mark_disconnected` |
| `handler.py` | `RaceHandler` — resolves slug → room → tenant via the unscoped lookup, binds `tenant_scope`, delegates to a lifecycle object; a raising handler is caught so it never kills the bot |
| `transport.py` | The `RacetimeTransport` seam: `RealRacetimeTransport` (`client_credentials` token fetch + liveness loop), the `RaceRoomEvent`/`RaceEntrant`/`EntrantStatus` value types, `RacetimeAuthError`/`RacetimeTransientError` |
| `mock.py` | `MockRacetimeTransport` — the scripted fake for `MOCK_RACETIME` and tests |

### race_room_profile_service.py — RaceRoomProfileService

Tenant-scoped CRUD for reusable racetime room settings (the `startrace` parameters a community reuses). Gated by `AuthService.can_manage_sync` (SYNC_ADMIN/STAFF/super-admin); audited (`race_room_profile.created|updated|deleted`). `list_selectable()` is an ungated read used to populate the tournament dialog's profile select.

| Method | Returns | Description |
|---|---|---|
| `list_profiles(actor)` / `get_profile(actor, id)` / `list_selectable()` | `[RaceRoomProfile]` | Gated list and single read; plus the ungated read that fills the tournament dialog's profile select. |
| `create_profile(actor, *, name, **fields)` | `RaceRoomProfile` | Create; per-tenant unique name; rejects negative timers. |
| `update_profile(actor, id, *, name=None, **fields)` | `RaceRoomProfile` | Update. |
| `delete_profile(actor, id)` | `None` | Delete. |

Collaborators: `AuditService`, `RaceRoomProfileRepository`.

### racetime_room_service.py — RacetimeRoomService

Record lookup + status writes for race-room→tenant routing. `get_by_slug(slug)` is the **unscoped** entry point the inbound-event router uses (a racetime event carries only the slug, no tenant); `get_for_match(match)` finds a match's room; `list_open_rooms()` returns not-yet-terminal rooms across all tenants (unscoped, for boot-time re-adoption) and `list_open_rooms_for_current_tenant()` the scoped equivalent for the API; `set_status(room, status)` writes the cached lifecycle status (stamping `opened_at` the first time a room reaches `in_progress`). The richer create/seed/result lifecycle lives in [`RaceRoomService`](#race_room_servicepy--raceroomservice).

Collaborators: `RacetimeRoomRepository`.

### race_room_service.py — RaceRoomService

The racetime room lifecycle mapped onto a `Match` — the business layer both the auto-open worker and the `racetimebot/` handlers call. Tenant-scoped (callers bind the tenant); **acts as the system user**; audits + publishes a `race_room.*` event on every transition.

| Method | Returns | Description |
|---|---|---|
| `create_room_for_match(match, *, actor=None, attach_seed=True)` | `RacetimeRoom` | Idempotent open: one room per match, serialised per match (`room_open_lock`) so the poll and the series push can't open two. Requires the tournament's authorized bot and a goal (the `RaceRoomProfile`'s, else `racetime_default_goal`; `ValueError` without one). Opens the room on racetime.gg first (`start_racetime_room` → the bot's `startrace` with the profile's settings), then writes the row under **racetime's returned slug**, already `OPEN`; a refused `startrace` raises `ValueError` and leaves no row, audit or DM. DMs each opted-in player a **Join the race room** button (`notification_links.race_room`), attaches the seed via `MatchScheduleService.generate_seed`. Emits `race_room.created` + `race_room.opened`. `@requires_feature(RACETIME_ROOMS)`. |
| `manual_create_room(actor, match_id)` | `RacetimeRoom` | STAFF/`SYNC_ADMIN`-gated manual open, ignoring the auto toggle. `@requires_feature(RACETIME_ROOMS)`. |
| `open_rooms_for_player(user)` | `List[RacetimeRoom]` | Open or running rooms on matches the user plays in, soonest first — the My Schedule **Your race room is open** card. `@requires_feature(RACETIME_ROOMS)`. |
| `auto_open_if_eligible(match, *, now, actor=None)` | `RacetimeRoom \| None` | Open the room iff every automatic-open condition holds (auto toggle, lead window, authorized bot, linked entrants); `None` when not eligible yet. Shared by the 60 s poll and the series push (`BracketService.release_next_game`); deliberately not folded into `create_room_for_match`, which the manual override also reaches. |
| `mark_in_progress(room, *, actor=None)` | `None` | Sets the match's `started_at`; emits `race_room.started`. Like `record_finish` and `cancel_room`, deliberately ungated: it drives a room that already exists, and switching the flag off mid-race must not drop the result. |
| `record_finish(room, entrants, *, actor=None)` | `None` | Maps entrants → linked `User`, records `finish_rank` (place) + `finish_time` (seconds), closes the match, feeds the existing result path (+ optional Challonge push). Handles forfeit / no-show / DQ / one-finisher; unmatched handles go to the audit for reconcile. Emits `race_room.finished` + `race_room.result_recorded` + `match.result_recorded`. |
| `cancel_room(room, *, actor=None, reason=None)` | `None` | Marks the room cancelled; emits `race_room.cancelled`. |

Module-level helpers shared with the qualifier live-race path: `start_racetime_room(bot, settings)` opens a room through `racetime_rooms_client` and maps a `RacetimeAPIError` to a `ValueError` carrying racetime's reason; `room_open_lock(key)` is the per-key `asyncio.Lock` (process-local, which the single-worker deployment makes sufficient).

`RaceRoomLifecycle` (same module) is the adapter the `racetimebot/` handler injects: `handle_event(room, event)` translates a transport `RaceRoomEvent` into the matching transition. Collaborators: `RacetimeRoomRepository`, `AuditService`, `AuthService`, `UserService`, `MatchScheduleService`, `ChallongeService`, the event bus.

**race_room_worker.py** — the auto-open background loop (peer of `volunteer_reminder`). Every 60 s it scans (cross-tenant, unscoped) not-yet-finished matches on auto-create tournaments in a wide window, plus slipped best-of-N games the window would miss (`BracketService.series_matches_due`), drops later series games still waiting on an earlier one (`held_match_ids`), then per match — inside `tenant_scope` — calls `RaceRoomService.auto_open_if_eligible`, which opens a room when it enters that tournament's `room_open_minutes_before` lead, is idempotent (one room per match), has an authorized bot, and every entrant has a linked racetime identity. Started from the lifespan only when `RACETIME_BOT_ENABLED` is on.

**match/stage_reminder.py** — the pre-match stage reminder loop (same shape as `volunteer_reminder`). Every 60 s it scans (cross-tenant, unscoped, via `MatchRepository.due_for_stage_reminder`) unfinished, unstamped matches that have a stage and are scheduled within `MAX_LEAD_MINUTES` (24 h), then per match — inside `tenant_scope` — re-checks it against its own tournament's `stage_reminder_minutes`, stamps `stage_reminder_sent_at` **before** enqueuing `MatchScheduleService.notify_stage_reminder`, and leaves anything still outside its lead unstamped for a later tick. A lead of 0 sends nothing; a lead beyond the scan window logs a warning and is still reminded once the match enters the window. Publishes no event: a reminder observes a match nobody changed. Started and stopped from the lifespan, ungated.

**async_qualifier/async_qualifier_worker.py** — the run-expiry loop (same shape). Every 60 s it takes every started, still-in-progress qualifier run (cross-tenant, unscoped — each qualifier configures its own limit, so no single age cutoff is right for all of them, and the set is small: one active run per player per qualifier), then per run — inside `tenant_scope` — warns once ahead of its deadline and forfeits it past it, via `RunExpiryMixin.warn_run_expiring` / `expire_run`. The same tick releases review claims that have aged out (`release_stale_claim`) and, per active qualifier, publishes window open/close crossings (`sync_window_state`) and nudges reviewers about a stale queue (`notify_review_backlog`). A tenant with `ASYNC_QUALIFIERS` off is skipped rather than raising. Always started; a tenant without the feature has no runs to find.

### sent_message_service.py — SentMessageService

The log of every DM ([`SentMessage`](data-model.md#sentmessage)). `send_dm` (real
and mock) calls `record_attempt` before sending, so the web-push and
email/Telegram mirrors can report back with `record_push` / `record_channels`
against the returned `SentMessageRef`, then `record_result` with the Discord
outcome (`sent_message_status` in `discord_service.py` maps the result tuple).
Every write swallows its own failure: logging never costs a DM. Reads
(`list_messages`, `count_messages`, `totals`, `get_message`) take a keyword-only
`actor` and are Staff only.

### exports/ — DataExportService, data_export_worker

The export queue ([data-exports.md](../features/data-exports.md)).
`DataExportService` takes requests (`request_tournaments`, `request_volunteers`,
`record_table_csv`), enforces who may export what and the throttle, and serves
the requester's own exports (`list_mine`, `get_mine`, `download`). `build(export)`
is the worker's entry point and never raises. `tournament_export_builder` turns
one tournament into the versioned JSON section and the CSVs. `data_export_worker`
(`run_worker_loop`, 5 s tick) builds one queued export per tick, fails
interrupted ones and prunes anything older than three days.

### member_details_service.py — MemberDetailsService

One member's footprint in the current community, for Admin → Members' member
sheet, `GET /api/users/{id}/details` and MCP `get_member_details`.
`get(member_id, *, actor)` is Staff only and raises `NotFoundError` for anyone
who isn't a member here. It returns `MemberDetails`: roles, tournaments,
upcoming matches and shifts, equipment out, check-in state (each `None` when
its feature is off), linked accounts, `Channels` (email and Telegram as states,
never addresses), the preset, every notice as `NoticeDetail` (community setting,
their choice, effective mode and a `reason`; built from
`NotificationPreferenceService.get_mine(member)`), and the last 10 sent
messages. The emergency contact stays behind `EmergencyContactService.open_for`.

### service_health_service.py — ServiceHealthService

Platform external-service health monitor. **Computed-and-cached, no persistence model**: an async probe registry (`_PROBES`) whose results live in a process-global in-memory cache (`_CACHE`, owned by the single worker/on-demand refresh — safe module-level state). `ServiceStatus` = `healthy / mock / degraded / credential_warning / down / unknown` (`mock` = the local mock stands in) (`credential_warning` is the distinct "up but a credential is expiring/rejected" signal). Each probe is wrapped with a hard timeout so one hung dependency can't stall the board; a raising probe becomes `down`.

| Method | Returns | Description |
|---|---|---|
| `refresh(*, alert=True)` | `list[ProbeResult]` | Run every probe concurrently, update the cache, and — on a transition *into* down / credential-warning — fire an alert. Sorted worst-first. |
| `snapshot()` | `list[ProbeResult]` | Cached results (UNKNOWN placeholders for not-yet-probed keys); the board's instant initial paint. |
| `tenant_subset(tenant_id)` | `list[ProbeResult]` | The read-only subset a tenant's STAFF may see — its authorized racetime bots + its own Challonge connection — probed live (not from the platform cache). Caller binds the tenant scope. |

Probes, by kind: **round-trip** — PostgreSQL; **reachability** — SpeedGaming, Challonge, and the seed-gen upstreams alttpr.com / ootrandomizer.com / maprando.com; **config presence** — Discord OAuth, Twitch OAuth, web-push/VAPID, Sentry; **runtime state** — the Discord bot's gateway readiness and `RacetimeBot.status` (an `error` with an auth message maps to credential-warning). Challonge additionally checks token expiry across all tenants via `ChallongeRepository.list_all_connections()`, an explicitly-unscoped read. Alerting: `refresh` publishes `EventType.SERVICE_HEALTH_ALERT` (a `down` result must repeat on the next check before it alerts; credential warnings alert immediately), which is platform-level so no tenant webhook fires; the real channels are Sentry `capture_message` plus an optional super-admin DM under `SERVICE_HEALTH_ALERT_DM`.

**service_health_worker.py** — the periodic probe loop (peer of `discord_event_worker`). Every 120 s it calls `ServiceHealthService().refresh()` with **no tenant scope** (all probes are platform-level). Always started from the lifespan; the board can also refresh on demand. A dependency in mock mode (`MOCK_DISCORD`, `MOCK_CHALLONGE`, `MOCK_SEEDGEN`, …) reports `mock` instead of being contacted. The loop never dies — a failing tick is logged and retried.

### speedgaming_etl_service.py — SpeedGamingETLService

One-way SpeedGaming → Wizzrobe schedule ETL. Per active `SpeedGamingEventLink` the sync worker (never a UI caller) drives the pipeline: **extract** a forward schedule window from the SG API (`SpeedGamingClient`), **transform** each episode to UTC and resolve every player to a `User` via the placeholder pattern, **load** by upserting a `SpeedGamingEpisode` staging row and materializing/refreshing its `Match` + `MatchPlayers`. Runs as the reserved **system `User`**, always inside the worker's `tenant_scope`. Two race-day guards uphold the hybrid read-only contract: a re-sync **skips** a match that is finished / manually progressed / racetime-linked, and **auto-finishes** SG-sourced matches more than 4h past their scheduled time (unless a room is linked). The per-field lock on a sourced match is [`match_source_guard.py`](#match_servicepy--matchservice). Audited under `sg_sync.*`; module constants `BACKFILL_GRACE_HOURS = 1`, `AUTO_FINISH_HOURS = 4`, `TITLE_MAX_LENGTH = 255`.

Three details of the live SG wire shape the transform exists to absorb (the shape itself is pinned by the `SG_*_KEYS` manifests in [`speedgaming_client.py`](../../application/utils/clients/speedgaming_client.py)):

- **Titles are mostly empty.** SG serves an episode-level `title` but leaves it blank on every episode; the matchup name is `match1.title`, and when *that* is blank too (a TBD matchup, or a doubleheader labelled per-slot) the side's free-text `note` — "Swiss Round 5", "Game 2" — is the only descriptor. `_episode_title` walks episode title → `match1`/`match2` title → `match1`/`match2` note, then truncates to the column width.
- **`unchanged` is decided on a projection, not the whole payload.** `_sync_fingerprint` hashes only what the loader consumes (`when`, resolved title, and each player's id/name/discord identity). Crew carry `approved` and `ready` flags that flip as people sign up and staff approve them, so hashing the raw payload re-materialized the match on nearly every poll — an audit row and a published event for a change the ETL does not read. The raw snapshot is still refreshed on the unchanged path, so `SpeedGamingEpisode.payload` tracks upstream regardless. **Widen the fingerprint whenever the loader starts reading another field.**
- **Absent means `''`, not `null`.** Every SG scalar is a string that is empty when unset (`discordId: ''` for a player with no linked Discord), and `match2` is the only genuinely nullable field. Identity resolution reads for truthiness and only parses `discordId` once it looks like a snowflake, so a malformed id degrades to tag/placeholder resolution instead of failing the episode.

SG's crew lists (`commentators`, `trackers`, `broadcasters`, `helpers`), restream `channels`, and episode `length` are **not** imported — they are retained only in `SpeedGamingEpisode.payload`.

| Method | Returns | Description |
|---|---|---|
| `sync_event_link(link, *, actor, now=None)` | `SyncResult` | Sync one active link (assumes ambient tenant = `link.tenant`): import every episode in the window, soft-detach episodes that vanished upstream, auto-finish stale matches, and record the link's observability fields (`last_synced_at`/`last_status`/`last_error`). Never raises on a per-episode failure — failures are tallied. |
| `import_episode(link, raw, *, actor, now=None)` | `str` | Transform+load one raw SG episode: upsert its staging row and materialize/refresh its match. Returns the outcome key (`'imported'`/`'unchanged'`/`'skipped'`) for the caller to tally; raises on a transform/load failure so the caller can mark that episode ERROR. |

`SyncResult` (same module) is the per-run tally dataclass — `imported`, `unchanged`, `skipped`, `cancelled`, `auto_finished`, `errors`, `error_messages` — with `as_dict()` for the audit/observability payload. Collaborators: `SpeedGamingEpisodeRepository`, `SpeedGamingEventLinkRepository`, `MatchRepository`/`MatchService`, `UserRepository`, `AuditService`, the event bus, `SpeedGamingClient`.

### speedgaming_sync_service.py — SpeedGamingSyncService

The human-driven management surface over the SG ETL: CRUD of `SpeedGamingEventLink` rows (which SG event slug feeds which tournament) and an on-demand "sync now" for one link. Every mutation is gated by `AuthService.can_manage_sync` (STAFF / super-admin / `SYNC_ADMIN`) and audited under `sg_sync.*`. The background worker calls the ETL directly as the system user; this service is the admin-facing peer.

| Method | Returns | Description |
|---|---|---|
| `list_links(actor)` | `list[SpeedGamingEventLink]` | The tenant's event links. |
| `list_episodes(actor, event_link_id)` | `list[SpeedGamingEpisode]` | Staging episodes imported for one link (observability). |
| `create_link(actor, *, tournament_id, event_slug, content_type=None, sync_interval_minutes=15, lookahead_hours=72, active=True)` | `SpeedGamingEventLink` | Link an SG event slug to a tournament; non-empty slug and existing tournament required; rejects a duplicate `(tournament, slug)` (`ValueError`). Audits `sg_sync.event_link_created`. |
| `update_link(actor, link_id, *, event_slug=None, content_type=None, sync_interval_minutes=None, lookahead_hours=None, active=None)` | `SpeedGamingEventLink` | Partial edit; re-checks slug uniqueness on change. Audits `sg_sync.event_link_updated`. |
| `delete_link(actor, link_id)` | `None` | Remove a link; audits `sg_sync.event_link_deleted`. |
| `sync_now(actor, link_id)` | `SyncResult` | Run `SpeedGamingETLService.sync_event_link` for one link on demand and return the run tally. |

Collaborators: `SpeedGamingEventLinkRepository`, `SpeedGamingEpisodeRepository`, `TournamentRepository`, `SpeedGamingETLService`, `AuthService`, `AuditService`.

**speedgaming_sync_worker.py** — the ETL background loop. Every 60 s it loads every active link (cross-tenant, unscoped), then per link — inside `tenant_scope` via `for_each_tenant_scoped`, as the system user — skips a link not yet due on its own `sync_interval_minutes` cadence or whose tenant has `SPEEDGAMING_ETL` off, and otherwise runs `SpeedGamingETLService.sync_event_link`. Per-link failures are logged and retried next tick. Started from the lifespan only when `SPEEDGAMING_SYNC_ENABLED` is on.

### user_service.py — UserService

User lookup, profile edits (self- and admin-driven), activation, per-tenant role grants, and tournament enrollment management. Audited under `user.*`; role grants gated by `can_grant_roles`, admin fields by `is_staff`.

| Method | Returns | Description |
|---|---|---|
| `get_user_by_discord_id(discord_id)` / `get_user_by_id(user_id)` | `User \| None` | Lookups the entry surfaces use instead of touching `UserRepository`. |
| `get_all_users(role=None, has_discord=False)` | `list[User]` | **Every account on the platform.** Identity is global, so this is a platform-level list; the only caller is `/platform`'s first-admin picker. |
| `get_system_user()` | `User` | Resolve the reserved automation actor (get-or-create on the sentinel `discord_id`, idempotent). Workers/bots pass it as `actor` so audit rows snapshot a real username. |
| `get_current_user_from_storage(storage_discord_id)` | `User \| None` | Resolve a storage-held Discord id to a `User` (`UserService` variant of the module-level `get_user_from_discord_id`). |
| `get_community_people(*, role=None, has_discord=False, include_user_ids=None, include_inactive=False)` | `list[User]` | **The people of the tenant in scope** — every per-community picker, the Users tab, `GET /users` and MCP `list_users`. `User` is global, so belonging is derived, and `TenantMembership` derives it: the same basis the access gate checks, so a picker cannot offer someone the app would turn away. (It used to union role-holders with tournament entrants, because membership was a frozen backfill nothing wrote to; the membership work closed that and the old union is a strict subset.) `include_user_ids` force-includes specific people (the actor; an asset's current holder) so they stay resolvable. Excludes the system account by **both** its flag and its sentinel id, and deactivated accounts unless `include_inactive` — which the match dialog passes, since a SpeedGaming placeholder is inactive by construction. Raises with no tenant in scope. **A picker default, not an authorization rule** — the hard rules live in the acting service and are narrower. |
| `provision_from_discord_role_sync(discord_id, username, avatar=None)` | `(User, bool)` | Get-or-create the account for a guild member who holds a mapped role but has never signed in. A new account audits `user.provisioned` (`source: discord_role_sync`); an existing one is returned untouched. |
| `provision_from_discord_login(discord_id, username, avatar=None)` | `(User, bool)` | Get-or-create the account for a real Discord OAuth login; returns `(user, created)`. A new account writes a self-attributed `user.provisioned` audit entry; an existing active account has its username and avatar hash synced (`avatar=None` leaves the stored hash alone, `''` clears it; inactive accounts are returned untouched for the caller to reject). |
| `sync_discord_avatar(discord_id, avatar)` | `bool` | Record the avatar hash Discord reports (the bot's member-update path); `True` when it changed. Unknown users are a no-op — no account is provisioned off a presence event. |
| `create_mock_login_user(discord_id, username, display_name=None, role_values=None)` | `User` | Dev-only (`MOCK_DISCORD`) account + role provisioning for the mock login picker; no permission check, but writes a `user.provisioned` audit entry (`source: mock_login`). |
| `get_user_tournament_registrations(user)` | `list[TournamentPlayers]` | The user's enrollment rows. |
| `update_user_personal_info(user, actor, display_name=None, pronouns=None, dm_notifications=None, matcherino_username=None)` | `User` | Self-profile edit (page-level auth assumed); blank strings become `None`; audits only when something changed. |
| `update_user_tournament_registrations(user, actor, selected_tournament_ids, current_registrations)` | `None` | Diff-and-apply enrollment set; audits added/removed tournament ids. |
| `create_user(username, actor, display_name=None, pronouns=None, is_active=True, discord_id=None)` | `User` | Staff-only manual user creation; non-empty username required. |
| `update_user_profile(user, actor, display_name=None, pronouns=None, check_concurrency=False, initial_updated_at=None)` | `User` | Edit display name/pronouns — allowed for self or Staff (`PermissionError` otherwise); optional optimistic-concurrency check on `updated_at` raises `ValueError` on conflict. |
| `update_user_admin_fields(user, actor, is_active=None, check_concurrency=False, initial_updated_at=None)` | `User` | Staff-only activation toggle with the same concurrency option; audits `user.activation_changed` only on a real change. |
| `grant_role(target, role, actor)` | `None` | `can_grant_roles`-gated; add a `UserRole` row (`source=manual`) recording who granted it, and ensure tenant membership. Refuses `SUPER_ADMIN` outright (`PermissionError`) — its only grant path is `TenantService.grant_super_admin`. |
| `revoke_role(target, role, actor)` | `None` | Remove a `UserRole` row; same gate and `SUPER_ADMIN` refusal. |
| `manage_tournament_enrollments(user, actor, tournament_ids, is_update=True)` | `None` | Update mode diffs against current enrollments; create mode (new users) only adds. |

Collaborators: `UserRepository`, `UserRoleRepository`, `TournamentRepository`, `TenantMembershipService`, `AuditService`, `AuthService`.

## Volunteering

The onsite volunteer subsystem is one service per concern plus a background reminder loop. The data flow: a community role (VOLUNTEER or STAFF) puts someone in the pool (`VolunteerPoolService`), and they declare availability (`VolunteerAvailabilityService`); coordinators define positions (`VolunteerPositionService`), track per-user qualifications (`VolunteerQualificationService`), and generate/assign shifts (`VolunteerScheduleService`), optionally seeding a draft from the pool (`VolunteerAutoscheduleService`); the `volunteer_reminder` loop DMs upcoming-shift reminders. All coordinator-side mutations are gated by `AuthService.can_manage_volunteers` and audited under `volunteer.*`.

A draft assignment (`auto_generated=True`) is the coordinator's sketch: it is excluded from the volunteer's own shift list and from the reminder sweep, cannot be acknowledged, and is deleted wholesale by `clear_draft`. `publish_draft` is what makes one real — it flips the flag, DMs the volunteer with the acknowledgment button, and emits `volunteer.assigned` per row. Coordinator-facing counts (the grid's badges, `coverage`, the CSV export, `volunteer_hour_trends`) deliberately keep counting drafts: the invariant is about what the *volunteer* has been told, not about the coordinator's arithmetic.

### volunteer_schedule_grid.py — the Full Schedule and Shift Board day grid

`VolunteerScheduleService.day_grid(viewer, day)` (gated on `VOLUNTEERS`, refuses anyone `AuthService.can_view_volunteer` rejects with `PermissionError`) loads the shifts overlapping one event day, 06:00 to 06:00 local so a late shift stays on the evening it starts, and hands them to the pure `build_day_grid(shifts, midnight, viewer_id)`. That returns a `DayGrid`: one `GridColumn` per position in repository order, each `GridShift` carrying its hours from local midnight (clipped to the day), the published names (draft assignments are left out), open slots, whether the viewer is on it, and a `lane`/`lanes` pair that puts overlapping shifts of one position side by side.

`board_day_grid(actor, day)` is the Shift Board's version (gated on `VOLUNTEERS`, coordinator-only): `build_day_grid(..., for_staff=True, positions=..., now=...)` keeps drafts (sorted after published names), gives every active position a column even with no shifts, and stamps each `GridPerson` with its `assignment_id`, `draft`, `acknowledged` and an `arrival` of `ARRIVED`, `LATE` or `NOT_HERE` once the shift has started (`arrival_state`). Each `GridShift` also carries `shift_id`, `slots_needed` and `notes`.

Per-viewer columns: `grid_roles(viewer)` (same gate) lists the community's active positions as `(id, name)`; `arrange_roles(positions, saved)` turns the stored column list into `GridRole`s in the viewer's order (unknown ids skipped, new positions appended shown); `apply_roles(grid, roles)` drops hidden columns, reorders the rest and re-fits the hour span; `roles_config(roles, saved, limit)` builds the list to store, keeping entries for other communities' positions since the preference is per user, not per tenant.

### volunteer_hours.py — VolunteerHoursService

Totals the time a volunteer actually served, against the per-tenant comp tiers from `SystemConfigService.get_volunteer_comp_tiers()`. Read-only: no `AuditActions` entry and no `EventType`, on purpose.

| Method | Returns | Description |
|---|---|---|
| `merged_hours(windows)` (module fn) | `float` | Hours covered by `[(start, end), …]`, counting overlap once. Touching windows (`a.end == b.start`) merge into one block; zero-length and inverted windows are dropped rather than subtracting. |
| `for_user(user, *, start=None, end=None)` | `HoursSummary` | One volunteer's own total, the highest tier cleared, and the next one. |
| `roster(*, start=None, end=None)` | `list[HoursSummary]` | Everyone in the pool (`VolunteerPoolService.assignable_user_ids`) plus anyone with counted hours, ordered by hours descending. |

`HoursSummary` carries `user_id`, `user_name`, `hours`, `tier_cleared`, `next_tier` and the derived `hours_to_next`.

Three rules decide what counts:

- **Published only.** `auto_generated=False` is the single predicate — a draft is the coordinator's sketch, and `release` deletes the row rather than flagging it, so a withdrawn shift needs no predicate.
- **Acknowledgement is not required.** Someone who turned up and ignored the DM still served the hours.
- **Union, not sum.** A coordinator can hand-assign overlapping shifts, so 08:00–12:00 plus 10:00–14:00 is six hours, not eight.

The window defaults to the tenant's event window (`get_event_window()`) rather than all time, so last year's shifts do not comp this year's badge, and its bounds are taken on the community's own clock. Shifts that straddle an edge are clipped to it. Both methods are `@requires_feature(FeatureFlag.VOLUNTEERS)`.

Collaborators: `VolunteerAssignmentRepository.published_for_window`, `VolunteerPoolService`, `UserRepository`, `SystemConfigService`, `TimezoneService`.

### volunteer_pool_service.py — VolunteerPoolService

The assignable-volunteer pool the coordinator UI, auto-scheduler, hours and export read. Membership is role-derived: there is no opt-in step.

| Method | Returns | Description |
|---|---|---|
| `assignable_user_ids()` | `set[int]` | Ids of every user holding the VOLUNTEER or STAFF role in the current tenant. |
| `assignable_volunteers()` | `list[User]` | The same people as users, ordered by preferred name. |

Collaborators: `UserRole` (read directly).

### volunteer_availability_service.py — VolunteerAvailabilityService

Self-service availability for anyone reaching the volunteer hub, plus the coordinator-picker lookups that flag who is available for a shift. **Method-for-method the same surface as [`PlayerAvailabilityService`](#player_availability_servicepy--playeravailabilityservice)** — `availability_for`, `set_windows`, `clear`, `availability_map`, and the two `availability_windows` delegates `covers` / `effective_segments`, over `VolunteerAvailability` rows. It reads **opt-in**: no `default` is passed, so an undeclared stretch is `None` and the shift picker treats it as no availability at all. `set_windows` has no gate of its own — the `VOLUNTEER`/`PROCTOR`/`STAFF` role on `/volunteer` is the guard, and stray rows from a user who is not in the pool are inert because the coordinator picker reads the role-derived pool. One further difference: writes are audited `volunteer.availability_updated` (not `player_availability.updated`).

Collaborators: `VolunteerAvailabilityRepository`, `AuditService`, `availability_windows`.

### volunteer_position_service.py — VolunteerPositionService

CRUD for the arbitrary, coordinator-defined position/job list. Positions optionally configure staggered rolling shifts via `shift_length_minutes`/`stagger_minutes` (both set or both blank).

| Method | Returns | Description |
|---|---|---|
| `list_all()` / `list_active()` / `get(position_id)` | reads | All positions, only active ones, one by id. |
| `create(actor, name, description=None, color=None, display_order=0, is_active=True, shift_length_minutes=None, stagger_minutes=None)` | `VolunteerPosition` | Coordinator-only; non-empty unique name required; validates the stagger config (positive, stagger ≤ shift length). Audits `volunteer.position_created`. |
| `update(actor, position, **fields)` | `VolunteerPosition` | Coordinator-only partial update; re-validates name uniqueness and stagger config when those fields change. Audits `volunteer.position_updated`. |
| `delete(actor, position)` | `None` | Coordinator-only; audits `volunteer.position_deleted`. |

`delete` also reconciles volunteer-sourced roles, since the cascade took the position's assignments and mappings with it.

Collaborators: `VolunteerPositionRepository`, `AuthService`, `AuditService`, `VolunteerRoleMappingService`.

### volunteer_role_mapping_service.py — VolunteerRoleMappingService

Maps a volunteer position onto an app `Role`. A **published** assignment to any shift of a mapped position grants the role as a `RoleSource.VOLUNTEER` `UserRole` row, and it stays while at least one such assignment exists (finished shifts count; drafts don't). Only `Role.volunteer_mappable()` may be mapped: a Volunteer Coordinator decides who's on a shift, so mapping STAFF or a subsystem-admin role would let a coordinator hand that authority to anyone, themselves included.

| Method | Returns | Description |
|---|---|---|
| `list_mappings()` | `list[VolunteerRoleMapping]` | `@requires_feature(VOLUNTEERS)`; prefetches `position`. |
| `add_mapping(actor, position_id, app_role)` | `VolunteerRoleMapping` | Staff-only (`can_grant_roles`); rejects a non-mappable role and duplicates. Audits `volunteer.role_mapping_added`, then `reconcile_all`. |
| `add_mappings(actor, position_ids, app_roles)` | `(created, already_mapped)` | Every position × every role. Checks all of them before the first write, skips existing pairs, audits each created row, then **one** `reconcile_all`. |
| `remove_mapping(actor, mapping_id)` | `None` | Staff-only. Audits `volunteer.role_mapping_removed`, then `reconcile_all`. |
| `remove_mappings(actor, mapping_ids)` | `int` | Bulk remove; skips ids already gone, audits each, then one `reconcile_all`. |
| `reconcile_users(actor, user_ids)` | `dict` | Per user: grant mapped roles they don't hold at all (membership first, `MembershipSource.ROLE_GRANT`), revoke `VOLUNTEER`-sourced rows no longer desired. Audits `role.volunteer_sync_granted` / `role.volunteer_sync_revoked`. **Never raises** and skips when the flag is off (`feature-gate: exempt` soft integration point). |
| `reconcile_all(actor)` | `dict` | `reconcile_users` over every published assignee plus every holder of a `VOLUNTEER`-sourced role. |

Called after their own commit by `VolunteerScheduleService.assign` (non-draft), `confirm_assignment`, `unassign`, `release`, `delete_shift`, `reset_all_shifts`, `update_shift` (position change only), `VolunteerPositionService.delete`, and by `DiscordRoleMappingService.sync_user_roles_for_tenant` after it revokes anything, so a Discord-held row that was standing in for a volunteer-conferred role is handed back. The reverse handoff isn't automatic: a role the volunteer sync revokes that a Discord mapping would grant comes back on the person's next login or a Sync All Users.

### volunteer_schedule_service.py — VolunteerScheduleService

Core shift/assignment operations: creating shifts (including bulk day generation with optional staggered rolling shifts), placing and removing volunteers, self-acknowledgment, and coverage. Mirrors the crew signup/approve/acknowledge flow for Discord notifications. Assignment acknowledgment timestamps are stamped in UTC.

| Method | Returns | Description |
|---|---|---|
| `list_shifts_for_window(start, end)` / `get_shift(shift_id)` | reads | Shifts overlapping `[start, end]`; one shift by id. |
| `create_shift(actor, position_id, starts_at, ends_at, label=None, slots_needed=1, notes=None)` | `VolunteerShift` | Coordinator-only; requires end after start and ≥1 slot. Audits `volunteer.shift_created`. |
| `generate_day_shifts(actor, date_str, position_ids, blocks)` | `list[VolunteerShift]` | Coordinator-only; create a day's shifts from `(label, start_HHMM, end_HHMM)` blocks (a block ending ≤ its start crosses midnight). Staggered positions instead get rolling shifts spanning the day window. Transactional; audits `volunteer.shift_created`. |
| `update_shift(actor, shift, **fields)` | `VolunteerShift` | Coordinator-only partial update; same end>start / slots≥1 validation. When `starts_at`/`ends_at` actually change, clears `acknowledged_at` on every non-draft assignment and re-asks by DM (someone who agreed to 08:00–12:00 has not agreed to 16:00–20:00), recording the count as `reacknowledge_cleared`. A slots-only or label-only edit notifies nobody; check-ins are never cleared. Audits `volunteer.shift_updated`. |
| `delete_shift(actor, shift)` | `None` | Coordinator-only; audits `volunteer.shift_deleted`. |
| `reset_all_shifts(actor)` | `int` | Coordinator-only; delete every shift; returns the count; audits `volunteer.shifts_reset`. |
| `assign(actor, shift, user, *, auto_generated=False, notify=True)` | `(VolunteerAssignment, list[str])` | Coordinator-only. Hard-fails (`ValueError`) on duplicate or overlapping assignment; returns soft warnings for overfilled shifts and stated-unavailability. Enqueues an acknowledgment-request DM unless auto-generated. Audits `volunteer.assigned`. |
| `confirm_assignment(actor, assignment, *, notify=True)` | `VolunteerAssignment` | Coordinator-only. Turn one draft into a commitment: clear `auto_generated`, DM the volunteer, emit `volunteer.assigned` with `published_from_draft: True`. Idempotent — a published assignment is returned untouched. |
| `count_drafts(start, end)` | `int` | How many unpublished drafts sit in the window (the coordinator's draft banner). |
| `unassign(actor, assignment)` | `None` | Coordinator-only; audits `volunteer.unassigned` and DMs the volunteer — unless the assignment was an unpublished draft, whose removal is silent because its creation was. |
| `release(assignment_id, user, reason=None)` | `None` | The volunteer's own decision, mirroring crew withdrawal: frees the slot immediately, records the reason and the `hours_notice` it was given with, audits + publishes `volunteer.released`, and DMs the `VOLUNTEER_COORDINATOR`/`STAFF` holders who have to find cover. Refuses someone else's assignment, a checked-in one, and a finished shift (`ValueError`). |
| `acknowledge(assignment_id, user)` | `VolunteerAssignment` | Self-acknowledge (idempotent); rejects other users' assignments and unpublished drafts (`ValueError`). Audits `volunteer.acknowledged`. |
| `assignments_for_user(user, upcoming_after=None, *, include_drafts=False, with_shiftmates=False)` | `list[VolunteerAssignment]` | A user's assignments, optionally only those starting after a cutoff. Drafts are excluded unless asked for; `with_shiftmates` adds the two joins the volunteer's "who else is on" line needs and nothing else pays for. |
| `get_assignment(assignment_id)` | `VolunteerAssignment \| None` | Read-only lookup for entry surfaces (`api/`, `discordbot/`). |
| `find_assignment(shift_id, user_id)` | `VolunteerAssignment \| None` | This volunteer's assignment on this shift, if they hold one. |
| `check_in(assignment_id, actor)` | `VolunteerAssignment` | Coordinator-only (`PermissionError` otherwise); stamp `checked_in_at`/`checked_in_by` once (idempotent). Audits `volunteer.checked_in`. |
| `VolunteerShiftMoveService.move_assignment(actor, assignment, shift)` | `(VolunteerAssignment, list[str])` | Coordinator-only. Runs `assign`'s hard checks first (ignoring the shift they're leaving, so a same-hours move to another position works), then `unassign` + `assign`: a published one gets the off-shift DM and a new acknowledgment request, a draft moves silently as a draft. |
| `VolunteerShiftMoveService.replace_assignment(actor, assignment, user)` | `(VolunteerAssignment, list[str])` | Coordinator-only swap: checks `user` can take the shift, then removes the old person and assigns the new one in the same place, keeping draft status. |

`VolunteerArrivalService` ([volunteer_arrival_service.py](../../application/services/volunteer/volunteer_arrival_service.py)) is the volunteer's own side of `check_in`, writing the same `checked_in_at`/`checked_in_by`. Every method is `VOLUNTEERS`-gated and refuses someone else's assignment or a draft (`ValueError`). The window is `CHECK_IN_LEAD` (30 minutes) before the start until the end; `can_self_check_in`, `can_undo_check_in` and `describe_late` are the pure rules the UI reads.

| Method | Returns | Notes |
|---|---|---|
| `awaiting_arrival(user)` | `list[VolunteerAssignment]` | Published, un-checked-in assignments whose window is open — the every-page banner (`theme/shift_arrival_banner.py`). |
| `check_in_self(assignment_id, user)` | `VolunteerAssignment` | Idempotent; clears `late_minutes`; audits + publishes `volunteer.checked_in` with `self_reported: True`. |
| `undo_check_in(assignment_id, user)` | `VolunteerAssignment` | Only a check-in the volunteer made themselves, before the shift ends; `volunteer.check_in_undone`. |
| `report_late(assignment_id, user, minutes)` | `VolunteerAssignment` | `minutes` in `LATE_CHOICES` (10/20/30); keeps the shift, publishes `volunteer.running_late` and DMs coordinators + staff via `volunteer_coordinator_dm.notify_coordinators` (shared with `release`). Repeating the same answer is a no-op. |

| `request_acknowledgment(assignment, shift, user)` | `None` | The best-effort acknowledgment DM (`send_dm_with_volunteer_acknowledgment_button`) that `assign` and `confirm_assignment` send. Never raises. |
| `coverage(start, end)` | `list[dict]` | Per-shift `filled`/`needed` counts (flagging understaffed shifts), with `drafts` and `acknowledged` breaking the filled total down. `filled` **counts drafts** — this is the coordinator's working schedule. |
| `day_summary(start, end)` | `dict` | The day's totals over `coverage`: `shifts`, `needed`, `filled`, `open`, `drafts`, `unacknowledged` (published but not acknowledged), `understaffed_shifts`. Feeds the coordinator's coverage strip. |

**Who gets told what.** The subsystem's most load-bearing rule, and it is spread across five methods, so it is written down here:

| Transition | Volunteer | Coordinators |
|---|---|---|
| Manual assign | DM + Acknowledge | — |
| Draft created | — | — (grid + banner) |
| Draft published | DM + Acknowledge | — |
| Draft cleared | — | — |
| Shift time changed | DM + Acknowledge (ack cleared) | — |
| Coordinator un-assigns | DM (no button) | — |
| Volunteer releases | — | DM |
| Reminder lead reached | DM + Acknowledge | — |

Every DM is best-effort, gated on `discord_id` (`send_dm` applies the Discord switch), and never raises. `_notify_coordinators_of_release` is the only role-addressed DM in the codebase (it dedupes a coordinator who is also staff); it stays private to this service rather than becoming a general broadcast utility for one caller.

Collaborators: `VolunteerShiftRepository`, `VolunteerAssignmentRepository`, `VolunteerPositionRepository`, `VolunteerAvailabilityService`, `UserRoleRepository` (the release fan-out), `DiscordService` (via `discord_queue`), `AuditService`, `AuthService`, [`discord_messages.py`](#discord_messagespy), [`timezone.py`](#timezonepy).

### volunteer_autoschedule_service.py — VolunteerAutoscheduleService

Greedy/heuristic draft generator. Fills open shift slots from the assignable pool using qualifications, availability, no-overlap, and an hour ceiling, producing `auto_generated` assignments the coordinator then reviews. Candidate ranking prefers qualified volunteers, then `PREFERRED`/`AVAILABLE` availability, then fewest assigned hours, then name.

What the filler may do is a **`DraftPolicy`** (frozen dataclass, exported from `application.services`), built by the coordinator's auto-fill dialog and defaulting to the conservative reading of a volunteer's declared availability:

| Field | Default | Meaning |
|---|---|---|
| `max_hours` | `DEFAULT_MAX_HOURS` (8.0) | Hard ceiling per volunteer per run; `0` disables it. |
| `fill_outside_availability` | `False` | Whether a volunteer who has *not* marked the time may be used at all. Someone who marked it **unavailable** is never used, under any policy. |

Both are **hard skips inside `_pick`**, not tiebreakers — that distinction is the finding this replaced, where one volunteer was given four consecutive blocks totalling 16 hours, 12 of them outside their declared window.

| Method | Returns | Description |
|---|---|---|
| `generate_draft(actor, start, end, *, position_ids=None, clear_existing_drafts=True, policy=None)` | `dict` | Coordinator-only; build a draft over `[start, end]` (optionally one or more positions) under `policy` (defaulting to `DraftPolicy()`). Returns `{created, pool_size, policy, unfilled, outside_availability, heavy_loads}` — `unfilled` carries a per-slot `reason` sentence saying *why* it stayed open, `outside_availability` names everyone placed outside their stated window, and `heavy_loads` names anyone left near the ceiling or on three touching blocks. Transactional; audits `volunteer.draft_generated` with the policy and the open-slot count, so the audit row answers "what did that run do". |
| `publish_draft(actor, start, end)` | `dict` | Coordinator-only; the inverse of `clear_draft`. Confirms every draft in the window through `VolunteerScheduleService.confirm_assignment` — so each volunteer gets the same DM and event a manual assign produces — and returns `{published, volunteers}`. Deliberately **not** transactional: a rollback cannot un-send a DM, and `confirm_assignment` is idempotent so a re-click finishes a partial run. Audits `volunteer.draft_published`. |
| `clear_draft(actor, start, end)` | `int` | Coordinator-only; remove auto-generated assignments in the window; returns the count; audits `volunteer.draft_cleared`. Silent by design — a draft was never announced. |

Collaborators: `VolunteerShiftRepository`, `VolunteerAssignmentRepository`, `VolunteerPoolService`, `VolunteerAvailabilityService`, `VolunteerScheduleService`, `AuthService`, `AuditService`; reads `VolunteerQualification` directly.

### volunteer_qualification_service.py — VolunteerQualificationService

Reads and replaces the set of positions a volunteer is qualified to fill. Qualifications are used by `VolunteerAutoscheduleService` when scoring and ranking candidates for a shift. All writes are gated by `AuthService.can_manage_volunteers` and audited under `volunteer.*`.

| Method | Returns | Description |
|---|---|---|
| `get_qualified_position_ids(user)` / `get_qualified_user_ids_for_position(position_id)` | `Set[int]` | The qualification set read from either side. |
| `list_all_qualifications()` | `list[VolunteerQualification]` | Every qualification row in the tenant (the coordinator matrix). |
| `set_qualifications(actor, user, position_ids)` | `None` | Coordinator-only; replace the user's qualification set atomically; audits `volunteer.qualifications_updated`. |

Collaborators: `VolunteerQualificationRepository`, `AuthService`, `AuditService`.

### volunteer_export_service.py — VolunteerExportService

Read-only. Flattens the coordinator's volunteer data into plain tables so an initial schedule can be drafted in a spreadsheet and brought back by hand. Gated by `AuthService.can_manage_volunteers` and `@requires_feature(FeatureFlag.VOLUNTEERS)`, and audited under `volunteer.data_exported` — the export copies every volunteer's declared availability and free-text notes out of the app, so who took a copy and when is worth a row.

| Method | Returns | Description |
|---|---|---|
| `build(actor, start, end)` | `VolunteerExportBundle` | Coordinator-only. Every sheet covering `[start, end]`; raises `ValueError` when the window is inverted. |

`VolunteerExportBundle` carries `sheets` (a list of `ExportSheet`: `name`, `title`, `description`, NiceGUI-style `columns`, `rows`), a `sheet(name)` lookup, and `readme()`. Six sheets: **`volunteers`** (the roster — pool membership, qualified positions, declared and assigned hours; **never windowed**, since a volunteer with no availability is exactly the gap to fill), **`availability`** (every window overlapping the range, `unavailable` ones included, in both display-local and ISO-8601 UTC), **`positions`**, **`shifts`** (filled/open counts + assigned names), **`assignments`** (one row per placement, its source `Auto-draft`/`Coordinator`, and whether it matches stated availability), and **`schedule`** (one row per **slot**, open slots included — the grid to draft on, which `assignments` cannot express because an unfilled slot has no record; states `Open`, `Draft`, `Assigned`, `Confirmed`, `Extra`).

`VolunteerExportDialog` renders the bundle as `README.txt` + one CSV per sheet in a single ZIP (`files_to_zip_bytes`). Collaborators: the volunteer repositories, `UserRepository`, `VolunteerPoolService.assignable_volunteers`, `VolunteerAvailabilityService.covers`, `AuthService`, `AuditService`, `TenantService`.

### volunteer_reminder.py — module functions

A background worker (built on `run_worker_loop` from [`background_loop.py`](#additional-utilities)) that periodically finds upcoming volunteer assignments within the configured lead time, and per assignment — inside `tenant_scope` via `for_each_tenant_scoped` — enqueues a reminder DM with an acknowledge button (closed over its tenant by `_scoped_dm`, so deep links and the web-push mirror resolve correctly), and stamps `reminder_sent_at` so each assignment is reminded only once (the stamp is written before the DM so a delivery failure or restart cannot re-fire). Module constant: `TICK_SECONDS = 60`.

| Function | Returns | Description |
|---|---|---|
| `start()` | `None` | Create the loop task on the running event loop (idempotent). Called from `main.py` lifespan startup. |
| `stop()` (async) | `None` | Cancel the loop and await its exit. Called from lifespan shutdown. |

Collaborators: `VolunteerAssignmentRepository`, `SystemConfigService.get_volunteer_reminder_lead_minutes`, `DiscordService` (via `discord_queue`), [`discord_messages.py`](#discord_messagespy), [`timezone.py`](#timezonepy). Imported as a module (`from application.services import volunteer_reminder`).

### web_push_service.py — WebPushService

Per-device browser push notifications ("Device Notifications"): subscription CRUD (audited `AuditActions.WEB_PUSH_*`) plus the encrypted delivery path. `mirror_dm(discord_id, message, navigate=None)` is enqueued fire-and-forget from `DiscordService.send_dm` for every outgoing DM (never raises; a no-op unless VAPID is configured and the user has subscriptions). It is the only send path: every push is a mirrored DM. Subscription management: `list_subscriptions(user)`, `subscribe(user, *, endpoint, p256dh, auth, user_agent=None)`, `rotate_subscription(*, old_endpoint, old_auth, endpoint, p256dh, auth, user_agent=None)` (the service worker's `pushsubscriptionchange`, authenticated by the old endpoint + auth secret), `unsubscribe(user, endpoint)` and `remove_subscription(user, subscription_id)`. Sends the Declarative Web Push JSON shape (`web_push: 8030`) — rendered natively on Safari/iOS 18.4+, by `static/sw.js` elsewhere — encrypted per RFC 8291 in a worker thread, with a per-origin-cached VAPID `Authorization` header, delivered concurrently per user through a shared `httpx.AsyncClient` (closed via `aclose_http_client()` in the lifespan). Prunes subscriptions the push service reports gone (404/410). Configured by `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT`, resolved once per env tuple so misconfiguration warns once rather than per DM; `is_configured()` / `get_public_key()` gate the settings UI. Collaborators: `WebPushRepository`, `AuditService`, [`web_push.py`](#web_pushpy), `application.utils.environment.get_base_url`. Feature doc: [web-push.md](../features/web-push.md).

### notification_channel_service.py — NotificationChannelService

Email (SendGrid) and Telegram as extra places a person's notifications land, behind `FeatureFlag.NOTIFICATION_CHANNELS`. Every public entry is `@requires_feature` except the paths reached without a request (the unsubscribe link, the Telegram bot's `/start` and `/stop`, and `mirror_dm`), which are marked `# feature-gate: exempt`.

| Method | Description |
|---|---|
| `start_email_verification(user, raw_email)` / `confirm_email(user, code)` | Mails a six-digit code (15 min, 5 tries, 5 codes/hour per account) and confirms it. The address is validated strictly so it can't carry header tricks. Audited `notification.email_confirmed`. |
| `use_discord_email(user, discord_id, email, verified)` | Takes the address from Discord's `/users/@me` when it belongs to the signed-in Discord account and Discord marks it verified. |
| `remove_email` / `set_email_enabled` / `unsubscribe_email(token)` | Turn email off or remove it. The unsubscribe token is signed and bound to the user and the current address, so an old link stops working after the address changes. |
| `create_telegram_link(user)` / `handle_telegram_start` / `handle_telegram_stop` / `disconnect_telegram` / `set_telegram_enabled` | `t.me/<bot>?start=<token>` deep link (15 min); the bot binds the chat on `/start`, refuses a chat already bound to another account, and `/stop` turns Telegram off. |
| `send_test(user, channels)` | Sends a test to the channels the person picked (Discord, devices, email, Telegram); 3 tests per 10 minutes. Returns per-channel success. |
| `mirror_dm(discord_id, message, navigate, label)` | Enqueued by `DiscordService.send_dm` for every DM; copies it to each channel the person has on. Never raises. Resets the flag cache because it runs on the long-lived dispatch worker. |

Transports: `application/utils/clients/sendgrid_client.py` and `telegram_client.py`; both print instead of sending under `MOCK_DISCORD`. Collaborators: `NotificationChannelRepository`, `AuditService`, `DiscordService`, `WebPushService`, `notification_links`. Feature doc: [notification-channels.md](../features/notification-channels.md).

### telegram_bot_worker.py — Telegram bot worker

A `BackgroundLoop` that long-polls Telegram's `getUpdates` (no webhook, so nothing to expose publicly). Handles private-chat commands only: `/start <token>` links, `/stop` turns notifications off, `/start` or `/help` alone replies with help. Started from `main.py` only when `TELEGRAM_BOT_TOKEN` is set and Discord isn't mocked.

### discord_email_service.py — DiscordEmailService

The provider half of "Use my Discord email". Sign-in keeps the `identify` scope; this runs a second authorization with `identify email` through the shared identity-link scaffolding (`pages/discord_email_oauth.py`, callback `/discord/email/callback`), rejects an account with no address or an unverified one, and hands the address to `NotificationChannelService.use_discord_email`. Discord's consent screen has no per-scope opt-out, so declining means pressing Cancel and typing an address instead.

### webhook_service.py — WebhookService

Staff-managed outbound webhooks: CRUD (`list_webhooks`/`get_webhook`/`create_webhook`/`update_webhook`/`delete_webhook`, plus `regenerate_secret` and the `list_deliveries` history and per-webhook `recent_health` ok/failed summary; gated on `AuthService.is_staff`, audited `AuditActions.WEBHOOK_*`) plus the delivery path. `deliver_event(event)` is subscribed to the [event bus](../features/event-system.md) in `main.py`; for each active webhook subscribed to that event it enqueues `_deliver_one` onto the dispatch worker, which POSTs an HMAC-SHA256-signed JSON body via `httpx.AsyncClient` (bounded retry + `WebhookDelivery` logging). Validates `https://`, the SSRF host rules (production), and event-type names; the signing secret comes from `secrets.token_urlsafe`. `build_delivery_headers(...)` (static) is the one header set every delivery sends, and `format_reference()` (static) derives the in-app format docs (payload keys, headers, retry constants, event list) from those live code objects so they cannot drift. Each delivery is enqueued on the event dispatch queue wrapped in the webhook's `tenant_scope`. Collaborators: `WebhookRepository`, `WebhookDeliveryRepository`, `AuditService`, `AuthService`, `application.events`.

> The event bus itself (`application/events/`) — the publish/subscribe backbone these deliveries hang off — is documented in [event-system.md](../features/event-system.md).

### async_qualifier_service.py — AsyncQualifierService

The self-paced permalink-pool qualifier — a peer aggregate of `Tournament` with its own state machine (window opens → draw → run → review → scored leaderboard → close). Every public method carries `@requires_feature(FeatureFlag.ASYNC_QUALIFIERS)`; management is gated by `AuthService.can_admin_qualifier`. Audits every state change (`AuditActions.ASYNC_QUALIFIER_*`) and mirrors `run_submitted`/`run_reviewed`/`run_expired` onto the event bus (`EventType.ASYNC_QUALIFIER_RUN_*`), with a best-effort Discord DM on review and on expiry. Design — state machine, fairness draw, results lockdown, scoring formulas: [online-tournaments.md](../features/online-tournaments.md#async-qualifiers).

| Method | Returns | Description |
|---|---|---|
| `list_qualifiers` / `get_qualifier` / `create_qualifier` / `update_qualifier` / `delete_qualifier` | `AsyncQualifier`(s) | Qualifier CRUD; config validated by `validate_async_qualifier_config`. |
| `add_admin` / `remove_admin` / `list_admins` | — / `list[User]` | The per-qualifier `admins` M2M, who are also its reviewers. |
| `list_pools` / `create_pool` / `update_pool` / `delete_pool` | `AsyncQualifierPool`(s) | Pool CRUD within a qualifier. |
| `add_permalink` / `add_permalinks_bulk` / `roll_permalinks` / `update_permalink` / `delete_permalink` | `AsyncQualifierPermalink`(s) | Permalink management; `roll_permalinks` rolls N seeds from the pool's `Preset` via `SeedGenerationService`, `add_permalinks_bulk` pastes many at once. |
| `list_open_qualifiers` / `get_qualifier_for_player` / `get_player_pools` / `list_user_runs` / `get_active_run` | reads | The player-facing surface. `get_player_pools` **raises** for a shut window and is retained for the REST client that depends on that. |
| `get_run_availability(user, qualifier_id)` | `RunAvailability` | The web surface's read: the pools this player may draw from, every pool's spent/remaining counts, and — when none are available — a `RunUnavailableReason` with the sentence it owes the runner. Never raises for a closed window; a closed window is the answer. |
| `start_run(user, qualifier_id, pool_id)` | `AsyncQualifierRun` | The **draw**: an atomic, row-locked transaction enforcing one active run per player, the `runs_per_pool` cap, and permalink no-repeat, then picking a permalink by imbalance-forcing fairness. Reveal == start. |
| `submit_run(user, run_id, *, elapsed_seconds, runner_vod_url=None)` / `forfeit_run(user, run_id)` / `reattempt_run(user, run_id, *, reason)` | `AsyncQualifierRun` | Run lifecycle: submit a finish time (positive, under `MAX_RUN_SECONDS`, and not longer than the run has existed — see `classify_claim`) for review, recording `measured_seconds`; forfeit irreversibly for 0; or void the prior run and free the slot, within `allowed_reattempts`. |
| `warn_run_expiring(run, deadline, *, now=None)` / `expire_run(run, *, actor=None, now=None)` | `AsyncQualifierRun` | From `RunExpiryMixin` (below), driven by `async_qualifier_worker`, not by a human: warn once before an abandoned run's deadline, then forfeit it — marked `expired_at` so it reads as a time rule rather than a decision anyone made. |
| `grant_reattempt(actor, run_id, *, reason)` | `AsyncQualifierRun` | A qualifier admin voids someone else's terminal run — the override for a mis-clicked forfeit or a bad seed. Requires a reason, **ignores** `allowed_reattempts` (recorded via `reattempt_granted_by`, so it does not shrink what the runner may still spend), and DMs the runner. Shares `_void_run` with `reattempt_run` so neither path can skip the par refresh. |
| `get_reattempt_allowance(user, qualifier_id)` | `ReattemptAllowance` | `spent` / `allowed` / `remaining` for one player — what the run surface says out loud and what the forfeit dialog promises. Counts only self-spent reattempts. |
| `list_review_queue` / `list_runs` / `claim_run` / `release_claim` / `review_run` / `get_run_notes` | — | Review: claim-locking, then `review_run(actor, run_id, *, approved, note=None, override=False)` approve/reject (**self-review blocked**; `override` re-decides an already-settled run), which recomputes the permalink's par and rescores its approved runs. A **rejection requires a note** — it is stored as a run note and DM'd to the runner. `list_runs` is every run in the qualifier (admin), because the queue holds only finished+pending rows and a forfeit is written straight to `APPROVED`. All of it lives in `async_qualifier_review.py` (`RunReviewMixin`). |
| `is_results_public(qualifier, now=None)` / `get_leaderboard(...)` | `bool` / `dict` | The board, pools, and pars are staff-only until the qualifier closes (inactive or past `closes_at`). |
| `recompute_par_and_scores(permalink_id)` | `None` | Recompute one permalink's par and rescore its approved runs; the public entry the live-race capture path reuses. |

Sibling modules keep the service under the file-length guideline:

- **async_qualifier_config.py** — `validate_async_qualifier_config`, the Pydantic `extra='forbid'` validator for `AsyncQualifier.config`.
- **async_qualifier_rules.py** — side-effect-free rule functions: `validate_counts`/`validate_window`, `ensure_window_open`, `is_results_public` (the lockdown predicate), `par_sample_size`/`imbalance_threshold`/`run_time_limit`/`expiry_warning_lead` (tunables off `qualifier.config`, with defaults) and `run_deadline` (when a started run expires), `display_name`, the `ReattemptAllowance` value object, the availability trio `window_reason`/`classify_availability`/`describe_unavailability` (with `RunUnavailableReason` and `PoolUsage`), and the claimed-vs-measured trio `measure_elapsed`/`classify_claim`/`describe_claim` (`ClaimVerdict.OK|IMPLAUSIBLE|IMPOSSIBLE`, thresholds `CLOCK_GRACE_SECONDS` and `IMPLAUSIBLE_DRIFT_SECONDS`) that the service, the run surface and the review queue all share, plus `window_state`/`WindowState` (what `sync_window_state` compares), `permalink_url_error`/`validate_permalink_url`, `claim_is_live` (review-claim expiry), `backlog_is_worth_reporting`, `review_status_label`, `run_scores` and `display_name_of`.
- **async_qualifier_pools.py** — `PoolManagementMixin`, mixed in alongside the reads and expiry mixins: the pool and permalink authoring a qualifier admin does before anyone runs (`list_pools`/`create_pool`/`update_pool`/`delete_pool`, `add_permalink`/`add_permalinks_bulk`/`roll_permalinks`/`update_permalink`/`delete_permalink`), plus the `_require_pool`/`_require_permalink`/`_ensure_pool_admin`/`_ensure_permalink_admin` gates those share. `roll_permalinks` rolls up to `MAX_ROLL_COUNT` (25) seeds from the pool's preset and aborts with nothing half-written when a keyed randomizer has no credential; **`roll_refusal(preset)`** is the pure half it and the page share, so the Roll button is not offered where it cannot work. Every URL that enters a pool goes through `rules.validate_permalink_url` — reveal is start, so a mangled permalink costs a run, not a click — and `add_permalinks_bulk` returns **`BulkPermalinkAdd(created, rejected)`**, skipping a bad line with its 1-based input position and reason rather than failing the whole paste or taking it whole.
- **async_qualifier_reads.py** — `PlayerReadsMixin`, mixed into the service: the competitor-facing reads (open qualifiers, `get_run_availability`/`get_player_pools`, own runs, active run, reattempt allowance) and the leaderboard. Reads only — nothing here writes or audits. `list_user_runs` returns the **`OwnRun`** projection rather than models, because an exact score is exactly solvable for the seed's par: it withholds `score` until results are public (offering `score_band` instead) and adds what the runner needs and the model does not spell out — the seed played, the deadline an in-progress run dies at, whether a void was a reviewer's, and `expired_at`. A frozen projection rather than a masked model, since a model with its score nulled for display is one `save()` away from writing that null back.
- **async_qualifier_expiry.py** — `RunExpiryMixin`, the other half of the run lifecycle, mixed in alongside `PlayerReadsMixin`. `warn_run_expiring(run, deadline)` sends the single pre-deadline DM and stamps `expiry_warned_at` **before** sending, so a delivery failure cannot turn one warning into one per tick; `expire_run(run)` forfeits an abandoned run — same terminal state and zero score as a chosen forfeit, but stamped `expired_at` so an appeal can tell the two apart, attributed to the runner rather than a staff member, idempotent against a partial worker tick, and still reattemptable by a reviewer. Also here, driven by the same worker: `notify_review_backlog` (the queue nobody worked), `release_stale_claim` (drops an aged-out review claim, unaudited because nobody decided anything), and **`sync_window_state`**, which publishes `async_qualifier.opened`/`.closed` once per crossing and stamps `window_state_notified` so a tick that changes nothing says nothing. That one publishes on the bus directly rather than through `write_and_publish`: nobody performs an opening, so there is no actor and no audit row to pair with. `create_qualifier`/`update_qualifier` call it too, since an admin's edit can cross the boundary immediately — and switching a qualifier off takes it out of the worker's active scan, making that the only place the crossing is seen. Driven only by `async_qualifier_worker`. The three members it borrows from the composing service (`run_repository`, `audit_service`, `_require_qualifier`) are declared as annotations, so the contract is stated rather than implicit.
- **async_qualifier_review.py** — `RunReviewMixin`, mixed in alongside the pools/reads/expiry mixins: the reviewer's whole surface (`list_review_queue`, `count_pending_review`, `review_queue_context`, `list_runs`/`count_runs`, `claim_run`/`release_claim`, `review_run`, `get_run_notes`) and the three gates it owns — `_require_reviewable` (which hands the actor back narrowed to a real `User`, so callers read `reviewer.id` without a redundant None check), `_ensure_claim_free` (refuses by **name**, because "someone has this" leaves the reader nothing to do about it) and `_reviewer_name` (for the message that refuses to re-settle a decided run silently). Its five borrowed members are declared as annotations, the way `RunExpiryMixin` declares its own.
- **async_qualifier_notifications.py** — the four runner-facing DMs (`notify_run_reviewed`, `notify_reattempt_granted`, `notify_run_expiring`, `notify_run_expired`) plus the reviewer-facing `notify_review_queue_waiting` (returns how many were sent, linking the review queue), best-effort and swallowed here rather than at each call site; the copy itself lives in `application/utils/discord_messages_qualifier.py`.
- **async_qualifier_access.py** — shared gate + entity resolution both qualifier services use: `ensure_qualifier_admin(...)` and the `require_qualifier`/`require_pool`/`require_permalink`/`require_run` load-or-`NotFoundError` lookups.
- **async_qualifier_draw.py** — `AsyncQualifierDraw`, the repository-touching draw and recompute engine (`draw_candidates`/`pick_permalink`/`recompute_par_and_scores`, with the pure `drawable` filter and the `async_seeds`/`async_seed_count` pool counts), composed as `self.draw`.
- **async_qualifier_scoring.py** — the pure par/score math: `compute_par`, `compute_score`, `build_leaderboard` (`ScoredRun` → `LeaderboardEntry`), and `score_band`/`ScoreBand`, the coarse band `OwnRun` shows before results are public.

Collaborators: the `AsyncQualifier*` repositories, `PresetRepository`, `SeedGenerationService`, `AuthService`, `AuditService`, the event bus.

### async_qualifier_live_race_service.py — AsyncQualifierLiveRaceService

Synchronous racetime qualifier races whose results flow into `AsyncQualifierRun`s, **reusing the existing racetime subsystem** (`racetimebot/` + `RaceRoomService`) rather than a second integration. Same `@requires_feature(FeatureFlag.ASYNC_QUALIFIERS)` + `can_admin_qualifier` gating. `RaceRoomLifecycle` (in `race_room_service.py`) routes a started/finished/cancelled room here instead of the match path when `RacetimeRoom.match_id` is null and the slug resolves to a live race.

| Method | Returns | Description |
|---|---|---|
| `list_live_races` / `get_live_race` / `list_runs` | reads | The admin **Live Races** sub-tab's data. |
| `create_live_race(actor, pool_id, *, match_title, permalink_id=None, episode_id=None)` | `AsyncQualifierLiveRace` | Schedule a race for a pool, optionally pinning a permalink and an SG `episode`. |
| `assign_permalink(actor, live_race_id, permalink_id)` | `AsyncQualifierLiveRace` | Point a race at the pool permalink it is raced on (the "assign later" path); refused once the race has runs, since par is per permalink. |
| `open_room(actor, live_race_id)` | `AsyncQualifierLiveRace` | Open a room on racetime.gg as one of the tenant's authorized bots (racetime's default settings, the qualifier's name as the goal, the race title as room info), record a `RacetimeRoom` (with `match=None`) under racetime's slug, mirror that slug onto the live race (`racetime_slug`), and move it to `PENDING`. Idempotent and serialised per race; a refused `startrace` raises `ValueError` and changes nothing. |
| `mark_in_progress(live_race)` / `mark_cancelled(live_race)` | `AsyncQualifierLiveRace` | Runtime transitions from the room handler (un-gated — the caller is the trusted bot loop). `mark_cancelled` leaves a `FINISHED` race alone, so cancelling the room afterwards does not un-score its runs. |
| `record_finish(live_race, entrants, *, actor=None)` | `List[AsyncQualifierRun]` | Map each racetime entrant to a `User` by `racetime_user_id`, then hand the resolved results to `_capture`. Handles it could not match are collected and recorded on the race as `unmatched_handles`. **Refuses to record while any entrant is still racing.** |
| `record_manual_finish(actor, live_race_id, results)` | `List[AsyncQualifierRun]` | The same capture from a human instead of a room, for a FINISHED event that never arrived — the only remedy for a dropped racetime connection, since `record_finish` is reachable only from that event. Takes `ManualResult(user_id, status, elapsed_seconds)`: a finisher needs a time (bounded by `MAX_RUN_SECONDS`), a forfeit/DQ may not carry one, a racer may appear once, and the actor must be a qualifier admin. |
| `_capture(...)` | `List[AsyncQualifierRun]` | What both paths share, so neither can drift: refuse a race with no permalink, derive the race start (`now − max(elapsed)`, since everyone started together and the slowest finisher only just finished), write each result `APPROVED` with `measured_seconds` = the raced time — live-race runs **skip reviewer sign-off**, a racetime result being self-attributing — void anything over the pool's `runs_per_pool`, par-score via `AsyncQualifierService.recompute_par_and_scores`, and move the race to `FINISHED`. |
| `cancel_live_race(actor, live_race_id)` | `None` | Call the race off. |

Audits `AuditActions.ASYNC_QUALIFIER_LIVE_RACE_*` (create/open/cancel are audit-only; both captures emit `EventType.ASYNC_QUALIFIER_LIVE_RACE_RECORDED`, the manual one carrying `manual: true` while auditing under its own `…_RECORDED_MANUALLY` action — the review-override shape: one external fact, two distinguishable acts). Collaborators: `AsyncQualifierLiveRaceRepository`, `AsyncQualifierRunRepository`, `RacetimeRoomRepository`, `RacetimeBotService`, `AsyncQualifierService`, `UserService`, `AuditService`.

## Utilities (`application/utils/`)

### challonge_client.py

Thin async `aiohttp` wrapper over the Challonge v2.1 (JSON:API) endpoints the integration needs, plus OAuth token exchange/refresh ([challonge_client.py](../../application/utils/clients/challonge_client.py)). Returns **normalized** Python dicts so `ChallongeService` never sees the JSON:API wire shape. Two wire quirks it absorbs, both seen on a live two-stage tournament: list endpoints page at 25 rows (it asks for `per_page=100` and follows `links.next`), and final-stage matches carry no `player1`/`player2` relationships, so their players are read from `attributes.points_by_participant` (player 1 first). Authenticated calls obtain a bearer token via an injected `token_provider` callback and transparently refresh-on-401 and retry once; an optional `on_request` hook counts real outbound requests for quota tracking. Module constants: `AUTHORIZE_URL`, `TOKEN_URL`, `BASE_URL`.

| Member | Returns | Description |
|---|---|---|
| `build_authorize_url(client_id, redirect_uri, scope, state)` (function) | `str` | Challonge OAuth authorize URL to redirect the browser to. |
| `ChallongeAPIError` (exception) | — | Raised on an API error or unexpected payload. |
| `ChallongeClient.exchange_code(code, redirect_uri)` | `dict` | Exchange an auth code for a token payload. |
| `ChallongeClient.refresh(refresh_token)` | `dict` | Exchange a refresh token for a new token payload. |
| `ChallongeClient.get_me(access_token)` | `{user_id, username}` | The authenticated account identity for a raw token. |
| `ChallongeClient.get_tournament(tournament_id)` | `{id, name, url, state}` | A tournament's summary. |
| `ChallongeClient.list_participants(tournament_id)` | `list[dict]` | Normalized participant records. |
| `ChallongeClient.list_matches(tournament_id)` | `list[dict]` | Normalized match records. |
| `ChallongeClient.get_tournament_full(tournament_id)` | `{tournament, participants, matches}` | Tournament plus embedded participants and matches in one request. The embed leaves each participant's `user` relationship empty, so when any entrant has a Challonge account (a `username`) it makes one more `/participants` call to backfill `challonge_user_id`. |
| `ChallongeClient.update_match(tournament_id, match_id, winner_participant_id, loser_participant_id, winner_score='1', loser_score='0')` | `None` | Report a match result (winner flagged `advancing`). |
| `ChallongeClient.change_state(tournament_id, state, community=None)` | `dict` | `PUT …/change_state.json` with a `TournamentState` body (`start_group_stage`, `finalize_group_stage`, `start`, `finalize`, …); returns the normalized tournament. Needs `tournaments:write`. |
| `MockChallongeClient` | — | `MOCK_CHALLONGE` stub returning a canned 4-player single-elim bracket so local dev can click through connect/link/sync/schedule/push. |

### racetime_rooms_client.py — race-room creation

The category bot's `startrace` call ([racetime_rooms_client.py](../../application/utils/clients/racetime_rooms_client.py)). `RacetimeRoomsClient.start_race(category=, client_id=, client_secret=, settings=)` takes a `client_credentials` token for the bot (cached per client id until shortly before expiry), reads the category's goal list from `/<category>/data` so a known goal goes as `goal` and anything else as `custom_goal`, POSTs the form to `/o/<category>/startrace`, and returns the slug from racetime's `Location` header (`slug_from_location`). Every failure is a `RacetimeAPIError` carrying racetime's reason. `RaceRoomSettings` is the form: `from_profile(profile, goal=, info_user=)` copies a `RaceRoomProfile` (racetime's defaults for `None`). `build_rooms_client()` returns `MockRacetimeRoomsClient` under `MOCK_RACETIME` (a fresh `<category>/mock-room-<hex>` slug, no network); services call it through the module so the test suite's autouse `racetime_rooms` fixture can hand every test the mock.

### OAuth identity clients

`twitch_client.py` and `racetime_client.py` are thin async `aiohttp` wrappers over the two endpoints each account-linking flow needs — the OAuth token exchange and the identity lookup (Helix `users` / `o/userinfo`). Both return a **normalized** identity dict so the service never sees the wire shape, and both are built on the shared base in `application/utils/clients/oauth_identity_client.py`.

| Member | Returns | Description |
|---|---|---|
| `build_authorize_url(client_id, redirect_uri, scope, state)` (function) | `str` | Provider OAuth authorize URL to redirect the browser to. |
| `TwitchAPIError` / `RacetimeAPIError` (exceptions) | — | Raised on an API error or unexpected payload. |
| `<Provider>Client.exchange_code(code, redirect_uri)` | `dict` | Exchange an auth code for a token payload. |
| `<Provider>Client.get_me(access_token)` | `dict` | The authenticated account identity for a raw token — `{user_id, username, display_name}` for Twitch (which sends the required `Client-Id` header), `{user_id, username}` for racetime. |
| `MockTwitchClient` / `MockRacetimeClient` | — | Stubs returning a deterministic canned identity (chosen via `MOCK_TWITCH_IDENTITY` / `MOCK_RACETIME_IDENTITY`) so local dev can click through link/unlink. |

Module constants: `AUTHORIZE_URL`, `OAUTH_EXCHANGE_URL`, plus `USERS_URL` (Twitch) / `USERINFO_URL` + `IDENTITY_SCOPE` (racetime).

### matcherino_client.py — event check-in

Transport for the two **unofficial** Matcherino endpoints check-in uses ([matcherino_client.py](../../application/utils/clients/matcherino_client.py)), the ones Matcherino's own web app calls: `GET /__api/bounties/participants` (zero-indexed pages of up to `PAGE_SIZE = 500`, capped at `MAX_PAGES = 20`) and `GET /__api/bounties/findById`. Uses `httpx.AsyncClient`. `MatcherinoClient.fetch_participants(bounty_id) -> list[MatcherinoParticipant]` and `fetch_bounty(bounty_id) -> MatcherinoBounty`. The parse is strict about `userId` and `displayName` and raises `MatcherinoAPIError` rather than guessing; an unknown bounty answers `200` with `contents: null`, which reads as empty. `MatcherinoParticipant` keeps the raw dict beside its parsed fields (`auth_provider`, `auth_id`, `twitch_login`, …). `get_matcherino_client()` returns `MockMatcherinoClient` (canned `MOCK_PARTICIPANTS`, no network) under `MOCK_MATCHERINO`. Wire detail: [event-check-in.md](../features/event-check-in.md#the-matcherino-api-is-unofficial).

### duration.py

Whole-second duration parsing and display for typed finish times
([duration.py](../../application/utils/duration.py)). Pure syntax — no ORM, no service
rules (the `MAX_RUN_SECONDS` ceiling stays in `AsyncQualifierService`, so the hour
segment is unbounded here). Shared by the player run surface, the reviewer queue and
anything else that shows a run duration.

| Function | Returns | Description |
|---|---|---|
| `format_hms(seconds, *, dash='—')` | `str` | `4325` → `'1:12:05'`; `None` → `dash`. Negatives clamp to zero. |
| `parse_hms(text)` | `int` | Parse **exactly** `H:MM:SS` into whole seconds; raises `ValueError` with a sentence naming the shape. |

`parse_hms` is deliberately strict. Accepting `SS` and `MM:SS` by folding parts into
base 60 meant `1:23` — the likeliest typo in a field labelled `H:MM:SS` — silently
submitted 83 seconds, and every later surface displayed it as a valid time.

### csv_export.py

CSV rendering for the report tables ([csv_export.py](../../application/utils/csv_export.py)).

| Function | Returns | Description |
|---|---|---|
| `rows_to_csv_bytes(columns, rows)` | `bytes` | Render NiceGUI-style column descriptors (`name`, `label`, optional `hidden`) plus row dicts as UTF-8-with-BOM CSV; hidden columns are skipped. |
| `timestamped_filename(prefix, ext='csv')` | `str` | `prefix-20251130T143015Z.csv`-style download filename (UTC stamp). |
| `files_to_zip_bytes(files)` | `bytes` | Bundle `{filename: bytes}` into one ZIP (the volunteer export's README + per-sheet CSVs). |

**CSV-injection safety:** every non-numeric cell whose stripped value starts with `=`, `+`, `-`, or `@` is prefixed with an apostrophe so spreadsheet apps treat it as text rather than a formula. Numbers are exempt (so negative values export cleanly); booleans render as `true`/`false`; datetimes as ISO strings; `None` as empty.

### discord_messages.py

The single source of all Discord DM and ephemeral message text ([discord_messages.py](../../application/utils/discord_messages.py)). Service and handler code imports builder functions and constants from here rather than inlining strings, so wording stays consistent and centrally editable. Messages deliberately never expose raw match id numbers — a match is identified by players, scheduled time, and stage.

Notable members:

- **Shared constants:** `MSG_NO_ACCOUNT`, `MSG_UNEXPECTED_ERROR`, and the builder `msg_unexpected_error(action='finish this')` for an action-specific variant.
- **Match scheduling DMs** (sent by `MatchScheduleService`/`MatchService`): `scheduled_dm`, `rescheduled_dm`, `acknowledgment_request_dm`, `checked_in_dm`, `state_changed_dm`, `cancelled_dm`, `stream_candidate_dm`, `seed_dm`; plus `matchup_ready_dm` (bracket matchups, `BracketService.notify_matchup_ready`).
- **Hard-preset opt-in** (`MatchHardPresetService`, `discordbot/`): `hard_preset_invite_dm`, `hard_preset_agreed_dm`, `hard_preset_broken_dm`, `hard_preset_override_dm`, `hard_preset_opt_in_confirmation`, `hard_preset_withdraw_confirmation`.
- **Stage DMs** (`MatchService.assign_stage`, `stage_reminder`): `stage_assigned_dm`, `stage_cleared_dm`, `stage_reminder_dm`.
- **Ephemeral button replies** (`discordbot/`): `match_ack_confirmation`, `unwatch_confirmation`.
- **`DMLink(label, url)`** — a DM's call-to-action route, rendered by `send_dm` as a Discord link button and used as the web-push mirror's tap target. Built by [`notification_links.py`](#notification_linkspy--module-functions), never inline: the URL must be absolute, since a DM is read outside any request context.

This module holds the match lifecycle. Every other domain has a sibling of its own, moved out as the module kept crossing the 800-line budget; the same "no message text inline" rule applies to each:

| Module | Domain | Members |
|---|---|---|
| `discord_messages_crew.py` | Crew (`CrewService`, `discordbot/crew_*.py`) | `crew_assignment_dm`, `crew_approval_withdrawn_dm`, `crew_withdrawn_dm`, `crew_ack_confirmation`, `crew_signup_confirmation` |
| `discord_messages_volunteer.py` | Volunteer shifts (`VolunteerScheduleService`, `volunteer_reminder`, `discordbot/volunteer_acknowledgment.py`) | `volunteer_assignment_dm`, `volunteer_reminder_dm`, `volunteer_unassigned_dm`, `volunteer_shift_changed_dm`, `volunteer_released_dm` (to the coordinators), `volunteer_ack_confirmation` |
| `discord_messages_qualifier.py` | Async qualifiers | `qualifier_run_reviewed_dm`, `qualifier_run_expiring_dm`, `qualifier_run_expired_dm`, `qualifier_reattempt_granted_dm`, `qualifier_review_queue_dm` (to reviewers) |
| `discord_messages_reschedule.py` | Reschedule requests | `reschedule_requested_dm` (to staff), `reschedule_opponent_dm`, `reschedule_decided_dm`, `reschedule_agree_confirmation` |
| `discord_messages_tenant.py` | Community join requests (`TenantMembershipService`) | `join_requested_dm`, `join_decided_dm`, `member_added_dm`, `member_removed_dm` |
| `discord_messages_accommodation.py` | ADA requests (`AccommodationService`) | `accommodation_requested_dm`, `accommodation_changed_dm` |
| `discord_messages_check_in.py` | Event check-in (`MatcherinoLoginService`) | `matcherino_login_refused_dm` |

All builders are pure functions returning `str` (`DMLink` is a `NamedTuple`); optional fields passed as `None`/`''` are omitted from the rendered message.

### easter_eggs.py

A bank of cat facts surfaced in incidental UI spots ([easter_eggs.py](../../application/utils/easter_eggs.py)). `CAT_FACT_SECTIONS: dict[str, list[str]]` holds the facts under user-facing headings (Anatomy, Senses, Breeds, …) that the `/cat-facts` page renders section by section; `CAT_FACTS` is derived by flattening it and is the pool everything else draws from. `random_fact() -> str` returns a uniformly-random fact — this is what the `X-Fun-Fact` response header carries. `random_cat_fact(exclude=None) -> str` draws from the same pool, and is what every UI surface uses: empty states and table `no-data` slots, the 404 page, `theme.waiting.waiting_panel`, the report busy overlay, `CatFactDialog`, and the `/cat-facts` page. Passing `exclude` drops that one fact from the pool so a re-roll always visibly changes. No external dependencies or side effects.

### environment.py

Environment detection and fail-fast startup validation ([environment.py](../../application/utils/environment.py)).

| Function | Returns | Description |
|---|---|---|
| `env_flag(name, default=False)` | `bool` | The canonical truthy-env grammar: true iff the stripped, lowercased value is `1`/`true`/`yes`/`on`; `default` when unset. Every `MOCK_*` helper reads through it. |
| `get_environment()` | `str` | `ENVIRONMENT` env var, trimmed and lowercased; defaults to `'development'`. |
| `is_production()` | `bool` | True when the environment is exactly `'production'`. |
| `get_base_url()` / `get_platform_host()` | `str` | `BASE_URL` (no trailing slash; default `http://localhost:8000`), and the shared platform host (`PLATFORM_HOST`, else `BASE_URL`'s host) that serves `/platform` and every path-mode tenant. |
| `host_oauth_handoff_enabled()` | `bool` | `HOST_OAUTH_MODE=handoff` — custom-domain login via the [`oauth_handoff_service`](#oauth_handoff_servicepy--module-functions) signed handoff. |
| `telemetry_enabled()` | `bool` | `TELEMETRY_ENABLED`, default **on**. |
| `racetime_bot_enabled()` / `speedgaming_sync_enabled()` / `discord_events_sync_enabled()` / `service_health_alert_dm_enabled()` | `bool` | The worker/runtime master switches (`RACETIME_BOT_ENABLED`, `SPEEDGAMING_SYNC_ENABLED`, `DISCORD_EVENTS_SYNC_ENABLED`, `SERVICE_HEALTH_ALERT_DM`), all default off. |
| `session_storage_url()` / `validate_session_storage()` | `str` / `None` | The `NICEGUI_REDIS_URL` NiceGUI reads for session storage, and a one-shot startup probe that fails fast when it is set but unusable. |
| `validate_security_config()` | `None` | Raises `RuntimeError` — aborting startup — when `STORAGE_SECRET` is missing/blank (always; it signs the session store the whole authorization model trusts), and additionally in production when `STORAGE_SECRET` is shorter than 32 characters or `DB_USERNAME` / `DB_PASSWORD` is empty. |

### Mock switches

Each `MOCK_*` env var is read through one helper in `application/utils/mocks/` (SpeedGaming's lives beside its client), returning True when `env_flag` says so (`1`/`true`/`yes`/`on`, case-insensitive). Every mock is an authentication, authorization, or integrity bypass, so each helper **raises `RuntimeError` rather than returning True while `ENVIRONMENT=production`** — the process refuses to start instead of silently exposing the bypass. Except where noted, the owning service reads its switch to select the `Mock*Client` over the real one and to report `is_configured()` true.

| Function | Env var | What it fakes |
|---|---|---|
| `is_mock_discord()` | `MOCK_DISCORD` | The whole Discord layer. A complete **authentication bypass**: `/login` becomes a public user picker that can impersonate anyone (including Staff) or mint privileged users. Rebinds `DiscordService = MockDiscordService` at import time (see [discord_service.py](#discord_servicepy--discordservice-mockdiscordservice-get_discord_bot)). Detail: [discord.md](../features/discord.md#mock-mode). |
| `is_mock_challonge()` | `MOCK_CHALLONGE` | An authenticated Challonge connection, so dev can exercise connect → link → sync → schedule → push against a canned bracket. |
| `is_mock_twitch()` | `MOCK_TWITCH` | A verified Twitch identity for the link/unlink flow. |
| `is_mock_racetime()` | `MOCK_RACETIME` | A verified racetime identity for link/unlink, the `racetimebot/` runtime, **and** room creation (`build_rooms_client()` returns `MockRacetimeRoomsClient`, whose slugs don't exist on racetime.gg); all three share the one production-refusal switch. |
| `is_mock_seedgen()` | `MOCK_SEEDGEN` | Seed rolling: `generate_seed` returns a believable permalink instead of reaching a live randomizer, most of which need credentials or are unreachable from a dev sandbox. |
| `is_mock_matcherino()` (in `clients/matcherino_client.py`) | `MOCK_MATCHERINO` | The Matcherino registration list: `get_matcherino_client()` returns `MockMatcherinoClient`, whose canned roster lines up with dev-seed fixtures so one check-in sync produces every link method. |
| `is_mock_speedgaming()` (in `clients/speedgaming_client.py`) | `MOCK_SPEEDGAMING` | The SpeedGaming schedule feed: `get_speedgaming_client()` returns `MockSpeedGamingClient`, so the ETL runs against canned episodes. |
| `is_mock_dk64()` | `MOCK_SEEDGEN` | DK64R only, and one layer lower: rather than short-circuiting the roll, it swaps `_generate_dk64r`'s `aiohttp` session for `MockDK64Session`, an in-process stand-in for the api.dk64rando.com task queue. The convert/submit/poll code runs for real against a fake task that walks `queued` → `started` → `finished` over `MOCK_DK64_SECONDS`, which is what puts the presentation layer into the minutes-long waiting state a real DK64 roll causes. Rides on `MOCK_SEEDGEN`, so it inherits the one production refusal. |

### provider_task_service.py — `ProviderTaskService`

Lifecycle of `ProviderTask`: `queue` (write the row *before* submitting, so a crash leaves evidence rather than silence), `mark_submitted`, `claim` (a conditional update — every terminal transition goes through it, because completing a seed roll twice would write two `GeneratedSeeds` rows and DM both players twice), `fail`, `get_by_id` (tenant-scoped, the read every entry surface uses), `due_for_poll` (cross-tenant, worker-facing) and `sweep_stale` (abandons tasks past `TASK_MAX_AGE`, 20 minutes). Knows nothing about what any task *is*.

### seed_roll_service.py — `SeedRollService`

The domain half of the persisted roll: `submit` and `poll`, and the completion that runs when a task lands. Delegates to `MatchScheduleService.complete_seed_roll` — the same method the synchronous path uses — so a restart mid-roll changes who finishes it, not what finishing means. Misconfiguration (a missing credential, an unknown branch) is re-raised and the queued row deleted, because nothing was submitted and filing a config mistake as a broken roll would file another on every click. No retry. Detail: [seed-generation.md](seed-generation.md#asynchronous-rolls).

### seed_roll_worker.py

5-second `run_worker_loop` tick: sweep stale tasks, then poll every unfinished one inside its own `tenant_scope` (`for_each_tenant_scoped`). The only thing that advances a task-queue roll. Ungated — with no async randomizer in use there is nothing in the queue, and gating it would mean a restart silently stranding rolls already in flight. Started/stopped in `main.py`.

### qrcode_util.py

QR code generation for equipment assets ([qrcode_util.py](../../application/utils/qrcode_util.py)). Each code encodes the absolute URL of an asset's detail page (login required) so scanning jumps straight to it. Callers pass the fully-built URL — tenant-qualified (`/t/<slug>/equipment/<id>` in path mode) — so the util itself stays tenant-agnostic.

| Function | Returns | Description |
|---|---|---|
| `asset_qr_png_bytes(url)` | `bytes` | The QR code rendered as PNG bytes. |
| `asset_qr_data_uri(url)` | `str` | The QR code as a `data:image/png;base64` URI for inline `<img>` display. |

### build_info.py

Which build is running ([build_info.py](../../application/utils/build_info.py)). `build_info()` (cached) returns a `BuildInfo` with `version`, `commit`, `commit_date`, `commit_short` and `source_url`, from the `GIT_SHA`, `GIT_COMMIT_DATE` and `APP_VERSION` build args. The version is `APP_VERSION` (leading `v` dropped), else the commit's UTC date as `YYYY.MM.DD`, else `dev`. Read by `/about`, `GET /api/version`, the MCP `server_version` tool and the MCP handshake.

### sentry.py

Sentry error-monitoring initialization ([sentry.py](../../application/utils/sentry.py)). Wires the Sentry Python SDK into the process; a no-op unless `SENTRY_DSN` is set, so local dev and tests are unaffected. Once initialized it auto-instruments FastAPI/Starlette and stdlib `logging`, capturing unhandled exceptions and `logger.error`/`logger.exception` records across the request path, NiceGUI pages, and the Discord bot.

| Function | Returns | Description |
|---|---|---|
| `init_sentry()` | `None` | Initialize the SDK when `SENTRY_DSN` is configured; must run before the FastAPI app/middleware are built. Sets `environment`, `release` (`SENTRY_RELEASE` / `GIT_SHA`), `send_default_pii=False`, `SENTRY_TRACES_SAMPLE_RATE` (default `0.1` in production), Sentry Logs at `SENTRY_LOGS_LEVEL` (INFO), and a `before_send` hook that drops the NiceGUI timer-teardown race (see [timer_teardown.py](#timer_teardownpy)), tags the tenant in scope, fills in the session user for websocket handlers, and scrubs `Authorization`/`Cookie`/`Set-Cookie`/`X-API-Key` headers and cookies. |
| `sentry_tags(**tags)` | context manager | Fork the scope and tag every event/log inside the block; `None` values skipped. Used by workers, queues, Discord and racetime handlers, MCP tools. |
| `tag_current_scope(**tags)` | `None` | Add a tag learned mid-block to the scope already in effect. |

Full picture: [observability.md](../features/observability.md).

### log_levels.py

Pure helpers for runtime logger levels ([log_levels.py](../../application/utils/log_levels.py)).

| Name | Description |
|---|---|
| `TRACE` / `register_trace_level()` | Level 5, named at startup so records and `LOG_LEVEL=TRACE` work. |
| `LEVELS` | `TRACE` … `CRITICAL`, the picker's options. |
| `KNOWN_LOGGERS` | The loggers offered by name on `/platform`, each with what it covers. |
| `level_number(level)` / `normalize_logger_name(name)` | Validate (raise `ValueError`); `'root'`/`''` is the root logger. |
| `apply_level(name, level)` | Set on the live tree; `None` restores inheritance (root → `default_root_level()`). |
| `effective_level_name(name)` | The level a logger actually runs at. |

### timer_teardown.py

Recognising a `ui.timer` that outlived its page ([timer_teardown.py](../../application/utils/timer_teardown.py)). NiceGUI's timer waits for the client to connect and then enters its parent slot without re-checking deletion, so a page closed during that wait raises `RuntimeError: The parent slot of the element has been deleted`. Nobody can act on it. Pure stdlib so Sentry setup can import it. `theme.timer_teardown.PageTimer` stops the race happening for our own timers; this module is the backstop for any that don't use it.

| Name | Description |
|---|---|
| `is_timer_teardown_race(exc)` | True only for that exact `RuntimeError` message raised through a `nicegui.timer` frame. The same error from application code is a real bug and stays reported. |
| `TimerTeardownLogFilter` | `logging.Filter` that drops records whose exception (`exc_info`, or the exception passed as the message) is the race. |
| `install_log_filter()` | Attaches the filter to the `nicegui` logger, idempotently. Called from `frontend.init()`: NiceGUI's default exception handler is `log.exception` on that logger and runs alongside the app's own `on_exception` handler, so dropping it there is the only way to keep it out of the log. Sentry's logging hook runs behind logger filters, so this keeps it out of Sentry too. |

### discord_avatar.py

Discord CDN avatar URLs built from the hash on `User.discord_avatar`. Nothing is proxied or re-hosted — the browser fetches straight from `cdn.discordapp.com`, and a stale hash 404s, so every caller keeps its own placeholder as the fallback ([discord_avatar.py](../../application/utils/discord_avatar.py)).

| Function | Returns | Description |
|---|---|---|
| `avatar_url(discord_id, avatar_hash, size=64)` | `str \| None` | CDN URL, GIF for an `a_`-prefixed (animated) hash and PNG otherwise. `None` when either argument is missing. |
| `user_avatar_url(user, size=64)` | `str \| None` | The same for a `User` row; `None` for a placeholder or an account with no custom avatar. |

### timezone.py

Canonical UTC-storage / local-display utilities. Every builder takes an optional `tz`; `tz=None` means the viewer's resolved zone (see [`timezone_context.py`](../../application/timezone_context.py) and `TimezoneService` above). Pass an explicit `tz` for output that is **not** for the current viewer — a cached page, a notification for someone else. Full rules and rationale: [timezone-handling.md](../timezone-handling.md) ([timezone.py](../../application/utils/timezone.py)).

| Function | Returns | Description |
|---|---|---|
| `EASTERN_TZ` (constant) | `ZoneInfo` | `America/New_York` — the historical fixed zone, retained as the fallback when nothing resolves. Not the display zone. |
| `now_local(tz=None)` | `datetime` | Current local-aware datetime. |
| `today_local(tz=None)` | `date` | The current calendar date on the display clock. Use instead of `date.today()`. |
| `parse_local_datetime(date_str, time_str, tz=None)` | `datetime` | Parse `YYYY-MM-DD` + `HH:MM` as local wall clock and return UTC (the storage path). `ValueError` on bad format **or on a wall clock the zone skips over a DST spring-forward**. |
| `combine_local(day, moment, tz=None)` | `datetime` | `date`+`time` objects → UTC. The programmatic twin of `parse_local_datetime`; use instead of bare `datetime.combine`, which yields a naive value the ORM reads as UTC. |
| `local_day_bounds(start, end, tz=None)` | `(datetime, datetime)` | A date range → half-open UTC query bounds on the display clock. |
| `to_local(dt, tz=None)` | `datetime \| None` | Convert any datetime to local; naive inputs are assumed UTC. |
| `to_utc_aware(dt)` | `datetime` | Normalize to UTC-aware. Deliberately takes no `tz` — normalization, not presentation. |
| `format_local_datetime(dt, fmt='%Y-%m-%d %H:%M', tz=None)` | `str` | Local-formatted string; empty for `None`. |
| `format_local_date(dt, tz=None)` | `str` | `YYYY-MM-DD` local; plain `date` values format as-is. |
| `format_local_time(dt, tz=None)` | `str` | `HH:MM` (24-hour) local. |
| `format_local_display(dt, tz=None)` | `str` | `YYYY-MM-DD HH:MM <label>` with a DST-aware zone label. |
| `timezone_label(tz=None, at=None)` | `str` | The zone's short label (`EST`, `AEDT`, `+05:30`) for captions and column headers. |

### web_push.py

Pure Web Push protocol primitives — no I/O, no ORM ([web_push.py](../../application/utils/web_push.py)). Used by `WebPushService`; pinned to the RFC 8291 Appendix A test vector in `tests/test_web_push_protocol.py`.

| Function | Returns | Description |
|---|---|---|
| `encrypt_payload(plaintext, p256dh, auth)` | `bytes` | RFC 8291 `aes128gcm` message body encrypted to the subscription's client keys. |
| `vapid_authorization(endpoint, private_key, subject)` | `str` | `vapid t=<ES256 JWT>, k=<pubkey>` Authorization header value (RFC 8292). |
| `generate_vapid_keys()` | `(str, str)` | Fresh `(private, public)` VAPID keypair, base64url (see `scripts/generate_vapid_keys.py`). |
| `load_vapid_private_key(value)` | `EllipticCurvePrivateKey` | Parse the base64url raw 32-byte `VAPID_PRIVATE_KEY`. |
| `public_key_b64url(private_key)` | `str` | Derived `applicationServerKey` browsers subscribe with. |
| `b64url_encode(bytes)` / `b64url_decode(str)` | — | Padding-tolerant base64url helpers. |

### Additional utilities

Shared primitives and clients used across the service layer; each is a thin, focused helper rather than a full service.

| Module | Purpose |
|---|---|
| `background_loop.py` | `run_worker_loop(tick, interval, name, logger=None, tick_timeout=None)` — builds the `BackgroundLoop` (start/stop) every cadence worker is built on; a failing tick is logged, never fatal, a tick past its limit is cancelled, and a loop that ends unexpectedly restarts ([observability.md](../features/observability.md#worker-self-healing-and-the-worker-watchdog)) — and `for_each_tenant_scoped(items, handle, *, tenant_id_of, logger=None, describe=repr)`, which runs a per-item body inside that item's `tenant_scope` (skipping items with no tenant) so one bad row never stops the rest. |
| `coroutine_queue.py` | `CoroutineQueue` — a serial coroutine worker queue (the primitive behind `discord_queue`, event dispatch and webhook delivery); a call past `ITEM_TIMEOUT_SECONDS` is cancelled and a worker that ends unexpectedly restarts. |
| `worker_watchdog.py` | The `worker watchdog` loop: every 30s, one ERROR per newly stalled `BackgroundLoop`/`CoroutineQueue`, one WARNING when it recovers. |
| `config_validation.py` | `validate_config_blob(...)` — validate/normalize a JSON config blob against a Pydantic model. |
| `hashing.py` | `stable_content_hash(obj)` — deterministic sha256 for cheap unchanged-since-last-run checks (used by the reconcilers). |
| `hostname.py` | Hostname normalization + effective-request-host resolution for host-based tenant routing. |
| `twitch_embed.py` | Stage stream link → Twitch channel (`twitch_channel`), port-free `parent_hostname`, and the muted `player.twitch.tv` iframe URL (`player_url`) for the On Air theater (`theme/twitch_theater.py`). |
| `ssrf.py` | `ensure_public_host(hostname, *, subject=...)` — SSRF guard for outbound requests to user-supplied URLs (webhook targets, web-push subscription endpoints). |
| `speedgaming_client.py` | `SpeedGamingClient.fetch_schedule(event_slug, start, end, content_type=None)` — SpeedGaming schedule-API transport (consumed by the SG ETL); `get_speedgaming_client()` picks it or `MockSpeedGamingClient`; `SpeedGamingAPIError`. |
| `oauth_identity_client.py` | Shared OAuth identity-link transport (base for the Twitch/racetime clients + `IdentityLinkService`). |
| `racetime_entrants.py` | The two judgements both racetime capture paths (match race rooms, qualifier live races) share: `is_scored_finish(entrant)` (a `DONE` status **and** a finish time) and `unmatched_handle(entrant)` (display name for an entrant with no linked user). |
| `discord_embeds.py` | Discord embed builders (colour-by-category / match-state) for notification DMs. |
| `mock_discord_data.py` | Canned mock Discord data used by `DiscordService` under `MOCK_DISCORD`. |
| `tenant_session.py` | Per-tenant namespacing of `app.storage.user` UI state (`tenant_session_get`/`set`). |
| `tenant_urls.py` | Tenant-qualified URL building/validation + post-login return-path guards. |
| `app_links.py` | Pure in-app route builders (`admin_url`, `home_url`, `player_schedule_url`) + the tab slugs. Shared by `pages/` and the notification layer so a DM's button and the page's own link address the identical route. `pages/admin_tabs/links.py` re-exports the admin half. |
| `color_contrast.py` | WCAG contrast helpers (used by `TenantThemeService`). |
| `http_headers.py` | Reduce arbitrary text to a latin-1-safe response-header value, so one non-encodable character in a header set on every response cannot take the response down. |
| `serialization.py` | JSON-safe coercion shared across entry surfaces — notably `decode_json_details`, which falls back to the raw string rather than losing a whole audit row to a malformed `details` blob. |
| `seed_provider.py` | The one execution envelope for every outbound randomizer call: `call_provider(...)` enforces a per-attempt timeout (`PROVIDER_TIMEOUT_SECONDS`), bounded retry with backoff for timeouts/connection errors/429/5xx only, and returns a `ProviderCall` (value + attempts + latency). Failures raise the `SeedProviderError` taxonomy (`Timeout`, `Unavailable`, `RateLimited`, `InvalidRequest`, `BadResponse`), all `ValueError` subclasses so existing `except ValueError` handling keeps working. `raise_for_status(resp, *, provider, operation)` classifies an `aiohttp` response. |
| `match_labels.py` | Pure human-readable match names for web copy (`players_label`, `match_label`, `match_model_label`, `match_row_label`) — the web-side counterpart to the Discord rule that user-facing text never prints a match id. |
| `html_cache.py` | `HtmlPageCache` — process-local whole-page cache for the anonymous bracket views: a per-tenant version counter (bumped from the event bus) plus a TTL, with a strong `ETag` per entry so reloads usually get a 304. |
| `migration_lock.py` | `migration_lock(dsn, acquire_timeout=...)` — async context manager holding a Postgres advisory lock around the startup Aerich upgrade so two booting processes cannot run the same migrations; a no-op on SQLite. |
| `tournament_signup.py` | Pure signup-window logic (`SignupWindow`, `signup_window_state`, `signup_window_is_open`) behind `TournamentService`'s self-service enrolment. |

## Testing the service layer

Service tests live in `tests/services/` — broadly one `test_<service>.py` module per service, covering the great majority of the layer. Coverage is not total — the live-infra paths (real Discord/racetime connections, the HTTP randomizer clients) are exercised only through their mocked seams; see [development.md](../development.md#known-gaps) for the intentional gaps. Key fixtures:

- [`tests/conftest.py`](../../tests/conftest.py) provides a function-scoped `db` fixture: a fresh in-memory SQLite database per test via `Tortoise.init(db_url="sqlite://:memory:")` + `generate_schemas()`, torn down by closing connections — no state leaks between tests.
- [`tests/services/conftest.py`](../../tests/services/conftest.py) adds an autouse `stub_discord_queue` fixture that monkeypatches `discord_queue.enqueue` to capture coroutines instead of running them, letting tests assert that notifications were enqueued while closing the coroutines to avoid "never awaited" warnings.
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions need no decorator.

For the full development workflow (running tests, CI, suite scope and known gaps) see [development.md](../development.md).
