"""Drift guard between audited actions and webhook-deliverable events.

``EventType`` names mirror ``AuditActions`` verbatim (identical ``object.verb``
strings), so every audited state-change is a *candidate* webhook event. A new
audited action should therefore either gain a matching ``EventType`` (making it
deliverable to webhook subscribers) or be consciously recorded below as
intentionally event-less. This test fails when a new ``AuditAction`` is added
without that decision — turning "did we forget to emit an event?" into a red
test instead of a code-review catch. (This is exactly how ``match.stage_assigned``
sat audited-but-not-emitted for a while.)

To resolve a failure:
- add the mirror member to ``EventType`` + ``EventType.ALL`` to emit it, **or**
- add the action to ``_EVENT_CANDIDATES`` (plausible future event) or
  ``_EXCLUDED_BY_DESIGN`` (never webhook it) with the reasoning.
"""

from application.events import EventType
from application.services.audit_service import AuditActions


def _audit_action_values() -> set[str]:
    return {
        value
        for name, value in vars(AuditActions).items()
        if not name.startswith('_') and isinstance(value, str)
    }


_EVENT_CANDIDATES = frozenset({
    AuditActions.MATCH_REQUESTED,
    AuditActions.MATCH_SET_EXTENDED,
    AuditActions.TOURNAMENT_CREATED,
    AuditActions.TOURNAMENT_UPDATED,
    AuditActions.TOURNAMENT_DELETED,
    AuditActions.BRACKET_UPDATED,
    AuditActions.BRACKET_DELETED,
    AuditActions.BRACKET_ENTRY_ADDED,
    AuditActions.BRACKET_ENTRY_REMOVED,
    AuditActions.STATION_CREATED,
    AuditActions.STATION_UPDATED,
    AuditActions.STATION_DELETED,
    AuditActions.STAGE_CREATED,
    AuditActions.STAGE_UPDATED,
    AuditActions.STAGE_DELETED,
    AuditActions.STREAM_LAYOUT_CREATED,
    AuditActions.STREAM_LAYOUT_UPDATED,
    AuditActions.STREAM_LAYOUT_DELETED,
    AuditActions.STAGE_OVERLAY_UPDATED,
    AuditActions.STAGE_OVERLAY_PINNED,
    AuditActions.STAGE_OVERLAY_UNPINNED,
    AuditActions.STAGE_OVERLAY_LINK_RENEWED,
    AuditActions.TRIFORCE_TEXT_SUBMITTED,
    AuditActions.TRIFORCE_TEXT_APPROVED,
    AuditActions.TRIFORCE_TEXT_REJECTED,
    AuditActions.EQUIPMENT_CHECKED_OUT,
    AuditActions.EQUIPMENT_CHECKED_IN,
})

_EXCLUDED_BY_DESIGN = frozenset({
    # Never emit: a lone opt-in must reach nobody, and a webhook subscriber is an outside listener.
    AuditActions.MATCH_HARD_PRESET_OPTED_IN,
    AuditActions.MATCH_HARD_PRESET_WITHDRAWN,
    AuditActions.MATCH_WATCHER_ADDED,
    AuditActions.MATCH_WATCHER_REMOVED,
    AuditActions.TOURNAMENT_ADMIN_GRANTED,
    AuditActions.TOURNAMENT_ADMIN_REVOKED,
    AuditActions.TOURNAMENT_CREW_COORDINATOR_GRANTED,
    AuditActions.TOURNAMENT_CREW_COORDINATOR_REVOKED,
    AuditActions.USER_ROLE_GRANTED,
    AuditActions.USER_ROLE_REVOKED,
    AuditActions.USER_CREATED,
    AuditActions.USER_PROVISIONED,
    AuditActions.USER_PROFILE_UPDATED,
    AuditActions.USER_SELF_PROFILE_UPDATED,
    AuditActions.USER_ACTIVATION_CHANGED,
    AuditActions.USER_TOURNAMENT_ENROLLMENT_UPDATED,
    AuditActions.USER_PASSWORD_SET,
    AuditActions.USER_PASSWORD_REMOVED,
    AuditActions.USER_PASSWORD_SIGNED_IN,
    AuditActions.DISCORD_ROLE_MAPPING_ADDED,
    AuditActions.DISCORD_ROLE_MAPPING_REMOVED,
    AuditActions.DISCORD_ROLE_RULE_SET,
    AuditActions.DISCORD_ROLE_RULE_CLEARED,
    AuditActions.DISCORD_ROLE_RULES_RESET,
    AuditActions.ROLE_DISCORD_SYNC_GRANTED,
    AuditActions.ROLE_DISCORD_SYNC_REVOKED,
    AuditActions.ROLE_DISCORD_SYNC_BULK,
    AuditActions.VOLUNTEER_ROLE_MAPPING_ADDED,
    AuditActions.VOLUNTEER_ROLE_MAPPING_REMOVED,
    AuditActions.ROLE_VOLUNTEER_SYNC_GRANTED,
    AuditActions.ROLE_VOLUNTEER_SYNC_REVOKED,
    AuditActions.SYSTEM_CONFIG_UPDATED,
    AuditActions.THEME_UPDATED,
    AuditActions.THEME_LOGO_UPDATED,
    AuditActions.THEME_LOGO_REMOVED,
    AuditActions.TIMEZONE_UPDATED,
    AuditActions.USER_TIMEZONE_UPDATED,
    AuditActions.USER_HOUR_CYCLE_UPDATED,
    AuditActions.PROFILE_NUDGES_UPDATED,
    AuditActions.PROFILE_NUDGE_DISMISSED,
    AuditActions.TRIFORCE_TEXT_DELETED,
    AuditActions.PRESET_CREATED,
    AuditActions.PRESET_UPDATED,
    AuditActions.PRESET_DELETED,
    AuditActions.PRESET_IMPORTED,
    AuditActions.PRESET_TEST_ROLLED,
    # Secrets — leaking these to arbitrary receivers is a risk.
    AuditActions.APITOKEN_CREATED,
    AuditActions.APITOKEN_REVOKED,
    AuditActions.OAUTH_APP_CREATED,
    AuditActions.OAUTH_APP_UPDATED,
    AuditActions.OAUTH_APP_SECRET_ROTATED,
    AuditActions.OAUTH_APP_DELETED,
    AuditActions.OAUTH_APP_DISABLED,
    AuditActions.OAUTH_APP_ENABLED,
    AuditActions.OAUTH_APP_BLOCKED,
    AuditActions.OAUTH_APP_UNBLOCKED,
    AuditActions.OAUTH_GRANT_REFRESH_REUSED,
    # Room tokens: a credential, never broadcast.
    AuditActions.ROOM_TOKEN_CREATED,
    AuditActions.ROOM_TOKEN_REVOKED,
    # Randomizer credentials and Matcherino logins: never broadcast to webhook receivers.
    AuditActions.RANDOMIZER_CREDENTIAL_SET,
    AuditActions.RANDOMIZER_CREDENTIAL_CLEARED,
    AuditActions.MATCHERINO_LOGIN_SET,
    AuditActions.MATCHERINO_LOGIN_CLEARED,
    AuditActions.MATCHERINO_LOGIN_ALERTS_UPDATED,
    # Webhook meta: a webhook about webhook config (incl. secret regen) is a footgun.
    AuditActions.WEBHOOK_CREATED,
    AuditActions.WEBHOOK_UPDATED,
    AuditActions.WEBHOOK_DELETED,
    AuditActions.WEBHOOK_SECRET_REGENERATED,
    AuditActions.PERSONAL_WEBHOOK_CREATED,
    AuditActions.PERSONAL_WEBHOOK_UPDATED,
    AuditActions.PERSONAL_WEBHOOK_DELETED,
    AuditActions.PERSONAL_WEBHOOK_SECRET_REGENERATED,
    AuditActions.FEEDBACK_SUBMITTED,
    AuditActions.FEEDBACK_REVIEWED,
    AuditActions.FEEDBACK_REOPENED,
    AuditActions.ACCOMMODATION_REQUESTED,
    AuditActions.ACCOMMODATION_UPDATED,
    AuditActions.ACCOMMODATION_WITHDRAWN,
    AuditActions.ACCOMMODATION_STATUS_CHANGED,
    AuditActions.ACCOMMODATION_NOTES_UPDATED,
    AuditActions.ACCOMMODATION_CHANGE_REVIEWED,
    # Emergency contacts: never broadcast.
    AuditActions.EMERGENCY_CONTACT_SAVED,
    AuditActions.EMERGENCY_CONTACT_REMOVED,
    AuditActions.EMERGENCY_CONTACT_VIEWED,
    # PII export: audited, never announced to webhook receivers.
    AuditActions.VOLUNTEER_DATA_EXPORTED,
    # Same for tournament exports (names and Discord ids).
    AuditActions.TOURNAMENT_DATA_EXPORTED,
    AuditActions.EQUIPMENT_CREATED,
    AuditActions.EQUIPMENT_UPDATED,
    AuditActions.EQUIPMENT_DELETED,
    AuditActions.CHECK_IN_EVENT_CREATED,
    AuditActions.CHECK_IN_EVENT_UPDATED,
    AuditActions.CHECK_IN_EVENT_DELETED,
    AuditActions.CHECK_IN_TIER_UPDATED,
    AuditActions.PLAYER_AVAILABILITY_UPDATED,
    AuditActions.CHALLONGE_CONNECTED,
    AuditActions.CHALLONGE_DISCONNECTED,
    AuditActions.CHALLONGE_COMMUNITY_REMOVED,
    AuditActions.CHALLONGE_PLAYER_LINKED,
    AuditActions.CHALLONGE_PLAYER_UNLINKED,
    AuditActions.CHALLONGE_PLAYER_USERNAME_UPDATED,
    AuditActions.CHALLONGE_TOURNAMENT_LINKED,
    AuditActions.CHALLONGE_TOURNAMENT_UNLINKED,
    AuditActions.CHALLONGE_BRACKET_SYNCED,
    AuditActions.CHALLONGE_RESULT_PUSHED,
    AuditActions.CHALLONGE_RESULT_SKIPPED,
    AuditActions.CHALLONGE_RESULT_PUSH_FAILED,
    AuditActions.CHALLONGE_AUTO_SYNC_UPDATED,
    AuditActions.CHALLONGE_WEBHOOK_SYNCED,
    AuditActions.CHALLONGE_PARTICIPANT_ASSIGNED,
    AuditActions.CHALLONGE_PARTICIPANT_UNASSIGNED,
    AuditActions.CHALLONGE_STAGE_ADVANCED,
    AuditActions.CHALLONGE_MATCH_LINKED,
    AuditActions.CHALLONGE_PARTICIPANT_SIGNED_UP,
    AuditActions.CHALLONGE_PARTICIPANT_WITHDRAWN,
    AuditActions.STARTGG_LINKED,
    AuditActions.STARTGG_UNLINKED,
    AuditActions.STARTGG_EVENT_LINKED,
    AuditActions.STARTGG_EVENT_UNLINKED,
    AuditActions.STARTGG_EVENT_SYNCED,
    AuditActions.STARTGG_BOOKABLE_PHASE_SET,
    AuditActions.STARTGG_ENTRANT_ASSIGNED,
    AuditActions.STARTGG_ENTRANT_UNASSIGNED,
    AuditActions.STARTGG_SUGGESTION_DISMISSED,
    AuditActions.STARTGG_REPORTING_SET,
    AuditActions.STARTGG_RESULT_REPORTED,
    AuditActions.STARTGG_SET_LINKED,
    AuditActions.STARTGG_CONNECTED,
    AuditActions.STARTGG_DISCONNECTED,
    AuditActions.TWITCH_LINKED,
    AuditActions.TWITCH_UNLINKED,
    AuditActions.RACETIME_LINKED,
    AuditActions.RACETIME_UNLINKED,
    # Racetime bot administration: platform-level (tenant=NULL) super-admin CRUD
    # and tenant grants carrying OAuth secrets — sensitive, and a webhook is
    # tenant-scoped so it would reach zero subscribers. Room profiles are
    # tenant-internal room-setting authoring, no external subscriber interest.
    AuditActions.RACETIME_BOT_CREATED,
    AuditActions.RACETIME_BOT_UPDATED,
    AuditActions.RACETIME_BOT_DELETED,
    AuditActions.RACETIME_BOT_GRANTED,
    AuditActions.RACETIME_BOT_REVOKED,
    AuditActions.RACE_ROOM_PROFILE_CREATED,
    AuditActions.RACE_ROOM_PROFILE_UPDATED,
    AuditActions.RACE_ROOM_PROFILE_DELETED,
    # Runtime log levels: platform-level (tenant=NULL) diagnostics, so a
    # tenant-scoped webhook would reach nobody.
    AuditActions.LOG_LEVEL_SET,
    AuditActions.LOG_LEVEL_CLEARED,
    AuditActions.BROWSER_MONITORING_UPDATED,
    AuditActions.BROWSER_IGNORE_RULE_ADDED,
    AuditActions.BROWSER_IGNORE_RULE_REMOVED,
    # Racetime bot runtime health: platform-level (tenant=NULL) connection-state
    # transitions written by the connection loop. A webhook is tenant-scoped, so
    # a platform-level health event would reach zero subscribers — kept audit-only.
    AuditActions.RACETIME_BOT_CONNECTED,
    AuditActions.RACETIME_BOT_DISCONNECTED,
    AuditActions.RACETIME_BOT_ERROR,
    AuditActions.RACETIME_BOT_RESTARTED,
    AuditActions.SG_EVENT_LINK_CREATED,
    AuditActions.SG_EVENT_LINK_UPDATED,
    AuditActions.SG_EVENT_LINK_DELETED,
    AuditActions.SG_SYNC_COMPLETED,
    AuditActions.SG_SYNC_FAILED,
    AuditActions.SG_EPISODE_SKIPPED,
    AuditActions.SG_PLACEHOLDER_CREATED,
    AuditActions.SG_PLACEHOLDER_UPGRADED,
    AuditActions.VOLUNTEER_POSITION_CREATED,
    AuditActions.VOLUNTEER_POSITION_UPDATED,
    AuditActions.VOLUNTEER_POSITION_DELETED,
    AuditActions.VOLUNTEER_SHIFT_CREATED,
    AuditActions.VOLUNTEER_SHIFT_UPDATED,
    AuditActions.VOLUNTEER_SHIFT_DELETED,
    AuditActions.VOLUNTEER_AVAILABILITY_UPDATED,
    AuditActions.VOLUNTEER_DRAFT_GENERATED,
    AuditActions.VOLUNTEER_DRAFT_CLEARED,
    AuditActions.VOLUNTEER_DRAFT_PUBLISHED,
    AuditActions.VOLUNTEER_SHIFTS_RESET,
    AuditActions.VOLUNTEER_QUALIFICATIONS_UPDATED,
    AuditActions.WEB_PUSH_SUBSCRIBED,
    AuditActions.WEB_PUSH_UNSUBSCRIBED,
    AuditActions.WEB_PUSH_ROTATED,
    AuditActions.NOTIFICATION_EMAIL_CONFIRMED,
    AuditActions.NOTIFICATION_EMAIL_REMOVED,
    AuditActions.NOTIFICATION_TELEGRAM_LINKED,
    AuditActions.NOTIFICATION_TELEGRAM_UNLINKED,
    AuditActions.NOTIFICATION_SETTING_CHANGED,
    AuditActions.NOTIFICATION_SETTINGS_RESET,
    AuditActions.DISCORD_SERVER_LINKED,
    AuditActions.DISCORD_SERVER_UNLINKED,
    AuditActions.DISCORD_LOGIN_PANEL_POSTED,
    AuditActions.DISCORD_EVENT_SYNC_COMPLETED,
    AuditActions.DISCORD_EVENT_SYNC_FAILED,
    AuditActions.DISCORD_EVENT_SETTINGS_UPDATED,
    # Never emit permalink authoring: a permalink is the seed, so publishing it hands out the pool.
    AuditActions.ASYNC_QUALIFIER_CREATED,
    AuditActions.ASYNC_QUALIFIER_UPDATED,
    AuditActions.ASYNC_QUALIFIER_DELETED,
    AuditActions.ASYNC_QUALIFIER_ADMIN_GRANTED,
    AuditActions.ASYNC_QUALIFIER_ADMIN_REVOKED,
    AuditActions.ASYNC_QUALIFIER_POOL_CREATED,
    AuditActions.ASYNC_QUALIFIER_POOL_UPDATED,
    AuditActions.ASYNC_QUALIFIER_POOL_DELETED,
    AuditActions.ASYNC_QUALIFIER_PERMALINK_ADDED,
    AuditActions.ASYNC_QUALIFIER_PERMALINK_UPDATED,
    AuditActions.ASYNC_QUALIFIER_PERMALINK_DELETED,
    AuditActions.ASYNC_QUALIFIER_RUN_STARTED,
    AuditActions.ASYNC_QUALIFIER_REATTEMPT_GRANTED,
    AuditActions.ASYNC_QUALIFIER_RUN_REVIEW_OVERRIDDEN,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_CREATED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_OPENED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_CANCELLED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_PERMALINK_ASSIGNED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_RECORDED_MANUALLY,
    # Feature-flag administration: the super-admin availability grant is
    # platform-level (tenant=NULL, so a tenant-scoped webhook reaches zero
    # subscribers) and the tenant enable/disable toggle is internal
    # feature-administration config — not a tournament domain event.
    AuditActions.FEATURE_FLAG_AVAILABILITY_SET,
    AuditActions.FEATURE_FLAG_ENABLED,
    AuditActions.FEATURE_FLAG_DISABLED,
    # Feature-flag group (tier) CRUD + per-tenant assignment: super-admin,
    # platform-level (tenant=NULL) — a tenant-scoped webhook reaches zero
    # subscribers, and this is feature-administration config, not a domain event.
    AuditActions.FEATURE_GROUP_CREATED,
    AuditActions.FEATURE_GROUP_UPDATED,
    AuditActions.FEATURE_GROUP_DELETED,
    AuditActions.FEATURE_GROUP_ASSIGNED,
    # Tenancy / platform administration: super-admin-only, platform-level
    # (tenant=NULL) rows. Webhooks are tenant-scoped, so a platform event would
    # reach zero subscribers — and tenant CRUD / role grants are sensitive.
    # Membership is the exception and is emitted: it is written *inside* a
    # tenant by that tenant's own staff, and who belongs to a community is
    # exactly what an external roster subscriber needs to mirror.
    AuditActions.TENANT_CREATED,
    AuditActions.TENANT_UPDATED,
    AuditActions.TENANT_DELETED,
    AuditActions.SUPER_ADMIN_GRANTED,
    AuditActions.SUPER_ADMIN_REVOKED,
    AuditActions.CREW_MOVED_TO_BOOKING,
    AuditActions.CREW_PARTNER_INVITED,
    AuditActions.CREW_PARTNER_INVITE_ANSWERED,
    AuditActions.CREW_PARTNER_INVITE_CANCELLED,
    AuditActions.SURVEY_CREATED,
    AuditActions.SURVEY_UPDATED,
    AuditActions.SURVEY_DELETED,
    AuditActions.SURVEY_REMINDED,
    AuditActions.SURVEY_QUESTIONS_SUGGESTED,
    AuditActions.SURVEY_COMMENT_TAG_CREATED,
    AuditActions.SURVEY_COMMENT_TAG_DELETED,
    AuditActions.AI_PROVIDER_SET,
    AuditActions.AI_PROVIDER_CLEARED,
})

_EVENTLESS_AUDIT_ACTIONS = _EVENT_CANDIDATES | _EXCLUDED_BY_DESIGN


def test_no_untriaged_audit_actions():
    """Every audited action is emitted OR explicitly recorded as event-less."""
    untriaged = _audit_action_values() - set(EventType.ALL) - _EVENTLESS_AUDIT_ACTIONS
    assert not untriaged, (
        "Audited actions with neither an EventType nor an eventless-ledger entry: "
        f"{sorted(untriaged)}. Emit them on the event bus (add to EventType), or "
        "add them to _EVENT_CANDIDATES / _EXCLUDED_BY_DESIGN with a rationale."
    )


def test_eventless_ledger_has_no_stale_entries():
    """The ledger can't list something that is now emitted or no longer audited."""
    audited = _audit_action_values()
    now_emitted = _EVENTLESS_AUDIT_ACTIONS & set(EventType.ALL)
    assert not now_emitted, (
        f"Ledger entries that are now emitted events: {sorted(now_emitted)}. "
        "Remove them from the eventless ledger."
    )
    unknown = _EVENTLESS_AUDIT_ACTIONS - audited
    assert not unknown, (
        f"Ledger entries that are no longer real audit actions: {sorted(unknown)}."
    )
