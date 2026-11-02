"""The app's Work tab: which areas you can open, and the volunteer schedule grid.

Screen-shaped reads, like ``/me/home``: each returns what one Work screen
draws. The check-in desk's own read lives with the rest of the desk under
``/check-in``.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.work import (
    GridColumnResponse,
    GridRoleResponse,
    ScheduleColumnsRequest,
    VolunteerScheduleResponse,
    WorkAreasResponse,
)
from application.services import PlayerHomeService, WorkService
from application.services.volunteer.volunteer_schedule_grid import CALLED_OFF
from models import User

router = APIRouter(
    prefix='/work', tags=['Work'], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding())],
)


@router.get(
    '',
    response_model=WorkAreasResponse,
    summary='Your Work tab',
    description=(
        "The areas your roles open here (check-in desk, volunteer schedule, your volunteer "
        "availability, Proctor Station), each with a live line. An area you can't open is null "
        "or false.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_work(actor: User = Depends(require_api_actor)) -> WorkAreasResponse:
    body = WorkAreasResponse.model_validate(await WorkService().areas(actor), from_attributes=True)
    body.zone = await PlayerHomeService.viewer_zone(actor)
    return body


def _schedule_response(view, zone) -> VolunteerScheduleResponse:
    grid = view.grid
    return VolunteerScheduleResponse(
        zone=zone,
        days=view.days,
        day=view.day,
        midnight=grid.midnight if grid else None,
        first_hour=grid.first_hour if grid else 0,
        last_hour=grid.last_hour if grid else 0,
        columns=[
            GridColumnResponse.model_validate({
                'position_id': column.position_id,
                'position': column.position,
                'shifts': [
                    {
                        'starts_at': shift.starts_at,
                        'ends_at': shift.ends_at,
                        'label': shift.label,
                        'start_hour': shift.start_hour,
                        'end_hour': shift.end_hour,
                        'lane': shift.lane,
                        'lanes': shift.lanes,
                        'slots_needed': shift.slots_needed,
                        'open_slots': shift.open_slots,
                        'people': [
                            {'name': p.name, 'is_viewer': p.is_viewer, 'called_off': p.arrival == CALLED_OFF}
                            for p in shift.people
                        ],
                    }
                    for shift in column.shifts
                ],
            })
            for column in (grid.columns if grid else ())
        ],
        roles=[GridRoleResponse.model_validate(r, from_attributes=True) for r in view.roles],
        hidden_with_shifts=view.hidden_with_shifts,
        customized=view.customized,
    )


@router.get(
    '/schedule',
    response_model=VolunteerScheduleResponse,
    summary='The volunteer schedule for one day',
    description=(
        "Every role's published shifts on one event day, laid out as columns on an hour scale "
        "in your saved column order. Leave `day` out for today during the event, else its "
        "first day.\n\n"
        "**Who can call this:** Volunteers, proctors, check-in desk, Broadcast Tech, volunteer "
        "coordinators and staff."
    ),
)
async def volunteer_schedule(
    day: Optional[date] = Query(None, description="The event day to show (local date)."),
    actor: User = Depends(require_api_actor),
) -> VolunteerScheduleResponse:
    return _schedule_response(await WorkService().schedule_view(actor, day), await PlayerHomeService.viewer_zone(actor))


@router.put(
    '/schedule/columns',
    response_model=list[GridRoleResponse],
    summary='Choose and order the volunteer schedule columns',
    description=(
        "Saves which roles you show and in what order, for you only. The website's Full "
        "Schedule uses the same choice.\n\n"
        "**Who can call this:** Anyone who can see the volunteer schedule."
    ),
)
async def save_schedule_columns(
    body: ScheduleColumnsRequest, actor: User = Depends(require_write_actor),
) -> list[GridRoleResponse]:
    roles = await WorkService().save_schedule_columns(actor, [(c.position_id, c.visible) for c in body.columns])
    return [GridRoleResponse.model_validate(r, from_attributes=True) for r in roles]


@router.delete(
    '/schedule/columns',
    response_model=list[GridRoleResponse],
    summary='Reset the volunteer schedule columns',
    description=(
        "Forgets your column choice: every role shown, in the community's order.\n\n"
        "**Who can call this:** Anyone who can see the volunteer schedule."
    ),
)
async def reset_schedule_columns(actor: User = Depends(require_write_actor)) -> list[GridRoleResponse]:
    roles = await WorkService().reset_schedule_columns(actor)
    return [GridRoleResponse.model_validate(r, from_attributes=True) for r in roles]
