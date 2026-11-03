"""Schemas for player self-service availability endpoints.

Player windows are read opt-out — see :mod:`api.routers.player_availability`.
The wire shape is the volunteer one, so ``status`` still defaults to
``available``; for a player that window is dropped on save, and the useful
values are ``unavailable`` and ``preferred``.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.common import AvailabilityWindowInput, SetAvailabilityRequest
from models import VolunteerAvailabilityStatus

PlayerAvailabilityWindowInput = AvailabilityWindowInput
SetPlayerAvailabilityRequest = SetAvailabilityRequest


class PlayerAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The window's ID.")
    starts_at: datetime = Field(..., description="When the window starts (UTC).")
    ends_at: datetime = Field(..., description="When the window ends (UTC).")
    status: VolunteerAvailabilityStatus = Field(
        ...,
        description=(
            "What the window says. One of: unavailable (can't play then), preferred (would rather "
            "play then). Stored windows are never available, since that is the default."
        ),
    )
    note: Optional[str] = Field(None, description="The player's note on the window, if any.")
