"""The app's Admin → Meetups screen: the review queue for member-run events."""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_staff, require_staff_write
from api.schemas.admin_meetups import (
    MeetupAttendeeResponse,
    MeetupRejectRequest,
    MeetupResponse,
    MeetupUpdate,
)
from application.services import CommunityEventService
from application.services.community_event_service import EventDraft
from models import CommunityEvent, User

router = APIRouter(
    prefix='/admin/meetups', tags=['Admin: meetups'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(meetup_id=CommunityEvent))],
)


@router.get(
    '',
    response_model=List[MeetupResponse],
    summary='Every meetup in this event',
    description=(
        "All of this event's meetups in every status, soonest first. The screen filters by "
        "status itself. 404 when the community doesn't run meetups.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_meetups(actor: User = Depends(require_staff)) -> List[MeetupResponse]:
    return [MeetupResponse.build(e) for e in await CommunityEventService().list_for_review(actor)]


@router.get(
    '/{meetup_id}',
    response_model=MeetupResponse,
    summary='One meetup',
    description="**Who can call this:** Staff.",
)
async def get_meetup(meetup_id: int, actor: User = Depends(require_staff)) -> MeetupResponse:
    return MeetupResponse.build(await CommunityEventService().get_event(actor, meetup_id))


@router.get(
    '/{meetup_id}/attendees',
    response_model=List[MeetupAttendeeResponse],
    summary="Who RSVP'd",
    description="In the order they RSVP'd.\n\n**Who can call this:** Staff.",
)
async def attendees(meetup_id: int, actor: User = Depends(require_staff)) -> List[MeetupAttendeeResponse]:
    return [
        MeetupAttendeeResponse(id=u.id, name=u.display_name or u.username)
        for u in await CommunityEventService().list_attendees(actor, meetup_id)
    ]


@router.post(
    '/{meetup_id}/approve',
    response_model=MeetupResponse,
    summary='List a meetup',
    description=(
        "Lists a waiting meetup, or puts back one staff took down, and DMs the host. Fails "
        "with 400 for a cancelled one.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def approve(meetup_id: int, actor: User = Depends(require_staff_write)) -> MeetupResponse:
    service = CommunityEventService()
    await service.approve(actor, meetup_id)
    return MeetupResponse.build(await service.get_event(actor, meetup_id))


@router.post(
    '/{meetup_id}/reject',
    response_model=MeetupResponse,
    summary='Turn a meetup down, or take it down',
    description=(
        "Rejects a waiting meetup or pulls a listed one, with a reason the host sees. Anyone "
        "who subscribed to a listed one is told it's off.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def reject(
    meetup_id: int, body: MeetupRejectRequest, actor: User = Depends(require_staff_write),
) -> MeetupResponse:
    service = CommunityEventService()
    await service.reject(actor, meetup_id, body.reason)
    return MeetupResponse.build(await service.get_event(actor, meetup_id))


@router.post(
    '/{meetup_id}/cancel',
    response_model=MeetupResponse,
    summary='Call a meetup off',
    description="Subscribers hear about it if it was listed.\n\n**Who can call this:** Staff.",
)
async def cancel(meetup_id: int, actor: User = Depends(require_staff_write)) -> MeetupResponse:
    service = CommunityEventService()
    await service.cancel(actor, meetup_id)
    return MeetupResponse.build(await service.get_event(actor, meetup_id))


@router.put(
    '/{meetup_id}',
    response_model=MeetupResponse,
    summary='Edit a meetup',
    description=(
        "Saves every field. `host_id` hands it to another member, who gets a DM. If a listed "
        "meetup's start moves, its subscribers are told.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update(
    meetup_id: int, body: MeetupUpdate, actor: User = Depends(require_staff_write),
) -> MeetupResponse:
    service = CommunityEventService()
    draft = EventDraft(
        title=body.title, description=body.description, starts_at=body.starts_at,
        duration_minutes=body.duration_minutes, location=body.location,
        rsvp_enabled=body.rsvp_enabled, rsvp_capacity=body.rsvp_capacity,
    )
    await service.update(actor, meetup_id, draft, host_id=body.host_id)
    return MeetupResponse.build(await service.get_event(actor, meetup_id))
