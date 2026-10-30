"""Native bracket endpoints — thin routers over ``BracketService``.

Tenant- and feature-scoped by the mount (``FeatureFlag.BRACKETS``). Reads use the
any-token actor dep and stay role-agnostic (the service scopes by tenant); writes
reject read-only tokens at the HTTP layer and re-gate on Staff in the service.

The one exception is ``POST /matches/{id}/games``: ``schedule_bracket_match`` is
open to Staff, the target tournament's admin, **and** the matchup's own two
entrants — in a bracket-run tournament the bracket is the only way a player
schedules at all. It gates for itself and routes privileged callers through
``create_match`` and entrants through ``submit_match_request``; the extra
scheduling fields (stage, crew) are rejected for an entrant.
"""

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.brackets import (
    AdvanceStageRequest,
    BracketCreateRequest,
    BracketEntrantResponse,
    BracketEntryResponse,
    BracketMatchResponse,
    BracketResponse,
    BracketUpdateRequest,
    CompleteStageRequest,
    EnrollRequest,
    EntrantCreateRequest,
    EntrantUserRequest,
    LinkGameRequest,
    ReportResultRequest,
    RosterImportRequest,
    RoundMetadataRequest,
    ScheduleGameRequest,
    SetBestOfRequest,
    SetSeedsRequest,
    StandingsGroupResponse,
)
from application.errors import require_found
from application.services import BracketService
from models import Bracket, BracketEntrant, BracketEntry, BracketMatch, Match, User

router = APIRouter(prefix="/brackets", tags=["Brackets"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(bracket_id=Bracket, match_id=BracketMatch, entrant_id=BracketEntrant, entry_id=BracketEntry, scheduled_match_id=Match))])


@router.get(
    "",
    response_model=List[BracketResponse],
    summary="List brackets for a tournament",
    description=(
        "Lists a tournament's bracket stages. Draft and cancelled stages are listed only for "
        "Staff.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_brackets(
    tournament_id: int = Query(..., description="The tournament whose stages to list."),
    actor: User = Depends(require_api_actor),
):
    # Viewer-aware: DRAFT and CANCELLED stages are staff-only here exactly as
    # on the public pages.
    return await BracketService().list_visible_brackets(actor, tournament_id)


@router.get(
    "/entrants",
    response_model=List[BracketEntrantResponse],
    summary="List a tournament's entrants",
    description=(
        "Lists a tournament's roster of entrants, shared by all its stages. A tournament with "
        "no published stage answers 404 to everyone but Staff.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_entrants(
    tournament_id: int = Query(..., description="The tournament whose roster to list."),
    actor: User = Depends(require_api_actor),
):
    # A tournament with nothing published has not been announced, and neither
    # has its roster: same 404 as an unknown id, to everyone but staff.
    service = BracketService()
    require_found(await service.get_visible_tournament(actor, tournament_id), "Tournament")
    return await service.list_entrants(tournament_id)


@router.get(
    "/my-open-matches",
    response_model=List[BracketMatchResponse],
    summary="List your own open, schedulable matchups",
    description=(
        "Lists your own open matchups that still have a game left to schedule, optionally in "
        "one tournament. Only matchups where both players have linked accounts are included, "
        "because only those can be booked.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_my_open_matches(
    tournament_id: Optional[int] = Query(None, description="Only matchups in this tournament. Omit for all."),
    actor: User = Depends(require_api_actor),
):
    """The caller's OPEN matchups that still have a game left to schedule.

    Only matchups whose *both* entrants are linked to a user are listed — those
    are the ones bookable into a real ``Match``.
    """
    return await BracketService().list_open_matches_for_user(actor.id, tournament_id)


@router.get(
    "/advancing-preview",
    response_model=List[BracketEntryResponse],
    summary="Preview which entries a stage advance would carry forward",
    description=(
        "Shows which entries of a completed stage would move into the next stage, in the "
        "order they'd advance, without changing anything. Fails with 400 in the same cases as "
        "`POST /brackets/advance-stage`.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def advancing_preview(
    tournament_id: int = Query(..., description="The tournament the stages belong to."),
    from_stage_order: int = Query(..., description="The completed stage to advance from."),
    actor: User = Depends(require_api_actor),
):
    """Dry run of ``POST /brackets/advance-stage``: the source-stage entries that
    would advance, in advancement order. Writes nothing, and raises the same
    guards the real advance does.
    """
    return await BracketService().preview_advance(actor, tournament_id, from_stage_order)


@router.get(
    "/matches/{match_id}",
    response_model=BracketMatchResponse,
    summary="Get one bracket match with its series games",
    description=(
        "Returns one matchup with the games booked for its series. A matchup in a draft or "
        "cancelled stage is visible only to Staff; anyone else gets 404.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_match(match_id: int, actor: User = Depends(require_api_actor)):
    return require_found(
        await BracketService().get_visible_match_with_games(actor, match_id), "Bracket match"
    )


@router.get(
    "/{bracket_id}",
    response_model=BracketResponse,
    summary="Get a bracket",
    description=(
        "Returns one stage. Draft and cancelled stages are visible only to Staff; anyone else "
        "gets 404.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_bracket(bracket_id: int, actor: User = Depends(require_api_actor)):
    return require_found(await BracketService().get_visible_bracket(actor, bracket_id), "Bracket")


@router.get(
    "/{bracket_id}/standings",
    response_model=List[StandingsGroupResponse],
    summary="Live standings of a round-robin or Swiss bracket",
    description=(
        "Returns live points standings for a round-robin or Swiss stage: one group per pool "
        "for round robin, a single group for Swiss. Fails with 400 for an elimination stage, "
        "whose placings come from its matchups and each entry's `final_rank`.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_standings(bracket_id: int, actor: User = Depends(require_api_actor)):
    """Points, tiebreakers and rank per entry, one entry per group.

    Round robin returns one group per pool; Swiss returns a single group.
    Elimination formats have no points table and are rejected with a 400 — read
    their placement from ``/matches`` and each entry's ``final_rank``.
    """
    service = BracketService()
    require_found(await service.get_visible_bracket(actor, bracket_id), "Bracket")
    return await service.standings(bracket_id)


@router.get(
    "/{bracket_id}/matches",
    response_model=List[BracketMatchResponse],
    summary="List a bracket's matches",
    description=(
        "Lists every matchup in a stage, each with the status the web bracket shows.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_matches(bracket_id: int, actor: User = Depends(require_api_actor)):
    service = BracketService()
    require_found(await service.get_visible_bracket(actor, bracket_id), "Bracket")
    return await _with_status(service, await service.list_matches(bracket_id))


@router.get(
    "/{bracket_id}/open-matches",
    response_model=List[BracketMatchResponse],
    summary="List a bracket's open (playable) matches",
    description=(
        "Lists a stage's matchups that are ready to play, each with the status the web "
        "bracket shows.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_open_matches(bracket_id: int, actor: User = Depends(require_api_actor)):
    service = BracketService()
    require_found(await service.get_visible_bracket(actor, bracket_id), "Bracket")
    return await _with_status(service, await service.get_open_matches(bracket_id))


async def _with_status(service: BracketService, matches: list) -> list:
    """Stamp each matchup with its derived cross-surface status (U1/U2).

    ``BracketMatchResponse`` reads attributes off the ORM rows, so the derived
    value is attached to each row rather than assembled into a second shape —
    one batched query for the whole list, and the API reports the same status
    the web bracket paints and the Discord DM quotes.
    """
    live = await service.matchup_live_state(matches)
    for match in matches:
        match.status = (live.get(match.id) or {}).get('status')
    return matches


@router.get(
    "/{bracket_id}/entries",
    response_model=List[BracketEntryResponse],
    summary="List a bracket's entries",
    description=(
        "Lists a stage's entries with their seeds, pools and final ranks.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_entries(bracket_id: int, actor: User = Depends(require_api_actor)):
    require_found(await BracketService().get_visible_bracket(actor, bracket_id), "Bracket")
    return await BracketService().list_entries(bracket_id)


@router.post(
    "",
    response_model=BracketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a bracket",
    description=(
        "Adds a draft stage to a tournament. Adding the first stage (`stage_order` 0) turns "
        "off the tournament's player match requests, because players then book through the "
        "bracket. Fails with 400 when the tournament is linked to Challonge, the name is "
        "empty, the config is invalid, or another stage already has that `stage_order`.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def create_bracket(body: BracketCreateRequest, actor: User = Depends(require_write_actor)):
    return await BracketService().create_bracket(
        actor,
        tournament_id=body.tournament_id,
        name=body.name,
        format=body.format,
        stage_order=body.stage_order,
        config=body.config,
    )


@router.patch(
    "/{bracket_id}",
    response_model=BracketResponse,
    summary="Edit a DRAFT bracket",
    description=(
        "Edits a draft stage; omitted fields keep their value. Fails with 400 when the stage "
        "has started, the name is empty, the config is invalid, or another stage already has "
        "the new `stage_order`.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update_bracket(
    bracket_id: int, body: BracketUpdateRequest, actor: User = Depends(require_write_actor)
):
    return await BracketService().update_bracket(
        actor, bracket_id, **body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{bracket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a DRAFT bracket",
    description=(
        "Deletes a draft or cancelled stage. Fails with 400 for an active or complete stage.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def delete_bracket(bracket_id: int, actor: User = Depends(require_write_actor)):
    await BracketService().delete_bracket(actor, bracket_id)


@router.put(
    "/{bracket_id}/rounds",
    response_model=BracketResponse,
    summary="Replace a bracket's per-round display metadata",
    description=(
        "Replaces a stage's per-round settings: series length and scheduling window. Works in "
        "any state; a new window changes future time suggestions only, not matches already "
        "booked. Fails with 400 when a value is invalid.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_round_metadata(
    bracket_id: int, body: RoundMetadataRequest, actor: User = Depends(require_write_actor)
):
    return await BracketService().set_round_metadata(actor, bracket_id, body.rounds)


@router.patch(
    "/{bracket_id}/seeds",
    response_model=List[BracketEntryResponse],
    summary="Set entry seeds for a DRAFT bracket",
    description=(
        "Sets seeds for entries in a draft stage and returns the stage's entries in their new "
        "order. The whole result is checked first, so a duplicate or out-of-range seed "
        "changes nothing. Fails with 400 when the stage has started, a seed is below 1 or "
        "used twice, or an entry belongs to another stage.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_seeds(
    bracket_id: int, body: SetSeedsRequest, actor: User = Depends(require_write_actor)
):
    """Reseed in one shot: the whole resulting seeding is validated before any
    write, so a duplicate or out-of-range seed changes nothing. Returns the
    bracket's entries in their new seed order.
    """
    service = BracketService()
    await service.set_seeds(actor, bracket_id, body.seeds)
    return await service.list_entries(bracket_id)


@router.post(
    "/entrants",
    response_model=BracketEntrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a tournament entrant",
    description=(
        "Adds an entrant to a tournament's roster, either linked to a member's account or as "
        "a placeholder to link later. Fails with 400 when the name is empty.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def add_entrant(body: EntrantCreateRequest, actor: User = Depends(require_write_actor)):
    return await BracketService().add_entrant(
        actor,
        tournament_id=body.tournament_id,
        display_name=body.display_name,
        user_id=body.user_id,
    )


@router.post(
    "/entrants/import",
    response_model=List[BracketEntrantResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Import a tournament's enrolled players as entrants",
    description=(
        "Adds a linked entrant for every player enrolled in the tournament who isn't on the "
        "roster yet, and returns only the new ones. Safe to run again after late signups.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def import_entrants(body: RosterImportRequest, actor: User = Depends(require_write_actor)):
    """Create a linked entrant per enrolled player. Returns only the new ones.

    Idempotent — a player who already has an entrant is skipped — so it is safe
    to re-run after late signups.
    """
    return await BracketService().import_entrants_from_roster(actor, body.tournament_id)


@router.patch(
    "/entrants/{entrant_id}/user",
    response_model=BracketEntrantResponse,
    summary="Link (or unlink) an entrant's user account",
    description=(
        "Links an entrant to a member's account, or unlinks it with null. Allowed in any "
        "state, because the link is what lets the entrant's matchups be scheduled and DMs "
        "reach them.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_entrant_user(
    entrant_id: int, body: EntrantUserRequest, actor: User = Depends(require_write_actor)
):
    """Attach a user to a placeholder entrant, or null to detach.

    The link is what makes the entrant's matchups schedulable and notifiable, so
    it stays editable after a stage has started.
    """
    return await BracketService().set_entrant_user(actor, entrant_id, body.user_id)


@router.post(
    "/entrants/{entrant_id}/drop",
    response_model=BracketEntrantResponse,
    summary="Drop a tournament entrant",
    description=(
        "Marks an entrant dropped from the tournament roster. Their played results still "
        "count. It doesn't retire them from a running stage; use `POST "
        "/brackets/entries/{entry_id}/retire` for that.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def drop_entrant(entrant_id: int, actor: User = Depends(require_write_actor)):
    """Mark the entrant DROPPED on the tournament roster.

    Their played results stand — they keep counting for everyone else's
    standings. Note this is the *roster* state only: it does not cascade into
    their per-stage ``BracketEntry.status``, which is what stage advancement
    filters on, so a drop mid-stage still needs the entry retired separately.
    """
    return await BracketService().drop_entrant(actor, entrant_id)


@router.post(
    "/{bracket_id}/entries",
    response_model=BracketEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll an entrant into a bracket",
    description=(
        "Adds a roster entrant to a draft stage. Fails with 400 when the stage has started, "
        "the entrant belongs to another tournament, or is already in this stage.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def enroll(bracket_id: int, body: EnrollRequest, actor: User = Depends(require_write_actor)):
    return await BracketService().enroll(
        actor,
        bracket_id=bracket_id,
        entrant_id=body.entrant_id,
        seed=body.seed,
        group_number=body.group_number,
    )


@router.post(
    "/{bracket_id}/start",
    response_model=BracketResponse,
    summary="Start a bracket (generate the match graph)",
    description=(
        "Starts a draft stage: generates its matchups and DMs the linked players of every "
        "matchup that's ready to play. A later stage can start only once the stage before it "
        "is complete. Fails with 400 when the stage isn't a draft, the previous stage isn't "
        "complete, or fewer than two active entries are enrolled.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def start_bracket(bracket_id: int, actor: User = Depends(require_write_actor)):
    return await BracketService().start_bracket(actor, bracket_id)


@router.post(
    "/matches/{match_id}/result",
    response_model=BracketMatchResponse,
    summary="Report a match result",
    description=(
        "Records the winner of an open matchup, with optional scores, and moves the winner "
        "and loser on. Players whose next matchup becomes ready get a DM, unplayed games left "
        "in the series are cancelled, and an elimination stage completes itself once its "
        "final is decided. Fails with 400 when the stage isn't active, the matchup isn't "
        "open, the winner isn't one of its entries, or the scores don't fit (both or neither, "
        "never negative, and the winner higher unless `forfeit` is set).\n\n"
        "**Who can call this:** Staff."
    ),
)
async def report_result(match_id: int, body: ReportResultRequest, actor: User = Depends(require_write_actor)):
    service = BracketService()
    await service.report_result(
        actor, match_id, body.winner_entry_id,
        entry1_score=body.entry1_score,
        entry2_score=body.entry2_score,
        forfeit=body.forfeit,
    )
    # Re-read with games loaded: the response embeds them, and report_result
    # cancels any pending game, so the caller should see that.
    return require_found(await service.get_match_with_games(match_id), "Bracket match")


@router.patch(
    "/matches/{match_id}/result",
    response_model=BracketMatchResponse,
    summary="Correct an already-reported match result",
    description=(
        "Corrects the winner or scores of a completed matchup and moves the corrected winner "
        "and loser into the following matchups. If the stage had already completed, its final "
        "ranks are worked out again. Fails with 400 when the matchup isn't complete, a "
        "matchup after it has already been played, the winner isn't one of its entries, or "
        "the scores don't fit.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def override_result(
    match_id: int, body: ReportResultRequest, actor: User = Depends(require_write_actor)
):
    """Staff correction of a COMPLETE match's winner and scores.

    Rejected while any match downstream of this one is itself already COMPLETE —
    undo those first. On success the amended winner/loser are re-pushed into the
    downstream slots and, if the stage had already finalized, its ranks are
    recomputed from the corrected results.
    """
    service = BracketService()
    await service.override_result(
        actor, match_id, body.winner_entry_id,
        entry1_score=body.entry1_score,
        entry2_score=body.entry2_score,
        forfeit=body.forfeit,
    )
    return require_found(await service.get_match_with_games(match_id), "Bracket match")


@router.post(
    "/matches/{match_id}/games",
    response_model=BracketMatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule the next game of a match's series",
    description=(
        "Books the next game of a matchup as a match at the given time; the game number is "
        "assigned for you. Call it once per game to book a whole series, including every game "
        "up front. The players get the usual match-scheduled notification. A player booking "
        "their own matchup can't set `stage_id`, and must pick a time in the future. Fails "
        "with 400 when the matchup isn't open, its stage was cancelled, either player isn't "
        "linked, every game is already booked or the series is decided, or the date or time "
        "is invalid.\n\n"
        "**Who can call this:** Staff, Admins of this tournament, or either player in the "
        "matchup."
    ),
)
async def schedule_game(
    match_id: int, body: ScheduleGameRequest, actor: User = Depends(require_write_actor)
):
    """Book the next game of this matchup into a real ``Match``.

    The game number is assigned server-side — call this once per game to book a
    best-of-N, including all N slots up front. Rejected once every slot is taken
    or the series is decided.

    Staff and the tournament's admins may book on anyone's behalf; the matchup's
    two entrants may book their own. ``stage_id`` is staff-only, so it is
    forwarded only when actually set — an entrant sending it explicitly gets a
    400 rather than having it silently dropped.
    """
    service = BracketService()
    extra: dict[str, Any] = {} if body.stage_id is None else {'stage_id': body.stage_id}
    await service.schedule_bracket_match(
        actor, match_id,
        scheduled_date=body.scheduled_date,
        scheduled_time=body.scheduled_time,
        comment=body.comment,
        **extra,
    )
    return require_found(await service.get_match_with_games(match_id), "Bracket match")


@router.post(
    "/matches/{match_id}/games/link",
    response_model=BracketMatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach an existing scheduled match to this matchup",
    description=(
        "Records a match that was already scheduled as the next game of this matchup. Fails "
        "with 400 when the matchup isn't open, the series is decided, the match is in another "
        "tournament or already linked, or its players aren't exactly the matchup's two "
        "entrants.\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def link_game(
    match_id: int, body: LinkGameRequest, actor: User = Depends(require_write_actor)
):
    """Record an already-created ``Match`` as the next game of this matchup.

    Staff / tournament admin only. The match's players must be exactly the
    matchup's two entrants, or advancement could never resolve a winner.
    """
    service = BracketService()
    await service.link_match_to_bracket_match(actor, match_id, body.scheduled_match_id)
    return require_found(await service.get_match_with_games(match_id), "Bracket match")


@router.delete(
    "/games/by-match/{scheduled_match_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Detach a scheduled match from its matchup",
    description=(
        "Detaches a match from its matchup's series and leaves the match itself on the "
        "schedule. Fails with 400 when the game has already been played, and 404 when the "
        "match isn't linked to a matchup.\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def unlink_game(
    scheduled_match_id: int, actor: User = Depends(require_write_actor)
):
    """Detach the match from its series, leaving the ``Match`` itself in place.

    Only a game that has not been played can be detached.
    """
    if not await BracketService().unlink_match(actor, scheduled_match_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That match is not linked to a bracket matchup.",
        )


@router.patch(
    "/matches/{match_id}/best-of",
    response_model=BracketMatchResponse,
    summary="Override a match's series length",
    description=(
        "Sets how many games this matchup's series runs, overriding the round's setting; null "
        "removes the override. Fails with 400 when the number isn't odd and at least 1, or a "
        "game is already booked.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_best_of(
    match_id: int, body: SetBestOfRequest, actor: User = Depends(require_write_actor)
):
    await BracketService().set_best_of(actor, match_id, body.best_of)
    return require_found(
        await BracketService().get_match_with_games(match_id), "Bracket match"
    )


@router.post(
    "/{bracket_id}/complete",
    response_model=BracketResponse,
    summary="Complete a bracket stage",
    description=(
        "Finishes an active stage and writes every entry's final rank. Send `tie_breaks` to "
        "settle entries the standings left tied. Fails with 400 when the stage isn't active, "
        "an elimination final hasn't been decided, or a round-robin or Swiss matchup is still "
        "unplayed.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def complete_stage(
    bracket_id: int,
    body: Optional[CompleteStageRequest] = None,
    actor: User = Depends(require_write_actor),
):
    """Finalize a stage, writing every entry's ``final_rank``.

    ``tie_breaks`` (``{entry_id: rank}``) resolves entries the standings pass
    left sharing a rank — the point of the staff-triggered finalizer on a Swiss
    stage, where an unresolved tie for the last advancing slot otherwise decides
    itself.
    """
    return await BracketService().complete_stage(
        actor, bracket_id, body.tie_breaks if body else None,
    )


@router.post(
    "/{bracket_id}/cancel",
    response_model=BracketResponse,
    summary="Cancel (abandon) a bracket stage",
    description=(
        "Abandons a stage that won't be finished. Played results are kept, but no final ranks "
        "are written, the stage disappears from public views, and no later stage can draw "
        "from it. Fails with 400 when the stage is already cancelled or complete.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def cancel_stage(bracket_id: int, actor: User = Depends(require_write_actor)):
    """Abandon a stage: terminal, but no ``final_rank`` and no champion.

    For a stage that was started and then called off. Its played results stay as
    history, it disappears from the public views, and nothing can advance out of
    it — there is no ranking to draw from. Rejected on a COMPLETE stage.
    """
    return await BracketService().cancel_stage(actor, bracket_id)


@router.delete(
    "/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Un-enroll an entry from a DRAFT bracket",
    description=(
        "Removes an entry from a draft stage. Once a stage has started, retire the entry "
        "instead. Fails with 400 when the stage has started.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def unenroll(entry_id: int, actor: User = Depends(require_write_actor)):
    """Remove an entry outright. DRAFT-only — retire it instead once started."""
    await BracketService().unenroll(actor, entry_id)


@router.post(
    "/entries/{entry_id}/retire",
    response_model=BracketEntryResponse,
    summary="Retire an entry from a running stage",
    description=(
        "Marks a stage entry dropped, in any state. Their played results still count; Swiss "
        "stops pairing them and stage advancement skips them. It doesn't drop them from the "
        "tournament roster. Fails with 400 when the entry is already retired.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def retire_entry(entry_id: int, actor: User = Depends(require_write_actor)):
    """Mark the entry DROPPED. Allowed in any state.

    Their played results stand and keep counting for everyone else; Swiss stops
    pairing them and stage advancement skips them. The peer of
    ``/entrants/{id}/drop``, which retires them from the tournament roster
    instead and deliberately does not cascade here.
    """
    return await BracketService().retire_entry(actor, entry_id)


@router.post(
    "/advance-stage",
    response_model=BracketResponse,
    summary="Advance into the next stage",
    description=(
        "Fills the next stage with the top finishers of a completed stage, using the next "
        "stage's advancement rule, and seeds them. The next stage stays a draft for you to "
        "review and start. Fails with 400 when the source stage isn't complete, or the next "
        "stage is missing, isn't a draft, has no advancement rule, or has already been "
        "filled.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def advance_stage(
    body: AdvanceStageRequest,
    tournament_id: int = Query(..., description="The tournament the stages belong to."),
    actor: User = Depends(require_write_actor),
):
    return await BracketService().advance_stage(actor, tournament_id, body.from_stage_order)
