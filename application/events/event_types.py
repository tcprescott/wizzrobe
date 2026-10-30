"""Canonical registry of domain event names published on the event bus.

Names are namespaced ``object.verb`` strings, mirroring the discipline of
:class:`~application.services.audit_service.AuditActions`. Unlike audit actions
these are an **external contract**: webhook subscribers pick from them and match
on them, so treat renames as breaking changes. Kept deliberately import-free of
the service layer so the events package stays cycle-free (like
``application.events.match_live``).
"""

from typing import FrozenSet


class EventType:
    EVENT_CREATED = 'event.created'
    EVENT_UPDATED = 'event.updated'
    EVENT_WENT_LIVE = 'event.went_live'
    EVENT_ARCHIVED = 'event.archived'
    EVENT_UNARCHIVED = 'event.unarchived'
    EVENT_DEFAULT_CHANGED = 'event.default_changed'
    EVENT_DELETED = 'event.deleted'
    EVENT_SETUP_COPIED = 'event.setup_copied'
    MATCH_CREATED = 'match.created'
    MATCH_UPDATED = 'match.updated'
    MATCH_DELETED = 'match.deleted'
    MATCH_CANCELLED = 'match.cancelled'
    MATCH_RESCHEDULED = 'match.rescheduled'
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
    MATCH_FLAGGED_FOR_REVIEW = 'match.flagged_for_review'
    MATCH_REVIEW_CLEARED = 'match.review_cleared'
    MATCH_STREAM_VOLUNTEERED = 'match.stream_volunteered'
    MATCH_STREAM_VOLUNTEER_WITHDRAWN = 'match.stream_volunteer_withdrawn'
    MATCH_COMMENT_ADDED = 'match.comment_added'
    MATCH_COMMENT_DELETED = 'match.comment_deleted'
    MATCH_PLAYER_RUNNING_LATE = 'match.player_running_late'
    MATCH_PLAYER_LATE_CLEARED = 'match.player_late_cleared'
    MATCH_PLAYER_SEED_READY = 'match.player_seed_ready'
    MATCH_PLAYER_SEED_NOT_READY = 'match.player_seed_not_ready'
    MATCH_HARD_PRESET_AGREED = 'match.hard_preset_agreed'
    MATCH_HARD_PRESET_AGREEMENT_REVOKED = 'match.hard_preset_agreement_revoked'
    MATCH_PRESET_OVERRIDE_SET = 'match.preset_override_set'
    MATCH_PRESET_OVERRIDE_CLEARED = 'match.preset_override_cleared'
    MATCH_RESCHEDULE_REQUESTED = 'match.reschedule_requested'
    MATCH_RESCHEDULE_AGREED = 'match.reschedule_agreed'
    MATCH_RESCHEDULE_APPROVED = 'match.reschedule_approved'
    MATCH_RESCHEDULE_DECLINED = 'match.reschedule_declined'
    MATCH_RESCHEDULE_WITHDRAWN = 'match.reschedule_withdrawn'

    CREW_SIGNUP_CREATED = 'crew.signup_created'
    CREW_SIGNUP_REMOVED = 'crew.signup_removed'
    CREW_APPROVAL_CHANGED = 'crew.approval_changed'
    CREW_ACKNOWLEDGED = 'crew.acknowledged'

    VOLUNTEER_ASSIGNED = 'volunteer.assigned'
    VOLUNTEER_UNASSIGNED = 'volunteer.unassigned'
    VOLUNTEER_ACKNOWLEDGED = 'volunteer.acknowledged'
    VOLUNTEER_RELEASED = 'volunteer.released'
    VOLUNTEER_CHECKED_IN = 'volunteer.checked_in'
    VOLUNTEER_CHECK_IN_UNDONE = 'volunteer.check_in_undone'
    VOLUNTEER_RUNNING_LATE = 'volunteer.running_late'
    VOLUNTEER_CALLED_OFF = 'volunteer.called_off'
    VOLUNTEER_CALL_OFF_UNDONE = 'volunteer.call_off_undone'

    RACE_ROOM_CREATED = 'race_room.created'
    RACE_ROOM_OPENED = 'race_room.opened'
    RACE_ROOM_STARTED = 'race_room.started'
    RACE_ROOM_FINISHED = 'race_room.finished'
    RACE_ROOM_CANCELLED = 'race_room.cancelled'
    RACE_ROOM_RESULT_RECORDED = 'race_room.result_recorded'
    RACE_ROOM_RESULT_HELD = 'race_room.result_held'
    RACE_ROOM_FPA_CALLED = 'race_room.fpa_called'

    SG_EPISODE_IMPORTED = 'sg_sync.episode_imported'
    SG_EPISODE_CANCELLED = 'sg_sync.episode_cancelled'
    SG_MATCH_AUTO_FINISHED = 'sg_sync.match_auto_finished'

    DISCORD_EVENT_CREATED = 'discord_event.created'
    DISCORD_EVENT_UPDATED = 'discord_event.updated'
    DISCORD_EVENT_CANCELLED = 'discord_event.cancelled'

    SERVICE_HEALTH_ALERT = 'service_health.alert'

    OAUTH_GRANT_CREATED = 'oauth_grant.created'
    OAUTH_GRANT_REVOKED = 'oauth_grant.revoked'

    ASYNC_QUALIFIER_RUN_SUBMITTED = 'async_qualifier.run_submitted'
    ASYNC_QUALIFIER_RUN_REVIEWED = 'async_qualifier.run_reviewed'
    ASYNC_QUALIFIER_RUN_EXPIRED = 'async_qualifier.run_expired'
    ASYNC_QUALIFIER_RUN_FORFEITED = 'async_qualifier.run_forfeited'
    ASYNC_QUALIFIER_RUN_REATTEMPTED = 'async_qualifier.run_reattempted'
    ASYNC_QUALIFIER_OPENED = 'async_qualifier.opened'
    ASYNC_QUALIFIER_CLOSED = 'async_qualifier.closed'

    ASYNC_QUALIFIER_LIVE_RACE_RECORDED = 'async_qualifier.live_race_recorded'

    BRACKET_CREATED = 'bracket.created'
    BRACKET_STARTED = 'bracket.started'
    BRACKET_MATCH_COMPLETED = 'bracket.match_completed'
    BRACKET_GAME_SCHEDULED = 'bracket.game_scheduled'
    BRACKET_GAME_COMPLETED = 'bracket.game_completed'
    BRACKET_GAME_CANCELLED = 'bracket.game_cancelled'
    BRACKET_GAME_LINKED = 'bracket.game_linked'
    BRACKET_GAME_UNLINKED = 'bracket.game_unlinked'
    BRACKET_GAME_RELEASED = 'bracket.game_released'
    BRACKET_ADVANCED = 'bracket.advanced'
    BRACKET_COMPLETED = 'bracket.completed'
    BRACKET_STAGE_ADVANCED = 'bracket.stage_advanced'
    BRACKET_ENTRANT_ADDED = 'bracket.entrant_added'
    BRACKET_ENTRANT_DROPPED = 'bracket.entrant_dropped'
    BRACKET_ENTRANT_UPDATED = 'bracket.entrant_updated'
    BRACKET_ENTRY_RETIRED = 'bracket.entry_retired'
    BRACKET_CANCELLED = 'bracket.cancelled'

    TENANT_MEMBER_ADDED = 'tenant.member_added'
    TENANT_MEMBER_REMOVED = 'tenant.member_removed'
    TENANT_MEMBERS_IMPORTED = 'tenant.members_imported'
    TENANT_JOIN_REQUESTED = 'tenant.join_requested'
    TENANT_JOIN_APPROVED = 'tenant.join_approved'
    TENANT_JOIN_DENIED = 'tenant.join_denied'

    TOURNAMENT_ENROLLED = 'tournament.enrolled'
    TOURNAMENT_WITHDRAWN = 'tournament.withdrawn'
    TOURNAMENT_PLAYER_DROPPED = 'tournament.player_dropped'

    TOURNAMENT_PRIZE_POOL_UPDATED = 'tournament.prize_pool_updated'
    TOURNAMENT_PAYOUT_UPDATED = 'tournament.payout_updated'
    TOURNAMENT_SGLIVE_LINK_UPDATED = 'tournament.sglive_link_updated'

    TOURNAMENT_ROUND_SCHEDULE_UPDATED = 'tournament.round_schedule_updated'

    CHECK_IN_EVENT_SYNCED = 'check_in_event.synced'
    CHECK_IN_ENTRANT_CHECKED_IN = 'check_in_entrant.checked_in'
    CHECK_IN_ENTRANT_CHECK_IN_UNDONE = 'check_in_entrant.check_in_undone'
    CHECK_IN_ENTRANT_LINKED = 'check_in_entrant.linked'
    CHECK_IN_ENTRANT_UNLINKED = 'check_in_entrant.unlinked'
    CHECK_IN_ENTRANT_WALK_UP_ADDED = 'check_in_entrant.walk_up_added'
    CHECK_IN_ENTRANT_REMOVED = 'check_in_entrant.removed'

    COMMUNITY_EVENT_SUBMITTED = 'community_event.submitted'
    COMMUNITY_EVENT_UPDATED = 'community_event.updated'
    COMMUNITY_EVENT_APPROVED = 'community_event.approved'
    COMMUNITY_EVENT_REJECTED = 'community_event.rejected'
    COMMUNITY_EVENT_CANCELLED = 'community_event.cancelled'

    SURVEY_SENT = 'survey.sent'
    SURVEY_CLOSED = 'survey.closed'

    ALL: FrozenSet[str] = frozenset({
        EVENT_CREATED, EVENT_UPDATED, EVENT_WENT_LIVE, EVENT_ARCHIVED, EVENT_UNARCHIVED,
        EVENT_DEFAULT_CHANGED, EVENT_DELETED, EVENT_SETUP_COPIED,
        MATCH_CREATED, MATCH_UPDATED, MATCH_DELETED, MATCH_CANCELLED, MATCH_RESCHEDULED,
        MATCH_SEATED, MATCH_STARTED, MATCH_FINISHED, MATCH_CONFIRMED, MATCH_FORFEITED,
        MATCH_ACKNOWLEDGED, MATCH_RESULT_RECORDED, MATCH_SEED_ROLLED,
        MATCH_SEED_ROLL_QUEUED, MATCH_SEED_ROLL_FAILED,
        MATCH_STAGE_ASSIGNED, MATCH_STAGE_CLEARED, MATCH_STATIONS_ASSIGNED,
        MATCH_STREAM_CANDIDATE_SET, MATCH_STREAM_CANDIDATE_CLEARED,
        MATCH_FLAGGED_FOR_REVIEW, MATCH_REVIEW_CLEARED,
        MATCH_STREAM_VOLUNTEERED, MATCH_STREAM_VOLUNTEER_WITHDRAWN,
        MATCH_COMMENT_ADDED, MATCH_COMMENT_DELETED,
        MATCH_PLAYER_RUNNING_LATE, MATCH_PLAYER_LATE_CLEARED,
        MATCH_PLAYER_SEED_READY, MATCH_PLAYER_SEED_NOT_READY,
        MATCH_HARD_PRESET_AGREED, MATCH_HARD_PRESET_AGREEMENT_REVOKED,
        MATCH_PRESET_OVERRIDE_SET, MATCH_PRESET_OVERRIDE_CLEARED,
        MATCH_RESCHEDULE_REQUESTED, MATCH_RESCHEDULE_AGREED,
        MATCH_RESCHEDULE_APPROVED, MATCH_RESCHEDULE_DECLINED,
        MATCH_RESCHEDULE_WITHDRAWN,
        CREW_SIGNUP_CREATED, CREW_SIGNUP_REMOVED, CREW_APPROVAL_CHANGED,
        CREW_ACKNOWLEDGED,
        VOLUNTEER_ASSIGNED, VOLUNTEER_UNASSIGNED, VOLUNTEER_ACKNOWLEDGED,
        VOLUNTEER_RELEASED, VOLUNTEER_CHECKED_IN, VOLUNTEER_CHECK_IN_UNDONE,
        VOLUNTEER_RUNNING_LATE, VOLUNTEER_CALLED_OFF, VOLUNTEER_CALL_OFF_UNDONE,
        RACE_ROOM_CREATED, RACE_ROOM_OPENED, RACE_ROOM_STARTED,
        RACE_ROOM_FINISHED, RACE_ROOM_CANCELLED, RACE_ROOM_RESULT_RECORDED,
        RACE_ROOM_RESULT_HELD, RACE_ROOM_FPA_CALLED,
        SG_EPISODE_IMPORTED, SG_EPISODE_CANCELLED, SG_MATCH_AUTO_FINISHED,
        DISCORD_EVENT_CREATED, DISCORD_EVENT_UPDATED, DISCORD_EVENT_CANCELLED,
        SERVICE_HEALTH_ALERT,
        OAUTH_GRANT_CREATED, OAUTH_GRANT_REVOKED,
        ASYNC_QUALIFIER_RUN_SUBMITTED, ASYNC_QUALIFIER_RUN_REVIEWED,
        ASYNC_QUALIFIER_RUN_EXPIRED, ASYNC_QUALIFIER_RUN_FORFEITED,
        ASYNC_QUALIFIER_RUN_REATTEMPTED,
        ASYNC_QUALIFIER_OPENED, ASYNC_QUALIFIER_CLOSED,
        ASYNC_QUALIFIER_LIVE_RACE_RECORDED,
        BRACKET_CREATED, BRACKET_STARTED, BRACKET_MATCH_COMPLETED,
        BRACKET_ADVANCED, BRACKET_COMPLETED, BRACKET_STAGE_ADVANCED,
        BRACKET_ENTRANT_ADDED, BRACKET_ENTRANT_DROPPED, BRACKET_ENTRANT_UPDATED,
        BRACKET_ENTRY_RETIRED, BRACKET_CANCELLED,
        BRACKET_GAME_SCHEDULED, BRACKET_GAME_COMPLETED, BRACKET_GAME_CANCELLED,
        BRACKET_GAME_LINKED, BRACKET_GAME_UNLINKED, BRACKET_GAME_RELEASED,
        TENANT_MEMBER_ADDED, TENANT_MEMBER_REMOVED, TENANT_MEMBERS_IMPORTED,
        TENANT_JOIN_REQUESTED, TENANT_JOIN_APPROVED, TENANT_JOIN_DENIED,
        TOURNAMENT_ENROLLED, TOURNAMENT_WITHDRAWN, TOURNAMENT_PLAYER_DROPPED,
        TOURNAMENT_PRIZE_POOL_UPDATED, TOURNAMENT_PAYOUT_UPDATED,
        TOURNAMENT_SGLIVE_LINK_UPDATED,
        TOURNAMENT_ROUND_SCHEDULE_UPDATED,
        CHECK_IN_EVENT_SYNCED, CHECK_IN_ENTRANT_CHECKED_IN,
        CHECK_IN_ENTRANT_CHECK_IN_UNDONE, CHECK_IN_ENTRANT_LINKED,
        CHECK_IN_ENTRANT_UNLINKED, CHECK_IN_ENTRANT_WALK_UP_ADDED,
        CHECK_IN_ENTRANT_REMOVED,
        COMMUNITY_EVENT_SUBMITTED, COMMUNITY_EVENT_UPDATED,
        COMMUNITY_EVENT_APPROVED, COMMUNITY_EVENT_REJECTED,
        COMMUNITY_EVENT_CANCELLED,
        SURVEY_SENT, SURVEY_CLOSED,
    })

    WILDCARD = '*'

    COMMUNITY_FAMILIES: FrozenSet[str] = frozenset({'tenant', 'oauth_grant', 'service_health'})

    @classmethod
    def is_event_owned(cls, name: str) -> bool:
        """Whether a message of this type happened in one of the community's events."""
        return name.split('.', 1)[0] not in cls.COMMUNITY_FAMILIES

    @classmethod
    def is_valid(cls, name: str) -> bool:
        """True for the wildcard or any registered event name."""
        return name == cls.WILDCARD or name in cls.ALL
