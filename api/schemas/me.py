"""Schemas for the calls about the token's owner that span communities."""

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class MyCommunityEvent(BaseModel):
    slug: str = Field(..., description="The event's slug, as in `/t/<community>/e/<event>` links.")
    name: str
    status: str = Field(..., description="`draft`, `live` or `archived`.")
    is_default: bool = Field(..., description="The event the community opens on when none is named.")
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None


class MyCommunity(BaseModel):
    slug: str = Field(..., description="Send this in the `X-Wizzrobe-Community` header to act in this community.")
    name: str
    roles: List[str] = Field(..., description="The roles you hold here, by value.")
    events: List[MyCommunityEvent] = Field(..., description="The events here you can open, drafts only if you're getting one ready.")
