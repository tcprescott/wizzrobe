"""Every notification Wizzrobe sends, who it goes to, and how by default.

The catalogue is code: a sender names its entry (``MATCH_SCHEDULED.to(PLAYERS)``)
and passes it to ``DiscordService.send_dm(notice=...)``, which looks up the
community's choice (Admin → Notifications) and sends it pinging, silent, or not
at all. A community stores only what it changed from these defaults.

Keys are stored in ``NotificationSetting.notice``, so renaming one drops the
overrides saved against it. Add an entry rather than reusing one for a new
message, and keep each sender pointed at the entry that describes it.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Mapping, Optional, Tuple

from models import NotificationMode, NotificationRecipient

OFF = NotificationMode.OFF
SILENT = NotificationMode.SILENT
PING = NotificationMode.PING

PLAYERS = NotificationRecipient.PLAYERS
CREW = NotificationRecipient.CREW
WATCHERS = NotificationRecipient.WATCHERS
FOLLOWERS = NotificationRecipient.FOLLOWERS
VOLUNTEERS = NotificationRecipient.VOLUNTEERS
ON_DUTY = NotificationRecipient.ON_DUTY
ADMINS = NotificationRecipient.ADMINS
STAFF = NotificationRecipient.STAFF
MEMBERS = NotificationRecipient.MEMBERS


@dataclass(frozen=True)
class NoticeTarget:
    """One notification as it goes to one kind of recipient."""

    key: str
    recipient: NotificationRecipient

    @property
    def default(self) -> NotificationMode:
        return CATALOG[self.key].defaults[self.recipient]


@dataclass(frozen=True)
class Notice:
    key: str
    group: str
    title: str
    when: str
    defaults: Mapping[NotificationRecipient, NotificationMode]
    # Who each recipient column means for this message, where the column name
    # alone would mislead ("Staff" is the on-duty desk for some, all staff for others).
    who: Mapping[NotificationRecipient, str]

    def to(self, recipient: NotificationRecipient) -> NoticeTarget:
        if recipient not in self.defaults:
            raise KeyError(f'{self.key} has no {recipient.value} recipient')
        return NoticeTarget(self.key, recipient)

    @property
    def section(self) -> str:
        """The subheading it sits under inside its group, or '' for a group with none."""
        return _SECTION_OF.get(self.key, '')

    @property
    def staff_recipients(self) -> Tuple[NotificationRecipient, ...]:
        return tuple(r for r in STAFF_RECIPIENTS if r in self.defaults)


GROUPS = (
    'Matches', 'Brackets', 'Reschedule requests', 'Crew', 'Volunteer shifts',
    'Community', 'Event check-in', 'Async qualifiers',
)

CATALOG: Dict[str, Notice] = {}


def _notice(
    key: str, group: str, title: str, when: str,
    defaults: Mapping[NotificationRecipient, NotificationMode],
    who: Optional[Mapping[NotificationRecipient, str]] = None,
) -> Notice:
    assert group in GROUPS and key not in CATALOG
    notice = Notice(key, group, title, when, dict(defaults), dict(who or {}))
    CATALOG[key] = notice
    return notice


_ON_SHIFT = "Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it"
_ON_SHIFT_ROSTER = "Whoever's on a shift with staff alerts on and can manage volunteers"
_TOURNAMENT_ADMINS = "The tournament's admins, unless they turned its alerts off"
_FALLBACK = 'Every staff member, only when nobody is on shift'
_OPTED_IN = 'Only people who turned it on in their Profile'


def _opted_in(who: str) -> str:
    return f'{who}, only if they turned it on in their Profile'

# Matches
MATCH_SCHEDULED = _notice(
    'match_scheduled', 'Matches', 'Match scheduled or rescheduled',
    'A match is booked, its time changes, or players are added to it',
    {PLAYERS: PING, CREW: PING, WATCHERS: SILENT, FOLLOWERS: SILENT},
    {PLAYERS: "Players who haven't acknowledged yet (with Acknowledge)",
     FOLLOWERS: 'People following the tournament, at their follow level'},
)
STREAM_CANDIDATE = _notice(
    'stream_candidate', 'Matches', 'Stream candidate',
    'A match without a stage is flagged as a possible stream',
    {FOLLOWERS: PING},
    {FOLLOWERS: 'Followers on "Streamed, plus possible streams"'},
)
MATCH_CHECKED_IN = _notice(
    'match_checked_in', 'Matches', 'Match checked in',
    'A proctor or staff member checks the match in',
    {PLAYERS: PING, CREW: PING, WATCHERS: PING},
)
MATCH_STARTED = _notice(
    'match_started', 'Matches', 'Match started', 'The match is started',
    {PLAYERS: OFF, CREW: SILENT, WATCHERS: SILENT},
)
MATCH_FINISHED = _notice(
    'match_finished', 'Matches', 'Match finished', 'The match is finished',
    {PLAYERS: OFF, CREW: SILENT, WATCHERS: SILENT},
)
MATCH_CONFIRMED = _notice(
    'match_confirmed', 'Matches', 'Result confirmed', 'Staff confirm the result',
    {PLAYERS: SILENT, CREW: SILENT, WATCHERS: SILENT},
)
MATCH_FORFEITED = _notice(
    'match_forfeited', 'Matches', 'Match forfeited',
    'Staff decide a match by forfeit: a no-show, a withdrawal or a DQ',
    {PLAYERS: PING, CREW: PING, WATCHERS: SILENT},
)
MATCH_RESULT = _notice(
    'match_result', 'Matches', 'Match result',
    'Staff confirm the result, with who placed where',
    {PLAYERS: PING, FOLLOWERS: PING},
    {PLAYERS: _opted_in('Every player in the match'),
     FOLLOWERS: _opted_in('People following the tournament, at their follow level')},
)
SEED_READY = _notice(
    'seed_ready', 'Matches', 'Seed ready', "The match's seed is rolled",
    {PLAYERS: PING},
)
SEED_FAILED = _notice(
    'seed_failed', 'Matches', "Seed didn't roll",
    'Rolling a seed in the background fails',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _opted_in(_ON_SHIFT), ADMINS: _opted_in(_TOURNAMENT_ADMINS), STAFF: _opted_in(_FALLBACK)},
)
STATION_ASSIGNED = _notice(
    'station_assigned', 'Matches', 'Station assigned',
    "Staff set or change a match's stations", {PLAYERS: PING}, {PLAYERS: _OPTED_IN},
)
STAGE_CALL = _notice(
    'stage_call', 'Matches', 'On stage', "A match's stage is set or changed",
    {PLAYERS: PING, CREW: PING, WATCHERS: SILENT},
)
STAGE_CLEARED = _notice(
    'stage_cleared', 'Matches', 'Stage call cancelled',
    "A match's stage is cleared, so it's back in the tournament room",
    {PLAYERS: PING, CREW: PING, WATCHERS: SILENT},
)
STAGE_REMINDER = _notice(
    'stage_reminder', 'Matches', 'Stage match coming up',
    "Before a stage match, at the tournament's reminder lead",
    {PLAYERS: PING, CREW: PING, WATCHERS: SILENT},
)
MATCH_CANCELLED = _notice(
    'match_cancelled', 'Matches', 'Match cancelled',
    'Staff cancel the match (deleting one sends nothing)',
    {PLAYERS: PING, CREW: PING, WATCHERS: PING},
)
MATCH_CANCELLED_ON_REQUEST = _notice(
    'match_cancelled_on_request', 'Matches', 'Match cancelled on request',
    "Staff approve a player's cancel request",
    {PLAYERS: PING, CREW: PING, WATCHERS: PING},
)
SERIES_GAME_CANCELLED = _notice(
    'series_game_cancelled', 'Matches', 'Leftover series game cancelled',
    'A best-of series is decided early and its booked games are called off',
    {PLAYERS: PING, CREW: PING, WATCHERS: PING},
)
REMOVED_FROM_MATCH = _notice(
    'removed_from_match', 'Matches', 'Removed from a match',
    'Staff take a player off a match', {PLAYERS: PING},
)
RACE_ROOM_OPEN = _notice(
    'race_room_open', 'Matches', 'Race room open',
    "The match's racetime.gg room opens", {PLAYERS: PING},
)
STAFF_COMMENT = _notice(
    'staff_comment', 'Matches', 'Staff comment on a match',
    'Staff post a "Message players" comment',
    {PLAYERS: PING},
    {PLAYERS: 'Players with "Staff comments on my matches" on'},
)
PLAYER_COMMENT = _notice(
    'player_comment', 'Matches', 'Player comment on a match',
    'A player comments on their match',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _ON_SHIFT, ADMINS: _TOURNAMENT_ADMINS, STAFF: _FALLBACK},
)
PLAYER_LATE = _notice(
    'player_late', 'Matches', 'Player running late',
    'A player taps Running late on their match',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _ON_SHIFT, ADMINS: _TOURNAMENT_ADMINS, STAFF: _FALLBACK},
)
OPPONENT_ACKNOWLEDGED = _notice(
    'opponent_acknowledged', 'Matches', 'Opponent acknowledged',
    'Another player in your match acknowledges its time', {PLAYERS: PING}, {PLAYERS: _OPTED_IN},
)
MATCH_FLAGGED = _notice(
    'match_flagged', 'Matches', 'Match flagged for review',
    'Someone flags a match for staff review',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _opted_in(_ON_SHIFT), ADMINS: _opted_in(_TOURNAMENT_ADMINS), STAFF: _opted_in(_FALLBACK)},
)
STREAM_OFFER = _notice(
    'stream_offer', 'Matches', 'Match offered for stream',
    'Crew offer to stream a match',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _opted_in("Whoever's on a shift with staff alerts on and can schedule streams"),
     ADMINS: _opted_in("The tournament's admins and Broadcast Techs"), STAFF: _opted_in(_FALLBACK)},
)
HARDER_SETTINGS_OFFER = _notice(
    'harder_settings_offer', 'Matches', 'Harder settings available',
    'A match is scheduled in a tournament with a harder preset', {PLAYERS: PING},
)
HARDER_SETTINGS_AGREED = _notice(
    'harder_settings_agreed', 'Matches', 'Harder settings agreed',
    'Every player opts in to the harder preset', {PLAYERS: PING},
)
HARDER_SETTINGS_DROPPED = _notice(
    'harder_settings_dropped', 'Matches', 'Back to the standard settings',
    'A player backs out or the roster changes, so the harder preset is off', {PLAYERS: PING},
)
SETTINGS_SET_BY_STAFF = _notice(
    'settings_set_by_staff', 'Matches', 'Settings set by staff',
    "Staff force the match's preset", {PLAYERS: PING},
)
SETTINGS_RELEASED = _notice(
    'settings_released', 'Matches', 'Settings back in players\' hands',
    'Staff release a forced preset, so players choose again', {PLAYERS: PING},
)

# Brackets
MATCHUP_READY = _notice(
    'matchup_ready', 'Brackets', 'Matchup ready to schedule',
    'A bracket matchup becomes bookable', {PLAYERS: SILENT},
)
MATCHUP_REOPENED = _notice(
    'matchup_reopened', 'Brackets', 'Matchup open again',
    "A booked bracket game is called off and can be booked again", {PLAYERS: SILENT},
)
SIGNED_UP = _notice(
    'signed_up', 'Brackets', 'Signed up for a tournament',
    'A player signs themselves up', {PLAYERS: PING},
)
BRACKET_STARTED = _notice(
    'bracket_started', 'Brackets', 'Bracket started',
    "A tournament's bracket starts, in Wizzrobe or on Challonge",
    {FOLLOWERS: PING}, {FOLLOWERS: _opted_in('People following the tournament')},
)
ADDED_TO_TOURNAMENT = _notice(
    'added_to_tournament', 'Brackets', 'Added to a tournament',
    'Staff add a player to a tournament', {PLAYERS: PING}, {PLAYERS: _OPTED_IN},
)
REMOVED_FROM_TOURNAMENT = _notice(
    'removed_from_tournament', 'Brackets', 'Removed from a tournament',
    'Staff take a player out of a tournament', {PLAYERS: PING}, {PLAYERS: _OPTED_IN},
)
DROPPED_FROM_TOURNAMENT = _notice(
    'dropped_from_tournament', 'Brackets', 'Dropped from a tournament',
    'Staff drop a player, forfeiting or calling off their open matches', {PLAYERS: PING},
)
PLAYER_WITHDREW = _notice(
    'player_withdrew', 'Brackets', 'Player withdrew',
    'A player takes themselves out of a tournament',
    {ADMINS: PING}, {ADMINS: _opted_in(_TOURNAMENT_ADMINS)},
)

# Reschedule requests
RESCHEDULE_REQUEST = _notice(
    'reschedule_request', 'Reschedule requests', 'Reschedule or cancel request',
    'A player asks to move or cancel a match',
    {PLAYERS: PING, ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {PLAYERS: 'The opponent, asked to agree (head-to-head only)',
     ON_DUTY: _ON_SHIFT, ADMINS: _TOURNAMENT_ADMINS, STAFF: _FALLBACK},
)
RESCHEDULE_DECLINED = _notice(
    'reschedule_declined', 'Reschedule requests', 'Request declined',
    'Staff decline a reschedule or cancel request',
    {PLAYERS: PING}, {PLAYERS: 'The player who asked'},
)
CANCEL_APPROVED = _notice(
    'cancel_approved', 'Reschedule requests', 'Cancel request approved',
    "Staff approve a cancel request (an approved move sends only the match's own message)",
    {PLAYERS: PING}, {PLAYERS: 'The player who asked'},
)
REQUEST_WITHDRAWN = _notice(
    'request_withdrawn', 'Reschedule requests', 'Request withdrawn',
    'A player withdraws a reschedule or cancel request',
    {PLAYERS: PING}, {PLAYERS: _opted_in('The opponent who was asked')},
)

# Crew
_CREW_OWNERS = "The tournament's admins and crew coordinators"
# Split by role so a community can hear about commentary and let tracking stay
# quiet. Silent by default: a busy event sends a lot of these and none is urgent.
COMMENTATOR_SIGNUP = _notice(
    'commentator_signup', 'Crew', 'New commentator signup',
    'Someone signs up to commentate', {ADMINS: SILENT}, {ADMINS: _CREW_OWNERS},
)
TRACKER_SIGNUP = _notice(
    'tracker_signup', 'Crew', 'New tracker signup',
    'Someone signs up to track', {ADMINS: SILENT}, {ADMINS: _CREW_OWNERS},
)
CREW_ASSIGNED = _notice(
    'crew_assigned', 'Crew', 'Crew assignment',
    'A signup is approved, or staff add crew directly', {CREW: PING},
)
CREW_UNASSIGNED = _notice(
    'crew_unassigned', 'Crew', 'Crew assignment withdrawn',
    'Approval is removed, or staff drop approved crew', {CREW: PING},
)
_CREW_DROPPED = "The tournament's admins, crew coordinators and whoever approved them"
COMMENTATOR_WITHDREW = _notice(
    'commentator_withdrew', 'Crew', 'Commentator withdrew',
    'An approved commentator drops out', {ADMINS: PING}, {ADMINS: _CREW_DROPPED},
)
TRACKER_WITHDREW = _notice(
    'tracker_withdrew', 'Crew', 'Tracker withdrew',
    'An approved tracker drops out', {ADMINS: PING}, {ADMINS: _CREW_DROPPED},
)

# Volunteer shifts
SHIFT_ASSIGNED = _notice(
    'shift_assigned', 'Volunteer shifts', 'Shift assigned',
    'A coordinator assigns a shift or publishes a draft', {VOLUNTEERS: PING},
)
SHIFT_MOVED = _notice(
    'shift_moved', 'Volunteer shifts', 'Shift moved', "A shift's time changes",
    {VOLUNTEERS: PING},
)
SHIFT_REMOVED = _notice(
    'shift_removed', 'Volunteer shifts', 'Shift removed',
    'A volunteer is unassigned or the shift is deleted', {VOLUNTEERS: PING},
)
SHIFT_REMINDER = _notice(
    'shift_reminder', 'Volunteer shifts', 'Shift reminder',
    "Before a shift, at the community's reminder lead", {VOLUNTEERS: PING},
)
SHIFT_STARTING = _notice(
    'shift_starting', 'Volunteer shifts', 'Shift starting (I\'m here / Running late)',
    'At shift start, if the volunteer has not checked in', {VOLUNTEERS: PING},
)
SHIFT_GIVEN_BACK = _notice(
    'shift_given_back', 'Volunteer shifts', 'Volunteer gave a shift back',
    'A volunteer gives a shift back',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _ON_SHIFT_ROSTER, ADMINS: 'Volunteer coordinators', STAFF: _FALLBACK},
)
VOLUNTEER_LATE = _notice(
    'volunteer_late', 'Volunteer shifts', 'Volunteer running late',
    'A volunteer taps Running late',
    {ON_DUTY: PING, ADMINS: PING, STAFF: PING},
    {ON_DUTY: _ON_SHIFT_ROSTER, ADMINS: 'Volunteer coordinators', STAFF: _FALLBACK},
)
VOLUNTEER_ARRIVED = _notice(
    'volunteer_arrived', 'Volunteer shifts', 'Volunteer arrived',
    "A volunteer taps I'm here for their shift",
    {ON_DUTY: SILENT, ADMINS: SILENT, STAFF: SILENT},
    {ON_DUTY: _opted_in(_ON_SHIFT_ROSTER), ADMINS: _opted_in('Volunteer coordinators'), STAFF: _opted_in(_FALLBACK)},
)

# Community
JOIN_REQUEST = _notice(
    'join_request', 'Community', 'Join request', 'Someone asks to join',
    {STAFF: PING}, {STAFF: 'All staff'},
)
JOIN_APPROVED = _notice(
    'join_approved', 'Community', 'Join request approved',
    'Staff approve a join request', {MEMBERS: PING}, {MEMBERS: 'The person who asked'},
)
JOIN_DECLINED = _notice(
    'join_declined', 'Community', 'Join request declined',
    'Staff decline a join request', {MEMBERS: PING}, {MEMBERS: 'The person who asked'},
)
WELCOME = _notice(
    'welcome', 'Community', 'Welcome in',
    'Staff add someone, or a role grant closes their pending request', {MEMBERS: PING},
)
MEMBERSHIP_REMOVED = _notice(
    'membership_removed', 'Community', 'Membership removed',
    'Staff remove a member', {MEMBERS: PING},
)
ADA_REQUEST = _notice(
    'ada_request', 'Community', 'ADA request', 'A member opens one',
    {STAFF: PING}, {STAFF: 'All staff'},
)
ADA_CHANGED = _notice(
    'ada_changed', 'Community', 'ADA request changed',
    'A member edits or reopens their request', {STAFF: PING}, {STAFF: 'All staff'},
)
EVENT_TO_REVIEW = _notice(
    'community_event_review', 'Community', 'Community event to review',
    'A member submits or resubmits an event', {STAFF: PING}, {STAFF: 'All staff'},
)
EVENT_APPROVED = _notice(
    'community_event_approved', 'Community', 'Community event approved',
    'Staff approve an event', {MEMBERS: PING}, {MEMBERS: 'The host'},
)
EVENT_POSTED_FOR_YOU = _notice(
    'community_event_posted_for_you', 'Community', 'Community event posted for you',
    'Staff post an event with a member as its host, or hand one over',
    {MEMBERS: PING}, {MEMBERS: 'The new host'},
)
EVENT_LISTED = _notice(
    'community_event_listed', 'Community', 'New community event',
    'Staff list a new community event', {MEMBERS: PING},
    {MEMBERS: _opted_in('Every member but the host')},
)
EVENT_REJECTED = _notice(
    'community_event_rejected', 'Community', 'Community event not listed',
    'Staff turn an event down, or take a listed one down',
    {MEMBERS: PING}, {MEMBERS: 'The host'},
)
EVENT_MOVED = _notice(
    'community_event_moved', 'Community', 'Community event moved',
    "An approved event's time changes",
    {FOLLOWERS: PING}, {FOLLOWERS: "The event's subscribers"},
)
EVENT_CANCELLED = _notice(
    'community_event_cancelled', 'Community', 'Community event cancelled',
    'An approved event is called off',
    {FOLLOWERS: PING}, {FOLLOWERS: "The event's subscribers"},
)
EVENT_REMINDER = _notice(
    'community_event_reminder', 'Community', 'Community event reminder',
    "Before an event, at the community's reminder lead",
    {FOLLOWERS: PING}, {FOLLOWERS: "The event's subscribers"},
)

# Event check-in
CHECKED_IN = _notice(
    'checked_in', 'Event check-in', "You're checked in",
    'The desk checks someone in (the desk also sees whether it arrived)',
    {MEMBERS: PING}, {MEMBERS: 'The attendee'},
)
MATCHERINO_REFUSED = _notice(
    'matcherino_refused', 'Event check-in', 'Matcherino login refused',
    'The roster sync is refused by Matcherino',
    {STAFF: PING}, {STAFF: 'The staff member who saved the login'},
)

# Async qualifiers
RUN_APPROVED = _notice(
    'run_approved', 'Async qualifiers', 'Run approved',
    'A reviewer approves a run, or overturns a rejection',
    {PLAYERS: PING}, {PLAYERS: 'The runner'},
)
RUN_REJECTED = _notice(
    'run_rejected', 'Async qualifiers', 'Run rejected',
    'A reviewer rejects a run, or overturns an approval',
    {PLAYERS: PING}, {PLAYERS: 'The runner'},
)
REATTEMPT_GRANTED = _notice(
    'reattempt_granted', 'Async qualifiers', 'Re-attempt granted',
    "A reviewer frees the runner's slot", {PLAYERS: PING}, {PLAYERS: 'The runner'},
)
RUN_EXPIRING = _notice(
    'run_expiring', 'Async qualifiers', 'Run expiring',
    'An hour before an open run is forfeited', {PLAYERS: PING}, {PLAYERS: 'The runner'},
)
RUN_EXPIRED = _notice(
    'run_expired', 'Async qualifiers', 'Run expired',
    'An open run is forfeited', {PLAYERS: PING}, {PLAYERS: 'The runner'},
)
QUALIFIER_OPENED = _notice(
    'qualifier_opened', 'Async qualifiers', 'Qualifier open',
    "An async qualifier's window opens", {MEMBERS: PING}, {MEMBERS: _OPTED_IN},
)
QUALIFIER_CLOSED = _notice(
    'qualifier_closed', 'Async qualifiers', 'Qualifier closed',
    "An async qualifier's window closes", {PLAYERS: PING}, {PLAYERS: _opted_in('Everyone who submitted a run')},
)
REVIEW_QUEUE = _notice(
    'review_queue', 'Async qualifiers', 'Review queue waiting',
    'The oldest unreviewed run is six hours old (at most daily)',
    {ADMINS: PING}, {ADMINS: 'Qualifier admins'},
)


# Subheadings inside the bigger groups, in display order. A group missing here
# shows its messages under the group heading alone.
SECTIONS: Dict[str, Tuple[Tuple[str, Tuple[Notice, ...]], ...]] = {
    'Matches': (
        ('Booking', (
            MATCH_SCHEDULED, OPPONENT_ACKNOWLEDGED, STREAM_CANDIDATE, MATCH_CANCELLED,
            MATCH_CANCELLED_ON_REQUEST, SERIES_GAME_CANCELLED, REMOVED_FROM_MATCH,
        )),
        ('Match day', (
            MATCH_CHECKED_IN, SEED_READY, STATION_ASSIGNED, RACE_ROOM_OPEN, STAGE_CALL,
            STAGE_CLEARED, STAGE_REMINDER, MATCH_STARTED, MATCH_FINISHED,
        )),
        ('Results', (MATCH_CONFIRMED, MATCH_FORFEITED, MATCH_RESULT)),
        ('Comments and staff alerts', (
            STAFF_COMMENT, PLAYER_COMMENT, PLAYER_LATE, MATCH_FLAGGED, STREAM_OFFER, SEED_FAILED,
        )),
        ('Seed settings', (
            HARDER_SETTINGS_OFFER, HARDER_SETTINGS_AGREED, HARDER_SETTINGS_DROPPED,
            SETTINGS_SET_BY_STAFF, SETTINGS_RELEASED,
        )),
    ),
    'Volunteer shifts': (
        ('Your shifts', (SHIFT_ASSIGNED, SHIFT_MOVED, SHIFT_REMOVED, SHIFT_REMINDER, SHIFT_STARTING)),
        ('Staff alerts', (SHIFT_GIVEN_BACK, VOLUNTEER_LATE, VOLUNTEER_ARRIVED)),
    ),
    'Community': (
        ('Membership', (JOIN_REQUEST, JOIN_APPROVED, JOIN_DECLINED, WELCOME, MEMBERSHIP_REMOVED)),
        ('ADA', (ADA_REQUEST, ADA_CHANGED)),
        ('Community events', (
            EVENT_TO_REVIEW, EVENT_APPROVED, EVENT_POSTED_FOR_YOU, EVENT_LISTED, EVENT_REJECTED, EVENT_MOVED,
            EVENT_CANCELLED, EVENT_REMINDER,
        )),
    ),
}
_SECTION_OF: Dict[str, str] = {
    n.key: name for sections in SECTIONS.values() for name, members in sections for n in members
}
_SECTION_ORDER: Dict[str, int] = {
    n.key: i
    for sections in SECTIONS.values()
    for i, n in enumerate(n for _, members in sections for n in members)
}


def notices() -> List[Notice]:
    """The catalogue in display order: by group, then section, then as declared."""
    declared = {key: i for i, key in enumerate(CATALOG)}
    return sorted(
        CATALOG.values(),
        key=lambda n: (GROUPS.index(n.group), _SECTION_ORDER.get(n.key, declared[n.key])),
    )


# The staff columns staff set together from one "Staff" dropdown on the settings
# page. The per-recipient settings stay the stored truth; a choice writes all of
# a message's staff recipients at once, and a mix that matches no choice reads
# back as custom.
STAFF_RECIPIENTS = (ON_DUTY, ADMINS, STAFF)


class StaffChoice(str, Enum):
    PING_ALL = 'ping_all'
    PING_ON_DUTY = 'ping_on_duty'
    SILENT = 'silent'
    OFF = 'off'

    @property
    def label(self) -> str:
        return {
            'ping_all': 'Ping all', 'ping_on_duty': 'Ping on-duty',
            'silent': 'Silent', 'off': 'Off',
        }[self.value]


def staff_modes(notice: Notice, choice: StaffChoice) -> Dict[NotificationRecipient, NotificationMode]:
    """What ``choice`` sets each of the message's staff recipients to.

    Ping on-duty drops the tournament admins but keeps the all-staff fallback, so
    a message still reaches someone when nobody is on shift. It only exists for a
    message with an on-shift recipient.
    """
    recipients = notice.staff_recipients
    if not recipients:
        raise ValueError(f'{notice.title} has no staff recipients.')
    if choice is StaffChoice.PING_ON_DUTY:
        if ON_DUTY not in recipients:
            raise ValueError(f'{notice.title} has no on-shift recipient.')
        return {r: OFF if r is ADMINS else PING for r in recipients}
    mode = {StaffChoice.PING_ALL: PING, StaffChoice.SILENT: SILENT, StaffChoice.OFF: OFF}[choice]
    return {r: mode for r in recipients}


def staff_choices(notice: Notice) -> List[StaffChoice]:
    """The choices the Staff dropdown offers for ``notice``."""
    if not notice.staff_recipients:
        return []
    return [c for c in StaffChoice if c is not StaffChoice.PING_ON_DUTY or ON_DUTY in notice.staff_recipients]


def staff_choice_for(
    notice: Notice, modes: Mapping[NotificationRecipient, NotificationMode],
) -> Optional[StaffChoice]:
    """The choice the message's staff recipients add up to, or None for a custom mix."""
    current = {r: modes[r] for r in notice.staff_recipients}
    for choice in staff_choices(notice):
        if staff_modes(notice, choice) == current:
            return choice
    return None


# What each message asks of the person who gets it, for the presets on Profile →
# Notifications. "Urgent" is time-critical (go to the stage, the room is open);
# "act" asks for something (acknowledge, answer, pick a time). Everything else is
# news. A target in neither set is news.
_URGENT: Dict[str, frozenset] = {
    MATCH_CHECKED_IN.key: frozenset({PLAYERS, CREW}),
    SEED_READY.key: frozenset({PLAYERS}),
    STAGE_CALL.key: frozenset({PLAYERS, CREW}),
    STAGE_CLEARED.key: frozenset({PLAYERS, CREW}),
    STAGE_REMINDER.key: frozenset({PLAYERS, CREW}),
    MATCH_CANCELLED.key: frozenset({PLAYERS, CREW}),
    MATCH_FORFEITED.key: frozenset({CREW}),
    MATCH_CANCELLED_ON_REQUEST.key: frozenset({PLAYERS, CREW}),
    SERIES_GAME_CANCELLED.key: frozenset({PLAYERS, CREW}),
    REMOVED_FROM_MATCH.key: frozenset({PLAYERS}),
    RACE_ROOM_OPEN.key: frozenset({PLAYERS}),
    SHIFT_REMINDER.key: frozenset({VOLUNTEERS}),
    SHIFT_STARTING.key: frozenset({VOLUNTEERS}),
    RUN_EXPIRING.key: frozenset({PLAYERS}),
}
_STAFF_ALERTS = frozenset({ON_DUTY, ADMINS, STAFF})
_ACT: Dict[str, frozenset] = {
    MATCH_SCHEDULED.key: frozenset({PLAYERS, CREW}),
    STAFF_COMMENT.key: frozenset({PLAYERS}),
    PLAYER_COMMENT.key: _STAFF_ALERTS,
    PLAYER_LATE.key: _STAFF_ALERTS,
    HARDER_SETTINGS_OFFER.key: frozenset({PLAYERS}),
    SETTINGS_RELEASED.key: frozenset({PLAYERS}),
    MATCHUP_READY.key: frozenset({PLAYERS}),
    MATCHUP_REOPENED.key: frozenset({PLAYERS}),
    RESCHEDULE_REQUEST.key: frozenset({PLAYERS}) | _STAFF_ALERTS,
    RESCHEDULE_DECLINED.key: frozenset({PLAYERS}),
    COMMENTATOR_SIGNUP.key: frozenset({ADMINS}),
    TRACKER_SIGNUP.key: frozenset({ADMINS}),
    CREW_ASSIGNED.key: frozenset({CREW}),
    CREW_UNASSIGNED.key: frozenset({CREW}),
    COMMENTATOR_WITHDREW.key: frozenset({ADMINS}),
    TRACKER_WITHDREW.key: frozenset({ADMINS}),
    SHIFT_ASSIGNED.key: frozenset({VOLUNTEERS}),
    SHIFT_MOVED.key: frozenset({VOLUNTEERS}),
    SHIFT_REMOVED.key: frozenset({VOLUNTEERS}),
    SHIFT_GIVEN_BACK.key: _STAFF_ALERTS,
    VOLUNTEER_LATE.key: _STAFF_ALERTS,
    JOIN_REQUEST.key: frozenset({STAFF}),
    ADA_REQUEST.key: frozenset({STAFF}),
    ADA_CHANGED.key: frozenset({STAFF}),
    EVENT_TO_REVIEW.key: frozenset({STAFF}),
    EVENT_REJECTED.key: frozenset({MEMBERS}),
    EVENT_CANCELLED.key: frozenset({FOLLOWERS}),
    EVENT_REMINDER.key: frozenset({FOLLOWERS}),
    MATCHERINO_REFUSED.key: frozenset({STAFF}),
    RUN_REJECTED.key: frozenset({PLAYERS}),
    REATTEMPT_GRANTED.key: frozenset({PLAYERS}),
    REVIEW_QUEUE.key: frozenset({ADMINS}),
}


# Messages nobody gets until they turn them on under Profile → Notifications.
# The community column still holds the ceiling (staff can switch one off for
# everybody), but every preset leaves an opt-in target off, so adding one never
# changes what anybody already receives.
_OPT_IN: Dict[str, frozenset] = {
    SEED_FAILED.key: _STAFF_ALERTS,
    STATION_ASSIGNED.key: frozenset({PLAYERS}),
    OPPONENT_ACKNOWLEDGED.key: frozenset({PLAYERS}),
    MATCH_FLAGGED.key: _STAFF_ALERTS,
    STREAM_OFFER.key: _STAFF_ALERTS,
    MATCH_RESULT.key: frozenset({PLAYERS, FOLLOWERS}),
    BRACKET_STARTED.key: frozenset({FOLLOWERS}),
    EVENT_LISTED.key: frozenset({MEMBERS}),
    ADDED_TO_TOURNAMENT.key: frozenset({PLAYERS}),
    REMOVED_FROM_TOURNAMENT.key: frozenset({PLAYERS}),
    PLAYER_WITHDREW.key: frozenset({ADMINS}),
    REQUEST_WITHDRAWN.key: frozenset({PLAYERS}),
    VOLUNTEER_ARRIVED.key: _STAFF_ALERTS,
    QUALIFIER_OPENED.key: frozenset({MEMBERS}),
    QUALIFIER_CLOSED.key: frozenset({PLAYERS}),
}


def is_opt_in(target: NoticeTarget) -> bool:
    return target.recipient in _OPT_IN.get(target.key, frozenset())


def is_urgent(target: NoticeTarget) -> bool:
    return target.recipient in _URGENT.get(target.key, frozenset())


def needs_action(target: NoticeTarget) -> bool:
    return is_urgent(target) or target.recipient in _ACT.get(target.key, frozenset())


def targets() -> List[NoticeTarget]:
    """Every (message, recipient) pair, in display order."""
    return [n.to(r) for n in notices() for r in n.defaults]
