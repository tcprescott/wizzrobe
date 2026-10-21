"""Crew moderation endpoints (approval and acknowledgment).

``crew_type`` is ``commentator`` or ``tracker``; ``crew_id`` is the id of the
Commentator/Tracker signup row.
"""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_write_actor
from api.schemas.crew import CrewApprovalRequest
from api.schemas.detail import DetailResponse
from application.errors import require_found
from application.services import CrewService
from models import User

router = APIRouter(prefix="/crew", tags=["Crew"], route_class=ServiceErrorRoute)


@router.post(
    "/{crew_type}/{crew_id}/approval",
    response_model=DetailResponse,
    summary="Approve or reject a crew signup",
    description=(
        "Approves or rejects a commentator or tracker signup. `crew_type` is "
        "`commentator` or `tracker`, and `crew_id` is the signup's id from "
        "`GET /matches/{match_id}/crew`. Approving DMs the crew member asking "
        "them to confirm; rejecting an approved signup clears their "
        "confirmation and DMs them that they're off the crew. Sending the state "
        "the signup is already in changes nothing and sends no DM. Fails with "
        "400 when `crew_type` isn't one of the two roles.\n\n"
        "**Who can call this:** Staff, Admins of this tournament or its Crew coordinators."
    ),
)
async def update_crew_approval(
    crew_type: str,
    crew_id: int,
    body: CrewApprovalRequest,
    actor: User = Depends(require_write_actor),
):
    service = CrewService()
    crew_member = require_found(
        await service.get_crew_member_by_id(crew_id, crew_type), "Crew signup"
    )
    await service.update_crew_approval(crew_member, crew_type, body.approved, actor=actor)
    return DetailResponse(detail="approved" if body.approved else "rejected")


@router.post(
    "/{crew_type}/{crew_id}/acknowledge",
    response_model=DetailResponse,
    summary="Acknowledge your own (approved) crew assignment",
    description=(
        "Confirms you'll cover a crew slot staff approved you for. "
        "`crew_type` is `commentator` or `tracker` and `crew_id` is the "
        "signup's id. Acknowledging twice is harmless. Fails with 400 when the "
        "signup isn't yours, isn't approved yet, or `crew_type` isn't one of "
        "the two roles.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def acknowledge_crew_assignment(
    crew_type: str,
    crew_id: int,
    actor: User = Depends(require_write_actor),
):
    await CrewService().acknowledge_crew_assignment(crew_id, crew_type, actor)
    return DetailResponse(detail="acknowledged")
