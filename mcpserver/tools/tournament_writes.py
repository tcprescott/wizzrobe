"""Tournament administration: create, edit, delete, staff, roster and integrations.

The third write module, held to the same rule as ``match_writes`` and
``event_writes``: each tool mirrors one REST endpoint, here from
``api/routers/tournament_actions.py`` and the per-tournament settings route in
``api/routers/discord_events.py``. A write that belongs here belongs in the REST
router too, and vice versa.

Every tool is registered at ``Gate.ACTOR`` with ``write=True``, mirroring
``require_write_actor``, except ``get_round_schedule``, the read that pairs with
``set_stage_best_of``. The owning service makes the real permission check
(``can_edit_tournament`` lets a tournament's own admins edit it; adding staff,
players and deleting is staff-only), so the two surfaces cannot disagree.
Tournaments are loaded through the tenant-scoped repository, so an id from
another community is simply not found.
"""

from typing import Callable, Dict, List, Literal, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.discord_events import DiscordEventTournamentResponse
from api.schemas.tournament_actions import (
    RoundScheduleStageResponse,
    StageBestOfRequest,
    TournamentCreateRequest,
    TournamentUpdateRequest,
)
from api.schemas.tournaments import TournamentResponse
from application.errors import require_found
from application.services import (
    ChallongeService,
    DiscordEventSyncService,
    RoundScheduleService,
    TournamentService,
    UserService,
)
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import OperationResult, TenantArg
from models import FeatureFlag, Tournament, User

ClearableField = Literal[
    'preset_id',
    'hard_preset_id',
    'racetime_bot_id',
    'race_room_profile_id',
    'racetime_default_goal',
    'event_start_date',
    'event_end_date',
    'tournament_hours',
    'signups_open_at',
    'signups_close_at',
]


async def _tournament(tournament_id: int) -> Tournament:
    return require_found(
        await TournamentService().get_tournament_by_id(tournament_id), 'Tournament'
    )


async def _user(user_id: int) -> User:
    return require_found(await UserService().get_user_by_id(user_id), 'User')


def _response(tournament: Tournament) -> TournamentResponse:
    return TournamentResponse.model_validate(tournament, from_attributes=True)


# --- The tournament itself ----------------------------------------------------


async def create_tournament(
    name: str,
    tenant: TenantArg = None,
    short_name: Optional[str] = None,
    description: Optional[str] = None,
    tournament_format: Optional[str] = None,
    bracket_url: Optional[str] = None,
    rules_url: Optional[str] = None,
    randomizer_notes: Optional[str] = None,
    triforce_access_message: Optional[str] = None,
    seed_generator: Optional[str] = None,
    preset_id: Optional[int] = None,
    hard_preset_id: Optional[int] = None,
    average_match_duration: Optional[int] = None,
    max_match_duration: Optional[int] = None,
    players_per_match: int = 2,
    required_commentators: int = 1,
    required_trackers: int = 1,
    is_active: bool = True,
    is_test: bool = False,
    uses_tournament_room: bool = True,
    allow_player_match_requests: bool = True,
    stage_reminder_minutes: int = 30,
    event_start_date: Optional[str] = None,
    event_end_date: Optional[str] = None,
    tournament_hours: Optional[Dict[str, List[str]]] = None,
    signups_open_at: Optional[str] = None,
    signups_close_at: Optional[str] = None,
    racetime_bot_id: Optional[int] = None,
    race_room_profile_id: Optional[int] = None,
    racetime_auto_create_rooms: bool = False,
    room_open_minutes_before: int = 30,
    require_racetime_link: bool = False,
    racetime_default_goal: Optional[str] = None,
) -> TournamentResponse:
    """Create a tournament. Staff only.

    Dates are `YYYY-MM-DD`, signup bounds are ISO 8601 timestamps (UTC), and
    `tournament_hours` is `{"YYYY-MM-DD": ["HH:MM", "HH:MM"]}` on the
    community's clock. Leave event dates and hours out to inherit the
    community's. `preset_id` comes from `list_presets`; attaching one also sets
    the randomizer. `is_test` keeps it off the public event-info list.
    `uses_tournament_room` false is for a tournament played outside the
    tournament room: its off-stream matches take no room capacity.
    """
    args = dict(locals())
    fields = {k: v for k, v in args.items() if k != 'tenant'}
    body = TournamentCreateRequest.model_validate(fields)
    tournament = await TournamentService().create_tournament(
        actor=current_actor().user, **body.model_dump(),
    )
    return _response(tournament)


async def update_tournament(
    tournament_id: int,
    tenant: TenantArg = None,
    name: Optional[str] = None,
    short_name: Optional[str] = None,
    description: Optional[str] = None,
    tournament_format: Optional[str] = None,
    bracket_url: Optional[str] = None,
    rules_url: Optional[str] = None,
    randomizer_notes: Optional[str] = None,
    triforce_access_message: Optional[str] = None,
    seed_generator: Optional[str] = None,
    preset_id: Optional[int] = None,
    hard_preset_id: Optional[int] = None,
    average_match_duration: Optional[int] = None,
    max_match_duration: Optional[int] = None,
    players_per_match: Optional[int] = None,
    required_commentators: Optional[int] = None,
    required_trackers: Optional[int] = None,
    is_active: Optional[bool] = None,
    is_test: Optional[bool] = None,
    uses_tournament_room: Optional[bool] = None,
    allow_player_match_requests: Optional[bool] = None,
    stage_reminder_minutes: Optional[int] = None,
    event_start_date: Optional[str] = None,
    event_end_date: Optional[str] = None,
    tournament_hours: Optional[Dict[str, List[str]]] = None,
    signups_open_at: Optional[str] = None,
    signups_close_at: Optional[str] = None,
    racetime_bot_id: Optional[int] = None,
    race_room_profile_id: Optional[int] = None,
    racetime_auto_create_rooms: Optional[bool] = None,
    room_open_minutes_before: Optional[int] = None,
    require_racetime_link: Optional[bool] = None,
    racetime_default_goal: Optional[str] = None,
    clear: Optional[List[ClearableField]] = None,
) -> TournamentResponse:
    """Change a tournament's details or configuration. Staff or that tournament's admins.

    Only the arguments you pass change. An empty string clears a text field
    such as `description` or `bracket_url`. To remove a preset, a date, the
    hours or a signup bound, name it in `clear`. Setting `is_active` false
    hides the tournament from players. Formats are as for `create_tournament`.
    """
    args = dict(locals())
    fields = {
        k: v for k, v in args.items()
        if k not in ('tournament_id', 'tenant', 'clear') and v is not None
    }
    for field in clear or ():
        if field in fields:
            raise ValueError(f'{field} is both set and cleared; pick one.')
        fields[field] = None
    body = TournamentUpdateRequest.model_validate(fields)
    tournament = await _tournament(tournament_id)
    await TournamentService().update_tournament(
        tournament, actor=current_actor().user, **body.model_dump(exclude_unset=True),
    )
    return _response(tournament)


async def delete_tournament(
    tournament_id: int,
    confirmation: str,
    tenant: TenantArg = None,
) -> OperationResult:
    """Permanently delete an inactive tournament. Staff only. Can't be undone.

    Removes its matches, entrants, brackets and triforce texts too. Deactivate
    it first with `update_tournament(is_active=false)`, and pass
    `confirmation="permanently delete"` once the user has confirmed.
    """
    tournament = await _tournament(tournament_id)
    name = tournament.name
    await TournamentService().delete_tournament(
        tournament, actor=current_actor().user, confirmation=confirmation,
    )
    return OperationResult(detail=f'{name} deleted.')


# --- Tournament admins and crew coordinators ----------------------------------


async def add_tournament_admin(
    tournament_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Make someone an admin of one tournament, so they can edit it and run its matches. Staff only."""
    tournament = await _tournament(tournament_id)
    await TournamentService().add_admin(tournament, await _user(user_id), actor=current_actor().user)
    return OperationResult(detail=f'Added user {user_id} as an admin of {tournament.name}.')


async def remove_tournament_admin(
    tournament_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take someone off a tournament's admins. Staff only."""
    tournament = await _tournament(tournament_id)
    await TournamentService().remove_admin(tournament, await _user(user_id), actor=current_actor().user)
    return OperationResult(detail=f'Removed user {user_id} from the admins of {tournament.name}.')


async def add_crew_coordinator(
    tournament_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Make someone a crew coordinator for one tournament, so they approve its crew signups. Staff only."""
    tournament = await _tournament(tournament_id)
    await TournamentService().add_crew_coordinator(
        tournament, await _user(user_id), actor=current_actor().user,
    )
    return OperationResult(detail=f'Added user {user_id} as a crew coordinator for {tournament.name}.')


async def remove_crew_coordinator(
    tournament_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take someone off a tournament's crew coordinators. Staff only."""
    tournament = await _tournament(tournament_id)
    await TournamentService().remove_crew_coordinator(
        tournament, await _user(user_id), actor=current_actor().user,
    )
    return OperationResult(detail=f'Removed user {user_id} from the crew coordinators of {tournament.name}.')


# --- Roster -------------------------------------------------------------------


async def enroll_tournament_player(
    tournament_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Add a member to a tournament's roster, ignoring the signup window. Staff or that tournament's admins.

    `user_id` comes from `list_users`; the person must already be a member of
    this community.
    """
    tournament = await _tournament(tournament_id)
    await TournamentService().enroll_player(tournament, await _user(user_id), current_actor().user)
    return OperationResult(detail=f'Enrolled user {user_id} in {tournament.name}.')


async def remove_tournament_player(
    tournament_id: int,
    user_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take someone off a tournament's roster, whatever the signup window says. Staff or that tournament's admins."""
    tournament = await _tournament(tournament_id)
    await TournamentService().unenroll_player(tournament, await _user(user_id), current_actor().user)
    return OperationResult(detail=f'Removed user {user_id} from {tournament.name}.')


async def drop_tournament_player(
    tournament_id: int,
    user_id: int,
    reason: Literal['no_show', 'withdrew', 'disqualified'],
    forfeit_matches: bool = True,
    tenant: TenantArg = None,
) -> OperationResult:
    """Drop someone from a tournament and settle every match they still owe. Staff or that tournament's admins.

    With `forfeit_matches` their head-to-head matches go to the opponent by
    forfeit; without it they are called off. They come off larger matches'
    line-ups, leave the roster, and are taken out of a linked Challonge bracket.
    """
    from models import ForfeitReason

    tournament = await _tournament(tournament_id)
    result = await TournamentService().drop_player(
        tournament, await _user(user_id), current_actor().user,
        reason=ForfeitReason(reason), forfeit_matches=forfeit_matches,
    )
    return OperationResult(detail=(
        f'Dropped user {user_id} from {tournament.name}: {result.forfeited} forfeited, '
        f'{result.called_off} called off, {result.removed_from} line-ups changed'
        + (', removed on Challonge.' if result.on_bracket_site else '.')
    ))


# --- Integrations ---------------------------------------------------------------


async def link_challonge_tournament(
    tournament_id: int,
    challonge: str,
    tenant: TenantArg = None,
) -> TournamentResponse:
    """Link a tournament to a Challonge bracket by URL or ID and import it. Staff only.

    Refused when the tournament already has a native bracket.
    """
    await _tournament(tournament_id)
    tournament = await ChallongeService().link_tournament(tournament_id, challonge, current_actor().user)
    return _response(tournament)


async def unlink_challonge_tournament(
    tournament_id: int,
    tenant: TenantArg = None,
) -> TournamentResponse:
    """Detach a tournament from Challonge and drop its mirrored bracket. Staff only.

    Nothing on Challonge is deleted; linking again re-imports it.
    """
    await _tournament(tournament_id)
    tournament = await ChallongeService().unlink_tournament(tournament_id, current_actor().user)
    return _response(tournament)


async def sync_challonge_tournament(
    tournament_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Pull the latest entrants and matches from a linked Challonge bracket now.

    Staff, or an admin of this tournament. Linked brackets also sync on their own.
    """
    await _tournament(tournament_id)
    counts = await ChallongeService().sync_bracket(tournament_id, current_actor().user, force=True)
    summary = ', '.join(f'{k}: {v}' for k, v in counts.items())
    return OperationResult(detail=f'Synced from Challonge ({summary}).')


async def update_tournament_discord_events(
    tournament_id: int,
    tenant: TenantArg = None,
    enabled: Optional[bool] = None,
    duration_minutes: Optional[int] = None,
    title_template: Optional[str] = None,
    description_template: Optional[str] = None,
) -> DiscordEventTournamentResponse:
    """Change whether a tournament's matches show as Discord scheduled events, and how. Staff or sync admins.

    Only the arguments you pass change. Templates take `{tournament}`,
    `{match}` and `{players}`; an empty string restores the default. Changes
    reach Discord on the next sync.
    """
    tournament = await DiscordEventSyncService().update_settings(
        current_actor().user, tournament_id,
        enabled=enabled, duration_minutes=duration_minutes,
        title_template=title_template, description_template=description_template,
    )
    return DiscordEventTournamentResponse.model_validate(tournament, from_attributes=True)


# --- Round schedule ---------------------------------------------------------------


async def get_round_schedule(
    tournament_id: int,
    tenant: TenantArg = None,
) -> List[RoundScheduleStageResponse]:
    """List a Challonge or start.gg tournament's stages with their booking windows and best-of. Staff or that tournament's admins.

    Stage keys are `group` (a group stage), `e0` final, `e1` semifinals, `e2`
    quarterfinals and so on for single elimination; `gf` grand final, `w0`/`l0`
    winners/losers final and back from there for double elimination. A
    start.gg phase prefixes its keys with `<phase_id>:`. `booked` counts
    matchups that already have a match.
    """
    await _tournament(tournament_id)
    stages = await RoundScheduleService().list_stages(tournament_id, current_actor().user)
    return [RoundScheduleStageResponse.from_stage(s) for s in stages]


async def set_stage_best_of(
    tournament_id: int,
    stage_key: str,
    best_of: Literal[1, 3, 5],
    tenant: TenantArg = None,
) -> RoundScheduleStageResponse:
    """Set how many games each matchup in one stage plays (1, 3 or 5). Staff or that tournament's admins.

    `stage_key` comes from `get_round_schedule`. Only the best-of changes; the
    stage's booking window stays. Each game is then booked as its own match.
    Matches already booked for the stage keep the length they were booked with.
    """
    body = StageBestOfRequest(best_of=best_of)
    await _tournament(tournament_id)
    stage = await RoundScheduleService().set_best_of(
        tournament_id, stage_key, body.best_of, current_actor().user,
    )
    return RoundScheduleStageResponse.from_stage(stage)


def register_tools(mcp: MCPServer) -> None:
    def write_tool(
        fn: Callable, title: str, feature: Optional[FeatureFlag] = None, *, destructive: bool = False,
    ) -> None:
        register(
            mcp, fn, gate=Gate.ACTOR, feature=feature, write=True, title=title,
            destructive=destructive,
        )

    write_tool(create_tournament, 'Create tournament')
    write_tool(update_tournament, 'Update tournament')
    write_tool(delete_tournament, 'Delete tournament', destructive=True)
    write_tool(add_tournament_admin, 'Add tournament admin')
    write_tool(remove_tournament_admin, 'Remove tournament admin', destructive=True)
    write_tool(add_crew_coordinator, 'Add crew coordinator')
    write_tool(remove_crew_coordinator, 'Remove crew coordinator', destructive=True)
    write_tool(enroll_tournament_player, 'Enroll tournament player')
    write_tool(remove_tournament_player, 'Remove tournament player', destructive=True)
    write_tool(drop_tournament_player, 'Drop tournament player', destructive=True)
    challonge = FeatureFlag.CHALLONGE
    write_tool(link_challonge_tournament, 'Link Challonge bracket', challonge)
    write_tool(unlink_challonge_tournament, 'Unlink Challonge bracket', challonge, destructive=True)
    write_tool(sync_challonge_tournament, 'Sync Challonge bracket', challonge)
    write_tool(update_tournament_discord_events, 'Update tournament Discord events')
    register(mcp, get_round_schedule, gate=Gate.ACTOR, title='Get round schedule')
    write_tool(set_stage_best_of, 'Set stage best-of')
