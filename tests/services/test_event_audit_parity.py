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


# Audited today, plausibly worth emitting later, but no consumer needs it yet.
_EVENT_CANDIDATES = frozenset({
    AuditActions.MATCH_REQUESTED,
    # Games 2 and up each emit MATCH_CREATED; the link back to game 1 has no
    # subscriber yet.
    AuditActions.MATCH_SET_EXTENDED,
    AuditActions.TOURNAMENT_CREATED,
    AuditActions.TOURNAMENT_UPDATED,
    AuditActions.TOURNAMENT_DELETED,
    # Bracket *definition* authoring, the peer of the tournament CRUD above: both
    # are DRAFT-only (name/stage/config, round chrome, reseeding, delete), so
    # nothing competitive has happened yet for a subscriber to react to. The
    # lifecycle events a bracket does emit start at BRACKET_STARTED.
    AuditActions.BRACKET_UPDATED,
    AuditActions.BRACKET_DELETED,
    # Per-stage enrollment, by the same reasoning: ``enroll`` is DRAFT-only, so a
    # subscriber learns the final field from BRACKET_STARTED instead. (The
    # tournament-level roster *does* emit BRACKET_ENTRANT_ADDED — that one is not
    # gated on DRAFT and survives the stage.)
    AuditActions.BRACKET_ENTRY_ADDED,
    # Un-enrolling is DRAFT-only by the same reasoning as enrolling: the field a
    # subscriber cares about is the one BRACKET_STARTED announces. (Retiring an
    # entry mid-stage *is* emitted — it changes a running field.)
    AuditActions.BRACKET_ENTRY_REMOVED,
    # The venue's station pool — the same reasoning as the stages below:
    # community-owned venue configuration, with no subscriber interest in the
    # seats themselves (the assignment of a player to one already emits
    # MATCH_STATIONS_ASSIGNED).
    AuditActions.STATION_CREATED,
    AuditActions.STATION_UPDATED,
    AuditActions.STATION_DELETED,
    AuditActions.STAGE_CREATED,
    AuditActions.STAGE_UPDATED,
    AuditActions.STAGE_DELETED,
    # Broadcast overlay authoring and pins: production plumbing for the stream,
    # read by the overlay page's own feed rather than by any outside listener.
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

# Deliberately never webhooked: security-sensitive, internal plumbing, or noise.
_EXCLUDED_BY_DESIGN = frozenset({
    # One player's private opt-in to a match's harder preset. Excluded because
    # the feature is the secrecy: a lone opt-in must reach nobody, and a webhook
    # subscriber is an arbitrary outside listener. The unanimous result *is*
    # published (MATCH_HARD_PRESET_AGREED), which is the point at which the
    # players themselves are told.
    AuditActions.MATCH_HARD_PRESET_OPTED_IN,
    AuditActions.MATCH_HARD_PRESET_WITHDRAWN,
    # Personal watch toggles — high volume, no domain interest.
    AuditActions.MATCH_WATCHER_ADDED,
    AuditActions.MATCH_WATCHER_REMOVED,
    # Permission/role administration.
    AuditActions.TOURNAMENT_ADMIN_GRANTED,
    AuditActions.TOURNAMENT_ADMIN_REVOKED,
    AuditActions.TOURNAMENT_CREW_COORDINATOR_GRANTED,
    AuditActions.TOURNAMENT_CREW_COORDINATOR_REVOKED,
    AuditActions.USER_ROLE_GRANTED,
    AuditActions.USER_ROLE_REVOKED,
    # Account / PII internals.
    AuditActions.USER_CREATED,
    AuditActions.USER_PROVISIONED,
    AuditActions.USER_PROFILE_UPDATED,
    AuditActions.USER_SELF_PROFILE_UPDATED,
    AuditActions.USER_ACTIVATION_CHANGED,
    AuditActions.USER_TOURNAMENT_ENROLLMENT_UPDATED,
    AuditActions.USER_PASSWORD_SET,
    AuditActions.USER_PASSWORD_REMOVED,
    AuditActions.USER_PASSWORD_SIGNED_IN,
    # Discord role mapping / sync plumbing.
    AuditActions.DISCORD_ROLE_MAPPING_ADDED,
    AuditActions.DISCORD_ROLE_MAPPING_REMOVED,
    AuditActions.DISCORD_ROLE_RULE_SET,
    AuditActions.DISCORD_ROLE_RULE_CLEARED,
    AuditActions.DISCORD_ROLE_RULES_RESET,
    AuditActions.ROLE_DISCORD_SYNC_GRANTED,
    AuditActions.ROLE_DISCORD_SYNC_REVOKED,
    AuditActions.ROLE_DISCORD_SYNC_BULK,
    # Volunteer-position role mapping / sync: the same plumbing, other source.
    AuditActions.VOLUNTEER_ROLE_MAPPING_ADDED,
    AuditActions.VOLUNTEER_ROLE_MAPPING_REMOVED,
    AuditActions.ROLE_VOLUNTEER_SYNC_GRANTED,
    AuditActions.ROLE_VOLUNTEER_SYNC_REVOKED,
    # System configuration.
    AuditActions.SYSTEM_CONFIG_UPDATED,
    # Per-tenant brand palette — tenant-internal appearance setting, no external
    # subscriber interest. The logo rides the same reasoning.
    AuditActions.THEME_UPDATED,
    AuditActions.THEME_LOGO_UPDATED,
    AuditActions.THEME_LOGO_REMOVED,
    # Display-timezone policy, and a member's own timezone preference. Both are
    # presentation settings: they change which clock a time is *rendered* on and
    # never the stored instant, so a webhook receiver — which is served UTC —
    # has nothing to react to. The per-user one is also PII-adjacent (it
    # discloses roughly where someone is), which is the same reasoning that
    # excludes the account internals above.
    AuditActions.TIMEZONE_UPDATED,
    AuditActions.USER_TIMEZONE_UPDATED,
    AuditActions.USER_HOUR_CYCLE_UPDATED,
    # Profile reminders — the rules are tenant-internal settings, and a member
    # dismissing one only hides a banner on their own screen.
    AuditActions.PROFILE_NUDGES_UPDATED,
    AuditActions.PROFILE_NUDGE_DISMISSED,
    AuditActions.TRIFORCE_TEXT_DELETED,
    # Seed-preset catalog CRUD — tenant-internal settings authoring, no external
    # subscriber interest (the seed roll itself already emits MATCH_SEED_ROLLED).
    AuditActions.PRESET_CREATED,
    AuditActions.PRESET_UPDATED,
    AuditActions.PRESET_DELETED,
    AuditActions.PRESET_IMPORTED,
    # A diagnostic roll by staff, not a match seed: nothing a subscriber acts on.
    AuditActions.PRESET_TEST_ROLLED,
    # Secrets — leaking these to arbitrary receivers is a risk.
    AuditActions.APITOKEN_CREATED,
    AuditActions.APITOKEN_REVOKED,
    # Third-party app registration is global (no community), so no tenant's
    # webhook could receive it. Refresh reuse already publishes
    # OAUTH_GRANT_REVOKED; its own audit row only records why.
    AuditActions.OAUTH_APP_CREATED,
    AuditActions.OAUTH_APP_UPDATED,
    AuditActions.OAUTH_APP_SECRET_ROTATED,
    AuditActions.OAUTH_APP_DELETED,
    AuditActions.OAUTH_APP_DISABLED,
    AuditActions.OAUTH_APP_ENABLED,
    # A community's block is staff housekeeping; the grants it ends are
    # revoked in bulk and already invisible to the app.
    AuditActions.OAUTH_APP_BLOCKED,
    AuditActions.OAUTH_APP_UNBLOCKED,
    AuditActions.OAUTH_GRANT_REFRESH_REUSED,
    # Room tokens, for the same reason: the credential a venue machine holds is
    # a security fact, not a domain one.
    AuditActions.ROOM_TOKEN_CREATED,
    AuditActions.ROOM_TOKEN_REVOKED,
    # Randomizer credential and Matcherino login rotation — same reasoning: a
    # community's credential activity is not something to broadcast to
    # arbitrary webhook receivers.
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
    # A member's own webhook config is theirs, not a community event.
    AuditActions.PERSONAL_WEBHOOK_CREATED,
    AuditActions.PERSONAL_WEBHOOK_UPDATED,
    AuditActions.PERSONAL_WEBHOOK_DELETED,
    AuditActions.PERSONAL_WEBHOOK_SECRET_REGENERATED,
    # In-app feedback triage. Reopening is the reverse of reviewing and stays
    # in the same bucket — the submitter learns the state from their own list on
    # the profile, not from a broadcast.
    AuditActions.FEEDBACK_SUBMITTED,
    AuditActions.FEEDBACK_REVIEWED,
    AuditActions.FEEDBACK_REOPENED,
    # ADA accommodation requests: personal by nature, so none of it is
    # broadcast. The requester sees their status on their own profile.
    AuditActions.ACCOMMODATION_REQUESTED,
    AuditActions.ACCOMMODATION_UPDATED,
    AuditActions.ACCOMMODATION_WITHDRAWN,
    AuditActions.ACCOMMODATION_STATUS_CHANGED,
    AuditActions.ACCOMMODATION_NOTES_UPDATED,
    AuditActions.ACCOMMODATION_CHANGE_REVIEWED,
    # Emergency contacts: a phone number for a member's family is the last
    # thing to broadcast, and a staff look is audited, not announced.
    AuditActions.EMERGENCY_CONTACT_SAVED,
    AuditActions.EMERGENCY_CONTACT_REMOVED,
    AuditActions.EMERGENCY_CONTACT_VIEWED,
    # Bulk copy of volunteer availability and notes out of the app. Audited so
    # the tenant can see who took a copy; deliberately not webhooked, since
    # announcing a PII export to arbitrary receivers is the wrong direction.
    AuditActions.VOLUNTEER_DATA_EXPORTED,
    # Same reasoning for tournament exports: names and Discord ids leaving the
    # app are audited for the community, never announced to receivers.
    AuditActions.TOURNAMENT_DATA_EXPORTED,
    # Equipment catalog CRUD (checkout/checkin are candidates above).
    AuditActions.EQUIPMENT_CREATED,
    AuditActions.EQUIPMENT_UPDATED,
    AuditActions.EQUIPMENT_DELETED,
    # Check-in event setup (and a badge type's lanyard) is tenant-internal
    # config; the desk's actions on people (check-in, link, walk-up) and
    # syncs are published.
    AuditActions.CHECK_IN_EVENT_CREATED,
    AuditActions.CHECK_IN_EVENT_UPDATED,
    AuditActions.CHECK_IN_EVENT_DELETED,
    AuditActions.CHECK_IN_TIER_UPDATED,
    # Player availability edits.
    AuditActions.PLAYER_AVAILABILITY_UPDATED,
    # Challonge integration side-effects (result_pushed mirrors match.result_recorded).
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
    # A player's own Challonge sign-up/withdrawal publishes tournament.enrolled /
    # tournament.withdrawn, the roster events every other enrolment path shares.
    AuditActions.CHALLONGE_PARTICIPANT_SIGNED_UP,
    AuditActions.CHALLONGE_PARTICIPANT_WITHDRAWN,
    # start.gg integration side-effects, same reasoning as Challonge above
    # (result_reported mirrors match.result_recorded).
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
    # Twitch account linking.
    AuditActions.TWITCH_LINKED,
    AuditActions.TWITCH_UNLINKED,
    # Racetime.gg account linking (verified identity link, no domain webhook).
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
    # Browser-side Sentry settings: platform-level (tenant=NULL), same reasoning.
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
    # SpeedGaming ETL: the domain-relevant outcomes (episode imported / cancelled,
    # match auto-finished) DO emit events (see EventType.SG_*). The rest are
    # audit-only: event-link CRUD is tenant-internal sync config, the per-run
    # sync summary/failure and per-episode skip are worker plumbing, and the
    # placeholder-user create/upgrade rows are identity bookkeeping — none carry
    # domain interest a webhook subscriber would act on.
    AuditActions.SG_EVENT_LINK_CREATED,
    AuditActions.SG_EVENT_LINK_UPDATED,
    AuditActions.SG_EVENT_LINK_DELETED,
    AuditActions.SG_SYNC_COMPLETED,
    AuditActions.SG_SYNC_FAILED,
    AuditActions.SG_EPISODE_SKIPPED,
    AuditActions.SG_PLACEHOLDER_CREATED,
    AuditActions.SG_PLACEHOLDER_UPGRADED,
    # Volunteer scheduling config and bulk draft churn.
    AuditActions.VOLUNTEER_POSITION_CREATED,
    AuditActions.VOLUNTEER_POSITION_UPDATED,
    AuditActions.VOLUNTEER_POSITION_DELETED,
    AuditActions.VOLUNTEER_SHIFT_CREATED,
    AuditActions.VOLUNTEER_SHIFT_UPDATED,
    AuditActions.VOLUNTEER_SHIFT_DELETED,
    AuditActions.VOLUNTEER_AVAILABILITY_UPDATED,
    AuditActions.VOLUNTEER_DRAFT_GENERATED,
    AuditActions.VOLUNTEER_DRAFT_CLEARED,
    # The coordinator's bulk decision; the per-volunteer commitments it makes are
    # each emitted as VOLUNTEER_ASSIGNED by confirm_assignment.
    AuditActions.VOLUNTEER_DRAFT_PUBLISHED,
    AuditActions.VOLUNTEER_SHIFTS_RESET,
    AuditActions.VOLUNTEER_QUALIFICATIONS_UPDATED,
    # Per-device push subscription state. The rotation row is the same class of
    # thing, and even less interesting: the push service moved the device's
    # endpoint and nothing about the user's intent changed.
    AuditActions.WEB_PUSH_SUBSCRIBED,
    AuditActions.WEB_PUSH_UNSUBSCRIBED,
    AuditActions.WEB_PUSH_ROTATED,
    # A person's own email / Telegram notification channel: personal contact
    # data, never something a community webhook should carry.
    AuditActions.NOTIFICATION_EMAIL_CONFIRMED,
    AuditActions.NOTIFICATION_EMAIL_REMOVED,
    AuditActions.NOTIFICATION_TELEGRAM_LINKED,
    AuditActions.NOTIFICATION_TELEGRAM_UNLINKED,
    # Which DMs a community sends and whether they ping (Admin → Notifications):
    # tenant-internal delivery config, not a domain event a subscriber acts on.
    AuditActions.NOTIFICATION_SETTING_CHANGED,
    AuditActions.NOTIFICATION_SETTINGS_RESET,
    # Tenant ↔ Discord-server connection: admin infrastructure configuration,
    # not a tournament domain event a webhook subscriber would act on.
    AuditActions.DISCORD_SERVER_LINKED,
    AuditActions.DISCORD_SERVER_UNLINKED,
    # Posting the bot's login panel: Discord-side admin setup, same reasoning.
    AuditActions.DISCORD_LOGIN_PANEL_POSTED,
    # Discord Scheduled Events mirror (PR 8): the per-event create/update/cancel
    # rows DO emit events (see EventType.DISCORD_EVENT_*). The rest are audit-only:
    # the per-run reconcile summary/failure is worker plumbing and the settings
    # edit is tenant-internal sync config — no external subscriber interest.
    AuditActions.DISCORD_EVENT_SYNC_COMPLETED,
    AuditActions.DISCORD_EVENT_SYNC_FAILED,
    AuditActions.DISCORD_EVENT_SETTINGS_UPDATED,
    # Async Qualifiers (PR 9): every audited action that moves a *standing* emits an
    # event (see EventType.ASYNC_QUALIFIER_*), and the window's own crossings are
    # published without an audit row at all — nobody performs an opening, so there is
    # no actor to record. What stays audit-only is below.
    #
    # Qualifier and pool/permalink authoring, plus the per-qualifier admin grants:
    # tenant-internal config. The permalinks are also the reason this is a decision
    # rather than an oversight — a permalink *is* the seed, revealed to a runner only
    # when their slot is spent, so shipping its URL to every webhook subscriber the
    # moment it is added would hand out the pool.
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
    # Drawing a seed promises nothing — the run may finish, expire or be voided, and
    # each of those is its own event. A draw is the only run action with no standing
    # to move, which is why it is the only one still here.
    AuditActions.ASYNC_QUALIFIER_RUN_STARTED,
    # A reviewer voiding a runner's run publishes ASYNC_QUALIFIER_RUN_REATTEMPTED
    # with ``granted: True``: to a subscriber it is the same external fact as the
    # runner spending their own allowance — a run voided, a slot reopened. The audit
    # keeps the two apart because only one of them costs an allowance.
    AuditActions.ASYNC_QUALIFIER_REATTEMPT_GRANTED,
    # Overturning a settled verdict. Not eventless in the usual sense: it publishes
    # ASYNC_QUALIFIER_RUN_REVIEWED with ``override: True``, because to a subscriber
    # this *is* a review — the run's status changed and the same payload describes
    # it. Only the audit trail needs the distinction, since "who overturned this,
    # and from what" is the question an appeal asks. A second EventType would split
    # one external fact across two names subscribers must both match on.
    AuditActions.ASYNC_QUALIFIER_RUN_REVIEW_OVERRIDDEN,
    # Async Qualifier live races (PR 10): recording the finished race emits
    # ASYNC_QUALIFIER_LIVE_RACE_RECORDED; create/open/cancel are tenant-internal
    # scheduling no external subscriber needs. Choosing the seed is the same kind of
    # act: it decides which permalink's par the results will be scored against, and
    # the results themselves are what subscribers hear about.
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_CREATED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_OPENED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_CANCELLED,
    AuditActions.ASYNC_QUALIFIER_LIVE_RACE_PERMALINK_ASSIGNED,
    # Staff recording a race by hand publishes ASYNC_QUALIFIER_LIVE_RACE_RECORDED with
    # ``manual: True`` — the same external fact, a race captured into scored runs. Only
    # the audit needs the distinction, since only there does it matter that a human
    # asserted the results rather than racetime reporting them.
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
    # Moving crew held on an unbooked bracket matchup onto its booking: each
    # approved member it moves is announced as crew.approval_changed on the
    # booking, which is what a crew subscriber mirrors. The hold itself never
    # reached the bus.
    AuditActions.CREW_MOVED_TO_BOOKING,
    # A partner invite signs nobody up. Accepting goes through the ordinary
    # crew signup, which publishes crew.signup_created; an invite still waiting,
    # declined or cancelled changes no crew a subscriber mirrors.
    AuditActions.CREW_PARTNER_INVITED,
    AuditActions.CREW_PARTNER_INVITE_ANSWERED,
    AuditActions.CREW_PARTNER_INVITE_CANCELLED,
    # Building a survey is staff drafting in private; what reaches people is the
    # send (survey.sent) and the close, which are published. A reminder DMs the
    # same people the send already did.
    AuditActions.SURVEY_CREATED,
    AuditActions.SURVEY_UPDATED,
    AuditActions.SURVEY_DELETED,
    AuditActions.SURVEY_REMINDED,
    # Asking the AI for draft questions and setting the community's AI key are
    # staff tooling: nothing a person outside sees changes until a draft is kept
    # (an ordinary question edit) and the survey is sent.
    AuditActions.SURVEY_QUESTIONS_SUGGESTED,
    # Comment tags are staff's private filing for survey comments; respondents
    # never see them.
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
