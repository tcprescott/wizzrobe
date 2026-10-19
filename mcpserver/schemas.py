"""Compact result shapes for MCP tools.

Two rules decide whether a tool reuses an ``api/schemas`` model or gets a shape
here:

* **Singular reads reuse.** ``get_match`` returns the REST ``MatchResponse`` via
  the same serializer, so rules baked into it (notably: unapproved crew is not
  disclosed) cannot drift between the two surfaces.
* **List reads get a compact shape.** A hundred full match records is mostly
  padding, and padding is the expensive kind of wrong for an LLM consumer — it
  buries the answer and burns the context the model needs to reason. These
  models carry what identifies a row and what a person actually asks about.

Every datetime is UTC, as stored. Tools do not localize; the model is told once,
in the server instructions, rather than per field.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# The module, not the class: a REST model bound here would be scanned as one of
# MCP's (tests/mcp/test_mcp_snowflake_ids.py) and its numeric discord_id flagged.
from api.schemas import member_details as rest_member_details
from mcpserver.snowflake import Snowflake

# Declared once and shared, so every community-scoped tool describes its tenant
# argument identically. The description is the only place the model learns where
# a slug comes from, so it earns its keep in the schema of all ~20 tools.
TenantArg = Annotated[
    Optional[str],
    Field(description='Community slug, as returned by list_tenants. Required.'),
]


class OperationResult(BaseModel):
    """The outcome of a write that has no record to hand back.

    Deliberately not a bare string: a tool must return a typed model or the SDK
    ships its result as an untyped text blob, and the model is then left parsing
    English to learn whether anything happened.
    """

    ok: bool = True
    detail: str = Field(description='What changed, in one line.')


class TenantInfo(BaseModel):
    """A community the caller can address."""

    model_config = ConfigDict(from_attributes=True)

    slug: str = Field(description="Pass this as the `tenant` argument to other tools.")
    name: str
    roles: List[str] = Field(
        default_factory=list,
        description="Roles the caller holds in this community; empty means none.",
    )
    features: List[str] = Field(
        default_factory=list,
        description=(
            "Optional subsystems this community has switched on. A tool for a "
            "feature absent here reports not_found, so check this before calling."
        ),
    )


class WhoAmI(BaseModel):
    """The caller's identity and reach, for orienting before any other call."""

    user_id: int
    username: str
    display_name: Optional[str] = None
    is_super_admin: bool
    can_write: bool = Field(
        description=(
            "Whether this connection was approved to change data. When false, "
            "only the read tools are served and any write is refused."
        )
    )
    tenants: List[TenantInfo] = Field(
        description="Communities the caller holds a role in, or all of them for a super admin."
    )


class ServerVersion(BaseModel):
    """Which build of Wizzrobe is answering."""

    version: str = Field(
        description="Release tag when built from one, else the commit's UTC date as YYYY.MM.DD; `dev` for a local checkout."
    )
    commit: Optional[str] = Field(None, description="Full git commit hash the build came from; null for a local checkout.")
    commit_short: Optional[str] = Field(None, description="First seven characters of `commit`.")
    commit_date: Optional[datetime] = Field(None, description="When that commit was made (UTC).")
    source_url: str = Field(description="The source code at that commit.")


class TournamentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    tournament_format: Optional[str] = None
    players_per_match: Optional[int] = None
    event_start_date: Optional[datetime] = None
    event_end_date: Optional[datetime] = None


class TournamentSignupInfo(BaseModel):
    """One tournament as a prospective entrant sees it.

    ``can_sign_up`` / ``can_withdraw`` are the service's own answer, not a
    guess from the window: a model that reasons from ``window`` alone would
    offer a signup a started Challonge bracket, an unlinked Challonge account or
    the caller's existing enrolment already rules out.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    window: str
    enrolled: bool
    entrant_count: int
    can_sign_up: bool
    can_withdraw: bool
    signups_open_at: Optional[datetime] = None
    signups_close_at: Optional[datetime] = None


class StageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    stream_url: Optional[str] = None


class MatchTimeSuggestion(BaseModel):
    """A suggested slot. Wrapped in a model rather than returned as a bare
    datetime so the tool publishes a named output schema."""

    suggested_at: datetime = Field(description='UTC start time for the match.')


class RandomizerInfo(BaseModel):
    """A randomizer this community can actually roll seeds from."""

    randomizer: str
    supports_triforce_texts: bool = False


class TriforceTextEntry(BaseModel):
    id: int
    tournament_id: int
    text: str
    author: Optional[str] = None
    status: str = Field(description='pending, approved, or rejected.')
    submitted_by: Optional[str] = None
    created_at: Optional[datetime] = None


class PayoutEntry(BaseModel):
    """One placement's share of a prize pool, with the money worked out."""

    place: int
    percentage: Decimal
    amount: Decimal
    entrant_name: Optional[str] = None
    matcherino_username: Optional[str] = None
    note: Optional[str] = None


class TournamentPayouts(BaseModel):
    tournament_id: int
    prize_pool: Decimal
    prize_bonus: Decimal
    total: Decimal = Field(description='Pool plus bonus — what the shares apply to.')
    lines: List[PayoutEntry]


class VolunteerPositionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    is_active: bool = True
    display_order: int = 0


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: Optional[str] = None
    discord_id: Optional[Snowflake] = Field(None, description="Discord user ID, as a string.")
    is_active: bool = True


class UserDetail(UserSummary):
    """A user plus the roles they hold in the community being queried."""

    roles: List[str] = Field(default_factory=list)
    pronouns: Optional[str] = None
    twitch_username: Optional[str] = None
    racetime_username: Optional[str] = None


class CrewMemberInfo(BaseModel):
    """A crew signup, including unapproved ones — see the tool's gate."""

    id: int
    user: Optional[UserSummary] = None
    approved: bool
    acknowledged_at: Optional[datetime] = None


class MatchCrew(BaseModel):
    match_id: int
    commentators: List[CrewMemberInfo] = Field(default_factory=list)
    trackers: List[CrewMemberInfo] = Field(default_factory=list)


class StageBlock(BaseModel):
    """One stage's slice of a day's schedule."""

    stage: Optional[str] = None
    matches: List['MatchSummary'] = Field(default_factory=list)


class ShiftSummary(BaseModel):
    """A volunteer shift and its staffing, without the full assignment rows."""

    id: int
    position: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    slots_needed: Optional[int] = None
    filled: int = 0
    assignees: List[str] = Field(default_factory=list)


class MyShift(BaseModel):
    """One of the caller's own volunteer shifts."""

    assignment_id: int = Field(description='Pass to check_in_volunteer (coordinators) or to the web give-back.')
    shift_id: int
    position: Optional[str] = None
    label: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    checked_in_at: Optional[datetime] = None


class AuditEntry(BaseModel):
    id: int
    user_id: Optional[int] = None
    action: str
    details: Optional[object] = None
    created_at: datetime


class AuditPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[AuditEntry] = Field(default_factory=list)


class SentMessageInfo(BaseModel):
    """One DM Wizzrobe sent, and what happened to it."""

    id: int
    recipient_id: Optional[int] = None
    recipient_name: Optional[str] = None
    discord_id: Snowflake = Field(..., description="Discord user ID it went to, as a string.")
    title: Optional[str] = None
    body: str
    link_label: Optional[str] = None
    link_url: Optional[str] = None
    silent: bool
    notice_key: Optional[str] = None
    audience: Optional[str] = None
    status: str = Field(..., description="delivered, discord_off, blocked, failed or pending.")
    error: Optional[str] = None
    push_devices: Optional[int] = None
    email_sent: Optional[bool] = None
    telegram_sent: Optional[bool] = None
    created_at: datetime


class MemberDetailsInfo(rest_member_details.MemberDetailsResponse):
    """Everything this community knows about one member, as staff see it on Admin → Members."""

    discord_id: Optional[Snowflake] = Field(None, description="Their Discord ID, as a string.")
    recent_messages: List[SentMessageInfo] = Field(  # type: ignore[assignment]
        default_factory=list, description="The last DMs sent to them here, newest first.",
    )


class SentMessagePage(BaseModel):
    total: int
    limit: int
    offset: int
    counts: Dict[str, int] = Field(
        default_factory=dict,
        description="sent, silent, off_or_blocked and failed over the time window.",
    )
    items: List[SentMessageInfo] = Field(default_factory=list)


class MatchSummary(BaseModel):
    """One match, at the detail level a scheduling question needs."""

    id: int
    tournament: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    status: str
    players: List[str] = Field(default_factory=list)
    stage: Optional[str] = None
    restream_url: Optional[str] = None


class AvailabilityWindow(BaseModel):
    """One window a player spoke for. Everything else is available."""

    starts_at: datetime
    ends_at: datetime
    status: str = Field(
        description='unavailable (cannot play then) or preferred. An available '
                    'window states the default and is never stored.',
    )
    note: Optional[str] = None


class PlayerAvailability(BaseModel):
    """A player's windows. No windows means available for the whole event."""

    user_id: int
    name: Optional[str] = None
    windows: List[AvailabilityWindow] = Field(default_factory=list)


class LoanRecord(BaseModel):
    """One checkout of a piece of equipment; open while `checked_in_at` is null."""

    id: int
    borrower: Optional[str] = None
    checked_out_at: Optional[datetime] = None
    checked_out_by: Optional[str] = None
    checked_in_at: Optional[datetime] = None
    checked_in_by: Optional[str] = None


class EquipmentSummary(BaseModel):
    id: int
    asset_number: int
    name: str
    status: str
    owner: Optional[str] = None
    borrower: Optional[str] = Field(
        default=None, description='Who currently holds it, when it is checked out.'
    )


class EquipmentDetail(EquipmentSummary):
    """An asset plus its checkout history.

    `private_notes` is deliberately absent: the field is staff-only scratch text
    about an asset, and nothing a scheduling question needs.
    """

    description: Optional[str] = None
    loans: List[LoanRecord] = Field(
        default_factory=list, description='The most recent checkouts, newest first.',
    )
    loan_count: int = Field(0, description='Checkouts ever recorded; more than `loans` when it is cut short.')


class PresetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    randomizer: str
    description: Optional[str] = None


class PresetDetail(PresetSummary):
    """A preset with its full randomizer settings payload."""

    settings: Optional[object] = None


class RaceRoomInfo(BaseModel):
    """A racetime.gg room Wizzrobe opened for a match."""

    id: int
    slug: str
    category: str
    room_name: Optional[str] = None
    status: str
    match_id: Optional[int] = None
    opened_at: Optional[datetime] = None


class RaceRoomProfileInfo(BaseModel):
    """The room settings a tournament rolls its racetime rooms with."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    goal: Optional[str] = None
    invitational: bool = False
    unlisted: bool = False
    auto_start: bool = True
    streaming_required: bool = False
    time_limit: Optional[int] = Field(
        default=None, description='Hours before the room auto-closes.'
    )


class SpeedGamingLink(BaseModel):
    """A SpeedGaming event whose episodes are pulled into a tournament."""

    id: int
    tournament_id: int
    event_slug: str
    content_type: Optional[str] = None
    active: bool = True
    last_synced_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None


class SpeedGamingEpisode(BaseModel):
    """One pulled episode and whether it turned into a match.

    The upstream `payload` is omitted: it is a large nested blob whose useful
    fields are already flattened onto the match this episode produced.
    """

    id: int
    sg_episode_id: str
    title: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    sync_status: str
    synced_at: Optional[datetime] = None
    sync_error: Optional[str] = None


class BracketDetail(BaseModel):
    """One bracket stage and its shape."""

    id: int
    tournament_id: int
    name: str
    format: str
    state: str
    stage_order: int = 0
    entry_count: int = 0


class BracketMatchInfo(BaseModel):
    """One pairing in a bracket, by round and position."""

    id: int
    round: int
    position: int
    group_number: Optional[int] = None
    state: str
    entry1: Optional[str] = None
    entry2: Optional[str] = None
    entry1_score: Optional[int] = None
    entry2_score: Optional[int] = None
    winner: Optional[str] = None
    forfeit: bool = False
    best_of: Optional[int] = None


class BracketEntrantInfo(BaseModel):
    """Someone enrolled in a tournament's brackets."""

    id: int
    display_name: str
    status: str
    user_id: Optional[int] = None


class QualifierPoolInfo(BaseModel):
    id: int
    name: str
    preset: Optional[str] = None
    permalink_count: int = 0


class QualifierDetail(BaseModel):
    """An async qualifier's configuration and its pools."""

    id: int
    name: str
    description: Optional[str] = None
    event_name: Optional[str] = None
    is_active: bool = True
    opens_at: Optional[datetime] = None
    closes_at: Optional[datetime] = None
    runs_per_pool: int = 1
    allowed_reattempts: int = 0
    results_public: bool = False
    pools: List[QualifierPoolInfo] = Field(default_factory=list)


class LeaderboardRow(BaseModel):
    """One entrant's standing in an async qualifier.

    `actual` scores unfilled slots as zero; `estimate` projects them at the
    runner's mean, so a partial entrant is not understated.
    """

    rank: int
    user_id: int
    name: str
    actual: float
    estimate: float
    slots_filled: int
    slots_total: int


class LiveRaceInfo(BaseModel):
    """A scheduled live race that feeds runs into a qualifier pool."""

    id: int
    match_title: str
    status: str
    pool: Optional[str] = None
    racetime_slug: Optional[str] = None


class ServiceHealthProbe(BaseModel):
    """The latest outcome of one dependency probe."""

    key: str
    label: str
    category: str
    status: str
    message: str
    checked_at: datetime


class LoopStall(BaseModel):
    """One time the event loop was blocked long enough to freeze every user."""

    started_at: float = Field(description='Unix time, UTC')
    seconds: float
    culprit: str = Field(description='Innermost app frame on the loop thread while it was blocked')


class EventLoopStats(BaseModel):
    """Heartbeat lag over the last minute. Healthy is a p95 in the low milliseconds."""

    running: bool
    lag_ms_p50: Optional[float]
    lag_ms_p95: Optional[float]
    lag_ms_max: Optional[float]
    cpu_percent: Optional[float] = Field(description='Process CPU over the last minute; 100 is one core')
    stalls_since_start: int
    recent_stalls: List[LoopStall]


class WorkerStats(BaseModel):
    """Timing of one background worker loop."""

    name: str
    running: bool
    interval_seconds: float
    ticks: int
    failures: int
    last_started_at: Optional[float]
    last_tick_seconds: Optional[float]
    max_tick_seconds: float
    last_failed_at: Optional[float]


class ClientCounts(BaseModel):
    total: Optional[int] = Field(description='NiceGUI page instances held in memory')
    connected: Optional[int] = Field(description='Of those, with a live websocket')


class DbPoolStats(BaseModel):
    size: Optional[int]
    idle: Optional[int]
    max: Optional[int]


class WatchPageAudience(BaseModel):
    live_viewers: int
    static_readers: int
    arrivals_last_minute: int
    static_mode_tripped: bool


class RuntimeStats(BaseModel):
    """A point-in-time picture of the app process's load."""

    collected_at: float = Field(description='Unix time, UTC')
    uptime_seconds: int
    release: Optional[str]
    rss_mb: Optional[float]
    asyncio_tasks: Optional[int]
    clients: ClientCounts
    db_pool: DbPoolStats
    event_loop: EventLoopStats
    workers: List[WorkerStats]
    watch_pages_by_tenant_id: Dict[str, WatchPageAudience]


class WebhookInfo(BaseModel):
    """An outbound webhook. The signing secret is never returned."""

    id: int
    name: str
    url: str
    event_types: List[str] = Field(default_factory=list)
    is_active: bool = True


class WebhookDeliveryInfo(BaseModel):
    """One delivery attempt. The serialized payload is omitted as bulk."""

    id: int
    event_type: str
    success: bool
    response_status: Optional[int] = None
    attempt_count: int = 0
    error: Optional[str] = None
    created_at: datetime
    delivered_at: Optional[datetime] = None


class FeedbackEntry(BaseModel):
    """One piece of user-submitted feedback."""

    id: int
    category: str
    status: str
    message: str
    page_url: Optional[str] = None
    submitted_by: Optional[str] = None
    created_at: datetime


# StageBlock forward-references MatchSummary, which is declared after it.
StageBlock.model_rebuild()


# --- Platform (super admin) ---------------------------------------------------


class PlatformCommunity(BaseModel):
    """One community as the /platform page lists it, switched-off ones included."""

    id: int
    slug: str = Field(description='Pass this as `community` to the platform tools.')
    name: str
    is_active: bool = Field(description='False means the community is hidden from everyone.')
    domain: Optional[str] = None
    discord_guild_id: Optional[Snowflake] = Field(None, description='Discord server id, as a string.')
    feature_group: Optional[str] = Field(None, description='Feature group (tier). Null means the default group.')
    setup_ready: bool = Field(description='Whether every required setup step is done.')
    setup_missing: List[str] = Field(default_factory=list, description='Setup steps still to do.')


class PlatformFeature(BaseModel):
    """One feature's availability for one community."""

    flag: str = Field(description='Feature key, as `platform_set_community_feature` takes it.')
    label: str
    group_available: bool = Field(description="Whether the community's feature group includes it.")
    override: Optional[bool] = Field(
        None, description='Platform override: true forced on, false forced off, null follows the group.'
    )
    available: bool = Field(description='Whether the community may use it, after the override.')
    enabled: bool = Field(description="Whether the community's staff have it switched on.")
    live: bool = Field(description='Available and enabled: the feature is on there.')


class PlatformCommunityDetail(PlatformCommunity):
    staff: List[UserSummary] = Field(default_factory=list, description='Everyone holding Staff there.')
    features: List[PlatformFeature] = Field(default_factory=list)


class PlatformUser(BaseModel):
    """An account as the /platform Users tab shows it."""

    id: int
    username: str
    display_name: Optional[str] = None
    discord_id: Optional[Snowflake] = Field(None, description='Discord user id, as a string. Null for a password-only account.')
    login_name: Optional[str] = Field(None, description='Password sign-in name. Null when there is no password.')
    is_active: bool
    is_super_admin: bool
    communities: List[str] = Field(default_factory=list, description='Names of the communities they belong to.')


class PlatformFeatureGroup(BaseModel):
    id: int
    name: str
    description: str = ''
    flags: List[str] = Field(default_factory=list, description='Feature keys the group makes available.')
    is_default: bool = Field(description='Whether communities with no group fall back to this one.')
    community_count: int


class PlatformRacetimeBot(BaseModel):
    """A shared racetime.gg bot. The client secret is never returned."""

    id: int
    category: str
    name: str
    client_id: str
    description: str = ''
    handler_class: str = ''
    is_active: bool
    status: str = Field(description='`unknown`, `connected`, `disconnected` or `error`.')
    status_message: str = ''
    communities: List[str] = Field(default_factory=list, description='Slugs of the communities allowed to use it.')
