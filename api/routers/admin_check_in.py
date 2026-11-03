"""The app's Admin → Check-in Roster screen: check-in events, their badge
lanyards, and the community's Matcherino login.

The desk itself (roster, checking people in, walk-ups) stays under ``/check-in``.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.admin_check_in import (
    AlertUserWrite,
    CheckInAdminEventResponse,
    CheckInBoardResponse,
    CheckInEventWrite,
    CheckInTierResponse,
    LanyardUpdate,
    MatcherinoLoginResponse,
    MatcherinoLoginWrite,
    PersonOption,
    VenuePreviewResponse,
)
from application.services import CheckInAdminService, CheckInService, MatcherinoLoginService
from models import CheckInEvent, CheckInTier, User

router = APIRouter(
    prefix='/admin/check-in', tags=['Admin: check-in'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(event_id=CheckInEvent, tier_id=CheckInTier))],
)


@router.get(
    '',
    response_model=CheckInBoardResponse,
    summary='Check-in events and the Matcherino login',
    description=(
        "Every check-in event in this event, draft and closed ones included, the Matcherino "
        "login card, and the choices the event form offers. 404 when the community doesn't "
        "run check-in.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def board(actor: User = Depends(require_api_actor)) -> CheckInBoardResponse:
    return CheckInBoardResponse.model_validate(await CheckInAdminService().board(actor), from_attributes=True)


@router.post(
    '/events',
    response_model=CheckInAdminEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary='Create a check-in event',
    description=(
        "Fails with 400 when the name is blank or the venue is already used by another "
        "event.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def create_event(body: CheckInEventWrite, actor: User = Depends(require_write_actor)) -> CheckInAdminEventResponse:
    event = await CheckInService().create_event(
        actor, body.name, venue_id=body.matcherino_venue_id, status=body.status,
        sync_interval_minutes=body.sync_interval_minutes,
        comp_roles=body.comp_roles, comp_volunteers=body.comp_volunteers,
    )
    return CheckInAdminEventResponse.model_validate(event, from_attributes=True)


@router.put(
    '/events/{event_id}',
    response_model=CheckInAdminEventResponse,
    summary='Edit a check-in event',
    description=(
        "Saves every field. Fails with 400 when the venue changes on an event that already "
        "has a Matcherino roster.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update_event(
    event_id: int, body: CheckInEventWrite, actor: User = Depends(require_write_actor),
) -> CheckInAdminEventResponse:
    event = await CheckInService().update_event(
        actor, event_id, body.name, body.matcherino_venue_id, body.status,
        body.sync_interval_minutes, comp_roles=body.comp_roles, comp_volunteers=body.comp_volunteers,
    )
    return CheckInAdminEventResponse.model_validate(event, from_attributes=True)


@router.delete(
    '/events/{event_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Delete a check-in event',
    description="Deletes the event and its whole roster, check-ins included.\n\n**Who can call this:** Staff.",
)
async def delete_event(event_id: int, actor: User = Depends(require_write_actor)) -> None:
    await CheckInService().delete_event(actor, event_id)


@router.get(
    '/events/{event_id}/tiers',
    response_model=List[CheckInTierResponse],
    summary="An event's badge types and their lanyards",
    description="Most expensive first.\n\n**Who can call this:** Staff.",
)
async def tiers(event_id: int, actor: User = Depends(require_api_actor)) -> List[CheckInTierResponse]:
    return [
        CheckInTierResponse.model_validate(t, from_attributes=True)
        for t in await CheckInAdminService().tiers(actor, event_id)
    ]


@router.put(
    '/tiers/{tier_id}/lanyard',
    response_model=CheckInTierResponse,
    summary='Pick the lanyard a badge type gets',
    description="The sync leaves it alone afterwards.\n\n**Who can call this:** Staff.",
)
async def set_lanyard(
    tier_id: int, body: LanyardUpdate, actor: User = Depends(require_write_actor),
) -> CheckInTierResponse:
    tier = await CheckInService().set_tier_lanyard(actor, tier_id, body.lanyard)
    return CheckInTierResponse.model_validate(tier, from_attributes=True)


@router.get(
    '/venues/{venue_id}',
    response_model=VenuePreviewResponse,
    summary='Look a Matcherino venue up',
    description=(
        "Reads the venue's name and badge types from Matcherino, so you can check the id "
        "before saving. Fails with 400 when Matcherino doesn't know it or no login is saved.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def preview_venue(venue_id: int, actor: User = Depends(require_api_actor)) -> VenuePreviewResponse:
    preview = await CheckInService().preview_venue(actor, venue_id)
    return VenuePreviewResponse(
        venue_id=preview.venue.id, title=preview.venue.title, badge_types=[t.title for t in preview.tiers],
    )


@router.put(
    '/matcherino-login',
    response_model=MatcherinoLoginResponse,
    summary='Save the Matcherino login',
    description=(
        "Checks the pasted refresh token with Matcherino before saving it. Fails with 400 "
        "when it's empty, looks wrong, or Matcherino refuses it.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_login(body: MatcherinoLoginWrite, actor: User = Depends(require_write_actor)) -> MatcherinoLoginResponse:
    return MatcherinoLoginResponse.model_validate(
        await MatcherinoLoginService().set_login(actor, body.refresh_token), from_attributes=True,
    )


@router.delete(
    '/matcherino-login',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Remove the Matcherino login',
    description="Syncs stop until a new one is saved.\n\n**Who can call this:** Staff.",
)
async def clear_login(actor: User = Depends(require_write_actor)) -> None:
    await MatcherinoLoginService().clear_login(actor)


@router.get(
    '/matcherino-login/alert-candidates',
    response_model=List[PersonOption],
    summary='Who can be told when the login stops working',
    description="**Who can call this:** Staff.",
)
async def alert_candidates(actor: User = Depends(require_api_actor)) -> List[PersonOption]:
    return [
        PersonOption(id=u.id, name=u.display_name or u.username)
        for u in await MatcherinoLoginService().alert_candidates(actor)
    ]


@router.put(
    '/matcherino-login/alert-user',
    response_model=MatcherinoLoginResponse,
    summary='Pick who hears when the login stops working',
    description="Fails with 400 before a login is saved.\n\n**Who can call this:** Staff.",
)
async def set_alert_user(body: AlertUserWrite, actor: User = Depends(require_write_actor)) -> MatcherinoLoginResponse:
    return MatcherinoLoginResponse.model_validate(
        await MatcherinoLoginService().set_alert_user(actor, body.user_id), from_attributes=True,
    )
