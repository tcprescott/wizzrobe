"""Comments on a match: players to staff, staff notes, and staff to players.

``MatchCommentService`` decides who reads and writes which kind, and sends the DMs.
"""

from typing import List

from fastapi import APIRouter, Depends, Response, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.match_comments import MatchCommentCreate, MatchCommentResponse
from application.services import MatchCommentService
from models import Match, MatchComment, User

router = APIRouter(
    tags=['Match comments'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(match_id=Match, comment_id=MatchComment))],
)


@router.get(
    '/matches/{match_id}/comments',
    response_model=List[MatchCommentResponse],
    summary="The comments you may read on a match",
    description=(
        "Oldest first. Whoever may run the match reads every comment. A player reads their own "
        "comments and what staff sent the players. Anyone else gets an empty list."
        "\n\n**Who can call this:** Any member."
    ),
)
async def list_comments(match_id: int, actor: User = Depends(require_api_actor)):
    rows = await MatchCommentService().list_for_viewer(match_id, actor)
    return [MatchCommentResponse.model_validate(r) for r in rows]


@router.post(
    '/matches/{match_id}/comments',
    response_model=MatchCommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comment on a match",
    description=(
        "A `player` comment DMs the admin on duty and the tournament's admins; a `staff_to_players` "
        "message DMs the players who want those; a `staff_private` note DMs nobody. Fails with 400 "
        "when the body is empty or too long, you aren't allowed that kind on this match, or a player "
        "posts more than 10 in a minute."
        "\n\n**Who can call this:** The match's players (`player`), or whoever may run the match."
    ),
)
async def add_comment(match_id: int, body: MatchCommentCreate, actor: User = Depends(require_write_actor)):
    service = MatchCommentService()
    comment = await service.add_comment(match_id, actor, body.body, body.kind)
    rows = await service.list_for_viewer(match_id, actor)
    return MatchCommentResponse.model_validate(next(r for r in rows if r['id'] == comment.id))


@router.delete(
    '/match-comments/{comment_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a comment",
    description="**Who can call this:** The comment's author, or whoever may run the match.",
)
async def delete_comment(comment_id: int, actor: User = Depends(require_write_actor)):
    await MatchCommentService().delete_comment(comment_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
