import re
from enum import Enum


class Role(str, Enum):
    STAFF = 'staff'
    PROCTOR = 'proctor'
    STREAM_MANAGER = 'stream_manager'
    TRIFORCE_SUBMITTER = 'triforce_submitter'
    VOLUNTEER_COORDINATOR = 'volunteer_coordinator'
    EQUIPMENT_MANAGER = 'equipment_manager'
    VOLUNTEER = 'volunteer'
    # Works the event check-in desk: checks people in, links registrants to
    # accounts, runs a sync. Walk-ups and event setup stay STAFF-only.
    CHECK_IN_DESK = 'check_in_desk'
    # Online-tournament admin surfaces (see docs/online-tournaments). Each gates a
    # new subsystem's management UI/worker actions the way STAFF does the rest.
    PRESET_MANAGER = 'preset_manager'
    SYNC_ADMIN = 'sync_admin'
    QUALIFIER_ADMIN = 'qualifier_admin'
    # Global platform role: manages tenants on the /platform surface. Its
    # UserRole rows carry tenant=NULL (the only role that may) and stay visible
    # inside any tenant request. Not grantable per-tenant.
    SUPER_ADMIN = 'super_admin'

    @property
    def label(self) -> str:
        """The name people read. Differs from the value only where a role was
        renamed in the UI without migrating stored grants and Discord mappings."""
        return _ROLE_LABELS.get(self, self.value.replace('_', ' ').title())

    @classmethod
    def tenant_grantable(cls) -> 'list[Role]':
        """The roles a community may hand out, in declaration order.

        Everything except ``SUPER_ADMIN``. That one is a *platform* role — its
        ``UserRole`` row carries ``tenant=NULL`` and bypasses every per-tenant
        gate — so a community that could grant it could mint an account with
        authority over every other community on the platform. It is granted on
        ``/platform`` by an existing super-admin, through
        ``TenantService.grant_super_admin``.

        The one list every per-tenant role surface reads: the Users dialog's
        picker, the Discord role-mapping picker, and the service gates behind
        both. A new global role added here is excluded from all of them at once
        rather than in three places someone has to remember.
        """
        return [role for role in cls if role is not cls.SUPER_ADMIN]

    @classmethod
    def volunteer_mappable(cls) -> 'list[Role]':
        """The roles a volunteer position may confer, in declaration order.

        Narrower than ``tenant_grantable`` because a Volunteer Coordinator, not
        staff, decides who sits on a shift. Mapping a position to STAFF or any
        role that administers a subsystem would let a coordinator hand that
        authority to anyone, themselves included, by assigning a shift.
        """
        return [
            cls.PROCTOR, cls.STREAM_MANAGER, cls.TRIFORCE_SUBMITTER,
            cls.EQUIPMENT_MANAGER, cls.VOLUNTEER, cls.CHECK_IN_DESK,
        ]


_ROLE_LABELS: dict[Role, str] = {
    Role.STREAM_MANAGER: 'Broadcast Tech',
}


class TournamentGrant(str, Enum):
    """Per-tournament authority a Discord role can confer.

    Deliberately **not** ``Role`` members: these grant a row on
    ``Tournament.admins`` / ``Tournament.crew_coordinators`` for one tournament,
    where a ``Role`` grants tenant-wide authority through ``UserRole``. A guild
    role is guild-wide, so a mapping that names one of these must also name the
    tournament it lands on.
    """

    TOURNAMENT_ADMIN = 'tournament_admin'
    CREW_COORDINATOR = 'crew_coordinator'


# Sentinel ``discord_id`` for the reserved system :class:`User` that automation
# (workers, racetime/Discord bot handlers, ETL, qualifier scoring) acts as. A
# real snowflake is always a large positive integer, so ``0`` can never collide
# with a genuine Discord account. The row is marked ``is_system`` and resolved
# via ``UserService.get_system_user()``.
SYSTEM_USER_DISCORD_ID = 0


class FeatureFlag(str, Enum):
    """Per-tenant feature flags — one member per deliberately-gated subsystem.

    A flag exists ONLY when a feature is intentionally gated; this is not a
    per-feature switch for the whole app. Flags default OFF and are governed
    two-tier (super-admin availability + tenant enable). The human copy and
    grouping for each flag live in :mod:`application.feature_flags`.

    ``(str, Enum)`` — the ``.value`` is the stable key persisted on
    ``TenantFeatureFlag.flag``, so renaming a value is a data migration.
    """

    ASYNC_QUALIFIERS = 'async_qualifiers'
    RACETIME_ROOMS = 'racetime_rooms'
    SPEEDGAMING_ETL = 'speedgaming_etl'
    CHALLONGE = 'challonge'
    EQUIPMENT = 'equipment'
    VOLUNTEERS = 'volunteers'
    TRIFORCE_TEXTS = 'triforce_texts'
    BRACKETS = 'brackets'
    FEEDBACK = 'feedback'
    EVENT_INFO = 'event_info'
    PAYOUTS = 'payouts'
    ADA_ACCOMMODATIONS = 'ada_accommodations'
    EVENT_CHECK_IN = 'event_check_in'
    COMMUNITY_EVENTS = 'community_events'
    EMERGENCY_CONTACTS = 'emergency_contacts'
    NOTIFICATION_CHANNELS = 'notification_channels'
    STARTGG = 'startgg'
    THIRD_PARTY_APPS = 'third_party_apps'


class BracketFormat(str, Enum):
    """The pairing/progression format of a single bracket stage.

    Selected per stage (``Bracket.format``) and resolved to a pairing engine
    through the ``('bracket_format', …)`` strategy registry
    (see docs/features/brackets.md). ``(str, Enum)`` (not ``StrEnum``) — render
    ``.value`` in f-strings, never the bare member.
    """

    SINGLE_ELIM = 'single_elim'
    DOUBLE_ELIM = 'double_elim'
    SWISS = 'swiss'
    ROUND_ROBIN = 'round_robin'


class BracketState(str, Enum):
    """Lifecycle of a bracket stage.

    ``DRAFT`` while entrants are enrolled/seeded (the engine has not run yet);
    ``ACTIVE`` once ``start`` generates and persists the match graph;
    ``COMPLETE`` once every match is resolved and ``final_rank`` is written.
    ``CANCELLED`` is the close-out for a stage that was started and then
    abandoned — without it the only way to finish such a stage is to fabricate a
    winner for every remaining match. It is terminal like ``COMPLETE`` but writes
    no ``final_rank``, so nothing downstream can draw a field from it.
    """

    DRAFT = 'draft'
    ACTIVE = 'active'
    COMPLETE = 'complete'
    CANCELLED = 'cancelled'


class BracketMatchState(str, Enum):
    """State of a single persisted bracket match slot.

    Deliberately parallels :class:`ChallongeMatchState` — a bracket match is
    ``PENDING`` until both entries are known, ``OPEN`` once it can be played and
    scheduled into a ``Match``, ``COMPLETE`` once a winner is recorded.
    """

    PENDING = 'pending'   # one or both entries not yet determined
    OPEN = 'open'         # both entries known; playable / schedulable
    COMPLETE = 'complete' # winner recorded


class BracketMatchGameState(str, Enum):
    """State of one game within a best-of-N series.

    There is no ``PENDING``: a :class:`BracketMatchGame` row is created only when
    the game is scheduled into a ``Match``, so it enters at ``SCHEDULED``. A game
    the series never needed (the loser of a 2-0 Bo3) ends ``CANCELLED`` rather
    than being deleted, which keeps ``game_number`` stable and records why it was
    never played.
    """

    SCHEDULED = 'scheduled' # linked to a Match, not yet decided
    COMPLETE = 'complete'   # winner recorded
    CANCELLED = 'cancelled' # never played (series clinched, or staff override)


class BracketEntrantStatus(str, Enum):
    """Status of a tournament-level bracket entrant (across all stages)."""

    ACTIVE = 'active'
    DROPPED = 'dropped'


class BracketEntryStatus(str, Enum):
    """Status of an entrant's participation within one stage."""

    ACTIVE = 'active'
    DROPPED = 'dropped'
    ELIMINATED = 'eliminated'


class ApiTokenOrigin(str, Enum):
    """How an :class:`~models.ApiToken` was issued, and therefore where it works.

    ``PAT`` tokens are created on the profile page, carry a ``tenant``, and are
    accepted only by the REST API. ``OAUTH`` tokens are minted by the MCP
    authorization server, carry no tenant (platform-wide), and are accepted only
    at ``/mcp``. ``APP`` tokens are grants a person approved for a third-party
    app (:class:`~models.OAuthApp`): bound to the one community picked on the
    consent screen, limited to the approved scopes, and accepted only by the
    REST API. Each surface rejects the other kinds so a credential minted for
    one can never be replayed against another.
    """

    PAT = 'pat'
    OAUTH = 'oauth'
    APP = 'app'


class OAuthClientType(str, Enum):
    """How a third-party app proves who it is at the token endpoint.

    ``CONFIDENTIAL`` apps run on a server and hold a client secret; ``PUBLIC``
    apps (desktop, mobile, browser-only) cannot keep one and rely on PKCE alone.
    Both must use PKCE.
    """

    CONFIDENTIAL = 'confidential'
    PUBLIC = 'public'


class RoleSource(str, Enum):
    MANUAL = 'manual'
    DISCORD = 'discord'
    VOLUNTEER = 'volunteer'


class VolunteerAvailabilityStatus(str, Enum):
    AVAILABLE = 'available'
    UNAVAILABLE = 'unavailable'
    PREFERRED = 'preferred'


class CommunityEventStatus(str, Enum):
    """Where a member-submitted community event stands with staff.

    A member's submission starts ``PENDING``; one submitted by staff starts
    ``APPROVED``. Only ``APPROVED`` events are listed to members and reminded.
    ``REJECTED`` covers both a submission staff turned down and an approved event
    staff pulled. ``CANCELLED`` is the host (or staff) calling it off.
    """

    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'


class AccommodationStatus(str, Enum):
    """Where a member's ADA accommodation request stands with staff.

    ``WITHDRAWN`` is set only by the requester unchecking the box; staff move a
    live request between the other three. A requester editing their details on
    an acknowledged or arranged request sends it back to ``NEW`` so staff see
    the change.
    """

    NEW = 'new'
    ACKNOWLEDGED = 'acknowledged'
    ARRANGED = 'arranged'
    WITHDRAWN = 'withdrawn'


class FeedbackCategory(str, Enum):
    BUG = 'bug'
    SUGGESTION = 'suggestion'
    PRAISE = 'praise'
    OTHER = 'other'


class FeedbackStatus(str, Enum):
    NEW = 'new'
    REVIEWED = 'reviewed'


class EquipmentStatus(str, Enum):
    AVAILABLE = 'available'
    CHECKED_OUT = 'checked_out'
    RETIRED = 'retired'


class CheckInEventStatus(str, Enum):
    """Lifecycle of a :class:`~models.CheckInEvent`.

    Only ``OPEN`` events are polled by the Matcherino sync worker; a ``DRAFT``
    can still be synced by hand while staff set it up.
    """

    DRAFT = 'draft'
    OPEN = 'open'
    CLOSED = 'closed'


class CheckInEntrantSource(str, Enum):
    MATCHERINO = 'matcherino'
    WALK_UP = 'walk_up'
    COMP = 'comp'


class CheckInTierLanyard(str, Enum):
    """The lanyard a Matcherino badge type earns at the check-in desk.

    Staff pick one per badge type. ``Staff`` and ``Volunteer`` lanyards aren't
    here: those come from a comp role or a volunteer assignment, and outrank
    any badge (``check_in_rules.lanyard_for``).
    """

    VIP = 'vip'
    BASE = 'base'
    DAY_PASS = 'day_pass'


class CheckInLinkMethod(str, Enum):
    """How a check-in entrant came to be linked to a Wizzrobe ``User``."""

    MATCHERINO_ID = 'matcherino_id'
    DISCORD_ID = 'discord_id'
    TWITCH_ID = 'twitch_id'
    MATCHERINO_HANDLE = 'matcherino_handle'
    MANUAL = 'manual'
    WALK_UP = 'walk_up'
    COMP = 'comp'


class CheckInDmStatus(str, Enum):
    """Whether the bot could DM someone, from the test DM sent at check-in.

    The desk asks while the person is still standing there, because every match
    call, crew ask and shift reminder at the event goes out the same way.
    ``BLOCKED`` is Discord refusing (DMs closed, no shared server); ``FAILED``
    is our side (bot down, an HTTP error) and says nothing about them.
    """

    PENDING = 'pending'
    SENT = 'sent'
    BLOCKED = 'blocked'
    OPTED_OUT = 'opted_out'
    FAILED = 'failed'


class StationSide(str, Enum):
    """Which half of the tournament room a station sits in.

    Exactly two, because the rule it exists for is "the two players of a match
    do not sit on the same side". A venue that has not described its layout
    leaves ``Station.side`` null and the seating suggestion simply has less to
    work with.
    """

    LEFT = 'left'
    RIGHT = 'right'


class StationFormat(str, Enum):
    FREE = 'free'
    NUMERIC = 'numeric'
    STRUCTURED = 'structured'
    ALPHANUMERIC = 'alphanumeric'


# The pattern each StationFormat enforces on a station label. Lives beside the
# enum as the single definition: both the service that validates an assignment
# and the dialog that pre-validates the field need it, and they are on opposite
# sides of the presentation/service boundary.
STATION_REGEXES = {
    StationFormat.FREE:         re.compile(r'^.{0,50}$'),
    StationFormat.NUMERIC:      re.compile(r'^\d+$'),
    StationFormat.STRUCTURED:   re.compile(r'^[A-Za-z][0-9]{1,2}$'),
    StationFormat.ALPHANUMERIC: re.compile(r'^[A-Za-z0-9\-\s]{1,20}$'),
}


class MatchNotificationLevel(str, Enum):
    NONE = 'none'
    STREAMED = 'streamed'
    STREAMED_AND_CANDIDATES = 'streamed_and_candidates'
    ALL = 'all'


class RescheduleRequestKind(str, Enum):
    """What a player is asking staff to do with their match.

    Two kinds rather than two models: both travel the same queue, carry the same
    reason, and are decided by the same person with the same authority. Only the
    approval branches, into ``update_match`` or ``cancel_match``.
    """

    RESCHEDULE = 'reschedule'
    CANCEL = 'cancel'


class MatchCommentKind(str, Enum):
    """Who wrote a match comment, and so who may read it.

    A player's comment goes to staff only; their opponent never sees it. Staff
    pick one of two paths: a private note other staff and proctors read, or a
    message the match's players see as well.
    """

    PLAYER = 'player'
    STAFF_PRIVATE = 'staff_private'
    STAFF_TO_PLAYERS = 'staff_to_players'


class RescheduleRequestStatus(str, Enum):
    """Where a request has got to.

    Three ways to stop being pending without being refused, and they are kept
    apart on purpose. ``WITHDRAWN`` is the requester taking it back;
    ``SUPERSEDED`` is another request on the same match having settled it, which
    staff did not decide at all; ``DECLINED`` is the only one that means someone
    looked at this ask and said no.
    """

    PENDING = 'pending'
    APPROVED = 'approved'
    DECLINED = 'declined'
    WITHDRAWN = 'withdrawn'
    SUPERSEDED = 'superseded'


class ChallongeMatchState(str, Enum):
    """Mirrors Challonge's match states relevant to scheduling."""

    PENDING = 'pending'   # participants not yet fully determined
    OPEN = 'open'         # both participants known and ready to play
    COMPLETE = 'complete' # result recorded on Challonge


class StartggSetState(str, Enum):
    """A mirrored start.gg set, reduced to what scheduling needs."""

    PENDING = 'pending'    # an entrant is still to be decided
    OPEN = 'open'          # both entrants known, no result yet
    COMPLETE = 'complete'  # result recorded on start.gg


class BotStatus(str, Enum):
    """Health of a racetime bot's websocket connection.

    The values are *written* by the PR 4 runtime (heartbeat/connect/error) and
    read by the platform health surface; in this PR the column exists but stays
    at its ``UNKNOWN`` default.
    """

    UNKNOWN = 'unknown'
    CONNECTED = 'connected'
    DISCONNECTED = 'disconnected'
    ERROR = 'error'


class RaceRoomStatus(str, Enum):
    """Cached racetime room lifecycle state (written by PR 4/6)."""

    OPEN = 'open'
    IN_PROGRESS = 'in_progress'
    FINISHED = 'finished'
    CANCELLED = 'cancelled'


class SyncStatus(str, Enum):
    """Reconciliation state of a synced SpeedGaming episode (PR 7).

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings, never the
    bare member (which repr's as ``SyncStatus.SYNCED``).
    """

    PENDING = 'pending'      # discovered upstream, not yet materialized
    SYNCED = 'synced'        # materialized/refreshed into a Match this cycle
    SKIPPED = 'skipped'      # a lifecycle guard held the refresh back
    CANCELLED = 'cancelled'  # upstream episode gone; the Match soft-detached
    ERROR = 'error'          # transform/load failed (see ``sync_error``)


class DiscordEventSource(str, Enum):
    """What Wizzrobe schedule row a mirrored Discord event came from (PR 8).

    The ``DiscordScheduledEvent`` link is polymorphic: ``(source_type, source_id)``
    identifies the Wizzrobe row a Discord Scheduled Event mirrors. Today only
    ``MATCH`` is materialized (native + SG-imported matches both live in ``Match``);
    qualifier windows / live races join later without a schema change.

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings.
    """

    MATCH = 'match'


class StaffRequestKind(str, Enum):
    """Which request a :class:`StaffRequestDM` asked staff to decide.

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings.
    """

    COMMUNITY_EVENT = 'community_event'
    ADA_REQUEST = 'ada_request'
    JOIN_REQUEST = 'join_request'
    RESCHEDULE_REQUEST = 'reschedule_request'


class AsyncQualifierRunStatus(str, Enum):
    """Execution state of a single async-qualifier run (PR 9).

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings, never the
    bare member (which repr's as ``AsyncQualifierRunStatus.FINISHED``).

    Web-first collapses reveal and start, so a run is created ``IN_PROGRESS`` the
    moment a player draws (the permalink is revealed then). ``PENDING`` is
    reserved for a run pre-created before a synchronous start — the live-race path
    (PR 10) — and is unused by the self-paced core flow.
    """

    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    FINISHED = 'finished'
    FORFEIT = 'forfeit'
    DISQUALIFIED = 'disqualified'


class AsyncQualifierReviewStatus(str, Enum):
    """Review state of a finished async-qualifier run (PR 9)."""

    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'


class AsyncQualifierLiveRaceStatus(str, Enum):
    """Lifecycle of a synchronous racetime qualifier race (PR 10).

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings, never the
    bare member (which repr's as ``AsyncQualifierLiveRaceStatus.FINISHED``).

    ``SCHEDULED`` before a room opens, ``PENDING`` once a room exists but the race
    has not started, ``IN_PROGRESS`` while racing, ``FINISHED`` once the entrants'
    results are captured into runs, ``CANCELLED`` when the racetime room was
    cancelled instead of raced.

    ``CANCELLED`` exists because the inbound handler used to update only the
    ``RacetimeRoom`` on a cancellation, leaving the race stuck at scheduled or
    in-progress and indistinguishable from one still to come.
    """

    SCHEDULED = 'scheduled'
    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    FINISHED = 'finished'
    CANCELLED = 'cancelled'


class MembershipSource(str, Enum):
    """How someone became a member of a community (``TenantMembership.source``).

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings. Null on
    rows from before it was recorded.
    """

    STAFF = 'staff'                          # Add Member, Add User, /platform bootstrap
    JOIN_REQUEST = 'join_request'            # an approved join request
    DISCORD_AUTO_JOIN = 'discord_auto_join'  # in the linked Discord server
    OPEN = 'open'                            # signed in while membership was open
    ROLE_GRANT = 'role_grant'                # a role granted by staff
    DISCORD_ROLE = 'discord_role'            # a role from the Discord role sync
    IMPORT = 'import'                        # the SpeedGaming import


class JoinRequestStatus(str, Enum):
    """Where a request to join a community stands.

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings, never the
    bare member.

    One row per ``(user, tenant)``: a denied request is **re-opened** by moving
    it back to ``PENDING``, not appended to. An append-only log of attempts is a
    spam vector with no reader.
    """

    PENDING = 'pending'
    APPROVED = 'approved'
    DENIED = 'denied'


class ProviderTaskStatus(str, Enum):
    """Where a long-running upstream provider call has got to.

    ``(str, Enum)`` (not ``StrEnum``) — render ``.value`` in f-strings, never the
    bare member.

    ``QUEUED`` is the row before (or at the moment of) submission — no upstream
    task id yet. ``RUNNING`` means the provider has accepted it and the poller
    owns it from here. The three terminal states are kept apart because they
    answer different questions: ``SUCCEEDED`` completed and its consumer was
    updated, ``FAILED`` is the provider reporting the work itself as broken, and
    ``ABANDONED`` is Wizzrobe giving up on a task that outlived its deadline —
    the provider may well have finished it, we simply stopped waiting.
    """

    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    ABANDONED = 'abandoned'

    @classmethod
    def terminal(cls) -> frozenset['ProviderTaskStatus']:
        """States a poller must never pick back up."""
        return frozenset({cls.SUCCEEDED, cls.FAILED, cls.ABANDONED})


class PresetOverride(str, Enum):
    """Staff's per-match answer to "which preset does this match roll with".

    ``NULL`` on ``Match.preset_override`` means "whatever the players decided"
    — the hard preset when every player opted in, the tournament's standard one
    otherwise. These two members are staff overruling that, in either direction:
    ``HARD`` rolls the hard preset with no opt-ins at all, ``STANDARD`` rolls the
    standard one even when everybody opted in.

    Deliberately a two-value enum rather than an FK to any preset: the override
    answers the same question the opt-in answers, so it has the same two answers,
    and it can never name a preset from a different randomizer than the
    tournament's own.
    """

    HARD = 'hard'
    STANDARD = 'standard'
