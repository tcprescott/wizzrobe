"""Schemas for the app's Admin → Meetups review queue."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MeetupResponse(BaseModel):
    id: int
    title: str
    description: str = Field(..., description="The host's text (markdown, links and formatting only).")
    location: Optional[str] = None
    starts_at: datetime
    duration_minutes: int
    status: str = Field(..., description="`pending`, `approved`, `rejected` or `cancelled`.")
    host_id: int
    host_name: str
    review_note: Optional[str] = Field(None, description="The last reason staff gave when rejecting it.")
    rsvp_enabled: bool
    rsvp_capacity: Optional[int] = None

    @classmethod
    def build(cls, event) -> 'MeetupResponse':
        host = event.host
        return cls(
            id=event.id, title=event.title, description=event.description or '',
            location=event.location, starts_at=event.starts_at,
            duration_minutes=event.duration_minutes, status=event.status.value,
            host_id=event.host_id, host_name=host.display_name or host.username,
            review_note=event.review_note, rsvp_enabled=event.rsvp_enabled,
            rsvp_capacity=event.rsvp_capacity,
        )


class MeetupRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Sent to the host so they can fix it.")


class MeetupUpdate(BaseModel):
    title: str
    description: str = ''
    location: Optional[str] = None
    starts_at: datetime = Field(..., description="UTC.")
    duration_minutes: int
    rsvp_enabled: bool = False
    rsvp_capacity: Optional[int] = None
    host_id: Optional[int] = Field(None, description="Hand the meetup to another member. Omit to keep the host.")


class MeetupAttendeeResponse(BaseModel):
    id: int
    name: str
