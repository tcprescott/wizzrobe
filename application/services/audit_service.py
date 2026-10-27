"""
Audit Service - Business Logic Layer

Handles audit logging for tracking user actions and system events.

All entries use namespaced action strings in the form ``verb.object`` (e.g.
``match.created``, ``user.role_granted``). Details are persisted as JSON so
the viewer can render them structurally and so they remain queryable.
"""

import json
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from application.event_context import get_current_event_id
from application.events import Event, event_bus
from application.repositories.audit_repository import AuditRepository
from application.repositories.stage_repository import StageRepository
from application.repositories.tournament_repository import TournamentRepository
from application.repositories.user_repository import UserRepository
from application.services.auth_service import AuthService
from application.tenant_context import get_current_tenant_id
from application.utils.audit_display import AuditNames, actions_matching, area_by_key, referenced_ids
from models import AuditLog, User


# Canonical action verbs used across the codebase. Keeping them here gives
# one place to grep when reviewing audit coverage.
class AuditActions:
    # A community's events (one SGL weekend each) and their lifecycle
    EVENT_CREATED = 'event.created'
    EVENT_UPDATED = 'event.updated'
    EVENT_WENT_LIVE = 'event.went_live'
    EVENT_ARCHIVED = 'event.archived'
    EVENT_UNARCHIVED = 'event.unarchived'
    EVENT_DEFAULT_CHANGED = 'event.default_changed'
    EVENT_DELETED = 'event.deleted'
    EVENT_SETUP_COPIED = 'event.setup_copied'
    # Match lifecycle and CRUD
    MATCH_CREATED = 'match.created'
    MATCH_UPDATED = 'match.updated'
    MATCH_RESCHEDULED = 'match.rescheduled'
    MATCH_DELETED = 'match.deleted'
    MATCH_CANCELLED = 'match.cancelled'
    MATCH_REQUESTED = 'match.requested'
    MATCH_SEATED = 'match.seated'
    MATCH_STARTED = 'match.started'
    MATCH_FINISHED = 'match.finished'
    MATCH_CONFIRMED = 'match.confirmed'
    MATCH_FORFEITED = 'match.forfeited'
    MATCH_ACKNOWLEDGED = 'match.acknowledged'
    MATCH_RESULT_RECORDED = 'match.result_recorded'
    MATCH_SEED_ROLLED = 'match.seed_rolled'
    MATCH_SEED_ROLL_QUEUED = 'match.seed_roll_queued'
    MATCH_SEED_ROLL_FAILED = 'match.seed_roll_failed'
    MATCH_STAGE_ASSIGNED = 'match.stage_assigned'
    MATCH_STAGE_CLEARED = 'match.stage_cleared'
    MATCH_STATIONS_ASSIGNED = 'match.stations_assigned'
    MATCH_STREAM_CANDIDATE_SET = 'match.stream_candidate_set'
    MATCH_STREAM_CANDIDATE_CLEARED = 'match.stream_candidate_cleared'
    # A proctor flagging a recorded result as contested, and the flag being
    # resolved (explicitly, or implicitly by the admin confirming the match).
    MATCH_FLAGGED_FOR_REVIEW = 'match.flagged_for_review'
    MATCH_REVIEW_CLEARED = 'match.review_cleared'
    MATCH_WATCHER_ADDED = 'match.watcher_added'
    MATCH_WATCHER_REMOVED = 'match.watcher_removed'
    # A player offering their own match for stream, and taking the offer back.
    # Advisory: neither one touches is_stream_candidate.
    MATCH_STREAM_VOLUNTEERED = 'match.stream_volunteered'
    MATCH_STREAM_VOLUNTEER_WITHDRAWN = 'match.stream_volunteer_withdrawn'
    # A comment on a match, from a player or staff, and its removal.
    MATCH_COMMENT_ADDED = 'match.comment_added'
    MATCH_COMMENT_DELETED = 'match.comment_deleted'
    MATCH_PLAYER_RUNNING_LATE = 'match.player_running_late'
    MATCH_PLAYER_LATE_CLEARED = 'match.player_late_cleared'
    MATCH_PLAYER_SEED_READY = 'match.player_seed_ready'
    MATCH_PLAYER_SEED_NOT_READY = 'match.player_seed_not_ready'
    # A player asking staff to move or call off their match, and what became of
    # it. The approval also writes the underlying match.rescheduled / match.cancelled,
    # since approving *performs* the change rather than blessing it.
    # One player privately agreeing to the tournament's harder preset, and
    # taking it back. Audited because it decides what a match is played on, but
    # deliberately NOT published as an event: a webhook subscriber is an
    # arbitrary outside listener, and the whole feature rests on a lone opt-in
    # reaching nobody. Only the unanimous result below is published.
    MATCH_HARD_PRESET_OPTED_IN = 'match.hard_preset_opted_in'
    MATCH_HARD_PRESET_WITHDRAWN = 'match.hard_preset_withdrawn'
    # Everyone in the match has opted in (or somebody backed out of a set that
    # was complete). Now public: at this point the players know, so there is
    # nothing left to keep.
    MATCH_HARD_PRESET_AGREED = 'match.hard_preset_agreed'
    MATCH_HARD_PRESET_AGREEMENT_REVOKED = 'match.hard_preset_agreement_revoked'
    # Staff overruling the players' choice for one match, in either direction.
    MATCH_PRESET_OVERRIDE_SET = 'match.preset_override_set'
    MATCH_PRESET_OVERRIDE_CLEARED = 'match.preset_override_cleared'
    MATCH_RESCHEDULE_REQUESTED = 'match.reschedule_requested'
    MATCH_RESCHEDULE_AGREED = 'match.reschedule_agreed'
    MATCH_RESCHEDULE_APPROVED = 'match.reschedule_approved'
    MATCH_RESCHEDULE_DECLINED = 'match.reschedule_declined'
    MATCH_RESCHEDULE_WITHDRAWN = 'match.reschedule_withdrawn'
    MATCH_SET_EXTENDED = 'match.set_extended'

    # Crew
    CREW_SIGNUP_CREATED = 'crew.signup_created'
    CREW_SIGNUP_REMOVED = 'crew.signup_removed'
    CREW_APPROVAL_CHANGED = 'crew.approval_changed'
    CREW_ACKNOWLEDGED = 'crew.acknowledged'
    CREW_MOVED_TO_BOOKING = 'crew.moved_to_booking'
    CREW_PARTNER_INVITED = 'crew.partner_invited'
    CREW_PARTNER_INVITE_ANSWERED = 'crew.partner_invite_answered'
    CREW_PARTNER_INVITE_CANCELLED = 'crew.partner_invite_cancelled'

    # Tournament
    TOURNAMENT_CREATED = 'tournament.created'
    TOURNAMENT_UPDATED = 'tournament.updated'
    TOURNAMENT_ROUND_SCHEDULE_UPDATED = 'tournament.round_schedule_updated'
    TOURNAMENT_DELETED = 'tournament.deleted'
    TOURNAMENT_PLAYER_DROPPED = 'tournament.player_dropped'
    TOURNAMENT_ADMIN_GRANTED = 'tournament.admin_granted'
    TOURNAMENT_ADMIN_REVOKED = 'tournament.admin_revoked'
    TOURNAMENT_CREW_COORDINATOR_GRANTED = 'tournament.crew_coordinator_granted'
    TOURNAMENT_CREW_COORDINATOR_REVOKED = 'tournament.crew_coordinator_revoked'
    # Prize money. Audited because money moving is what an audit log is for:
    # who set the pool, and who decided each placement's share.
    TOURNAMENT_PRIZE_POOL_UPDATED = 'tournament.prize_pool_updated'
    TOURNAMENT_PAYOUT_UPDATED = 'tournament.payout_updated'
    TOURNAMENT_SGLIVE_LINK_UPDATED = 'tournament.sglive_link_updated'
    # A participants + matches export left the app (Reports → Exports).
    TOURNAMENT_DATA_EXPORTED = 'tournament.data_exported'

    # User
    USER_CREATED = 'user.created'
    USER_PROVISIONED = 'user.provisioned'
    USER_PROFILE_UPDATED = 'user.profile_updated'
    USER_SELF_PROFILE_UPDATED = 'user.self_profile_updated'
    USER_ACTIVATION_CHANGED = 'user.activation_changed'
    USER_ROLE_GRANTED = 'user.role_granted'
    USER_ROLE_REVOKED = 'user.role_revoked'
    USER_TOURNAMENT_ENROLLMENT_UPDATED = 'user.tournament_enrollment_updated'
    # Password sign-in, administered on /platform. The password itself is never
    # in ``details``; only the sign-in name and whether the account is new.
    USER_PASSWORD_SET = 'user.password_set'
    USER_PASSWORD_REMOVED = 'user.password_removed'
    USER_PASSWORD_SIGNED_IN = 'user.password_signed_in'

    # Discord role mapping / sync
    DISCORD_ROLE_MAPPING_ADDED = 'discord_role.mapping_added'
    DISCORD_ROLE_MAPPING_REMOVED = 'discord_role.mapping_removed'
    ROLE_DISCORD_SYNC_GRANTED = 'role.discord_sync_granted'
    ROLE_DISCORD_SYNC_REVOKED = 'role.discord_sync_revoked'
    ROLE_DISCORD_SYNC_BULK = 'role.discord_sync_bulk'
    # Outbound: Discord roles Wizzrobe hands out (DiscordRoleSyncService)
    DISCORD_ROLE_RULE_SET = 'discord_role.rule_set'
    DISCORD_ROLE_RULE_CLEARED = 'discord_role.rule_cleared'
    DISCORD_ROLE_RULES_RESET = 'discord_role.rules_reset'
    # Tenant ↔ Discord server link (tenant-scoped; stamped with the tenant)
    DISCORD_SERVER_LINKED = 'discord.server_linked'
    DISCORD_SERVER_UNLINKED = 'discord.server_unlinked'
    DISCORD_LOGIN_PANEL_POSTED = 'discord.login_panel_posted'

    # Discord Scheduled Events mirror (PR 8). The create/update/cancel rows are
    # written by the reconciler (system user on the worker, or a human on-demand);
    # the per-run summary/failure and the per-tournament settings edit are
    # audit-only plumbing.
    DISCORD_EVENT_CREATED = 'discord_event.created'
    DISCORD_EVENT_UPDATED = 'discord_event.updated'
    DISCORD_EVENT_CANCELLED = 'discord_event.cancelled'
    DISCORD_EVENT_SYNC_COMPLETED = 'discord_event.sync_completed'
    DISCORD_EVENT_SYNC_FAILED = 'discord_event.sync_failed'
    DISCORD_EVENT_SETTINGS_UPDATED = 'discord_event.settings_updated'

    # Station pool (the venue's physical seats)
    STATION_CREATED = 'station.created'
    STATION_UPDATED = 'station.updated'
    STATION_DELETED = 'station.deleted'

    # Stage
    STAGE_CREATED = 'stage.created'
    STAGE_UPDATED = 'stage.updated'
    STAGE_DELETED = 'stage.deleted'

    # System
    SYSTEM_CONFIG_UPDATED = 'system_config.updated'
    THEME_UPDATED = 'theme.updated'
    THEME_LOGO_UPDATED = 'theme.logo_updated'
    THEME_LOGO_REMOVED = 'theme.logo_removed'
    TIMEZONE_UPDATED = 'timezone.updated'
    USER_TIMEZONE_UPDATED = 'user.timezone_updated'
    USER_HOUR_CYCLE_UPDATED = 'user.hour_cycle_updated'
    PROFILE_NUDGES_UPDATED = 'profile_nudges.updated'
    PROFILE_NUDGE_DISMISSED = 'profile_nudge.dismissed'

    # Triforce texts
    TRIFORCE_TEXT_SUBMITTED = 'triforce_text.submitted'
    TRIFORCE_TEXT_APPROVED = 'triforce_text.approved'
    TRIFORCE_TEXT_REJECTED = 'triforce_text.rejected'
    TRIFORCE_TEXT_DELETED = 'triforce_text.deleted'

    # API tokens
    APITOKEN_CREATED = 'apitoken.created'
    APITOKEN_REVOKED = 'apitoken.revoked'

    # Third-party apps (the /oauth authorization server). App registration is
    # global, so those rows carry no community; grants are per community.
    OAUTH_APP_CREATED = 'oauth_app.created'
    OAUTH_APP_UPDATED = 'oauth_app.updated'
    OAUTH_APP_SECRET_ROTATED = 'oauth_app.secret_rotated'
    OAUTH_APP_DELETED = 'oauth_app.deleted'
    OAUTH_APP_DISABLED = 'oauth_app.disabled'
    OAUTH_APP_ENABLED = 'oauth_app.enabled'
    OAUTH_APP_BLOCKED = 'oauth_app.blocked'
    OAUTH_APP_UNBLOCKED = 'oauth_app.unblocked'
    OAUTH_GRANT_CREATED = 'oauth_grant.created'
    OAUTH_GRANT_REVOKED = 'oauth_grant.revoked'
    # A spent refresh token presented again: the grant is revoked as stolen.
    OAUTH_GRANT_REFRESH_REUSED = 'oauth_grant.refresh_reused'

    # Tournament-room kiosk tokens
    ROOM_TOKEN_CREATED = 'roomtoken.created'
    ROOM_TOKEN_REVOKED = 'roomtoken.revoked'

    # In-app feedback
    FEEDBACK_SUBMITTED = 'feedback.submitted'
    FEEDBACK_REVIEWED = 'feedback.reviewed'
    FEEDBACK_REOPENED = 'feedback.reopened'

    # Community events (member-submitted, staff-approved)
    COMMUNITY_EVENT_SUBMITTED = 'community_event.submitted'
    COMMUNITY_EVENT_UPDATED = 'community_event.updated'
    COMMUNITY_EVENT_APPROVED = 'community_event.approved'
    COMMUNITY_EVENT_REJECTED = 'community_event.rejected'
    COMMUNITY_EVENT_CANCELLED = 'community_event.cancelled'

    # Post-event surveys. Details carry ids and counts, never answers.
    SURVEY_CREATED = 'survey.created'
    SURVEY_UPDATED = 'survey.updated'
    SURVEY_DELETED = 'survey.deleted'
    SURVEY_SENT = 'survey.sent'
    SURVEY_REMINDED = 'survey.reminded'
    SURVEY_CLOSED = 'survey.closed'
    SURVEY_QUESTIONS_SUGGESTED = 'survey.questions_suggested'

    # ADA accommodation requests. Details carry ids and status only, never the
    # requester's text or staff notes.
    ACCOMMODATION_REQUESTED = 'accommodation.requested'
    ACCOMMODATION_UPDATED = 'accommodation.updated'
    ACCOMMODATION_WITHDRAWN = 'accommodation.withdrawn'
    ACCOMMODATION_STATUS_CHANGED = 'accommodation.status_changed'
    ACCOMMODATION_NOTES_UPDATED = 'accommodation.notes_updated'
    ACCOMMODATION_CHANGE_REVIEWED = 'accommodation.change_reviewed'

    # Emergency contacts. Details carry ids only, never the name or number.
    # ``viewed`` is the point of the feature: every staff look leaves a row.
    EMERGENCY_CONTACT_SAVED = 'emergency_contact.saved'
    EMERGENCY_CONTACT_REMOVED = 'emergency_contact.removed'
    EMERGENCY_CONTACT_VIEWED = 'emergency_contact.viewed'
    # Stream layouts (OBS overlays)
    STREAM_LAYOUT_CREATED = 'stream_layout.created'
    STREAM_LAYOUT_UPDATED = 'stream_layout.updated'
    STREAM_LAYOUT_DELETED = 'stream_layout.deleted'
    STAGE_OVERLAY_UPDATED = 'stage_overlay.updated'
    STAGE_OVERLAY_PINNED = 'stage_overlay.pinned'
    STAGE_OVERLAY_UNPINNED = 'stage_overlay.unpinned'
    STAGE_OVERLAY_LINK_RENEWED = 'stage_overlay.link_renewed'

    # Equipment lending
    EQUIPMENT_CREATED = 'equipment.created'
    EQUIPMENT_UPDATED = 'equipment.updated'
    EQUIPMENT_DELETED = 'equipment.deleted'
    EQUIPMENT_CHECKED_OUT = 'equipment.checked_out'
    EQUIPMENT_CHECKED_IN = 'equipment.checked_in'

    # Event check-in
    CHECK_IN_EVENT_CREATED = 'check_in_event.created'
    CHECK_IN_EVENT_UPDATED = 'check_in_event.updated'
    CHECK_IN_EVENT_DELETED = 'check_in_event.deleted'
    CHECK_IN_EVENT_SYNCED = 'check_in_event.synced'
    CHECK_IN_TIER_UPDATED = 'check_in_tier.updated'
    CHECK_IN_ENTRANT_CHECKED_IN = 'check_in_entrant.checked_in'
    CHECK_IN_ENTRANT_CHECK_IN_UNDONE = 'check_in_entrant.check_in_undone'
    CHECK_IN_ENTRANT_LINKED = 'check_in_entrant.linked'
    CHECK_IN_ENTRANT_UNLINKED = 'check_in_entrant.unlinked'
    CHECK_IN_ENTRANT_WALK_UP_ADDED = 'check_in_entrant.walk_up_added'
    CHECK_IN_ENTRANT_REMOVED = 'check_in_entrant.removed'
    MATCHERINO_LOGIN_SET = 'matcherino_login.set'
    MATCHERINO_LOGIN_CLEARED = 'matcherino_login.cleared'
    MATCHERINO_LOGIN_ALERTS_UPDATED = 'matcherino_login.alerts_updated'

    # Player availability
    PLAYER_AVAILABILITY_UPDATED = 'player.availability_updated'

    # Challonge integration
    CHALLONGE_CONNECTED = 'challonge.connected'
    CHALLONGE_DISCONNECTED = 'challonge.disconnected'
    CHALLONGE_COMMUNITY_REMOVED = 'challonge.community_removed'
    CHALLONGE_PLAYER_LINKED = 'challonge.player_linked'
    CHALLONGE_PLAYER_UNLINKED = 'challonge.player_unlinked'
    CHALLONGE_PLAYER_USERNAME_UPDATED = 'challonge.player_username_updated'
    CHALLONGE_TOURNAMENT_LINKED = 'challonge.tournament_linked'
    CHALLONGE_TOURNAMENT_UNLINKED = 'challonge.tournament_unlinked'
    CHALLONGE_BRACKET_SYNCED = 'challonge.bracket_synced'
    CHALLONGE_RESULT_PUSHED = 'challonge.result_pushed'
    CHALLONGE_RESULT_SKIPPED = 'challonge.result_skipped'
    CHALLONGE_RESULT_PUSH_FAILED = 'challonge.result_push_failed'
    CHALLONGE_AUTO_SYNC_UPDATED = 'challonge.auto_sync_updated'
    CHALLONGE_PARTICIPANT_ASSIGNED = 'challonge.participant_assigned'
    CHALLONGE_PARTICIPANT_UNASSIGNED = 'challonge.participant_unassigned'
    CHALLONGE_PARTICIPANT_SIGNED_UP = 'challonge.participant_signed_up'
    CHALLONGE_PARTICIPANT_WITHDRAWN = 'challonge.participant_withdrawn'
    CHALLONGE_STAGE_ADVANCED = 'challonge.stage_advanced'
    CHALLONGE_WEBHOOK_SYNCED = 'challonge.webhook_synced'
    CHALLONGE_MATCH_LINKED = 'challonge.match_linked'

    # Twitch integration
    STARTGG_LINKED = 'startgg.linked'
    STARTGG_UNLINKED = 'startgg.unlinked'
    STARTGG_EVENT_LINKED = 'startgg.event_linked'
    STARTGG_EVENT_UNLINKED = 'startgg.event_unlinked'
    STARTGG_EVENT_SYNCED = 'startgg.event_synced'
    STARTGG_BOOKABLE_PHASE_SET = 'startgg.bookable_phase_set'
    STARTGG_ENTRANT_ASSIGNED = 'startgg.entrant_assigned'
    STARTGG_ENTRANT_UNASSIGNED = 'startgg.entrant_unassigned'
    STARTGG_SUGGESTION_DISMISSED = 'startgg.suggestion_dismissed'
    STARTGG_REPORTING_SET = 'startgg.reporting_set'
    STARTGG_RESULT_REPORTED = 'startgg.result_reported'
    STARTGG_SET_LINKED = 'startgg.set_linked'
    STARTGG_CONNECTED = 'startgg.connected'
    STARTGG_DISCONNECTED = 'startgg.disconnected'
    TWITCH_LINKED = 'twitch.linked'
    TWITCH_UNLINKED = 'twitch.unlinked'

    # Racetime.gg identity linking
    RACETIME_LINKED = 'racetime.linked'
    RACETIME_UNLINKED = 'racetime.unlinked'

    # Racetime bots (platform-managed; CRUD + tenant grants are platform-level,
    # tenant=NULL) and their reusable per-tenant room profiles
    RACETIME_BOT_CREATED = 'racetime_bot.created'
    RACETIME_BOT_UPDATED = 'racetime_bot.updated'
    RACETIME_BOT_DELETED = 'racetime_bot.deleted'
    RACETIME_BOT_GRANTED = 'racetime_bot.granted'
    RACETIME_BOT_REVOKED = 'racetime_bot.revoked'
    RACE_ROOM_PROFILE_CREATED = 'race_room_profile.created'
    RACE_ROOM_PROFILE_UPDATED = 'race_room_profile.updated'
    RACE_ROOM_PROFILE_DELETED = 'race_room_profile.deleted'

    # Racetime bot runtime health (platform-level, tenant=NULL; audit-only —
    # written by the racetimebot/ connection loop as the system user)
    RACETIME_BOT_CONNECTED = 'racetime_bot.connected'
    RACETIME_BOT_DISCONNECTED = 'racetime_bot.disconnected'
    RACETIME_BOT_ERROR = 'racetime_bot.error'
    RACETIME_BOT_RESTARTED = 'racetime_bot.restarted'

    # Runtime log levels (platform-level, tenant=NULL; set on /platform)
    LOG_LEVEL_SET = 'log_level.set'
    LOG_LEVEL_CLEARED = 'log_level.cleared'

    # Browser-side Sentry (platform-level, tenant=NULL; set on /platform)
    BROWSER_MONITORING_UPDATED = 'browser_monitoring.updated'
    BROWSER_IGNORE_RULE_ADDED = 'browser_ignore_rule.added'
    BROWSER_IGNORE_RULE_REMOVED = 'browser_ignore_rule.removed'

    # Racetime race-room lifecycle (tenant-scoped; mirrored on the event bus as
    # ``race_room.*`` domain events — the system user is the actor)
    RACE_ROOM_CREATED = 'race_room.created'
    RACE_ROOM_OPENED = 'race_room.opened'
    RACE_ROOM_STARTED = 'race_room.started'
    RACE_ROOM_FINISHED = 'race_room.finished'
    RACE_ROOM_CANCELLED = 'race_room.cancelled'
    RACE_ROOM_RESULT_RECORDED = 'race_room.result_recorded'
    RACE_ROOM_RESULT_HELD = 'race_room.result_held'
    RACE_ROOM_FPA_CALLED = 'race_room.fpa_called'

    # Volunteer scheduling
    VOLUNTEER_POSITION_CREATED = 'volunteer.position_created'
    VOLUNTEER_POSITION_UPDATED = 'volunteer.position_updated'
    VOLUNTEER_POSITION_DELETED = 'volunteer.position_deleted'
    VOLUNTEER_SHIFT_CREATED = 'volunteer.shift_created'
    VOLUNTEER_SHIFT_UPDATED = 'volunteer.shift_updated'
    VOLUNTEER_SHIFT_DELETED = 'volunteer.shift_deleted'
    VOLUNTEER_ASSIGNED = 'volunteer.assigned'
    VOLUNTEER_UNASSIGNED = 'volunteer.unassigned'
    VOLUNTEER_ACKNOWLEDGED = 'volunteer.acknowledged'
    VOLUNTEER_CHECKED_IN = 'volunteer.checked_in'
    VOLUNTEER_CHECK_IN_UNDONE = 'volunteer.check_in_undone'
    VOLUNTEER_RUNNING_LATE = 'volunteer.running_late'
    VOLUNTEER_CALLED_OFF = 'volunteer.called_off'
    VOLUNTEER_CALL_OFF_UNDONE = 'volunteer.call_off_undone'
    VOLUNTEER_AVAILABILITY_UPDATED = 'volunteer.availability_updated'
    VOLUNTEER_DRAFT_GENERATED = 'volunteer.draft_generated'
    VOLUNTEER_DRAFT_CLEARED = 'volunteer.draft_cleared'
    VOLUNTEER_DRAFT_PUBLISHED = 'volunteer.draft_published'
    VOLUNTEER_RELEASED = 'volunteer.released'
    VOLUNTEER_SHIFTS_RESET = 'volunteer.shifts_reset'
    VOLUNTEER_QUALIFICATIONS_UPDATED = 'volunteer.qualifications_updated'
    VOLUNTEER_DATA_EXPORTED = 'volunteer.data_exported'
    VOLUNTEER_ROLE_MAPPING_ADDED = 'volunteer.role_mapping_added'
    VOLUNTEER_ROLE_MAPPING_REMOVED = 'volunteer.role_mapping_removed'
    ROLE_VOLUNTEER_SYNC_GRANTED = 'role.volunteer_sync_granted'
    ROLE_VOLUNTEER_SYNC_REVOKED = 'role.volunteer_sync_revoked'

    # Web push (device notifications)
    WEB_PUSH_SUBSCRIBED = 'web_push.subscribed'
    WEB_PUSH_UNSUBSCRIBED = 'web_push.unsubscribed'
    WEB_PUSH_ROTATED = 'web_push.rotated'
    NOTIFICATION_EMAIL_CONFIRMED = 'notification_email.confirmed'
    NOTIFICATION_EMAIL_REMOVED = 'notification_email.removed'
    NOTIFICATION_TELEGRAM_LINKED = 'notification_telegram.linked'
    NOTIFICATION_TELEGRAM_UNLINKED = 'notification_telegram.unlinked'
    NOTIFICATION_SETTING_CHANGED = 'notification_setting.changed'
    NOTIFICATION_SETTINGS_RESET = 'notification_settings.reset'

    # Presets (seed-rolling settings)
    PRESET_CREATED = 'preset.created'
    PRESET_UPDATED = 'preset.updated'
    PRESET_DELETED = 'preset.deleted'
    PRESET_IMPORTED = 'preset.imported'
    PRESET_TEST_ROLLED = 'preset.test_rolled'
    RANDOMIZER_CREDENTIAL_SET = 'randomizer_credential.set'
    RANDOMIZER_CREDENTIAL_CLEARED = 'randomizer_credential.cleared'
    AI_PROVIDER_SET = 'ai_provider.set'
    AI_PROVIDER_CLEARED = 'ai_provider.cleared'

    # SpeedGaming ETL (PR 7). Config CRUD is tenant-scoped, actor = a human
    # SYNC_ADMIN; the sync/import/skip/cancel/auto-finish rows are written by the
    # sync worker acting as the system user, tenant from ``tenant_scope``.
    SG_EVENT_LINK_CREATED = 'sg_sync.event_link_created'
    SG_EVENT_LINK_UPDATED = 'sg_sync.event_link_updated'
    SG_EVENT_LINK_DELETED = 'sg_sync.event_link_deleted'
    SG_SYNC_COMPLETED = 'sg_sync.completed'
    SG_SYNC_FAILED = 'sg_sync.failed'
    SG_EPISODE_IMPORTED = 'sg_sync.episode_imported'
    SG_EPISODE_SKIPPED = 'sg_sync.episode_skipped'
    SG_EPISODE_CANCELLED = 'sg_sync.episode_cancelled'
    SG_MATCH_AUTO_FINISHED = 'sg_sync.match_auto_finished'
    SG_PLACEHOLDER_CREATED = 'sg_sync.placeholder_created'
    SG_PLACEHOLDER_UPGRADED = 'sg_sync.placeholder_upgraded'

    # Async Qualifiers (PR 9). A peer aggregate of Tournament: qualifier/pool/
    # permalink authoring and the per-qualifier ``admins`` grants are tenant-
    # internal config (event-less); the run submitted/reviewed outcomes DO emit
    # events for subscribers (see EventType.ASYNC_QUALIFIER_RUN_*). The actor is
    # a human QUALIFIER_ADMIN for management/review and the running player for
    # start/submit/forfeit/reattempt.
    ASYNC_QUALIFIER_CREATED = 'async_qualifier.created'
    ASYNC_QUALIFIER_UPDATED = 'async_qualifier.updated'
    ASYNC_QUALIFIER_DELETED = 'async_qualifier.deleted'
    ASYNC_QUALIFIER_ADMIN_GRANTED = 'async_qualifier.admin_granted'
    ASYNC_QUALIFIER_ADMIN_REVOKED = 'async_qualifier.admin_revoked'
    ASYNC_QUALIFIER_POOL_CREATED = 'async_qualifier.pool_created'
    ASYNC_QUALIFIER_POOL_UPDATED = 'async_qualifier.pool_updated'
    ASYNC_QUALIFIER_POOL_DELETED = 'async_qualifier.pool_deleted'
    ASYNC_QUALIFIER_PERMALINK_ADDED = 'async_qualifier.permalink_added'
    ASYNC_QUALIFIER_PERMALINK_UPDATED = 'async_qualifier.permalink_updated'
    ASYNC_QUALIFIER_PERMALINK_DELETED = 'async_qualifier.permalink_deleted'
    ASYNC_QUALIFIER_RUN_STARTED = 'async_qualifier.run_started'
    ASYNC_QUALIFIER_RUN_SUBMITTED = 'async_qualifier.run_submitted'
    ASYNC_QUALIFIER_RUN_FORFEITED = 'async_qualifier.run_forfeited'
    # The worker forfeited a run the player drew and never came back to. Distinct
    # from a chosen forfeit because it is not something the runner did.
    ASYNC_QUALIFIER_RUN_EXPIRED = 'async_qualifier.run_expired'
    ASYNC_QUALIFIER_RUN_REATTEMPTED = 'async_qualifier.run_reattempted'
    # A reviewer voided someone else's run: distinct from the runner spending their
    # own allowance, because it bypasses that allowance entirely.
    ASYNC_QUALIFIER_REATTEMPT_GRANTED = 'async_qualifier.reattempt_granted'
    ASYNC_QUALIFIER_RUN_REVIEWED = 'async_qualifier.run_reviewed'
    # Reversing a settled verdict. Distinct from the first one because it is the
    # rarer, more contestable act: it changes an outcome the runner was already
    # told about, and "who overturned this, and why" is the question an appeal asks.
    ASYNC_QUALIFIER_RUN_REVIEW_OVERRIDDEN = 'async_qualifier.run_review_overridden'
    # Async Qualifier live races (PR 10). Create/open/cancel are tenant-internal
    # scheduling (event-less); recording the finished race captures runs and DOES
    # emit an event (see EventType.ASYNC_QUALIFIER_LIVE_RACE_RECORDED).
    ASYNC_QUALIFIER_LIVE_RACE_CREATED = 'async_qualifier.live_race_created'
    ASYNC_QUALIFIER_LIVE_RACE_OPENED = 'async_qualifier.live_race_opened'
    # Choosing (or changing) the seed a race is run on. Its own action because it
    # decides which permalink's par the results are scored against.
    ASYNC_QUALIFIER_LIVE_RACE_PERMALINK_ASSIGNED = 'async_qualifier.live_race_permalink_assigned'
    ASYNC_QUALIFIER_LIVE_RACE_CANCELLED = 'async_qualifier.live_race_cancelled'
    ASYNC_QUALIFIER_LIVE_RACE_RECORDED = 'async_qualifier.live_race_recorded'
    # Staff typing the results in because the room's event never arrived. Distinct
    # from the line above for the same reason a review override is distinct from a
    # review: a human asserting a result is the more contestable act, and "who typed
    # this" is what an appeal asks. Subscribers get the ordinary recorded event.
    ASYNC_QUALIFIER_LIVE_RACE_RECORDED_MANUALLY = 'async_qualifier.live_race_recorded_manually'

    # Native brackets (docs/features/brackets.md). Bracket/entrant lifecycle and the
    # match/advancement/stage events the lifecycle drives. Actor is a human STAFF
    # for management; the started/completed/advanced rows capture the graph
    # transitions the service performs.
    BRACKET_CREATED = 'bracket.created'
    # Covers every edit to a bracket's own definition — name/stage/config,
    # per-round display metadata and reseeding — with ``details`` naming which.
    BRACKET_UPDATED = 'bracket.updated'
    BRACKET_DELETED = 'bracket.deleted'
    BRACKET_STARTED = 'bracket.started'
    BRACKET_MATCH_COMPLETED = 'bracket.match_completed'
    BRACKET_GAME_SCHEDULED = 'bracket.game_scheduled'
    BRACKET_GAME_COMPLETED = 'bracket.game_completed'
    BRACKET_GAME_CANCELLED = 'bracket.game_cancelled'
    # A manually-scheduled Match attached to / detached from a matchup by staff,
    # as opposed to BRACKET_GAME_SCHEDULED's book-it-from-the-bracket path.
    BRACKET_GAME_LINKED = 'bracket.game_linked'
    BRACKET_GAME_UNLINKED = 'bracket.game_unlinked'
    # A cancelled/deleted Match handing its series slot back (D3), as opposed
    # to BRACKET_GAME_UNLINKED's deliberate staff detach.
    BRACKET_GAME_RELEASED = 'bracket.game_released'
    BRACKET_ADVANCED = 'bracket.advanced'
    BRACKET_COMPLETED = 'bracket.completed'
    BRACKET_STAGE_ADVANCED = 'bracket.stage_advanced'
    BRACKET_ENTRANT_ADDED = 'bracket.entrant_added'
    BRACKET_ENTRANT_DROPPED = 'bracket.entrant_dropped'
    BRACKET_ENTRANT_UPDATED = 'bracket.entrant_updated'
    # Per-stage participation, distinct from the tournament-level roster above:
    # who is enrolled in a stage decides who the generated graph contains.
    BRACKET_ENTRY_ADDED = 'bracket.entry_added'
    BRACKET_ENTRY_REMOVED = 'bracket.entry_removed'
    # Retiring an entry mid-stage is a different fact from un-enrolling one while
    # DRAFT: the played results stand and the field shrinks under way.
    BRACKET_ENTRY_RETIRED = 'bracket.entry_retired'
    BRACKET_CANCELLED = 'bracket.cancelled'

    # Webhooks
    WEBHOOK_CREATED = 'webhook.created'
    WEBHOOK_UPDATED = 'webhook.updated'
    WEBHOOK_DELETED = 'webhook.deleted'
    WEBHOOK_SECRET_REGENERATED = 'webhook.secret_regenerated'
    PERSONAL_WEBHOOK_CREATED = 'personal_webhook.created'
    PERSONAL_WEBHOOK_UPDATED = 'personal_webhook.updated'
    PERSONAL_WEBHOOK_DELETED = 'personal_webhook.deleted'
    PERSONAL_WEBHOOK_SECRET_REGENERATED = 'personal_webhook.secret_regenerated'

    # Feature flags. Availability is a platform-level grant (super-admin on
    # /platform, tenant=NULL row, target tenant in details); the enable/disable
    # toggle is a tenant-level action by that community's STAFF (tenant-scoped).
    FEATURE_FLAG_AVAILABILITY_SET = 'feature_flag.availability_set'
    FEATURE_FLAG_ENABLED = 'feature_flag.enabled'
    FEATURE_FLAG_DISABLED = 'feature_flag.disabled'
    # Feature-flag groups (live tiers) — super-admin, platform-level (tenant=NULL).
    # Group CRUD and the per-tenant group assignment.
    FEATURE_GROUP_CREATED = 'feature_group.created'
    FEATURE_GROUP_UPDATED = 'feature_group.updated'
    FEATURE_GROUP_DELETED = 'feature_group.deleted'
    FEATURE_GROUP_ASSIGNED = 'feature_group.assigned'

    # Tenancy / platform (these audit rows carry tenant=NULL — platform-level)
    TENANT_CREATED = 'tenant.created'
    TENANT_UPDATED = 'tenant.updated'
    TENANT_DELETED = 'tenant.deleted'
    TENANT_MEMBER_ADDED = 'tenant.member_added'
    TENANT_MEMBER_REMOVED = 'tenant.member_removed'
    # Admin → Users › Import from Discord: one row for the whole run.
    TENANT_MEMBERS_IMPORTED = 'tenant.members_imported'
    # The self-serve enrollment path. The request is written by someone who is
    # not yet in the tenant, so its audit row is stamped with the *target*
    # tenant rather than the requester's ambient one.
    TENANT_JOIN_REQUESTED = 'tenant.join_requested'
    TENANT_JOIN_APPROVED = 'tenant.join_approved'
    TENANT_JOIN_DENIED = 'tenant.join_denied'
    SUPER_ADMIN_GRANTED = 'platform.super_admin_granted'
    SUPER_ADMIN_REVOKED = 'platform.super_admin_revoked'


def _encode_details(details: Optional[Mapping[str, Any]]) -> Optional[str]:
    if details is None:
        return None
    return json.dumps(dict(details), default=str, sort_keys=True)


class AuditService:
    """Service for audit logging operations."""

    def __init__(self) -> None:
        self.repository = AuditRepository()

    @staticmethod
    async def _ensure_can_read(actor: Optional[User]) -> None:
        """Staff only: the log is the whole community's history, including role
        grants, webhook receiver URLs and who asked for an accommodation.
        ``actor=None`` is an internal caller skipping the check deliberately."""
        if actor is not None and not await AuthService.is_staff(actor):
            raise PermissionError('Only Staff can read the audit log.')

    @staticmethod
    def _query_filters(
        action_contains: Optional[str], area: Optional[str], search: Optional[str],
    ) -> dict[str, Any]:
        """Repository filters for the report's Area and plain-language search.

        ``search`` matches the action key or any action whose plain label
        contains the text, so "stage" finds ``match.stage_assigned`` and
        "reminder" finds ``profile_nudge.dismissed``. An unknown ``area`` key
        matches nothing rather than silently widening to every row.
        """
        filters: dict[str, Any] = {'action_contains': action_contains or None}
        if area:
            found = area_by_key(area)
            filters['action_prefixes'] = found.prefixes if found else ()
        if search and search.strip():
            filters['action_contains'] = search.strip()
            filters['actions_in'] = actions_matching(search)
        return filters

    async def list_logs(
        self,
        *,
        actor: Optional[User] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        user_id: Optional[int] = None,
        action_contains: Optional[str] = None,
        area: Optional[str] = None,
        search: Optional[str] = None,
        event_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        await self._ensure_can_read(actor)
        return await self.repository.list(
            start=start,
            end=end,
            user_id=user_id,
            event_id=event_id,
            limit=limit,
            offset=offset,
            **self._query_filters(action_contains, area, search),
        )

    async def count_logs(
        self,
        *,
        actor: Optional[User] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        user_id: Optional[int] = None,
        action_contains: Optional[str] = None,
        area: Optional[str] = None,
        search: Optional[str] = None,
        event_id: Optional[int] = None,
    ) -> int:
        await self._ensure_can_read(actor)
        return await self.repository.count(
            start=start,
            end=end,
            user_id=user_id,
            event_id=event_id,
            **self._query_filters(action_contains, area, search),
        )

    async def reference_names(self, *, actor: User, details: Sequence[Any]) -> AuditNames:
        """Names for the members, stages and tournaments a page of rows mentions.

        Stages and tournaments resolve within the current community only, so a
        stray id from elsewhere renders as ``Stage #7`` rather than leaking a name.
        """
        await self._ensure_can_read(actor)
        user_ids, stage_ids, tournament_ids = referenced_ids(details)
        users = await UserRepository.get_by_ids(list(user_ids))
        stages = await StageRepository.get_all_as_dict() if stage_ids else {}
        tournaments = await TournamentRepository.get_all_as_dict() if tournament_ids else {}
        return AuditNames(
            users={uid: u.preferred_name for uid, u in users.items()},
            stages={k: v for k, v in stages.items() if k in stage_ids},
            tournaments={k: v for k, v in tournaments.items() if k in tournament_ids},
        )

    async def write_log(
        self,
        actor: User,
        action: str,
        details: Optional[Mapping[str, Any]] = None,
    ) -> AuditLog:
        """Write an audit log entry.

        Args:
            actor: The User performing the action. Required - if no actor is
                available the caller has a bug and should be fixed rather
                than the audit silently dropped.
            action: Namespaced action string (``verb.object``). Use a
                constant from :class:`AuditActions` where one exists.
            details: Optional structured context, JSON-encoded before
                storage. Use plain JSON-serializable values; datetimes and
                other objects fall back to ``str``.

        Raises:
            ValueError: If ``actor`` is None.
        """
        if actor is None:
            raise ValueError("AuditService.write_log requires an actor")

        # Snapshot the actor's identity into details so attribution survives a
        # later user deletion (AuditLog.user is ON DELETE SET NULL). getattr
        # keeps auditing resilient if the actor is a partial object.
        enriched: dict[str, Any] = dict(details) if details else {}
        actor_username = getattr(actor, 'username', None)
        if actor_username is not None:
            enriched.setdefault('actor_username', actor_username)
        actor_discord_id = getattr(actor, 'discord_id', None)
        if actor_discord_id is not None:
            enriched.setdefault('actor_discord_id', str(actor_discord_id))

        # Stamp the ambient tenant so the trail is per-tenant; NULL when the
        # action is platform-level (super-admin tenant CRUD on /platform, which
        # runs with no tenant context). The event is tagged the same way.
        return await AuditLog.create(
            tenant_id=get_current_tenant_id(),
            event_id=get_current_event_id(),
            user=actor,
            action=action,
            details=_encode_details(enriched),
        )

    async def write_and_publish(
        self,
        actor: User,
        action: str,
        details: Optional[Mapping[str, Any]],
        event_type: str,
        *,
        event_extra: Optional[Mapping[str, Any]] = None,
        event_details: Optional[Mapping[str, Any]] = None,
    ) -> AuditLog:
        """Write an audit row, then fire the matching event on the in-process bus.

        Promotes the audit-then-publish pairing that services previously
        hand-rolled into one call. Publishing is synchronous, fire-and-forget, and
        never raises, so a subscriber failure cannot roll back the audit write.

        By default the event carries the same ``details`` and ``actor`` as the
        audit row. The two payloads legitimately diverge, though — the audit trail
        is tenant-internal and records *what a human did*, while the event is an
        **external contract** whose subscribers need routing keys (``user_id``,
        ``tournament_id``) that the audit row already implies from its own
        ``user`` FK and context. Two ways to express that:

        - ``event_extra`` — the common case: ``details`` plus these keys. Prefer
          this, because the shared keys stay written once.
        - ``event_details`` — a wholly independent event payload, for the rare
          case where the audit row carries detail the contract should *not*
          expose (an internal ``guild_id``, a verbose diff).

        Passing both is a caller bug: ``event_extra`` would silently do nothing.
        """
        if event_extra is not None and event_details is not None:
            raise ValueError(
                "write_and_publish takes event_extra or event_details, not both"
            )
        log = await self.write_log(actor, action, details)
        if event_details is not None:
            payload = dict(event_details)
        else:
            payload = dict(details) if details else {}
            if event_extra:
                payload.update(event_extra)
        event_bus.publish(Event.create(event_type, payload, actor))
        return log
