"""Schemas for the service-health endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProbeResultResponse(BaseModel):
    """One dependency's latest probe outcome.

    Mirrors :meth:`ProbeResult.as_dict` — ``status`` is the plain enum value
    string and ``checked_at`` is a UTC ISO-8601 timestamp.
    """

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., description="Stable id of the checked service, such as `racetime_bots` or `challonge`.")
    label: str = Field(..., description="Human-readable name of the service.")
    category: str = Field(
        ..., description="Group the service belongs to: `core`, `discord`, `racetime`, `integrations`, `seedgen` or `monitoring`."
    )
    status: str = Field(
        ...,
        description="Health: `healthy`, `mock` (the local mock stands in, so the real service wasn't contacted), `degraded`, `credential_warning` (working, but a credential is expiring or was rejected), `down` or `unknown`.",
    )
    message: str = Field(..., description="What the check found, in a sentence.")
    checked_at: datetime = Field(..., description="When the check ran (UTC).")
