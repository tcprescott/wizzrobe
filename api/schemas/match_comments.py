"""Schemas for comments on a match."""

from datetime import datetime

from pydantic import BaseModel, Field

from models import MatchCommentKind


class MatchCommentResponse(BaseModel):
    id: int
    kind: str = Field(..., description=(
        "`player` (a player to staff), `staff_private` (a note only staff and proctors read) or "
        "`staff_to_players` (staff to the match's players)."
    ))
    body: str
    author: str
    own: bool = Field(..., description="You wrote it.")
    created_at: datetime
    can_delete: bool


class MatchCommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)
    kind: MatchCommentKind = Field(..., description=(
        "`player` for a player in the match; `staff_private` or `staff_to_players` for whoever may run it."
    ))
