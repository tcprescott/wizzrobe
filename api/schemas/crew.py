"""Request and response schemas for crew moderation actions."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.common import UserBase


class CrewApprovalRequest(BaseModel):
    approved: bool = Field(..., description="True to approve the signup, false to reject it or withdraw an approval.")


class CrewSignupInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Id of the signup. Use it as `crew_id` on the `/crew` endpoints.")
    user: UserBase = Field(..., description="The person who signed up.")
    approved: bool = Field(..., description="Whether staff have approved the signup.")
    acknowledged_at: Optional[datetime] = Field(
        None,
        description=(
            "When the crew member confirmed their approved assignment, in UTC, "
            "or null if they haven't."
        ),
    )


class MatchCrewResponse(BaseModel):
    """A match's crew *including* signups nobody has approved yet."""

    match_id: int = Field(..., description="The match's id.")
    commentators: List[CrewSignupInfo] = Field(
        default_factory=list, description="Every commentator signup, approved or not."
    )
    trackers: List[CrewSignupInfo] = Field(
        default_factory=list, description="Every tracker signup, approved or not."
    )
    broadcasters: List[CrewSignupInfo] = Field(
        default_factory=list, description="Every broadcaster signup, approved or not."
    )
