"""Schemas for volunteer scheduling endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# Re-exported for callers that import the availability schemas from this module.
from api.schemas.common import AvailabilityWindowInput as AvailabilityWindowInput
from api.schemas.common import SetAvailabilityRequest as SetAvailabilityRequest
from models import VolunteerAvailabilityStatus


class VolunteerPositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Position id.")
    name: str = Field(..., description="Position name, such as \"Runner\" or \"Info desk\".")
    description: Optional[str] = Field(None, description="What the role involves, shown to volunteers.")
    color: Optional[str] = Field(None, description="Display colour for the position on schedules.")
    display_order: int = Field(..., description="Sort key; lower numbers list first.")
    is_active: bool = Field(..., description="Whether the position is in use. `active_only` on the list hides inactive ones.")
    shift_length_minutes: Optional[int] = Field(None, description="Length of each generated shift in minutes when shifts are staggered; empty for back-to-back blocks.")
    stagger_minutes: Optional[int] = Field(None, description="Minutes between the start times of overlapping generated shifts; empty for back-to-back blocks.")
    staff_alerts: bool = Field(False, description="Whoever holds one of this position's shifts gets match alerts (player comments, reschedule requests) instead of every staff member.")
    created_at: datetime = Field(..., description="When the position was created (UTC).")
    updated_at: datetime = Field(..., description="When the position was last changed (UTC).")


class VolunteerPositionCreate(BaseModel):
    name: str = Field(..., description="Position name. Must be unique in the community.")
    description: Optional[str] = Field(None, description="What the role involves, shown to volunteers.")
    color: Optional[str] = Field(None, description="Display colour for the position on schedules.")
    display_order: int = Field(0, description="Sort key; lower numbers list first.")
    is_active: bool = Field(True, description="Whether the position is in use.")
    shift_length_minutes: Optional[int] = Field(None, description="Length of each generated shift in minutes. Set together with `stagger_minutes`, or leave both empty.")
    stagger_minutes: Optional[int] = Field(None, description="Minutes between the start times of overlapping generated shifts. Positive and no longer than `shift_length_minutes`.")
    staff_alerts: bool = Field(False, description="Whoever holds one of this position's shifts gets match alerts (player comments, reschedule requests) instead of every staff member.")


class VolunteerPositionUpdate(BaseModel):
    name: Optional[str] = Field(None, description="New position name. Must be unique in the community and not empty.")
    description: Optional[str] = Field(None, description="What the role involves, shown to volunteers.")
    color: Optional[str] = Field(None, description="Display colour for the position on schedules.")
    display_order: Optional[int] = Field(None, description="Sort key; lower numbers list first.")
    is_active: Optional[bool] = Field(None, description="Whether the position is in use.")
    shift_length_minutes: Optional[int] = Field(None, description="Length of each generated shift in minutes. Set together with `stagger_minutes`, or leave both empty.")
    stagger_minutes: Optional[int] = Field(None, description="Minutes between the start times of overlapping generated shifts. Positive and no longer than `shift_length_minutes`.")
    staff_alerts: Optional[bool] = Field(None, description="Whoever holds one of this position's shifts gets match alerts (player comments, reschedule requests) instead of every staff member.")


class VolunteerAssignmentResponse(BaseModel):
    id: int = Field(..., description="Assignment id.")
    shift_id: int = Field(..., description="The shift this assignment is on.")
    user_id: int = Field(..., description="The assigned volunteer.")
    user_name: Optional[str] = Field(None, description="The volunteer's display name.")
    auto_generated: bool = Field(..., description="True for a draft from auto-fill that a coordinator hasn't published yet. The volunteer hasn't been told about a draft.")
    acknowledged_at: Optional[datetime] = Field(None, description="When the volunteer acknowledged the assignment (UTC); empty if they haven't.")
    reminder_sent_at: Optional[datetime] = Field(None, description="When the pre-shift reminder DM went out (UTC); empty if it hasn't.")
    checked_in_at: Optional[datetime] = Field(None, description="When the volunteer arrived for the shift (UTC); empty until they check in or are checked in.")
    checked_in_by_id: Optional[int] = Field(None, description="Who recorded the arrival: the volunteer themselves or a coordinator.")
    called_off_at: Optional[datetime] = Field(None, description="When staff called this shift off for the volunteer (UTC); empty unless they did. A called-off shift skips check-in and still counts as fully worked.")
    called_off_by_id: Optional[int] = Field(None, description="Who called it off.")
    created_at: datetime = Field(..., description="When the assignment was made (UTC).")

    @classmethod
    def from_row(cls, a) -> 'VolunteerAssignmentResponse':
        """Shared by the REST router and the MCP ``check_in_volunteer`` tool."""
        user = a.user if a.user_id and hasattr(a, 'user') else None
        return cls(
            id=a.id,
            shift_id=a.shift_id,
            user_id=a.user_id,
            user_name=(user.preferred_name if user else None),
            auto_generated=a.auto_generated,
            acknowledged_at=a.acknowledged_at,
            reminder_sent_at=a.reminder_sent_at,
            checked_in_at=a.checked_in_at,
            checked_in_by_id=a.checked_in_by_id,
            called_off_at=a.called_off_at,
            called_off_by_id=a.called_off_by_id,
            created_at=a.created_at,
        )


class AssignRequest(BaseModel):
    user_id: int = Field(..., description="The member to put on the shift. Must belong to this community.")


class CallOffRequest(BaseModel):
    """An optional note from staff, sent to the volunteer in the call-off DM."""

    note: Optional[str] = Field(None, description="Shown to the volunteer in the DM. Up to 300 characters are kept.")


class AssignResponse(BaseModel):
    assignment: VolunteerAssignmentResponse = Field(..., description="The new assignment.")
    warnings: List[str] = Field(default_factory=list, description="Soft problems that didn't block the assignment, such as an already full shift or the volunteer's availability.")


class VolunteerShiftResponse(BaseModel):
    id: int = Field(..., description="Shift id.")
    position_id: int = Field(..., description="The position this shift belongs to.")
    position_name: Optional[str] = Field(None, description="The position's name.")
    starts_at: datetime = Field(..., description="When the shift starts (UTC).")
    ends_at: datetime = Field(..., description="When the shift ends (UTC).")
    label: Optional[str] = Field(None, description="Optional short name for the shift, such as \"Opening\".")
    slots_needed: int = Field(..., description="How many volunteers the shift needs.")
    notes: Optional[str] = Field(None, description="Notes for the volunteers on this shift.")
    filled: int = Field(..., description="How many assignments are listed in `assignments`. Drafts count only when you can see them.")
    assignments: List[VolunteerAssignmentResponse] = Field(default_factory=list, description="Volunteers on the shift. Drafts are included only for callers who manage volunteers.")

    @classmethod
    def from_row(cls, s, *, include_drafts: bool) -> 'VolunteerShiftResponse':
        """A shift as the caller may see it. Shared by the REST router and the MCP shift tools.

        Drafts are the coordinator's unannounced sketch: only someone who can
        manage volunteers sees them, so a volunteer never learns of one sideways.
        """
        assignments = list(s.assignments) if hasattr(s, 'assignments') else []
        if not include_drafts:
            assignments = [a for a in assignments if not a.auto_generated]
        return cls(
            id=s.id,
            position_id=s.position_id,
            position_name=(s.position.name if s.position else None),
            starts_at=s.starts_at,
            ends_at=s.ends_at,
            label=s.label,
            slots_needed=s.slots_needed,
            notes=s.notes,
            filled=len(assignments),
            assignments=[VolunteerAssignmentResponse.from_row(a) for a in assignments],
        )


class VolunteerShiftCreate(BaseModel):
    position_id: int = Field(..., description="The position to add the shift under.")
    starts_at: datetime = Field(..., description="When the shift starts (UTC).")
    ends_at: datetime = Field(..., description="When the shift ends (UTC). Must be after `starts_at`.")
    label: Optional[str] = Field(None, description="Optional short name for the shift, such as \"Opening\".")
    slots_needed: int = Field(1, description="How many volunteers the shift needs. At least 1.")
    notes: Optional[str] = Field(None, description="Notes for the volunteers on this shift.")


class VolunteerShiftUpdate(BaseModel):
    position_id: Optional[int] = Field(None, description="Move the shift under another position. Omit to leave it unchanged.")
    starts_at: Optional[datetime] = Field(None, description="New start (UTC). Moving a shift asks everyone on it to acknowledge again. Omit to leave it unchanged.")
    ends_at: Optional[datetime] = Field(None, description="New end (UTC). Must stay after the start. Omit to leave it unchanged.")
    label: Optional[str] = Field(None, description="Short name for the shift. An empty string clears it. Omit to leave it unchanged.")
    slots_needed: Optional[int] = Field(None, description="How many volunteers the shift needs. At least 1. Omit to leave it unchanged.")
    notes: Optional[str] = Field(None, description="Notes for the volunteers. An empty string clears them. Omit to leave them unchanged.")


class VolunteerAvailabilityResponse(BaseModel):
    id: int = Field(..., description="Availability window id.")
    user_id: int = Field(..., description="The volunteer the window belongs to.")
    starts_at: datetime = Field(..., description="When the window starts (UTC).")
    ends_at: datetime = Field(..., description="When the window ends (UTC).")
    status: VolunteerAvailabilityStatus = Field(..., description="One of `available`, `unavailable` or `preferred`.")
    note: Optional[str] = Field(None, description="Optional note from the volunteer.")


class ReleaseAssignmentRequest(BaseModel):
    """Why you can no longer cover a shift. The coordinators see it."""

    reason: Optional[str] = Field(None, description="Why you can no longer cover the shift. The coordinators see it; up to 500 characters are kept.")


class CoverageRow(BaseModel):
    shift_id: int = Field(..., description="Shift id.")
    position: str = Field(..., description="The position's name.")
    label: str = Field(..., description="The shift's label, or an empty string.")
    starts_at: datetime = Field(..., description="When the shift starts (UTC).")
    ends_at: datetime = Field(..., description="When the shift ends (UTC).")
    filled: int = Field(..., description="Assignments on the shift, drafts included.")
    needed: int = Field(..., description="How many volunteers the shift needs.")
    understaffed: bool = Field(..., description="True when `filled` is below `needed`.")


class VolunteerHoursResponse(BaseModel):
    """Hours served against the community's comp tiers.

    ``hours`` counts published assignments once across overlaps, over the
    event window unless ``start``/``end`` narrow it.
    """

    user_id: int = Field(..., description="The volunteer.")
    user_name: str = Field(..., description="The volunteer's display name.")
    hours: float = Field(..., description="Hours served on published assignments, rounded to two decimals. Overlapping shifts count once.")
    tier_cleared: Optional[float] = Field(None, description="The highest comp tier reached, in hours; empty if none.")
    next_tier: Optional[float] = Field(None, description="The next comp tier, in hours; empty when every tier is cleared.")
    hours_to_next: Optional[float] = Field(None, description="Hours still needed to reach `next_tier`; empty when there is none.")
