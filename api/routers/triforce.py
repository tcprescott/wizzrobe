"""Triforce text endpoints: submit, list your own, and moderate a tournament's pool."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.triforce import (
    TriforceModerateRequest,
    TriforceSubmitRequest,
    TriforceTextResponse,
)
from application.services import TriforceTextService
from models import User

router = APIRouter(prefix="/triforce-texts", tags=["Triforce Texts"], route_class=ServiceErrorRoute)


@router.get(
    "/mine",
    response_model=List[TriforceTextResponse],
    summary="List your own triforce text submissions for a tournament",
    description=(
        "Lists the texts you've submitted to the tournament, newest first, with their "
        "moderation state. An unknown tournament returns an empty list."
        "\n\n**Who can call this:** Any member; it only ever returns your own submissions."
    ),
)
async def list_my_submissions(
    tournament_id: int = Query(..., description="Tournament to list your submissions for."),
    actor: User = Depends(require_api_actor),
):
    return await TriforceTextService().list_user_submissions(tournament_id, actor)


@router.get(
    "",
    response_model=List[TriforceTextResponse],
    summary="List submissions for moderation",
    description=(
        "Lists every submission to the tournament's pool, newest first, optionally narrowed "
        "by moderation state. An unknown tournament returns an empty list.\n\n"
        "Fails with 400 when `status` isn't `pending`, `approved` or `rejected`."
        "\n\n**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def list_for_moderation(
    tournament_id: int = Query(..., description="Tournament whose pool to list."),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by moderation state: `pending`, `approved` or `rejected`. Omit for all."
    ),
    actor: User = Depends(require_api_actor),
):
    return await TriforceTextService().list_for_moderation(
        tournament_id, status=status_filter, actor=actor,
    )


# --- Writes -----------------------------------------------------------------


@router.post(
    "",
    response_model=TriforceTextResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a triforce text",
    description=(
        "Adds a three-line text to the tournament's pool as pending, signed with your display "
        "name, and records it in the audit log. Approved texts can be embedded in the "
        "tournament's seeds.\n\n"
        "Fails with 400 when the tournament doesn't exist, isn't active or uses a generator "
        "that can't embed texts, when you don't hold Staff or Triforce submitter, or when the "
        "lines break the rules (exactly three, at most 19 allowed characters each, not all blank)."
        "\n\n**Who can call this:** Staff or Triforce submitter."
    ),
)
async def submit_text(body: TriforceSubmitRequest, actor: User = Depends(require_write_actor)):
    return await TriforceTextService().submit(body.tournament_id, body.lines, actor)


@router.post(
    "/{text_id}/moderate",
    response_model=TriforceTextResponse,
    summary="Approve or reject a submission (Staff or Tournament Admin)",
    description=(
        "Sets the submission to approved or rejected, stamps who moderated it and when, and "
        "records the decision in the audit log. Calling it again overrides the earlier "
        "decision.\n\n"
        "Fails with 400 when the submission doesn't exist or you're neither Staff nor an "
        "admin of its tournament."
        "\n\n**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def moderate_text(
    text_id: int, body: TriforceModerateRequest, actor: User = Depends(require_write_actor),
):
    return await TriforceTextService().moderate(text_id, body.approved, actor)


@router.delete(
    "/{text_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a submission (Staff or Tournament Admin)",
    description=(
        "Deletes the submission for good and records the deletion in the audit log.\n\n"
        "Fails with 400 when the submission doesn't exist or you're neither Staff nor an "
        "admin of its tournament."
        "\n\n**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def delete_text(text_id: int, actor: User = Depends(require_write_actor)):
    await TriforceTextService().delete(text_id, actor)
