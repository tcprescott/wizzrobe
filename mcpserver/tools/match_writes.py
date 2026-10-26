"""The write surface: match management and your own participation.

Scoped to exactly what ``api/routers/match_actions.py`` (and crew approval from
``api/routers/crew.py``) exposes, tool for tool.
That is the whole rule, and it is worth stating because the alternative — adding
whichever writes seemed useful — leaves two surfaces that drift apart and a
question with no answer when the next one is proposed. A write that belongs here
belongs in the REST router too, and vice versa.

Every tool is registered at ``Gate.ACTOR``, mirroring ``require_write_actor``:
holding a live token that was approved for writing is the bar at this layer, and
the real permission check is the service's own (``can_crud_match``,
``can_run_match``, ``can_confirm_match``, ``can_assign_match_stream``). The
service check is the one that knows about tournament admins, so duplicating it
here would only give the two surfaces a way to disagree.

The parity is not total, and the gap is worth knowing about. ``mcpserver`` has a
**membership floor** (``mcpserver/auth.py``) that refuses anyone holding no role
in the community, which the REST API has no equivalent of — a PAT is bound to
one community, so it never needed one. The floor governs the whole MCP surface,
reads included, so it is not something these tools introduced; but it does mean
the five self-service tools here (crew signup, acknowledge, watch) are out of
reach for a plain member who has joined a community without being given a role,
even though the same actions work for them on the web and through a PAT.
Widening the floor to membership is a decision about the read surface's
disclosure posture, not one to make quietly from here.

Ids are never guessed. Every tool takes ids a read tool returns — `match_id`
from `list_matches`, user ids from `list_users`, and `winner_id` from
`get_match`'s `players[].id`, which is the participation row rather than the
user.
"""

from typing import Callable, Dict, List, Literal, Optional

from mcp.server.mcpserver import MCPServer

from api._match_view import load_match_response
from api.schemas.match_actions import ExtendToSetRequest, SeedResultResponse, SetGameTime
from api.schemas.matches import MatchResponse
from application.api_scopes import ApiScope
from application.errors import require_found
from application.services import (
    BracketSetService,
    CrewService,
    MatchScheduleService,
    MatchService,
    MatchStreamVolunteerService,
    MatchWatcherService,
)
from application.tenant_context import require_tenant_id
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import OperationResult, TenantArg

# In the schema rather than only the prose, so the model reads the two legal
# values off the tool definition instead of a docstring it may skim.
CrewRole = Literal['commentator', 'tracker']


async def _load_match(match_id: int):
    """Load a match in the bound community, or raise ``not_found``.

    The sanctioned load-or-404 shape for an entry surface. Hand-scoped to the
    bound tenant because a bare id from a model is otherwise a way to reach into
    another community's match.
    """
    from models import Match

    return require_found(
        await Match.get_or_none(id=match_id, tenant_id=require_tenant_id()), 'Match'
    )


async def _match_response(match_id: int) -> MatchResponse:
    """Read a match back after writing it, scoped to the bound community.

    ``load_match_response`` filters on the id alone — it is written for the REST
    routers, which reach it only after a scoped service call. Re-checking the
    tenant here costs nothing and means the next tool added to this file cannot
    return a match by bare id without a scoped write in front of it.
    """
    await _load_match(match_id)
    return require_found(await load_match_response(match_id), 'Match')


# --- Scheduling -------------------------------------------------------------


async def create_match(
    tournament_id: int,
    scheduled_date: str,
    scheduled_time: str,
    player_ids: List[int],
    tenant: TenantArg = None,
    comment: Optional[str] = None,
    stage_id: Optional[int] = None,
    commentator_ids: Optional[List[int]] = None,
    tracker_ids: Optional[List[int]] = None,
    is_stream_candidate: bool = False,
) -> MatchResponse:
    """Schedule a new match and notify its players.

    `scheduled_date` is YYYY-MM-DD and `scheduled_time` is HH:MM, both read on
    the community's own clock rather than UTC. `player_ids` are Wizzrobe user
    ids from `list_users`. Returns the created match.
    """
    actor = current_actor().user
    match = await MatchService().create_match(
        tournament_id=tournament_id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        player_ids=player_ids,
        comment=comment,
        stage_id=stage_id,
        commentator_ids=commentator_ids,
        tracker_ids=tracker_ids,
        is_stream_candidate=is_stream_candidate,
        actor=actor,
    )
    return await _match_response(match.id)


async def submit_match_request(
    tournament_id: int,
    scheduled_date: str,
    scheduled_time: str,
    player_ids: List[int],
    tenant: TenantArg = None,
    comment: Optional[str] = None,
) -> MatchResponse:
    """Propose a match as one of its players, for staff to approve.

    The player-initiated path: you must be among `player_ids`. Use `create_match`
    instead when you hold the role to schedule directly. Dates and times are on
    the community's clock.
    """
    actor = current_actor().user
    match = await MatchService().submit_match_request(
        tournament_id=tournament_id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        player_ids=player_ids,
        actor=actor,
        comment=comment,
    )
    return await _match_response(match.id)


async def update_match(
    match_id: int,
    tenant: TenantArg = None,
    tournament_id: Optional[int] = None,
    scheduled_date: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    player_ids: Optional[List[int]] = None,
    commentator_ids: Optional[List[int]] = None,
    tracker_ids: Optional[List[int]] = None,
    comment: Optional[str] = None,
    clear_seated: bool = False,
    clear_started: bool = False,
    clear_finished: bool = False,
    clear_confirmed: bool = False,
    clear_seed: bool = False,
) -> MatchResponse:
    """Change a scheduled match: time, tournament, players, crew or comment.

    Only the arguments you pass are changed. The `clear_*` flags roll back a
    lifecycle step that was reached in error — clearing a later step does not
    clear the earlier ones for you. Rescheduling re-notifies the players.
    """
    actor = current_actor().user
    await MatchService().update_match(
        match_id,
        tournament_id=tournament_id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        player_ids=player_ids,
        commentator_ids=commentator_ids,
        tracker_ids=tracker_ids,
        comment=comment,
        clear_seated=clear_seated,
        clear_started=clear_started,
        clear_finished=clear_finished,
        clear_confirmed=clear_confirmed,
        clear_seed=clear_seed,
        actor=actor,
    )
    return await _match_response(match_id)


async def delete_match(
    match_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Delete a match. This cannot be undone — confirm with the user first.

    Nobody is notified: players and crew are not told it is off, so tell them
    yourself or have staff call the match off in the app, which does send DMs.
    """
    actor = current_actor().user
    await MatchService().delete_match(match_id, actor=actor)
    return OperationResult(detail=f'Match {match_id} deleted.')


async def extend_match_to_set(
    match_id: int,
    later_games: List[SetGameTime],
    tenant: TenantArg = None,
    allow_outside_round: bool = False,
) -> List[MatchResponse]:
    """Turn a booked Challonge or start.gg match into a best-of set, keeping it as game 1.

    For a matchup booked as one game before its stage became a best-of. The
    match keeps its crew, seed, stage and comments; games 2 and up are booked
    with the same players, who are DMed as for any booking.
    `later_games` lists games 2, 3 and on in order, each a date (YYYY-MM-DD)
    and time (HH:MM) on the community's own clock: two entries make a
    best-of-3, four a best-of-5. Returns every game, game 1 first.
    """
    body = ExtendToSetRequest(later_games=later_games, allow_outside_round=allow_outside_round)
    games = await BracketSetService().extend_booking(
        match_id, [(g.scheduled_date, g.scheduled_time) for g in body.later_games], current_actor().user,
        allow_outside_round=body.allow_outside_round,
    )
    return [await _match_response(g.id) for g in games]


# --- Stream and stations ----------------------------------------------------


async def set_match_stream_candidate(
    match_id: int,
    flag: bool,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Mark a match as a restream candidate, or drop the mark."""
    actor = current_actor().user
    await MatchService().set_stream_candidate(match_id, flag, actor=actor)
    return await _match_response(match_id)


async def assign_match_stage(
    match_id: int,
    tenant: TenantArg = None,
    stage_id: Optional[int] = None,
) -> MatchResponse:
    """Put a match in a stage, or take it out.

    `stage_id` comes from `list_stages`; omit it to clear the stage.
    """
    actor = current_actor().user
    await MatchService().assign_stage(match_id, stage_id, actor=actor)
    return await _match_response(match_id)


async def assign_match_stations(
    match_id: int,
    assignments: Dict[int, Optional[str]],
    tenant: TenantArg = None,
) -> MatchResponse:
    """Set which station each player is on.

    `assignments` maps a participation row id — `get_match`'s `players[].id`,
    not a user id — to a station label, or to null to clear it.
    """
    actor = current_actor().user
    await MatchService().assign_stations(match_id, assignments, actor=actor)
    return await _match_response(match_id)


# --- Lifecycle --------------------------------------------------------------


async def seat_match(
    match_id: int,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Check a match in. The first lifecycle step, before starting it."""
    actor = current_actor().user
    await MatchScheduleService().seat_match(await _load_match(match_id), actor=actor)
    return await _match_response(match_id)


async def start_match(
    match_id: int,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Start a match. It must be checked in first."""
    actor = current_actor().user
    await MatchScheduleService().start_match(await _load_match(match_id), actor=actor)
    return await _match_response(match_id)


async def finish_match(
    match_id: int,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Finish a match. It must be started first."""
    actor = current_actor().user
    await MatchScheduleService().finish_match(await _load_match(match_id), actor=actor)
    return await _match_response(match_id)


async def confirm_match(
    match_id: int,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Confirm a finished match, closing it out.

    It must be finished and have a recorded result. Confirming also clears any
    review flag.
    """
    actor = current_actor().user
    await MatchScheduleService().confirm_match(await _load_match(match_id), actor=actor)
    return await _match_response(match_id)


async def record_match_result(
    match_id: int,
    winner_id: int,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Record who won a two-player match.

    `winner_id` is the winner's participation row id — `get_match`'s
    `players[].id` — not their user id. Read the match first rather than
    guessing which of the two it is.
    """
    actor = current_actor().user
    await MatchService().record_match_result(match_id, winner_id, actor=actor)
    return await _match_response(match_id)


async def set_match_review(
    match_id: int,
    needs_review: bool,
    tenant: TenantArg = None,
) -> MatchResponse:
    """Flag a recorded result as contested, or clear the flag.

    Raising the flag is the proctor's call, clearing it an admin's. The flag
    carries no note — it says "an admin should look at this", and the
    conversation about what happened lives outside Wizzrobe.
    """
    actor = current_actor().user
    service = MatchService()
    if needs_review:
        await service.flag_for_review(match_id, actor=actor)
    else:
        await service.clear_review(match_id, actor=actor)
    return await _match_response(match_id)


async def generate_match_seed(
    match_id: int,
    tenant: TenantArg = None,
) -> SeedResultResponse:
    """Roll the seed for a match and DM it to the players.

    Uses the tournament's configured randomizer and preset. Refuses while a
    generation for the same match is already running.

    A task-queue randomizer (dk64r) takes a few minutes, so the roll is only
    *started* here: `seed_url` comes back empty and the players are DMed when it
    lands. Every other randomizer returns the permalink directly.
    """
    actor = current_actor().user
    # Resolve the id before handing it to the service. ``generate_seed`` reports
    # every failure as a ``(False, message, None)`` tuple, so an unknown id would
    # otherwise come back as `invalid_request` — and reach Sentry as a logged
    # traceback — where every other tool here answers `not_found`.
    await _load_match(match_id)
    success, message, seed_url = await MatchScheduleService().generate_seed(
        match_id, actor=actor
    )
    if not success:
        raise ValueError(message)
    return SeedResultResponse(message=message, seed_url=seed_url)


async def set_crew_approval(
    crew_id: int,
    role: CrewRole,
    approved: bool,
    tenant: TenantArg = None,
) -> OperationResult:
    """Approve or reject someone's `commentator` or `tracker` signup.

    `crew_id` is the signup's `id` from `list_match_crew`. Approving DMs them
    asking them to confirm; rejecting an approved signup clears their
    confirmation and DMs them that they're off the crew. Sending the state the
    signup is already in changes nothing. Staff, admins of the match's
    tournament and its crew coordinators.
    """
    actor = current_actor().user
    service = CrewService()
    crew_member = require_found(await service.get_crew_member_by_id(crew_id, role), 'Crew signup')
    await service.update_crew_approval(crew_member, role, approved, actor=actor)  # type: ignore[arg-type]
    verdict = 'Approved' if approved else 'Rejected'
    return OperationResult(detail=f'{verdict} {role} signup {crew_id}.')


# --- Your own participation -------------------------------------------------


async def signup_as_crew(
    match_id: int,
    role: CrewRole,
    tenant: TenantArg = None,
) -> OperationResult:
    """Sign yourself up to crew a match as `commentator` or `tracker`.

    Signs up the person this connection belongs to, never anyone else. The
    signup may need staff approval before it counts.
    """
    actor = current_actor().user
    await CrewService().signup_crew(match_id, actor, role)
    return OperationResult(detail=f'Signed up as {role} for match {match_id}.')


async def withdraw_crew_signup(
    match_id: int,
    role: CrewRole,
    tenant: TenantArg = None,
) -> OperationResult:
    """Withdraw your own `commentator` or `tracker` signup from a match."""
    actor = current_actor().user
    await CrewService().undo_crew_signup(match_id, actor, role)
    return OperationResult(detail=f'Withdrew your {role} signup for match {match_id}.')


async def acknowledge_match(
    match_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Acknowledge a match you are playing in, confirming you know about it.

    Only a player of the match can acknowledge it, and only once.
    """
    actor = current_actor().user
    await MatchService().acknowledge_match(match_id, actor)
    return OperationResult(detail=f'Acknowledged match {match_id}.')


async def watch_match(
    match_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Get DMs when a match you are not in changes. Not available once confirmed."""
    actor = current_actor().user
    await MatchWatcherService().watch(match_id, actor)
    return OperationResult(detail=f'Watching match {match_id}.')


async def unwatch_match(
    match_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Stop getting updates about a match you were watching."""
    actor = current_actor().user
    removed = await MatchWatcherService().unwatch(match_id, actor)
    return OperationResult(
        ok=removed,
        detail=(
            f'No longer watching match {match_id}.' if removed
            else f'You were not watching match {match_id}.'
        ),
    )


async def offer_match_for_stream(
    match_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Offer your own match for stream coverage, before it starts.

    Only a player in the match can offer it. Broadcast techs see the offer when
    they choose stream candidates; it does not put the match on stream.
    """
    await MatchStreamVolunteerService().volunteer(match_id, current_actor().user)
    return OperationResult(detail=f'Offered match {match_id} for stream.')


async def withdraw_stream_offer(
    match_id: int,
    tenant: TenantArg = None,
) -> OperationResult:
    """Take back your offer to have your match streamed."""
    removed = await MatchStreamVolunteerService().withdraw(match_id, current_actor().user)
    return OperationResult(
        ok=removed,
        detail=(
            f'Took back the stream offer on match {match_id}.' if removed
            else f'You had not offered match {match_id} for stream.'
        ),
    )


def register_tools(mcp: MCPServer) -> None:
    def write_tool(fn: Callable, title: str, *, scope: ApiScope, destructive: bool = False) -> None:
        """Every tool in this module, registered the same way.

        The gate and ``write=True`` are not per-tool decisions here — they are
        what the module is — so they are stated once rather than repeated
        nineteen times where one of them could quietly go missing.
        """
        register(
            mcp, fn, gate=Gate.ACTOR, scope=scope, write=True, title=title,
            destructive=destructive,
        )

    write_tool(create_match, 'Create match', scope=ApiScope.STAFF_WRITE)
    write_tool(submit_match_request, 'Request match', scope=ApiScope.PLAY)
    write_tool(update_match, 'Update match', scope=ApiScope.STAFF_WRITE)
    write_tool(delete_match, 'Delete match', destructive=True, scope=ApiScope.STAFF_WRITE)
    write_tool(extend_match_to_set, 'Turn match into a set', scope=ApiScope.STAFF_WRITE)
    write_tool(set_match_stream_candidate, 'Set stream candidate', scope=ApiScope.STAFF_WRITE)
    write_tool(assign_match_stage, 'Assign stage', scope=ApiScope.STAFF_WRITE)
    write_tool(assign_match_stations, 'Assign stations', scope=ApiScope.STAFF_WRITE)
    write_tool(seat_match, 'Check in match', scope=ApiScope.STAFF_WRITE)
    write_tool(start_match, 'Start match', scope=ApiScope.STAFF_WRITE)
    write_tool(finish_match, 'Finish match', scope=ApiScope.STAFF_WRITE)
    write_tool(confirm_match, 'Confirm match', scope=ApiScope.STAFF_WRITE)
    write_tool(record_match_result, 'Record result', scope=ApiScope.STAFF_WRITE)
    write_tool(set_match_review, 'Flag or clear review', scope=ApiScope.STAFF_WRITE)
    write_tool(generate_match_seed, 'Generate seed', scope=ApiScope.STAFF_WRITE)
    write_tool(set_crew_approval, 'Approve or reject crew', scope=ApiScope.STAFF_WRITE)
    write_tool(signup_as_crew, 'Sign up as crew', scope=ApiScope.CREW)
    write_tool(withdraw_crew_signup, 'Withdraw crew signup', destructive=True, scope=ApiScope.CREW)
    write_tool(acknowledge_match, 'Acknowledge match', scope=ApiScope.PLAY)
    write_tool(watch_match, 'Watch match', scope=ApiScope.CREW)
    write_tool(unwatch_match, 'Stop watching match', scope=ApiScope.CREW)
    write_tool(offer_match_for_stream, 'Offer match for stream', scope=ApiScope.PLAY)
    write_tool(withdraw_stream_offer, 'Withdraw stream offer', destructive=True, scope=ApiScope.PLAY)
