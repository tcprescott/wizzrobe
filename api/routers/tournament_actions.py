"""Tournament write endpoints: create/update/delete, admins, crew coordinators, roster and Challonge link."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api._helpers import load_user_or_404
from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.detail import DetailResponse
from api.schemas.match_actions import MatchSuggestionResponse
from api.schemas.tournament_actions import (
    ChallongeLinkRequest,
    ChallongeSyncResponse,
    MembershipRequest,
    TournamentCreateRequest,
    TournamentUpdateRequest,
)
from api.schemas.tournaments import TournamentResponse
from api.schemas.users import UserListItem
from application.errors import require_found
from application.services import ChallongeService, MatchSuggestionService, TournamentService
from application.tenant_context import require_tenant_id
from models import Tournament, User

router = APIRouter(prefix="/tournaments", tags=["Tournaments"], route_class=ServiceErrorRoute)


async def _load_tournament_or_404(tournament_id: int) -> Tournament:
    return require_found(
        await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id()),
        "Tournament",
    )


@router.post(
    "",
    response_model=TournamentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tournament (Staff only)",
    description=(
        "Creates a tournament in this community. Fails with 400 when the name is blank, a link "
        "isn't http or https, the event end date is before its start, or signups close before "
        "they open.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def create_tournament(body: TournamentCreateRequest, actor: User = Depends(require_write_actor)):
    return await TournamentService().create_tournament(actor=actor, **body.model_dump())


@router.patch(
    "/{tournament_id}",
    response_model=TournamentResponse,
    summary="Update a tournament (Staff or Tournament Admin)",
    description=(
        "Changes only the fields you send; sending null for a date, the hours or a signup "
        "bound clears it. Fails with 400 for the same reasons as creating one.\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def update_tournament(
    tournament_id: int, body: TournamentUpdateRequest, actor: User = Depends(require_write_actor),
):
    tournament = await _load_tournament_or_404(tournament_id)
    # The repository mutates ``tournament`` in place (and returns None), so we
    # return the now-updated instance.
    await TournamentService().update_tournament(
        tournament, actor=actor, **body.model_dump(exclude_unset=True),
    )
    return tournament


@router.delete(
    "/{tournament_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete an inactive tournament (Staff only)",
    description=(
        "Deletes the tournament along with its matches, entrants, brackets and triforce texts. "
        "This can't be undone. Fails with 400 when the tournament is still active or the "
        "confirmation phrase doesn't match.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def delete_tournament(
    tournament_id: int,
    confirmation: str = Query(
        ...,
        description=(
            'Must be "permanently delete". The tournament must already be '
            "inactive; deleting it also removes its matches, entrants, brackets "
            "and triforce texts."
        ),
    ),
    actor: User = Depends(require_write_actor),
):
    tournament = await _load_tournament_or_404(tournament_id)
    await TournamentService().delete_tournament(
        tournament, actor=actor, confirmation=confirmation,
    )


@router.get(
    "/{tournament_id}/match-suggestion",
    response_model=MatchSuggestionResponse,
    summary="Suggest a match start time for the given players",
    description=(
        "Picks the start time (UTC) that best fits the players' availability, the tournament's "
        "hours and the matches already on the schedule. It looks at the next four hours first, "
        "then the rest of the event. Nothing is booked. Fails with 400 when no slot fits.\n\n"
        "**Who can call this:** Any member who is one of the players, or Staff, or Admins of "
        "this tournament."
    ),
)
async def suggest_match_time(
    tournament_id: int,
    player_ids: List[int] = Query(..., description="User IDs of the players"),
    bracket_match_id: Optional[int] = Query(
        None,
        description=(
            "Bracket matchup being scheduled. When given, the suggestion is "
            "confined to that round's configured window."
        ),
    ),
    actor: User = Depends(require_api_actor),
):
    suggested_at = await MatchSuggestionService().suggest_match_time(
        tournament_id, player_ids, bracket_match_id=bracket_match_id, actor=actor,
    )
    return MatchSuggestionResponse(suggested_at=suggested_at)


@router.post(
    "/{tournament_id}/admins",
    response_model=DetailResponse,
    summary="Add a tournament admin (Staff only)",
    description=(
        "Makes the person an admin of this tournament, so they can edit it and run its "
        "matches. A grant made here counts as made by hand, so Discord role sync won't take it "
        "away.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def add_admin(tournament_id: int, body: MembershipRequest, actor: User = Depends(require_write_actor)):
    tournament = await _load_tournament_or_404(tournament_id)
    target = await load_user_or_404(body.user_id)
    await TournamentService().add_admin(tournament, target, actor=actor)
    return {"detail": "Admin added"}


@router.delete(
    "/{tournament_id}/admins/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a tournament admin (Staff only)",
    description=(
        "Takes the person off this tournament's admins.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def remove_admin(tournament_id: int, user_id: int, actor: User = Depends(require_write_actor)):
    tournament = await _load_tournament_or_404(tournament_id)
    target = await load_user_or_404(user_id)
    await TournamentService().remove_admin(tournament, target, actor=actor)


@router.post(
    "/{tournament_id}/crew-coordinators",
    response_model=DetailResponse,
    summary="Add a crew coordinator (Staff only)",
    description=(
        "Makes the person a crew coordinator for this tournament, so they can approve crew "
        "signups for its matches. A grant made here counts as made by hand, so Discord role "
        "sync won't take it away.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def add_crew_coordinator(
    tournament_id: int, body: MembershipRequest, actor: User = Depends(require_write_actor),
):
    tournament = await _load_tournament_or_404(tournament_id)
    target = await load_user_or_404(body.user_id)
    await TournamentService().add_crew_coordinator(tournament, target, actor=actor)
    return {"detail": "Crew coordinator added"}


@router.delete(
    "/{tournament_id}/crew-coordinators/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a crew coordinator (Staff only)",
    description=(
        "Takes the person off this tournament's crew coordinators.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def remove_crew_coordinator(
    tournament_id: int, user_id: int, actor: User = Depends(require_write_actor),
):
    tournament = await _load_tournament_or_404(tournament_id)
    target = await load_user_or_404(user_id)
    await TournamentService().remove_crew_coordinator(tournament, target, actor=actor)


@router.post(
    "/{tournament_id}/signup",
    response_model=DetailResponse,
    summary="Sign yourself up for a tournament",
    description=(
        "Signs you up, the same as the Tournaments tab does, and sends you a Discord DM "
        "confirming it. For a tournament linked to Challonge it adds you to the Challonge "
        "bracket under your linked Challonge account, until the bracket starts. Staff enrolling "
        "someone else use PUT /users/{user_id}/tournaments, which ignores the signup window.\n\n"
        "Fails with 400 when the tournament is inactive, signups haven't opened or have closed, "
        "you're already signed up, or a Challonge tournament needs a linked Challonge account "
        "you don't have.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def sign_up_for_tournament(tournament_id: int, actor: User = Depends(require_write_actor)):
    tournament = await TournamentService().self_enroll(tournament_id, actor)
    return {"detail": f"Signed up for {tournament.name}"}


@router.delete(
    "/{tournament_id}/signup",
    response_model=DetailResponse,
    summary="Withdraw yourself from a tournament",
    description=(
        "Takes you off the tournament's roster, and off its Challonge bracket when it has one. "
        "Allowed until the signup window closes (and, for a Challonge bracket, until it "
        "starts); after that, withdrawing is staff's call, so a seeded bracket never loses an "
        "entrant silently. Fails with 400 when the tournament is inactive, signups have closed "
        "or you aren't signed up.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def withdraw_from_tournament(tournament_id: int, actor: User = Depends(require_write_actor)):
    tournament = await TournamentService().self_withdraw(tournament_id, actor)
    return {"detail": f"Withdrew from {tournament.name}"}


@router.get(
    "/{tournament_id}/players",
    response_model=List[UserListItem],
    summary="List a tournament's players (Staff or Tournament Admin)",
    description=(
        "Everyone enrolled in the tournament, the same list as Manage players on the Tournaments "
        "tab.\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def list_players(tournament_id: int, actor: User = Depends(require_api_actor)):
    tournament = await _load_tournament_or_404(tournament_id)
    return await TournamentService().list_players(tournament, actor)


@router.post(
    "/{tournament_id}/players",
    response_model=DetailResponse,
    summary="Enroll a player (Staff or Tournament Admin)",
    description=(
        "Adds a member to the tournament's roster. Unlike self-signup this ignores the signup "
        "window. Fails with 400 when the person isn't a member of this community or is already "
        "enrolled.\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def enroll_player(tournament_id: int, body: MembershipRequest, actor: User = Depends(require_write_actor)):
    tournament = await _load_tournament_or_404(tournament_id)
    target = await load_user_or_404(body.user_id)
    await TournamentService().enroll_player(tournament, target, actor)
    return {"detail": "Player enrolled"}


@router.delete(
    "/{tournament_id}/players/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a player from the roster (Staff or Tournament Admin)",
    description=(
        "Takes the person off the tournament's roster, whatever the signup window says.\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def unenroll_player(tournament_id: int, user_id: int, actor: User = Depends(require_write_actor)):
    tournament = await _load_tournament_or_404(tournament_id)
    target = await load_user_or_404(user_id)
    await TournamentService().unenroll_player(tournament, target, actor)


@router.put(
    "/{tournament_id}/challonge",
    response_model=TournamentResponse,
    summary="Link a tournament to a Challonge bracket (Staff only)",
    description=(
        "Links the tournament to a Challonge bracket by its URL or ID and imports it. Needs the "
        "Challonge feature on and the community's Challonge connection set up. Fails with 400 "
        "when the tournament already has a native bracket or Challonge can't find the bracket.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def link_challonge(
    tournament_id: int, body: ChallongeLinkRequest, actor: User = Depends(require_write_actor),
):
    await _load_tournament_or_404(tournament_id)
    return await ChallongeService().link_tournament(tournament_id, body.challonge, actor)


@router.delete(
    "/{tournament_id}/challonge",
    response_model=TournamentResponse,
    summary="Unlink a tournament from Challonge (Staff only)",
    description=(
        "Detaches the tournament from Challonge and drops the mirrored bracket here. Nothing on "
        "Challonge is deleted, and linking again re-imports it.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def unlink_challonge(tournament_id: int, actor: User = Depends(require_write_actor)):
    await _load_tournament_or_404(tournament_id)
    return await ChallongeService().unlink_tournament(tournament_id, actor)


@router.post(
    "/{tournament_id}/challonge/sync",
    response_model=ChallongeSyncResponse,
    summary="Sync a tournament's Challonge bracket now",
    description=(
        "Pulls the latest entrants and matches from Challonge, the same as Sync bracket in "
        "the tournament's ⋮ on Admin → Tournaments.\n\n"
        "**Who can call this:** Staff, or an admin of this tournament."
    ),
)
async def sync_challonge(tournament_id: int, actor: User = Depends(require_write_actor)):
    await _load_tournament_or_404(tournament_id)
    return {"counts": await ChallongeService().sync_bracket(tournament_id, actor, force=True)}
