"""Schemas for a community's events (``/api/events``)."""

from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from models import EventStatus

COPY_HELP = (
    "Setup to copy from `copy_from`: any of `tournaments`, `positions` (volunteer positions), "
    "`stages` (stages and stations), `check_in` (check-in tiers) and `discord_roles` "
    "(Discord roles from Wizzrobe). People, matches and shifts are never copied."
)


class EventResponse(BaseModel):
    id: int
    slug: str = Field(..., description="The event's link name, as `?event=` and `/e/<slug>` take it.")
    name: str
    status: EventStatus = Field(..., description="`draft` (staff only until it goes live), `live` or `archived` (read-only).")
    is_default: bool = Field(..., description="The event a bare link and a call without `event` use.")
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None


class EventCreateRequest(BaseModel):
    name: str = Field(..., description="Up to 120 characters.")
    slug: Optional[str] = Field(
        None, description="2 to 64 lowercase letters, digits and hyphens. Made from the name when left out.",
    )
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    copy_from: Optional[str] = Field(None, description="Slug of the event to copy setup from.")
    copy_setup: List[str] = Field(default_factory=list, alias='copy', description=COPY_HELP)

    model_config = {'populate_by_name': True}


class EventCreateResponse(BaseModel):
    event: EventResponse
    copied: Dict[str, object] = Field(
        default_factory=dict, description="What was copied: a count per group, plus anything skipped and why.",
    )


class EventUpdateRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = Field(None, description="Only a draft's link name can change.")
    starts_on: Optional[date] = Field(None, description="Send null to clear it; leave it out to keep it.")
    ends_on: Optional[date] = Field(None, description="Send null to clear it; leave it out to keep it.")


class EventArchiveRequest(BaseModel):
    new_default: Optional[str] = Field(
        None, description="Slug of the live event to become the default, required when archiving the default.",
    )


class EventSettingResponse(BaseModel):
    name: str
    value: str
