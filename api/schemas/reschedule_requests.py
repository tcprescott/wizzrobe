"""Schemas for player reschedule/cancellation request endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from models import MatchRescheduleRequest, RescheduleRequestKind


class RescheduleRequestResponse(BaseModel):
    id: int = Field(..., description="The request's id.")
    match_id: int = Field(..., description="The match the request is about.")
    kind: str = Field(..., description="What was asked for: 'reschedule' to move the match, 'cancel' to call it off.")
    status: str = Field(
        ...,
        description=(
            "Where the request stands: 'pending', 'approved', 'declined', "
            "'withdrawn' (the requester took it back) or 'superseded' (another "
            "request on the same match was approved first)."
        ),
    )
    reason: str = Field(..., description="The requester's own words on why they need the change.")
    proposed_at: Optional[datetime] = Field(
        None, description="The time the requester asked for, in UTC, or null when they left it to staff."
    )
    original_scheduled_at: Optional[datetime] = Field(
        None, description="When the match was scheduled at the time of asking, in UTC."
    )
    opponent_agreed_at: Optional[datetime] = Field(
        None, description="When the other player agreed to the request, in UTC, or null if they haven't."
    )
    requested_by_id: int = Field(..., description="User id of the player who asked.")
    requested_by_name: Optional[str] = Field(None, description="Display name of the player who asked.")
    decided_by_id: Optional[int] = Field(
        None,
        description=(
            "User id of whoever decided it (for 'superseded', whoever approved "
            "the other request). Null while pending or once withdrawn."
        ),
    )
    decided_by_name: Optional[str] = Field(None, description="Display name of whoever decided it.")
    decided_at: Optional[datetime] = Field(
        None, description="When the request stopped being pending, for any reason, in UTC."
    )
    decision_note: Optional[str] = Field(None, description="The note staff left with their decision, shown to the requester.")
    created_at: datetime = Field(..., description="When the request was raised, in UTC.")

    @classmethod
    def from_row(cls, row: MatchRescheduleRequest) -> 'RescheduleRequestResponse':
        """``requested_by`` and ``decided_by`` must be loaded. Shared with MCP."""
        decider = row.decided_by
        return cls(
            id=row.id,
            match_id=row.match_id,  # type: ignore[attr-defined]
            kind=getattr(row.kind, 'value', row.kind),
            status=getattr(row.status, 'value', row.status),
            reason=row.reason,
            proposed_at=row.proposed_at,
            original_scheduled_at=row.original_scheduled_at,
            opponent_agreed_at=row.opponent_agreed_at,
            requested_by_id=row.requested_by_id,  # type: ignore[attr-defined]
            requested_by_name=row.requested_by.preferred_name if row.requested_by else None,
            decided_by_id=row.decided_by_id,  # type: ignore[attr-defined]
            decided_by_name=decider.preferred_name if decider else None,
            decided_at=row.decided_at,
            decision_note=row.decision_note,
            created_at=row.created_at,
        )


class RescheduleRequestCreate(BaseModel):
    kind: RescheduleRequestKind = Field(
        RescheduleRequestKind.RESCHEDULE,
        description="'reschedule' to move the match, 'cancel' to call it off.",
    )
    reason: str = Field(
        ...,
        description="Why the change is needed. Staff decide on this, so it is required.",
    )
    proposed_at: Optional[datetime] = Field(
        None,
        description=(
            "The time you are asking for, in UTC. Omit to say only that you "
            "cannot make the current slot and let staff pick. Ignored for a "
            "'cancel' request, which proposes no time."
        ),
    )


class RescheduleApproveRequest(BaseModel):
    scheduled_at: Optional[datetime] = Field(
        None,
        description=(
            "Approve at a different time than the one proposed, in UTC. Required "
            "when the request named no time. Ignored for a 'cancel' request."
        ),
    )
    note: Optional[str] = Field(
        None, description="Optional message shown to the player with the outcome."
    )


class RescheduleDeclineRequest(BaseModel):
    note: str = Field(
        ...,
        description=(
            "Why the request was refused. Required: a bare 'no' is what this "
            "feature exists to replace."
        ),
    )
