"""A community's events: list, create (copying setup), and run their lifecycle.

Every other event-owned router takes ``?event=<slug>``; this one names the
event in the path because the event is what it acts on. A draft is a 404 for
anyone but staff and its own role holders, as on the pages.
"""

from typing import List

from fastapi import APIRouter, Depends, Response, status

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.config import ConfigValueUpdate
from api.schemas.events import (
    EventArchiveRequest,
    EventCreateRequest,
    EventCreateResponse,
    EventResponse,
    EventSettingResponse,
    EventUpdateRequest,
)
from application.event_context import event_scope
from application.services import EventService, SystemConfigService
from application.services.system_config_service import is_event_key
from models import User

router = APIRouter(prefix="/events", tags=["Events"], route_class=ServiceErrorRoute)

STAFF_ONLY = "**Who can call this:** Staff."


def _out(event) -> EventResponse:
    return EventResponse(
        id=event.id, slug=event.slug, name=event.name, status=event.status,
        is_default=event.is_default, starts_on=event.starts_on, ends_on=event.ends_on,
    )


async def _event_id(actor: User, slug: str) -> int:
    event_id = await EventService.call_event_id(actor, slug)
    assert event_id is not None
    return event_id


@router.get(
    "",
    response_model=List[EventResponse],
    summary="List events",
    description=(
        "The community's events you can see: the default first, then live events, drafts and "
        "archived ones. Drafts are listed for staff and for the people holding a role in them.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_events(actor: User = Depends(require_api_actor)):
    return [_out(e) for e in await EventService.visible_events(actor)]


@router.get(
    "/{slug}",
    response_model=EventResponse,
    summary="Get an event",
    description="One event by its slug. A draft you can't see is a 404.\n\n**Who can call this:** Any member.",
)
async def get_event(slug: str, actor: User = Depends(require_api_actor)):
    event_id = await _event_id(actor, slug)
    return _out(await EventService.get(event_id))


@router.post(
    "",
    response_model=EventCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an event",
    description=(
        "Creates a draft event, optionally copying setup from another event in the same "
        "transaction. Fails with 400 on a blank or too-long name, a slug that's taken, reserved or "
        "badly formed, an end date before the start, or an unknown setup group.\n\n" + STAFF_ONLY
    ),
)
async def create_event(body: EventCreateRequest, actor: User = Depends(require_write_actor)):
    copy_from = await _event_id(actor, body.copy_from) if body.copy_from else None
    event, copied = await EventService.create_event(
        actor, name=body.name, slug=body.slug, starts_on=body.starts_on, ends_on=body.ends_on,
        copy_from=copy_from, copy=frozenset(body.copy_setup),
    )
    return EventCreateResponse(event=_out(event), copied=copied)


@router.patch(
    "/{slug}",
    response_model=EventResponse,
    summary="Update an event",
    description=(
        "Renames an event or changes its dates. Only a draft's slug can change, since links "
        "already point at a live or archived one.\n\n" + STAFF_ONLY
    ),
)
async def update_event(slug: str, body: EventUpdateRequest, actor: User = Depends(require_write_actor)):
    event_id = await _event_id(actor, slug)
    sent = body.model_fields_set
    dates = {key: getattr(body, key) for key in ('starts_on', 'ends_on') if key in sent}
    return _out(await EventService.update_event(actor, event_id, name=body.name, slug=body.slug, **dates))


@router.post(
    "/{slug}/go-live",
    response_model=EventResponse,
    summary="Make a draft live",
    description="Opens a draft to everyone. Fails with 400 when it isn't a draft.\n\n" + STAFF_ONLY,
)
async def go_live(slug: str, actor: User = Depends(require_write_actor)):
    return _out(await EventService.go_live(actor, await _event_id(actor, slug)))


@router.post(
    "/{slug}/make-default",
    response_model=EventResponse,
    summary="Make an event the default",
    description=(
        "The default is where bare links and calls without `event` land. Only a live event can be "
        "the default. Moving it never re-grants Discord roles.\n\n" + STAFF_ONLY
    ),
)
async def make_default(slug: str, actor: User = Depends(require_write_actor)):
    return _out(await EventService.make_default(actor, await _event_id(actor, slug)))


@router.post(
    "/{slug}/archive",
    response_model=EventResponse,
    summary="Archive an event",
    description=(
        "Makes the event read-only: its race rooms close, seed rolls still waiting are dropped and "
        "the Discord roles its rules gave are taken back. Archiving the default needs "
        "`new_default`, a live event to take over.\n\n" + STAFF_ONLY
    ),
)
async def archive(slug: str, body: EventArchiveRequest, actor: User = Depends(require_write_actor)):
    event_id = await _event_id(actor, slug)
    new_default = await _event_id(actor, body.new_default) if body.new_default else None
    return _out(await EventService.archive(actor, event_id, new_default_id=new_default))


@router.post(
    "/{slug}/unarchive",
    response_model=EventResponse,
    summary="Unarchive an event",
    description="Makes an archived event live and editable again.\n\n" + STAFF_ONLY,
)
async def unarchive(slug: str, actor: User = Depends(require_write_actor)):
    return _out(await EventService.unarchive(actor, await _event_id(actor, slug)))


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft",
    description="Deletes a draft and everything in it. Live and archived events can't be deleted.\n\n" + STAFF_ONLY,
)
async def delete_draft(slug: str, actor: User = Depends(require_write_actor)):
    await EventService.delete_draft(actor, await _event_id(actor, slug))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{slug}/settings",
    response_model=List[EventSettingResponse],
    summary="List an event's settings",
    description=(
        "The settings stored on this event: dates, hours, capacity, stations, volunteer reward "
        "tiers, broadcasters and the rest of Admin → Event settings. Community-wide settings stay "
        "at `/api/config`.\n\n" + STAFF_ONLY
    ),
)
async def list_settings(slug: str, actor: User = Depends(require_api_actor)):
    with event_scope(await _event_id(actor, slug)):
        rows = await SystemConfigService.list_event_entries(actor)
    return [EventSettingResponse(name=name, value=value) for name, value in rows]


@router.put(
    "/{slug}/settings/{key}",
    response_model=EventSettingResponse,
    summary="Set one of an event's settings",
    description=(
        "Validated and audited like `PUT /api/config/{key}`. Fails with 400 for a key that isn't an "
        "event setting.\n\n" + STAFF_ONLY
    ),
)
async def set_setting(slug: str, key: str, body: ConfigValueUpdate, actor: User = Depends(require_write_actor)):
    if not is_event_key(key):
        raise ValueError(f"'{key}' isn't an event setting. Community settings are at /api/config/{key}.")
    with event_scope(await _event_id(actor, slug)):
        entry = await SystemConfigService.set_validated(key, body.value, actor)
    return EventSettingResponse(name=entry.name, value=entry.value)
