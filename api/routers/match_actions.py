"""Match write endpoints.

Every route delegates to the service layer, which enforces permissions, writes
audit logs, and queues Discord notifications. Service ``PermissionError`` /
``ValueError`` are mapped to 403 / 400 by ``ServiceErrorRoute``.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from api._match_view import load_match_response
from api.dependencies import ServiceErrorRoute, event_binding, require_write_actor
from api.schemas.detail import DetailResponse
from api.schemas.match_actions import (
    AssignStageRequest,
    AssignStationsRequest,
    CrewSignupRequest,
    ExtendToSetRequest,
    MatchCreateRequest,
    MatchRequestCreate,
    MatchUpdateRequest,
    RecordResultRequest,
    SeedResultResponse,
    SetReviewRequest,
    StationSuggestionResponse,
    StreamCandidateRequest,
)
from api.schemas.matches import MatchResponse
from api.schemas.my_schedule import RunningLateRequest
from application.errors import require_found
from application.services import (
    BracketSetService,
    CrewService,
    MatchScheduleService,
    MatchService,
    MatchStreamVolunteerService,
    MatchWatcherService,
    PlayerLateService,
)
from application.tenant_context import require_tenant_id
from models import Match, User

router = APIRouter(prefix="/matches", tags=["Matches"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(match_id=Match))])


async def _load_match_or_404(match_id: int) -> Match:
    return require_found(
        await Match.get_or_none(id=match_id, tenant_id=require_tenant_id()), "Match"
    )


@router.post(
    "",
    response_model=MatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a match",
    description=(
        "Schedules a match. Players not yet signed up for the tournament are "
        "enrolled, and crew named here are added already approved. Players "
        "get a DM asking them to acknowledge the match, assigned crew get a "
        "DM asking them to confirm, and people who follow the tournament (or "
        "stream candidates, when flagged) are alerted. Fails with 400 when "
        "there are no players, the date or time doesn't parse, it falls "
        "outside the tournament's hours, or someone is listed as both player "
        "and crew."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def create_match(body: MatchCreateRequest, actor: User = Depends(require_write_actor)):
    match = await MatchService().create_match(
        tournament_id=body.tournament_id,
        scheduled_date=body.scheduled_date,
        scheduled_time=body.scheduled_time,
        player_ids=body.player_ids,
        comment=body.comment,
        stage_id=body.stage_id,
        commentator_ids=body.commentator_ids,
        tracker_ids=body.tracker_ids,
        broadcaster_ids=body.broadcaster_ids,
        is_stream_candidate=body.is_stream_candidate,
        actor=actor,
    )
    return await load_match_response(match.id)


@router.post(
    "/request",
    response_model=MatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a match request (player-initiated)",
    description=(
        "Books a match for yourself and your opponents. It's scheduled "
        "straight away, with the same DMs as a staff-created match. Fails "
        "with 400 when the tournament has finished, an opponent isn't signed "
        "up for it, there are more players than the tournament allows, or the "
        "time is in the past or outside the tournament's hours. Fails with "
        "403 when you aren't one of the players or the tournament is "
        "scheduled from its bracket instead."
        "\n\n"
        "**Who can call this:** Any member who plays in the match."
    ),
)
async def submit_match_request(body: MatchRequestCreate, actor: User = Depends(require_write_actor)):
    match = await MatchService().submit_match_request(
        tournament_id=body.tournament_id,
        scheduled_date=body.scheduled_date,
        scheduled_time=body.scheduled_time,
        player_ids=body.player_ids,
        actor=actor,
        comment=body.comment,
    )
    return await load_match_response(match.id)


@router.patch(
    "/{match_id}",
    response_model=MatchResponse,
    summary="Update a match",
    description=(
        "Changes any of the fields sent and leaves the rest alone; the "
        "`clear_*` flags rewind the match's progress or drop its seed. Moving "
        "the time or changing the players asks the players to acknowledge the "
        "match again; a new time is also DMed to crew and the tournament's "
        "followers. Players and crew who are added or removed are DMed. Fails "
        "with 400 when the new time is outside the tournament's hours, "
        "someone is listed as both player and crew, or the match is synced "
        "from SpeedGaming and the change touches its tournament, time or "
        "players. Moving the match to another tournament also needs admin "
        "rights there."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def update_match(match_id: int, body: MatchUpdateRequest, actor: User = Depends(require_write_actor)):
    await MatchService().update_match(
        match_id,
        tournament_id=body.tournament_id,
        scheduled_date=body.scheduled_date,
        scheduled_time=body.scheduled_time,
        player_ids=body.player_ids,
        commentator_ids=body.commentator_ids,
        tracker_ids=body.tracker_ids,
        broadcaster_ids=body.broadcaster_ids,
        comment=body.comment,
        clear_seated=body.clear_seated,
        clear_started=body.clear_started,
        clear_finished=body.clear_finished,
        clear_confirmed=body.clear_confirmed,
        clear_seed=body.clear_seed,
        actor=actor,
    )
    return await load_match_response(match_id)


@router.delete(
    "/{match_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a match",
    description=(
        "Deletes the match and everything attached to it (players, crew, "
        "acknowledgments, watchers). A bracket slot the match held is freed. "
        "Nobody is DMed. To call a match off and tell its players, approve a "
        "'cancel' reschedule request instead."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def delete_match(match_id: int, actor: User = Depends(require_write_actor)):
    await MatchService().delete_match(match_id, actor=actor)


@router.post(
    "/{match_id}/extend-to-set",
    response_model=List[MatchResponse],
    summary="Turn a booked bracket match into a best-of set",
    description=(
        "For a Challonge or start.gg matchup booked as one game before its stage "
        "became a best-of. The match stays as game 1, keeping its crew, seed, "
        "stage and comments, and games 2 and up are booked at the times given, "
        "with the same players, who are DMed as for any booking. Returns every "
        "game, game 1 first. Fails with 400 when the match is already in a set, "
        "has started, isn't linked to a bracket, or a time is out of order or "
        "outside the stage's window (unless `allow_outside_round`)."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def extend_to_set(match_id: int, body: ExtendToSetRequest, actor: User = Depends(require_write_actor)):
    games = await BracketSetService().extend_booking(
        match_id,
        [(g.scheduled_date, g.scheduled_time) for g in body.later_games],
        actor,
        allow_outside_round=body.allow_outside_round,
    )
    return [await load_match_response(g.id) for g in games]


@router.post(
    "/{match_id}/stream-candidate",
    response_model=MatchResponse,
    summary="Set/clear stream candidate",
    description=(
        "Marks the match as a stream candidate, or clears the mark. Setting "
        "it alerts people who follow stream candidates. It doesn't put the "
        "match on a stage."
        "\n\n"
        "**Who can call this:** Staff, Broadcast tech or Admins of this tournament."
    ),
)
async def set_stream_candidate(match_id: int, body: StreamCandidateRequest, actor: User = Depends(require_write_actor)):
    await MatchService().set_stream_candidate(match_id, body.flag, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/stage",
    response_model=MatchResponse,
    summary="Assign or clear the stage",
    description=(
        "Puts the match on a stage, or takes it off one when `stage_id` is "
        "null. Players, approved crew and watchers are DMed either way, and "
        "the pre-match stage reminder is re-armed."
        "\n\n"
        "**Who can call this:** Staff, Broadcast tech or Admins of this tournament."
    ),
)
async def assign_stage(match_id: int, body: AssignStageRequest, actor: User = Depends(require_write_actor)):
    await MatchService().assign_stage(match_id, body.stage_id, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/stations",
    response_model=MatchResponse,
    summary="Assign player stations",
    description=(
        "Sets the station each player sits at. Fails with 400 when the "
        "tournament races on racetime.gg, two players share a station, a "
        "label doesn't match the community's station format or list, a "
        "station is taken by another match, or a key isn't one of the match's "
        "player slots."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def assign_stations(match_id: int, body: AssignStationsRequest, actor: User = Depends(require_write_actor)):
    await MatchService().assign_stations(match_id, body.assignments, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/stations/suggest",
    response_model=StationSuggestionResponse,
    summary="Draw a random station for each player, on opposite sides",
    description=(
        "Proposes a seating for a two-player match without saving it; send "
        "the result to `POST /matches/{match_id}/stations` to apply it. Fails "
        "with 400 when the tournament races on racetime.gg, the match doesn't "
        "have exactly two players, or the community has no stations set up."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def suggest_stations(match_id: int, actor: User = Depends(require_write_actor)):
    """Propose a seating without applying it.

    A preview of a write rather than a read, which is why it takes the write
    actor: the caller's next move is ``POST /matches/{id}/stations`` with the
    assignments it returns, and a read-only token has no business drawing seats
    it cannot then take.
    """
    suggestion = await MatchService().suggest_stations(match_id, actor=actor)
    return StationSuggestionResponse(
        assignments=suggestion.assignments,
        relaxations=suggestion.relaxations,
    )


@router.post(
    "/{match_id}/seat",
    response_model=MatchResponse,
    summary="Check in (seat) a match",
    description=(
        "Marks the players as checked in. Players, approved crew and watchers "
        "are DMed. Fails with 400 when the tournament races on racetime.gg, "
        "the match has no players, it's already checked in, or someone else "
        "changed it first."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def seat_match(match_id: int, actor: User = Depends(require_write_actor)):
    match = await _load_match_or_404(match_id)
    await MatchScheduleService().seat_match(match, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/start",
    response_model=MatchResponse,
    summary="Start a match",
    description=(
        "Marks the match as started, which closes crew signup. Players, "
        "approved crew and watchers are DMed. Fails with 400 when the match "
        "isn't checked in yet, is already started, or someone else changed it "
        "first."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def start_match(match_id: int, actor: User = Depends(require_write_actor)):
    match = await _load_match_or_404(match_id)
    await MatchScheduleService().start_match(match, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/finish",
    response_model=MatchResponse,
    summary="Finish a match",
    description=(
        "Marks the match as finished. Players, approved crew and watchers are "
        "DMed. Fails with 400 when the match hasn't started, is already "
        "finished, or someone else changed it first."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def finish_match(match_id: int, actor: User = Depends(require_write_actor)):
    match = await _load_match_or_404(match_id)
    await MatchScheduleService().finish_match(match, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/confirm",
    response_model=MatchResponse,
    summary="Confirm a match",
    description=(
        "Makes the recorded result official and clears any review flag. "
        "Players, approved crew and watchers are DMed, the result is pushed "
        "to Challonge when the match is linked to it, and a linked bracket "
        "advances. Fails with 400 when the match isn't finished, is already "
        "confirmed, has no result recorded, or someone else changed it first."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def confirm_match(match_id: int, actor: User = Depends(require_write_actor)):
    match = await _load_match_or_404(match_id)
    await MatchScheduleService().confirm_match(match, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/result",
    response_model=MatchResponse,
    summary="Record a match result",
    description=(
        "Records the winner of a two-player match; everyone else is ranked "
        "second. Nobody is DMed until the match is confirmed. Fails with 400 "
        "when the match has no players, `winner_id` isn't one of its player "
        "slots, or its bracket game is already settled (correct it from the "
        "bracket instead). Changing the result of a confirmed match needs "
        "Staff or Admins of this tournament."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def record_result(match_id: int, body: RecordResultRequest, actor: User = Depends(require_write_actor)):
    await MatchService().record_match_result(match_id, body.winner_id, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/review",
    response_model=MatchResponse,
    summary="Flag or clear a result review",
    description=(
        "Flags a recorded result for an admin to check (`needs_review: "
        "true`), or clears the flag without confirming (`false`). Fails with "
        "400 when flagging a match that isn't finished or is already "
        "confirmed."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament to flag; Staff or Admins of this tournament to clear."
    ),
)
async def set_review(match_id: int, body: SetReviewRequest, actor: User = Depends(require_write_actor)):
    """Raise or drop the dispute flag on a recorded result.

    The two directions are deliberately gated differently: flagging is the
    proctor's (``can_run_match``), clearing is the admin's (``can_confirm_match``).
    """
    service = MatchService()
    if body.needs_review:
        await service.flag_for_review(match_id, actor=actor)
    else:
        await service.clear_review(match_id, actor=actor)
    return await load_match_response(match_id)


@router.post(
    "/{match_id}/seed",
    response_model=SeedResultResponse,
    summary="Generate a seed for a match",
    description=(
        "Rolls the match's seed with the tournament's preset or randomizer "
        "and DMs it to the players. Some randomizers roll in the background: "
        "the call returns at once with no `seed_url`, and the players are "
        "DMed when the seed lands. A match gets one seed; clear it with "
        "`PATCH /matches/{match_id}` to roll again. Fails with 400 when you "
        "lack permission, a seed already exists or is rolling, the tournament "
        "has no randomizer set up, the match has no players, or the "
        "randomizer fails."
        "\n\n"
        "**Who can call this:** Staff, Proctor, Broadcast tech (matches on a stage) or Admins of this tournament."
    ),
)
async def generate_seed(match_id: int, actor: User = Depends(require_write_actor)):
    success, message, seed_url = await MatchScheduleService().generate_seed(match_id, actor=actor)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return SeedResultResponse(message=message, seed_url=seed_url)


@router.post(
    "/{match_id}/crew",
    response_model=DetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign yourself up as crew",
    description=(
        "Signs you up as commentator, tracker or broadcaster. The signup waits for "
        "approval, and the tournament's admins and crew coordinators are DMed "
        "about it. Fails with 400 when the role is unknown or the tournament "
        "doesn't use it (broadcaster also needs an event with broadcasters on), the match has started or finished, you're already "
        "signed up for that role, or you play in the match."
        "\n\n"
        "**Who can call this:** Any member."
    ),
)
async def signup_crew(match_id: int, body: CrewSignupRequest, actor: User = Depends(require_write_actor)):
    await CrewService().signup_crew(match_id, actor, body.role)
    return DetailResponse(detail=f"Signed up as {body.role}")


@router.delete(
    "/{match_id}/crew/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Undo your crew signup",
    description=(
        "Removes your own crew signup; `role` is "
        "`commentator`, `tracker` or `broadcaster`. When it was already approved, the "
        "tournament's admins and crew coordinators, and whoever approved you, "
        "are DMed that you dropped out. Fails with 400 when you aren't signed "
        "up for that role."
        "\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def undo_crew_signup(match_id: int, role: str, actor: User = Depends(require_write_actor)):
    await CrewService().undo_crew_signup(match_id, actor, role)


@router.post(
    "/{match_id}/acknowledge",
    response_model=DetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Acknowledge your match",
    description=(
        "Confirms you've seen when your match is. Fails with 400 when you "
        "don't play in it or have already acknowledged it."
        "\n\n"
        "**Who can call this:** Any member who plays in the match."
    ),
)
async def acknowledge_match(match_id: int, actor: User = Depends(require_write_actor)):
    await MatchService().acknowledge_match(match_id, actor)
    return DetailResponse(detail="Match acknowledged")


@router.post(
    "/{match_id}/running-late",
    response_model=DetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Say you're running late",
    description=(
        "Tells the match's staff and on-duty desk you're on your way, with how late you "
        "expect to be and an optional note. Saying it again with a new number updates it. "
        "Opens 30 minutes before the match (earlier for a stage match with a reminder). "
        "Fails with 400 when you don't play in it, you're already checked in, the window "
        "isn't open, or the match is raced online."
        "\n\n"
        "**Who can call this:** Any member who plays in the match."
    ),
)
async def running_late(match_id: int, body: RunningLateRequest, actor: User = Depends(require_write_actor)):
    await PlayerLateService().report_late(match_id, actor, body.minutes, body.note)
    return DetailResponse(detail="Heads-up sent")


@router.post(
    "/{match_id}/watch",
    response_model=DetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Watch a match for updates",
    description=(
        "Starts sending you a Discord DM at each step of the match's run. "
        "Watching a match you already watch changes nothing. Fails with 400 "
        "when the match is already confirmed."
        "\n\n"
        "**Who can call this:** Any member."
    ),
)
async def watch_match(match_id: int, actor: User = Depends(require_write_actor)):
    await MatchWatcherService().watch(match_id, actor)
    return DetailResponse(detail="Watching match")


@router.delete(
    "/{match_id}/watch",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop watching a match",
    description=(
        "Stops the DMs `POST /matches/{match_id}/watch` started. Succeeds "
        "even when you weren't watching."
        "\n\n"
        "**Who can call this:** Any member."
    ),
)
async def unwatch_match(match_id: int, actor: User = Depends(require_write_actor)):
    await MatchWatcherService().unwatch(match_id, actor)


@router.post(
    "/{match_id}/stream-offer",
    response_model=DetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Offer your match for stream",
    description=(
        "Tells the people who pick stream matches that you're happy to be "
        "streamed. It doesn't put the match on stream or make it a stream "
        "candidate. Fails with 400 when you don't play in the match or it has "
        "started."
        "\n\n"
        "**Who can call this:** Any member who plays in the match."
    ),
)
async def offer_for_stream(match_id: int, actor: User = Depends(require_write_actor)):
    """For a player in the match, before it starts. Broadcast techs see the offer
    when they pick stream candidates; it does not put the match on stream."""
    await MatchStreamVolunteerService().volunteer(match_id, actor)
    return DetailResponse(detail="Offered for stream")


@router.delete(
    "/{match_id}/stream-offer",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take back your stream offer",
    description=(
        "Withdraws your stream offer, even after the match has started. Fails "
        "with 400 when you don't play in the match."
        "\n\n"
        "**Who can call this:** Any member who plays in the match."
    ),
)
async def withdraw_stream_offer(match_id: int, actor: User = Depends(require_write_actor)):
    await MatchStreamVolunteerService().withdraw(match_id, actor)
