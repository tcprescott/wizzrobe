"""Plain-language rendering of audit log rows.

The audit trail stores a machine action key (``match.stage_assigned``) and a JSON
blob of whatever the writing service thought relevant. Staff reading the log want
"Put a match on a stage · Match #1 · Stage 1", so this module owns the mapping:
one label per action, the area each action belongs to (the report's Area filter),
and a describer that turns the blob into a summary line, before/after changes and
labelled facts. Pure functions over plain data: the caller resolves ids to names
first (``AuditNames``) so nothing here touches the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class AuditArea:
    key: str
    label: str
    prefixes: tuple[str, ...]


AREAS: tuple[AuditArea, ...] = (
    AuditArea('matches', 'Matches', ('match.', 'crew.', 'race_room.')),
    AuditArea('tournaments', 'Tournaments', (
        'tournament.', 'bracket.', 'preset.', 'async_qualifier.', 'randomizer_credential.',
        'triforce_text.', 'player.',
    )),
    AuditArea('people', 'People', (
        'user.', 'role.', 'profile_nudge.', 'emergency_contact.', 'accommodation.', 'twitch.',
        'racetime.', 'web_push.', 'notification_email.', 'notification_telegram.', 'tenant.member_', 'tenant.join_',
    )),
    AuditArea('volunteers', 'Volunteers', ('volunteer.',)),
    AuditArea('check_in', 'Check-in', (
        'check_in_event.', 'check_in_tier.', 'check_in_entrant.', 'matcherino_login.',
    )),
    AuditArea('equipment', 'Equipment', ('equipment.',)),
    AuditArea('challonge', 'Challonge', ('challonge.',)),
    AuditArea('startgg', 'start.gg', ('startgg.',)),
    AuditArea('community', 'Community', ('community_event.', 'feedback.')),
    AuditArea('events', 'Events', ('event.',)),
    AuditArea('settings', 'Settings', (
        'system_config.', 'notification_setting.', 'notification_settings.', 'theme.', 'timezone.', 'profile_nudges.', 'station.', 'stage.',
        'discord.', 'discord_role.', 'discord_event.', 'apitoken.', 'roomtoken.', 'log_level.',
        'browser_monitoring.', 'browser_ignore_rule.',
        'racetime_bot.', 'race_room_profile.', 'sg_sync.', 'feature_flag.', 'feature_group.',
        'webhook.', 'personal_webhook.', 'tenant.', 'platform.',
    )),
)
_AREAS_BY_KEY = {a.key: a for a in AREAS}


def area_for(action: str) -> Optional[AuditArea]:
    for area in AREAS:
        if action.startswith(area.prefixes):
            return area
    return None


def area_by_key(key: Optional[str]) -> Optional[AuditArea]:
    return _AREAS_BY_KEY.get(key or '')


ACTION_LABELS: dict[str, str] = {
    # Matches
    'match.created': 'Created a match',
    'match.updated': 'Edited a match',
    'match.rescheduled': 'Rescheduled a match',
    'match.deleted': 'Deleted a match',
    'match.cancelled': 'Called off a match',
    'match.set_extended': 'Turned a match into a best-of set',
    'match.requested': 'Requested a match',
    'match.seated': 'Seated a match',
    'match.started': 'Started a match',
    'match.finished': 'Finished a match',
    'match.confirmed': 'Confirmed a match result',
    'match.forfeited': 'Decided a match by forfeit',
    'match.acknowledged': 'Acknowledged a match',
    'match.result_recorded': 'Recorded a match result',
    'match.seed_rolled': 'Rolled a seed',
    'match.seed_roll_queued': 'Queued a seed roll',
    'match.seed_roll_failed': 'Seed roll failed',
    'match.stage_assigned': 'Put a match on a stage',
    'match.stage_cleared': 'Took a match off its stage',
    'match.stations_assigned': 'Assigned stations',
    'match.stream_candidate_set': 'Marked a match as a stream candidate',
    'match.stream_candidate_cleared': 'Unmarked a stream candidate',
    'match.flagged_for_review': 'Flagged a match for review',
    'match.review_cleared': 'Cleared a match review flag',
    'match.watcher_added': 'Started watching a match',
    'match.watcher_removed': 'Stopped watching a match',
    'match.stream_volunteered': 'Volunteered a match for stream',
    'match.stream_volunteer_withdrawn': 'Withdrew a match from stream',
    'match.comment_added': 'Commented on a match',
    'match.comment_deleted': 'Deleted a match comment',
    'match.player_running_late': 'Said they are running late for a match',
    'match.player_late_cleared': 'Took back running late for a match',
    'match.player_seed_ready': 'Said they are ready for the seed',
    'match.player_seed_not_ready': 'Took back ready for the seed',
    'match.hard_preset_opted_in': 'Opted in to the hard preset',
    'match.hard_preset_withdrawn': 'Withdrew from the hard preset',
    'match.hard_preset_agreed': 'Agreed to the hard preset',
    'match.hard_preset_agreement_revoked': 'Took back hard preset agreement',
    'match.preset_override_set': 'Set a preset override',
    'match.preset_override_cleared': 'Cleared a preset override',
    'match.reschedule_requested': 'Asked to reschedule a match',
    'match.reschedule_agreed': 'Agreed to a reschedule',
    'match.reschedule_approved': 'Approved a reschedule',
    'match.reschedule_declined': 'Declined a reschedule',
    'match.reschedule_withdrawn': 'Withdrew a reschedule request',
    'crew.signup_created': 'Signed up as crew',
    'crew.signup_removed': 'Removed a crew signup',
    'crew.approval_changed': 'Changed crew approval',
    'crew.acknowledged': 'Acknowledged a crew slot',
    'crew.moved_to_booking': 'Moved crew signups onto a booked bracket match',
    'crew.partner_invited': 'Invited a commentary partner',
    'crew.partner_invite_answered': 'Answered a commentary partner invite',
    'crew.partner_invite_cancelled': 'Cancelled a commentary partner invite',
    'race_room.created': 'Created a race room',
    'race_room.opened': 'Opened a race room',
    'race_room.started': 'Race room started',
    'race_room.finished': 'Race room finished',
    'race_room.cancelled': 'Cancelled a race room',
    'race_room.result_recorded': 'Recorded a race room result',
    # Tournaments
    'tournament.created': 'Created a tournament',
    'tournament.updated': 'Edited a tournament',
    'tournament.deleted': 'Deleted a tournament',
    'tournament.player_dropped': 'Dropped a player from a tournament',
    'tournament.admin_granted': 'Made someone a tournament admin',
    'tournament.admin_revoked': 'Removed a tournament admin',
    'tournament.crew_coordinator_granted': 'Made someone a crew coordinator',
    'tournament.crew_coordinator_revoked': 'Removed a crew coordinator',
    'tournament.prize_pool_updated': 'Updated a prize pool',
    'tournament.payout_updated': 'Updated a payout',
    'tournament.sglive_link_updated': 'Linked a prize pool to sglive',
    'tournament.round_schedule_updated': 'Changed a round schedule',
    'bracket.created': 'Created a bracket',
    'bracket.updated': 'Edited a bracket',
    'bracket.deleted': 'Deleted a bracket',
    'bracket.started': 'Started a bracket',
    'bracket.match_completed': 'Completed a bracket match',
    'bracket.game_scheduled': 'Scheduled a bracket game',
    'bracket.game_completed': 'Completed a bracket game',
    'bracket.game_cancelled': 'Cancelled a bracket game',
    'bracket.game_linked': 'Linked a bracket game to a match',
    'bracket.game_unlinked': 'Unlinked a bracket game from a match',
    'bracket.game_released': 'Released a bracket game',
    'bracket.advanced': 'Advanced a bracket',
    'bracket.completed': 'Completed a bracket',
    'bracket.stage_advanced': 'Advanced a bracket stage',
    'bracket.cancelled': 'Cancelled a bracket',
    'bracket.entrant_added': 'Added a bracket entrant',
    'bracket.entrant_dropped': 'Dropped a bracket entrant',
    'bracket.entrant_updated': 'Edited a bracket entrant',
    'bracket.entry_added': 'Added a bracket entry',
    'bracket.entry_removed': 'Removed a bracket entry',
    'bracket.entry_retired': 'Retired a bracket entry',
    'preset.created': 'Created a seed preset',
    'preset.updated': 'Edited a seed preset',
    'preset.deleted': 'Deleted a seed preset',
    'preset.imported': 'Imported a seed preset',
    'preset.test_rolled': 'Test-rolled a seed preset',
    'randomizer_credential.set': 'Saved a randomizer login',
    'randomizer_credential.cleared': 'Removed a randomizer login',
    'triforce_text.submitted': 'Submitted a triforce text',
    'triforce_text.approved': 'Approved a triforce text',
    'triforce_text.rejected': 'Rejected a triforce text',
    'triforce_text.deleted': 'Deleted a triforce text',
    'player.availability_updated': 'Updated their availability',
    'async_qualifier.created': 'Created a qualifier',
    'async_qualifier.updated': 'Edited a qualifier',
    'async_qualifier.deleted': 'Deleted a qualifier',
    'async_qualifier.admin_granted': 'Made someone a qualifier admin',
    'async_qualifier.admin_revoked': 'Removed a qualifier admin',
    'async_qualifier.pool_created': 'Created a qualifier pool',
    'async_qualifier.pool_updated': 'Edited a qualifier pool',
    'async_qualifier.pool_deleted': 'Deleted a qualifier pool',
    'async_qualifier.permalink_added': 'Added a qualifier seed',
    'async_qualifier.permalink_updated': 'Edited a qualifier seed',
    'async_qualifier.permalink_deleted': 'Deleted a qualifier seed',
    'async_qualifier.run_started': 'Started a qualifier run',
    'async_qualifier.run_submitted': 'Submitted a qualifier run',
    'async_qualifier.run_forfeited': 'Forfeited a qualifier run',
    'async_qualifier.run_expired': 'Qualifier run expired',
    'async_qualifier.run_reattempted': 'Reattempted a qualifier run',
    'async_qualifier.reattempt_granted': 'Granted a qualifier reattempt',
    'async_qualifier.run_reviewed': 'Reviewed a qualifier run',
    'async_qualifier.run_review_overridden': 'Overrode a qualifier review',
    'async_qualifier.live_race_created': 'Created a qualifier live race',
    'async_qualifier.live_race_opened': 'Opened a qualifier live race',
    'async_qualifier.live_race_permalink_assigned': 'Set the seed for a live race',
    'async_qualifier.live_race_cancelled': 'Cancelled a qualifier live race',
    'async_qualifier.live_race_recorded': 'Recorded a qualifier live race',
    'async_qualifier.live_race_recorded_manually': 'Recorded a live race by hand',
    # People
    'user.created': 'Added a member',
    'user.provisioned': 'Set up a member account',
    'user.profile_updated': 'Edited a profile',
    'user.self_profile_updated': 'Updated their own profile',
    'user.activation_changed': 'Changed whether an account is active',
    'user.role_granted': 'Gave a role',
    'user.role_revoked': 'Took away a role',
    'user.tournament_enrollment_updated': 'Changed tournament entries',
    'user.password_set': 'Set a password',
    'user.password_removed': 'Removed a password',
    'user.password_signed_in': 'Signed in with a password',
    'user.timezone_updated': 'Changed their time zone',
    'user.hour_cycle_updated': 'Changed their 12/24-hour clock',
    'role.discord_sync_granted': 'Gave a role from Discord',
    'role.discord_sync_revoked': 'Took away a role from Discord',
    'role.discord_sync_bulk': 'Synced roles from Discord',
    'role.volunteer_sync_granted': 'Gave a role from volunteering',
    'role.volunteer_sync_revoked': 'Took away a role from volunteering',
    'profile_nudge.dismissed': 'Dismissed a profile reminder',
    'emergency_contact.saved': 'Saved an emergency contact',
    'emergency_contact.removed': 'Removed an emergency contact',
    'emergency_contact.viewed': 'Viewed an emergency contact',
    'accommodation.requested': 'Asked for an accommodation',
    'accommodation.updated': 'Edited an accommodation request',
    'accommodation.withdrawn': 'Withdrew an accommodation request',
    'accommodation.status_changed': 'Changed an accommodation status',
    'accommodation.notes_updated': 'Edited accommodation notes',
    'accommodation.change_reviewed': 'Reviewed an accommodation change',
    'twitch.linked': 'Linked Twitch',
    'twitch.unlinked': 'Unlinked Twitch',
    'racetime.linked': 'Linked racetime.gg',
    'racetime.unlinked': 'Unlinked racetime.gg',
    'web_push.subscribed': 'Turned on phone notifications',
    'web_push.unsubscribed': 'Turned off phone notifications',
    'web_push.rotated': 'Refreshed phone notifications',
    'notification_email.confirmed': 'Confirmed a notification email',
    'notification_email.removed': 'Removed their notification email',
    'notification_telegram.linked': 'Connected Telegram',
    'notification_telegram.unlinked': 'Disconnected Telegram',
    'tenant.member_added': 'Added a member to the community',
    'tenant.member_removed': 'Removed a member from the community',
    'tenant.members_imported': 'Imported members from Discord',
    'tenant.join_requested': 'Asked to join the community',
    'tenant.join_approved': 'Approved a join request',
    'tenant.join_denied': 'Turned down a join request',
    # Volunteers
    'volunteer.position_created': 'Created a volunteer position',
    'volunteer.position_updated': 'Edited a volunteer position',
    'volunteer.position_deleted': 'Deleted a volunteer position',
    'volunteer.shift_created': 'Created a shift',
    'volunteer.shift_updated': 'Edited a shift',
    'volunteer.shift_deleted': 'Deleted a shift',
    'volunteer.assigned': 'Assigned a volunteer',
    'volunteer.unassigned': 'Unassigned a volunteer',
    'volunteer.acknowledged': 'Acknowledged a shift',
    'volunteer.checked_in': 'Checked in for a shift',
    'volunteer.check_in_undone': 'Undid a shift check-in',
    'volunteer.running_late': 'Said they are running late',
    'volunteer.called_off': "Called off a volunteer's shift (still counts as worked)",
    'volunteer.call_off_undone': 'Put a volunteer back on a called-off shift',
    'volunteer.availability_updated': 'Updated volunteer availability',
    'volunteer.draft_generated': 'Drafted the volunteer schedule',
    'volunteer.draft_cleared': 'Cleared the schedule draft',
    'volunteer.draft_published': 'Published the volunteer schedule',
    'volunteer.released': 'Released a shift',
    'volunteer.shifts_reset': 'Reset volunteer shifts',
    'volunteer.qualifications_updated': 'Updated volunteer qualifications',
    'volunteer.data_exported': 'Exported volunteer data',
    'tournament.data_exported': 'Exported tournament data',
    'volunteer.role_mapping_added': 'Linked a volunteer position to a role',
    'volunteer.role_mapping_removed': 'Unlinked a volunteer position from a role',
    # Check-in
    'check_in_event.created': 'Created a check-in event',
    'check_in_event.updated': 'Edited a check-in event',
    'check_in_event.deleted': 'Deleted a check-in event',
    'check_in_event.synced': 'Synced the Matcherino roster',
    'check_in_tier.updated': 'Edited a ticket tier',
    'check_in_entrant.checked_in': 'Checked someone in',
    'check_in_entrant.check_in_undone': 'Undid a check-in',
    'check_in_entrant.linked': 'Linked an entrant to a member',
    'check_in_entrant.unlinked': 'Unlinked an entrant',
    'check_in_entrant.walk_up_added': 'Added a walk-up',
    'check_in_entrant.removed': 'Removed an entrant',
    'matcherino_login.set': 'Saved the Matcherino login',
    'matcherino_login.cleared': 'Removed the Matcherino login',
    'matcherino_login.alerts_updated': 'Changed Matcherino login alerts',
    # Equipment
    'equipment.created': 'Added equipment',
    'equipment.updated': 'Edited equipment',
    'equipment.deleted': 'Deleted equipment',
    'equipment.checked_out': 'Checked out equipment',
    'equipment.checked_in': 'Returned equipment',
    # Challonge
    'challonge.connected': 'Connected Challonge',
    'challonge.disconnected': 'Disconnected Challonge',
    'challonge.community_removed': 'Removed the Challonge connection',
    'challonge.player_linked': 'Linked a Challonge account',
    'challonge.player_unlinked': 'Unlinked a Challonge account',
    'challonge.player_username_updated': 'Updated a Challonge username',
    'challonge.tournament_linked': 'Linked a Challonge bracket',
    'challonge.tournament_unlinked': 'Unlinked a Challonge bracket',
    'challonge.bracket_synced': 'Synced a Challonge bracket',
    'challonge.result_pushed': 'Sent a result to Challonge',
    'challonge.result_skipped': "Didn't send a result: Challonge already had one",
    'challonge.result_push_failed': "Couldn't send a result to Challonge",
    'challonge.auto_sync_updated': 'Changed how often Challonge brackets sync',
    'challonge.participant_assigned': 'Matched a Challonge entrant to a member',
    'challonge.participant_unassigned': 'Unmatched a Challonge entrant',
    'challonge.participant_signed_up': 'Signed up on Challonge',
    'challonge.participant_withdrawn': 'Withdrew on Challonge',
    'challonge.stage_advanced': 'Advanced a Challonge stage',
    'challonge.webhook_synced': 'Challonge sent an update',
    'challonge.match_linked': 'Linked a match to its Challonge matchup',
    # start.gg
    'startgg.linked': 'Linked a start.gg account',
    'startgg.unlinked': 'Unlinked a start.gg account',
    'startgg.event_linked': 'Linked a start.gg event',
    'startgg.event_unlinked': 'Unlinked a start.gg event',
    'startgg.event_synced': 'Synced a start.gg event',
    'startgg.bookable_phase_set': 'Changed which start.gg phase players book',
    'startgg.entrant_assigned': 'Matched a start.gg entrant to a member',
    'startgg.entrant_unassigned': 'Unmatched a start.gg entrant',
    'startgg.suggestion_dismissed': 'Dismissed a start.gg match suggestion',
    'startgg.reporting_set': 'Changed start.gg result reporting',
    'startgg.result_reported': 'Sent a result to start.gg',
    'startgg.set_linked': 'Linked a match to its start.gg matchup',
    'startgg.connected': 'Connected start.gg',
    'startgg.disconnected': 'Disconnected start.gg',
    # Community
    'community_event.submitted': 'Submitted a community event',
    'community_event.updated': 'Edited a community event',
    'community_event.approved': 'Approved a community event',
    'community_event.rejected': 'Rejected a community event',
    'community_event.cancelled': 'Cancelled a community event',
    'feedback.submitted': 'Sent feedback',
    'feedback.reviewed': 'Reviewed feedback',
    'feedback.reopened': 'Reopened feedback',
    # Settings
    'system_config.updated': 'Changed a setting',
    'notification_setting.changed': 'Changed how a notification is sent',
    'notification_settings.reset': 'Reset notification settings to defaults',
    'theme.updated': 'Changed the colours',
    'theme.logo_updated': 'Uploaded a logo',
    'theme.logo_removed': 'Removed the logo',
    'timezone.updated': 'Changed the community time zone',
    'profile_nudges.updated': 'Changed profile reminders',
    'station.created': 'Added a station',
    'station.updated': 'Edited a station',
    'station.deleted': 'Deleted a station',
    'stage.created': 'Added a stage',
    'stage.updated': 'Edited a stage',
    'stage.deleted': 'Deleted a stage',
    'discord.server_linked': 'Linked a Discord server',
    'discord.server_unlinked': 'Unlinked the Discord server',
    'discord.login_panel_posted': 'Posted the Discord sign-in panel',
    'discord_role.mapping_added': 'Linked a Discord role',
    'discord_role.mapping_removed': 'Unlinked a Discord role',
    'discord_role.rule_set': 'Set a Discord role Wizzrobe gives',
    'discord_role.rule_cleared': 'Turned off a Discord role Wizzrobe gives',
    'discord_role.rules_reset': 'Removed every Discord role Wizzrobe gave',
    'discord_event.created': 'Created a Discord event',
    'discord_event.updated': 'Updated a Discord event',
    'discord_event.cancelled': 'Cancelled a Discord event',
    'discord_event.sync_completed': 'Synced Discord events',
    'discord_event.sync_failed': 'Discord event sync failed',
    'discord_event.settings_updated': 'Changed Discord event settings',
    'apitoken.created': 'Created an API token',
    'apitoken.revoked': 'Revoked an API token',
    'roomtoken.created': 'Created a room link',
    'roomtoken.revoked': 'Revoked a room link',
    'log_level.set': 'Changed the log level',
    'log_level.cleared': 'Reset the log level',
    'browser_monitoring.updated': 'Changed browser error reporting',
    'browser_ignore_rule.added': 'Ignored a browser error',
    'browser_ignore_rule.removed': 'Stopped ignoring a browser error',
    'racetime_bot.created': 'Added a racetime bot',
    'racetime_bot.updated': 'Edited a racetime bot',
    'racetime_bot.deleted': 'Deleted a racetime bot',
    'racetime_bot.granted': 'Gave access to a racetime bot',
    'racetime_bot.revoked': 'Took away racetime bot access',
    'racetime_bot.connected': 'Racetime bot connected',
    'racetime_bot.disconnected': 'Racetime bot disconnected',
    'racetime_bot.error': 'Racetime bot hit an error',
    'racetime_bot.restarted': 'Restarted a racetime bot',
    'race_room_profile.created': 'Created a race room profile',
    'race_room_profile.updated': 'Edited a race room profile',
    'race_room_profile.deleted': 'Deleted a race room profile',
    'sg_sync.event_link_created': 'Linked a SpeedGaming event',
    'sg_sync.event_link_updated': 'Edited a SpeedGaming event link',
    'sg_sync.event_link_deleted': 'Unlinked a SpeedGaming event',
    'sg_sync.completed': 'Synced from SpeedGaming',
    'sg_sync.failed': 'SpeedGaming sync failed',
    'sg_sync.episode_imported': 'Imported a SpeedGaming episode',
    'sg_sync.episode_skipped': 'Skipped a SpeedGaming episode',
    'sg_sync.episode_cancelled': 'SpeedGaming episode cancelled',
    'sg_sync.match_auto_finished': 'Finished a match from SpeedGaming',
    'sg_sync.placeholder_created': 'Added a SpeedGaming placeholder player',
    'sg_sync.placeholder_upgraded': 'Matched a SpeedGaming placeholder to a member',
    'feature_flag.enabled': 'Turned on a feature',
    'feature_flag.disabled': 'Turned off a feature',
    'feature_flag.availability_set': 'Changed whether a feature is available',
    'feature_group.created': 'Created a feature tier',
    'feature_group.updated': 'Edited a feature tier',
    'feature_group.deleted': 'Deleted a feature tier',
    'feature_group.assigned': 'Changed the feature tier',
    'webhook.created': 'Added a webhook',
    'webhook.updated': 'Edited a webhook',
    'webhook.deleted': 'Deleted a webhook',
    'webhook.secret_regenerated': 'Made a new webhook secret',
    'personal_webhook.created': 'Added a personal webhook',
    'personal_webhook.updated': 'Edited a personal webhook',
    'personal_webhook.deleted': 'Deleted a personal webhook',
    'personal_webhook.secret_regenerated': 'Made a new personal webhook secret',
    'event.created': 'Created an event',
    'event.updated': 'Edited an event',
    'event.went_live': 'Put an event live',
    'event.archived': 'Archived an event',
    'event.unarchived': 'Brought an event back from the archive',
    'event.default_changed': 'Changed the default event',
    'event.deleted': 'Deleted a draft event',
    'event.setup_copied': "Copied an event's setup",
    'tenant.created': 'Created the community',
    'tenant.updated': 'Edited the community',
    'tenant.deleted': 'Deleted the community',
    'platform.super_admin_granted': 'Made someone a platform admin',
    'platform.super_admin_revoked': 'Removed a platform admin',
}


def _humanize(text: str) -> str:
    text = text.replace('_', ' ').strip()
    return text[:1].upper() + text[1:]


def action_label(action: str) -> str:
    """The plain label for an action key, or a tidied key for one with none."""
    label = ACTION_LABELS.get(action)
    if label:
        return label
    obj, _, verb = action.partition('.')
    return f'{_humanize(obj)}: {verb.replace("_", " ")}' if verb else _humanize(obj)


def actions_matching(text: str) -> list[str]:
    """Action keys whose plain label contains ``text`` (case-insensitive).

    Lets a search for "stage" or "reminder" find rows whose key never says so.
    """
    needle = text.strip().lower()
    if not needle:
        return []
    return [key for key, label in ACTION_LABELS.items() if needle in label.lower()]


# --- Describing one row --------------------------------------------------------

HIDDEN_KEYS = frozenset({'actor_discord_id', 'actor_username', 'tenant_id'})
_USER_KEYS = frozenset({'user_id', 'target_user_id', 'winner_id', 'borrower_id', 'volunteer_id'})
_USER_LIST_KEYS = frozenset({'player_ids', 'commentator_ids', 'tracker_ids', 'user_ids'})
_SUBJECT_NAME_KEYS = ('display_name', 'name', 'challonge_username', 'title')
_BOOL_TEXT = {'true': 'Yes', 'false': 'No'}

FIELD_LABELS: dict[str, str] = {
    'scheduled_at': 'start time',
    'seated_at': 'seated time',
    'started_at': 'start',
    'finished_at': 'finish',
    'confirmed_at': 'confirmation',
    'tournament_id': 'tournament',
    'stage_id': 'stage',
    'match_id': 'match',
    'user_id': 'member',
    'target_user_id': 'member',
    'winner_id': 'winner',
    'player_ids': 'players',
    'commentator_ids': 'commentators',
    'tracker_ids': 'trackers',
    'matcherino_user_id': 'Matcherino ID',
    'matcherino_username': 'Matcherino username',
    'challonge_user_id': 'Challonge ID',
    'challonge_username': 'Challonge username',
    'challonge_participant_id': 'Challonge entrant ID',
    'startgg_user_id': 'start.gg ID',
    'startgg_username': 'start.gg username',
    'stream_url': 'stream URL',
    'seed_url': 'seed',
    'is_active': 'active',
    'check': 'reminder',
    'kind': 'type',
    'ok': 'worked',
    'read_only': 'read only',
    'sync_interval_minutes': 'sync every (minutes)',
    'link_method': 'linked by',
    'entrant_id': 'entrant',
    'event_id': 'event',
    'preset_id': 'preset',
    'token_id': 'token',
    'comment_id': 'comment',
    'request_id': 'request',
}


def field_label(key: str) -> str:
    label = FIELD_LABELS.get(key)
    if label is None:
        label = key[:-3] if key.endswith('_id') else key
        label = label.replace('_', ' ')
    return label[:1].upper() + label[1:]


@dataclass
class AuditNames:
    """Ids a page of rows refers to, resolved to display names by the caller."""
    users: Mapping[int, str] = field(default_factory=dict)
    stages: Mapping[int, str] = field(default_factory=dict)
    tournaments: Mapping[int, str] = field(default_factory=dict)


@dataclass
class AuditChange:
    field: str
    before: Optional[str]
    after: Optional[str]


@dataclass
class AuditDescription:
    label: str
    area: Optional[str]
    subject: str
    summary: str
    changes: list[AuditChange]
    changed_fields: list[str]
    facts: list[tuple[str, str]]
    raw: str
    match_id: Optional[int] = None


def referenced_ids(details_list: Sequence[Any]) -> tuple[set[int], set[int], set[int]]:
    """(user ids, stage ids, tournament ids) mentioned across a page of rows."""
    users: set[int] = set()
    stages: set[int] = set()
    tournaments: set[int] = set()
    for details in details_list:
        if not isinstance(details, dict):
            continue
        for key, value in details.items():
            if key in _USER_KEYS and isinstance(value, int):
                users.add(value)
            elif key in _USER_LIST_KEYS and isinstance(value, list):
                users.update(v for v in value if isinstance(v, int))
            elif key == 'stage_id' and isinstance(value, int):
                stages.add(value)
            elif key == 'tournament_id' and isinstance(value, int):
                tournaments.add(value)
    return users, stages, tournaments


def _plain(value: Any) -> str:
    if value is None or value == '':
        return '(empty)'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, str) and value.lower() in _BOOL_TEXT:
        return _BOOL_TEXT[value.lower()]
    if isinstance(value, list):
        return ', '.join(_plain(v) for v in value) if value else '(none)'
    if isinstance(value, dict):
        return ', '.join(f'{field_label(str(k))}: {_plain(v)}' for k, v in value.items()) or '(none)'
    text = str(value)
    return text if len(text) <= 160 else text[:159] + '…'


def _value(key: str, value: Any, names: AuditNames) -> str:
    if key in _USER_KEYS and isinstance(value, int):
        return names.users.get(value, f'Member #{value}')
    if key in _USER_LIST_KEYS and isinstance(value, list):
        return ', '.join(names.users.get(v, f'Member #{v}') for v in value) if value else '(none)'
    if key == 'stage_id':
        return names.stages.get(value, f'Stage #{value}') if isinstance(value, int) else '(none)'
    if key == 'tournament_id' and isinstance(value, int):
        return names.tournaments.get(value, f'Tournament #{value}')
    if key.endswith('_id') and isinstance(value, int):
        return f'#{value}'
    return _plain(value)


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ('{', '['):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _path_label(part: str) -> str:
    try:
        return date.fromisoformat(part).strftime('%a %b %d').replace(' 0', ' ')
    except ValueError:
        return field_label(part)


def _flatten(value: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(value, dict) and value:
        out: dict[tuple[str, ...], Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, (*prefix, str(k))))
        return out
    return {prefix: value}


def _diff(label: str, old: Any, new: Any) -> list[AuditChange]:
    old, new = _maybe_json(old), _maybe_json(new)
    if isinstance(old, dict) or isinstance(new, dict):
        flat_old = _flatten(old if isinstance(old, dict) else {})
        flat_new = _flatten(new if isinstance(new, dict) else {})
        changes = []
        for path in sorted(set(flat_old) | set(flat_new)):
            before, after = flat_old.get(path), flat_new.get(path)
            if before != after:
                name = ' · '.join(_path_label(p) for p in path) if path else label
                changes.append(AuditChange(name, _plain(before), _plain(after)))
        return changes
    if old == new:
        return []
    return [AuditChange(label, _plain(old), _plain(new))]


def _join_words(words: list[str]) -> str:
    if len(words) <= 1:
        return ''.join(words)
    return ', '.join(words[:-1]) + ' and ' + words[-1]


def describe(
    action: str,
    details: Any,
    names: AuditNames,
    *,
    actor_id: Optional[int] = None,
) -> AuditDescription:
    """Turn one audit row into what the report shows for it."""
    area = area_for(action)
    label = action_label(action)
    if not isinstance(details, dict):
        text = '' if details is None else str(details)
        return AuditDescription(label, area.label if area else None, '', _plain(text) if text else '',
                                [], [], [], text)

    raw = json.dumps({k: _maybe_json(v) for k, v in details.items()}, indent=2, sort_keys=True, default=str)
    shown = {k: v for k, v in details.items() if k not in HIDDEN_KEYS}
    used: set[str] = set()
    changes: list[AuditChange] = []
    changed_fields: list[str] = []
    no_op = False

    if 'old_value' in shown or 'new_value' in shown:
        what = field_label(str(shown['key'])) if 'key' in shown else 'Value'
        changes += _diff(what, shown.get('old_value'), shown.get('new_value'))
        no_op = not changes
        used |= {'old_value', 'new_value', 'key'}
    for key in list(shown):
        if key.startswith('previous_') and key[len('previous_'):] in shown:
            base = key[len('previous_'):]
            changes += _diff(field_label(base), _value(base, shown[key], names), _value(base, shown[base], names))
            used |= {key, base}
    for key in ('changed', 'changed_fields'):
        value = shown.get(key)
        if isinstance(value, dict):
            changes += [AuditChange(field_label(k), None, _plain(v)) for k, v in value.items()]
            used.add(key)
        elif isinstance(value, list):
            changed_fields = [field_label(str(v)).lower() for v in value]
            used.add(key)

    match_id = shown.get('match_id') if isinstance(shown.get('match_id'), int) else None
    subject = ''
    if match_id is not None:
        subject = f'Match #{match_id}'
    else:
        for key in ('target_user_id', 'user_id'):
            uid = shown.get(key)
            if isinstance(uid, int) and uid != actor_id:
                subject = names.users.get(uid, f'Member #{uid}')
                break
        if not subject:
            for key in _SUBJECT_NAME_KEYS:
                if isinstance(shown.get(key), str) and shown[key]:
                    subject = shown[key]
                    break
        if not subject and isinstance(shown.get('tournament_id'), int):
            subject = names.tournaments.get(shown['tournament_id'], '')
        if not subject and isinstance(shown.get('stage_id'), int):
            subject = names.stages.get(shown['stage_id'], '')

    facts = [
        (field_label(k), _value(k, v, names))
        for k, v in sorted(shown.items()) if k not in used
    ]

    parts = [subject] if subject else []
    if changes:
        if len(changes) == 1:
            c = changes[0]
            parts.append(f'{c.field}: {c.before} → {c.after}' if c.before is not None else f'{c.field}: {c.after}')
        else:
            parts.append(f'{len(changes)} changes: ' + _join_words([c.field for c in changes[:3]])
                         + ('…' if len(changes) > 3 else ''))
    elif no_op:
        what = field_label(str(shown['key'])) if 'key' in shown else 'Value'
        parts.append(f'{what}: saved unchanged')
    elif changed_fields:
        parts.append('Changed ' + _join_words(changed_fields))
    else:
        extras = [f'{k}: {v}' for k, v in facts if v != subject and not k.endswith(' ID')
                  and not v.startswith('#') and v not in ('(empty)', '(none)', '0')]
        parts += extras[:2]
    return AuditDescription(
        label=label,
        area=area.label if area else None,
        subject=subject,
        summary=' · '.join(parts),
        changes=changes,
        changed_fields=changed_fields,
        facts=facts,
        raw=raw,
        match_id=match_id,
    )
