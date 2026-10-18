"""Volunteer scheduling endpoints."""

from datetime import date, datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query, status

from api._helpers import load_community_user_or_404
from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.volunteers import (
    AssignRequest,
    AssignResponse,
    CoverageRow,
    ReleaseAssignmentRequest,
    SetAvailabilityRequest,
    VolunteerAssignmentResponse,
    VolunteerAvailabilityResponse,
    VolunteerHoursResponse,
    VolunteerPositionCreate,
    VolunteerPositionResponse,
    VolunteerPositionUpdate,
    VolunteerShiftCreate,
    VolunteerShiftResponse,
    VolunteerShiftUpdate,
)
from application.errors import require_found
from application.services.auth_service import AuthService
from application.services.volunteer.volunteer_availability_service import VolunteerAvailabilityService
from application.services.volunteer.volunteer_hours import VolunteerHoursService
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from models import User

router = APIRouter(
    prefix="/volunteers",
    tags=["Volunteers"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(require_api_actor)],
)


# --- response builders ----------------------------------------------------

_assignment_resp = VolunteerAssignmentResponse.from_row


_shift_resp = VolunteerShiftResponse.from_row


# --- Positions ------------------------------------------------------------

@router.get(
    "/positions",
    response_model=List[VolunteerPositionResponse],
    summary="List volunteer positions",
    description=(
        "Returns this community's volunteer positions (the roles shifts are grouped under), in display order.\n\n"
        "**Who can call this:** Anyone the Volunteer page admits: Staff, Volunteer coordinator, Proctor or Volunteer."
    ),
)
async def list_positions(
    active_only: bool = Query(False, description="Return only active positions."),
    actor: User = Depends(require_api_actor),
):
    return await VolunteerPositionService().list_for_viewer(actor, active_only=active_only)


@router.post(
    "/positions",
    response_model=VolunteerPositionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a position",
    description=(
        "Adds a volunteer position. Set `shift_length_minutes` and `stagger_minutes` together to have generated shifts overlap; leave both empty for back-to-back blocks.\n\n"
        "Fails with 400 when the name is empty or already taken, or the stagger settings are incomplete, not positive, or the stagger is longer than the shift.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def create_position(payload: VolunteerPositionCreate, actor: User = Depends(require_write_actor)):
    return await VolunteerPositionService().create(actor, **payload.model_dump())


@router.patch(
    "/positions/{position_id}",
    response_model=VolunteerPositionResponse,
    summary="Update a position",
    description=(
        "Changes only the fields you send. An empty body changes nothing and returns the position as it is.\n\n"
        "Fails with 400 for the same reasons as creating a position.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def update_position(position_id: int, payload: VolunteerPositionUpdate, actor: User = Depends(require_write_actor)):
    service = VolunteerPositionService()
    position = require_found(await service.get(position_id), "Position")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return position
    return await service.update(actor, position, **fields)


@router.delete(
    "/positions/{position_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a position",
    description=(
        "Deletes the position along with its shifts and their assignments. Nobody is notified, and members' Discord roles are re-synced to match.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def delete_position(position_id: int, actor: User = Depends(require_write_actor)):
    service = VolunteerPositionService()
    position = require_found(await service.get(position_id), "Position")
    await service.delete(actor, position)


# --- Shifts ---------------------------------------------------------------

@router.get(
    "/shifts",
    response_model=List[VolunteerShiftResponse],
    summary="List shifts in a window",
    description=(
        "Returns every shift overlapping the window, with its assignments. Draft assignments (an unpublished auto-fill) are left out unless you can manage volunteers.\n\n"
        "**Who can call this:** Anyone the Volunteer page admits: Staff, Volunteer coordinator, Proctor or Volunteer."
    ),
)
async def list_shifts(
    start: datetime = Query(..., description="Window start (UTC ISO 8601)"),
    end: datetime = Query(..., description="Window end (UTC ISO 8601)"),
    actor: User = Depends(require_api_actor),
):
    shifts = await VolunteerScheduleService().shifts_for_viewer(actor, start, end)
    include_drafts = await AuthService.can_manage_volunteers(actor)
    return [_shift_resp(s, include_drafts=include_drafts) for s in shifts]


@router.get(
    "/shifts/{shift_id}",
    response_model=VolunteerShiftResponse,
    summary="Get a shift",
    description=(
        "Returns one shift with its assignments. Draft assignments are left out unless you can manage volunteers.\n\n"
        "**Who can call this:** Anyone the Volunteer page admits: Staff, Volunteer coordinator, Proctor or Volunteer."
    ),
)
async def get_shift(shift_id: int, actor: User = Depends(require_api_actor)):
    shift = require_found(await VolunteerScheduleService().shift_for_viewer(actor, shift_id), "Shift")
    return _shift_resp(shift, include_drafts=await AuthService.can_manage_volunteers(actor))


@router.post(
    "/shifts",
    response_model=VolunteerShiftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shift",
    description=(
        "Adds an empty shift under a position.\n\n"
        "Fails with 400 when the shift doesn't end after it starts, needs fewer than one slot, or names a position that doesn't exist in this community.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def create_shift(payload: VolunteerShiftCreate, actor: User = Depends(require_write_actor)):
    service = VolunteerScheduleService()
    shift = await service.create_shift(actor, **payload.model_dump())
    # create_shift has already required can_manage_volunteers.
    return _shift_resp(require_found(await service.get_shift(shift.id), "Shift"), include_drafts=True)


@router.patch(
    "/shifts/{shift_id}",
    response_model=VolunteerShiftResponse,
    summary="Update a shift",
    description=(
        "Changes only the fields you send. Moving the shift's start or end clears everyone's acknowledgment and DMs them the new time; changing slots, label or notes notifies nobody.\n\n"
        "Fails with 400 when the shift wouldn't end after it starts, needs fewer than one slot, or names a position that doesn't exist in this community.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def update_shift(shift_id: int, payload: VolunteerShiftUpdate, actor: User = Depends(require_write_actor)):
    service = VolunteerScheduleService()
    shift = require_found(await service.get_shift(shift_id), "Shift")
    fields = payload.model_dump(exclude_unset=True)
    for key in ('label', 'notes'):
        if key in fields:
            fields[key] = (fields[key] or '').strip() or None
    if fields:
        await service.update_shift(actor, shift, **fields)
    return _shift_resp(require_found(await service.get_shift(shift_id), "Shift"), include_drafts=True)


@router.delete(
    "/shifts/{shift_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a shift",
    description=(
        "Deletes the shift and its assignments. Everyone published on it whose shift hadn't finished gets a Discord DM saying they've been taken off, and any Discord roles tied to the position are re-synced for them.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def delete_shift(shift_id: int, actor: User = Depends(require_write_actor)):
    service = VolunteerScheduleService()
    shift = require_found(await service.get_shift(shift_id), "Shift")
    await service.delete_shift(actor, shift)


# --- Assignments ----------------------------------------------------------

@router.post(
    "/shifts/{shift_id}/assignments",
    response_model=AssignResponse,
    summary="Assign a volunteer to a shift",
    description=(
        "Puts a member on the shift and DMs them on Discord with a button to acknowledge it. `warnings` flags soft problems that don't block the assignment: the shift is already full, or the member marked the time unavailable or didn't mark it available.\n\n"
        "Fails with 400 when the member is already on this shift or on another shift that overlaps it.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def assign(shift_id: int, payload: AssignRequest, actor: User = Depends(require_write_actor)):
    service = VolunteerScheduleService()
    shift = require_found(await service.get_shift(shift_id), "Shift")
    target = await load_community_user_or_404(payload.user_id, actor)
    assignment, warnings = await service.assign(actor, shift, target)
    assignment.user = target  # for the response name
    return AssignResponse(assignment=_assignment_resp(assignment), warnings=warnings)


@router.delete(
    "/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an assignment",
    description=(
        "Takes a volunteer off a shift. For a published assignment they get a Discord DM and their position roles are re-synced; removing a draft is silent.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def unassign(assignment_id: int, actor: User = Depends(require_write_actor)):
    service = VolunteerScheduleService()
    assignment = require_found(await service.get_assignment(assignment_id), "Assignment")
    await service.unassign(actor, assignment)


@router.post(
    "/assignments/{assignment_id}/acknowledge",
    response_model=VolunteerAssignmentResponse,
    summary="Acknowledge your assignment",
    description=(
        "Confirms you've seen one of your shift assignments. Acknowledging twice changes nothing.\n\n"
        "Fails with 400 when the assignment isn't yours or is still an unpublished draft.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def acknowledge(assignment_id: int, actor: User = Depends(require_write_actor)):
    assignment = await VolunteerScheduleService().acknowledge(assignment_id, actor)
    return _assignment_resp(assignment)


@router.post(
    "/assignments/{assignment_id}/check-in",
    response_model=VolunteerAssignmentResponse,
    summary="Record that a volunteer showed up for their shift (coordinators and staff)",
    description=(
        "Marks the volunteer as arrived, recording the time and you as the person who checked them in. Doing it twice keeps the first arrival time.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def check_in(assignment_id: int, actor: User = Depends(require_write_actor)):
    assignment = await VolunteerScheduleService().check_in(assignment_id, actor)
    return _assignment_resp(assignment)


# --- Coverage -------------------------------------------------------------

@router.get(
    "/coverage",
    response_model=List[CoverageRow],
    summary="Per-shift coverage in a window",
    description=(
        "Returns filled and needed slot counts for every shift overlapping the window. `filled` counts draft assignments too.\n\n"
        "**Who can call this:** Anyone the Volunteer page admits: Staff, Volunteer coordinator, Proctor or Volunteer."
    ),
)
async def coverage(
    start: datetime = Query(..., description="Window start (UTC ISO 8601)"),
    end: datetime = Query(..., description="Window end (UTC ISO 8601)"),
    actor: User = Depends(require_api_actor),
):
    return await VolunteerScheduleService().coverage_for_viewer(actor, start, end)


# --- Hours ----------------------------------------------------------------

def _hours_resp(summary) -> VolunteerHoursResponse:
    return VolunteerHoursResponse(
        user_id=summary.user_id,
        user_name=summary.user_name,
        hours=summary.hours,
        tier_cleared=summary.tier_cleared,
        next_tier=summary.next_tier,
        hours_to_next=summary.hours_to_next,
    )


@router.get(
    "/hours",
    response_model=List[VolunteerHoursResponse],
    summary="Volunteer hours against the comp tiers",
    description=(
        "Returns every volunteer's served hours, most first, against the community's comp tiers. Only published assignments count, and overlapping shifts count once. Anyone who could be assigned is listed, even with zero hours. Without `start` and `end` the count covers the community's event window.\n\n"
        "**Who can call this:** Staff or Volunteer coordinator."
    ),
)
async def hours(
    start: date | None = Query(None, description="First event day to count (local date)"),
    end: date | None = Query(None, description="Last event day to count (local date)"),
    actor: User = Depends(require_api_actor),
):
    summaries = await VolunteerHoursService().roster(actor=actor, start=start, end=end)
    return [_hours_resp(s) for s in summaries]


@router.get(
    "/hours/me",
    response_model=VolunteerHoursResponse,
    summary="Your own volunteer hours",
    description=(
        "Returns your served hours and how far you are from the next comp tier, counted the same way as the hours roster.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def my_hours(
    start: date | None = Query(None, description="First event day to count (local date)"),
    end: date | None = Query(None, description="Last event day to count (local date)"),
    actor: User = Depends(require_api_actor),
):
    return _hours_resp(await VolunteerHoursService().for_user(actor, start=start, end=end))


# --- Self-service: availability, assignments ---------------------

@router.get(
    "/me/availability",
    response_model=List[VolunteerAvailabilityResponse],
    summary="List your availability windows",
    description=(
        "Returns the times you've marked yourself available, unavailable or preferred. Coordinators see these when they assign you.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def my_availability(actor: User = Depends(require_api_actor)):
    return await VolunteerAvailabilityService().availability_for(actor)


@router.put(
    "/me/availability",
    response_model=List[VolunteerAvailabilityResponse],
    summary="Replace your availability windows",
    description=(
        "Replaces all of your availability windows with the list you send. Send an empty list to clear them.\n\n"
        "Fails with 400 when a window doesn't end after it starts.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def set_my_availability(payload: SetAvailabilityRequest, actor: User = Depends(require_write_actor)):
    windows = [(w.starts_at, w.ends_at, w.status, w.note) for w in payload.windows]
    return await VolunteerAvailabilityService().set_windows(actor, windows)


@router.get(
    "/me/assignments",
    response_model=List[VolunteerAssignmentResponse],
    summary="List your upcoming shift assignments",
    description=(
        "Returns your shift assignments, soonest first. By default only shifts that haven't finished yet; set `upcoming_only` to false for all of them.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def my_assignments(
    upcoming_only: bool = Query(True, description="Leave out shifts that have already finished."),
    actor: User = Depends(require_api_actor),
):
    after = datetime.now(timezone.utc) if upcoming_only else None
    assignments = await VolunteerScheduleService().assignments_for_user(actor, upcoming_after=after)
    return [_assignment_resp(a) for a in assignments]


@router.delete(
    "/me/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Give one of your own shifts back",
    description=(
        "Takes you off one of your shifts and DMs the volunteer coordinators on Discord, with your reason if you give one, so they can find cover. This is the self-service version of removing an assignment.\n\n"
        "Fails with 400 when the assignment isn't yours, you're already checked in for it, or the shift has finished.\n\n"
        "**Who can call this:** Yourself only."
    ),
)
async def release_my_assignment(
    assignment_id: int,
    payload: ReleaseAssignmentRequest | None = None,
    actor: User = Depends(require_write_actor),
):
    await VolunteerScheduleService().release(
        assignment_id, actor, payload.reason if payload else None,
    )
