"""Player reschedule/cancellation request endpoints.

Every route delegates to ``MatchRescheduleService``, which enforces who may ask
and who may decide, writes the audit rows, and queues the Discord notifications.

The permission split mirrors the web surface exactly. Raising, withdrawing and
agreeing take any write token, because the service checks that the actor plays
in the match. Deciding is re-gated inside the service on ``can_crud_match`` —
staff or the tournament's admin — which is wider than STAFF alone, so the HTTP
layer takes the coarse write gate and lets the service answer.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.reschedule_requests import (
    RescheduleApproveRequest,
    RescheduleDeclineRequest,
    RescheduleRequestCreate,
    RescheduleRequestResponse,
)
from application.services import MatchRescheduleService
from models import Match, MatchRescheduleRequest, User

router = APIRouter(tags=["Reschedule requests"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(match_id=Match, request_id=MatchRescheduleRequest))])

MAX_ROWS = 500


_serialize = RescheduleRequestResponse.from_row


async def _reload(service: MatchRescheduleService, request_id: int, fallback):
    """Re-read a decided request so the response carries its prefetched relations.

    Falls back to the in-memory row for an approved cancellation, whose match —
    and with it the request — is deleted by the time this runs.
    """
    row = await service.get_by_id(request_id)
    return _serialize(row) if row is not None else _serialize(fallback)


@router.get(
    "/reschedule-requests/mine",
    response_model=List[RescheduleRequestResponse],
    summary="List your own reschedule requests and what happened to them",
    description=(
        "Requests you raised, newest first, with their outcome and any note "
        "staff left."
        "\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_mine(
    limit: int = Query(25, ge=1, le=MAX_ROWS, description="Maximum rows to return."),
    actor: User = Depends(require_api_actor),
):
    rows = await MatchRescheduleService().list_mine(actor, limit=limit)
    return [_serialize(row) for row in rows]


@router.get(
    "/reschedule-requests",
    response_model=List[RescheduleRequestResponse],
    summary="List reschedule requests waiting on a decision (admin)",
    description=(
        "Pending requests in this community, oldest first. A tournament admin "
        "without Staff sees only their own tournaments' requests."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def list_pending(actor: User = Depends(require_api_actor)):
    """The staff queue. Gated in the service to Staff, or a tournament admin
    for their own tournaments — a request carries the player's own words about
    why they need the change, which is not schedule data and is nobody else's
    to read."""
    rows = await MatchRescheduleService().list_pending(actor)
    return [_serialize(row) for row in rows]


@router.post(
    "/matches/{match_id}/reschedule-requests",
    response_model=RescheduleRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask staff to move or call off a match you are playing in",
    description=(
        "Raises a request to move the match or call it off. Staff and the "
        "tournament's admins are DMed, and in a two-player match so is your "
        "opponent, who can agree. Fails with 400 when the match has no time "
        "yet, is already under way, is synced from SpeedGaming, you already "
        "have a request waiting on it, the reason is empty or too long, or the "
        "proposed time is in the past or outside the tournament's hours. Fails "
        "with 403 when you don't play in the match or the tournament doesn't "
        "take requests."
        "\n\n"
        "**Who can call this:** Any member who plays in the match."
    ),
)
async def submit_request(
    match_id: int,
    body: RescheduleRequestCreate,
    actor: User = Depends(require_write_actor),
):
    service = MatchRescheduleService()
    request = await service.submit(
        match_id, actor,
        kind=body.kind, reason=body.reason, proposed_at=body.proposed_at,
    )
    return await _reload(service, request.id, request)


@router.get(
    "/matches/{match_id}/reschedule-requests",
    response_model=List[RescheduleRequestResponse],
    summary="List the reschedule requests raised against a match",
    description=(
        "Every request on one match, newest first, whatever its status."
        "\n\n"
        "**Who can call this:** Staff, Admins of this tournament or the match's players."
    ),
)
async def list_for_match(match_id: int, actor: User = Depends(require_api_actor)):
    """Whoever could decide these, or a player in the match reading their own."""
    rows = await MatchRescheduleService().list_for_match(match_id, actor)
    return [_serialize(row) for row in rows]


@router.post(
    "/reschedule-requests/{request_id}/agree",
    response_model=RescheduleRequestResponse,
    summary="Agree with your opponent's request (advisory; staff still decide)",
    description=(
        "Records that the other player is fine with the change. Staff see it, "
        "but it doesn't decide anything. Agreeing twice is harmless. Fails "
        "with 400 when the request is already decided or the match doesn't "
        "have exactly two players."
        "\n\n"
        "**Who can call this:** The other player in the match."
    ),
)
async def agree(request_id: int, actor: User = Depends(require_write_actor)):
    service = MatchRescheduleService()
    request = await service.record_opponent_agreement(request_id, actor)
    return await _reload(service, request_id, request)


@router.post(
    "/reschedule-requests/{request_id}/withdraw",
    response_model=RescheduleRequestResponse,
    summary="Take back a request you raised",
    description=(
        "Withdraws a pending request. Nobody is DMed. Fails with 400 when the "
        "request is already decided."
        "\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def withdraw(request_id: int, actor: User = Depends(require_write_actor)):
    service = MatchRescheduleService()
    request = await service.withdraw(request_id, actor)
    return await _reload(service, request_id, request)


@router.post(
    "/reschedule-requests/{request_id}/approve",
    response_model=RescheduleRequestResponse,
    summary="Approve a request, performing the reschedule or cancellation",
    description=(
        "Approves the request and makes the change. A reschedule moves the "
        "match to `scheduled_at` if given, else the proposed time, with the "
        "usual reschedule DMs, and closes the match's other pending requests as "
        "superseded. A cancellation deletes the match and DMs everyone who was "
        "expecting it; the response is then the request as it was just before. "
        "The requester is DMed the outcome. The body is optional. Fails with "
        "400 when the request is already decided, no time was proposed and "
        "none is given, or the new time is outside the tournament's hours."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def approve(
    request_id: int,
    body: Optional[RescheduleApproveRequest] = None,
    actor: User = Depends(require_write_actor),
):
    service = MatchRescheduleService()
    body = body or RescheduleApproveRequest(scheduled_at=None, note=None)
    request = await service.approve(
        request_id, actor, scheduled_at=body.scheduled_at, note=body.note,
    )
    return await _reload(service, request_id, request)


@router.post(
    "/reschedule-requests/{request_id}/decline",
    response_model=RescheduleRequestResponse,
    summary="Decline a request, in your own words",
    description=(
        "Declines the request. The requester is DMed your note, which is "
        "required. Fails with 400 when the request is already decided or the "
        "note is empty."
        "\n\n"
        "**Who can call this:** Staff or Admins of this tournament."
    ),
)
async def decline(
    request_id: int,
    body: RescheduleDeclineRequest,
    actor: User = Depends(require_write_actor),
):
    service = MatchRescheduleService()
    request = await service.decline(request_id, actor, body.note)
    return await _reload(service, request_id, request)
