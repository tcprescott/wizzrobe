"""The app's Me screen: your profile, connected accounts, alerts and the personal records
staff keep for you (accommodation, emergency contact).

Every route is about the caller. Sections a community hasn't switched on answer 404.
"""

from typing import List, Literal

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.my_profile import (
    AccommodationResponse,
    AccommodationUpdate,
    ClockUpdate,
    ConnectedAccountResponse,
    EmergencyContactResponse,
    EmergencyContactUpdate,
    FollowableTournamentResponse,
    ProfileResponse,
    StaffAlertsResponse,
    StaffAlertUpdate,
)
from application.errors import NotFoundError
from application.services import (
    AccommodationService,
    EmergencyContactService,
    MyProfileService,
    StaffAlertService,
    TimezoneService,
)
from application.services.accommodation_service import DETAILS_MAX_LENGTH
from models import User

router = APIRouter(
    prefix='/me', tags=['My profile'], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding())],
)

Provider = Literal['challonge', 'startgg', 'twitch', 'racetime']


@router.get(
    '/profile',
    response_model=ProfileResponse,
    summary='Your profile and its sections',
    description=(
        "Your name, pronouns and clock, plus one line on each section of your profile this "
        "community has (notifications, availability, accounts and so on).\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_profile(actor: User = Depends(require_api_actor)) -> ProfileResponse:
    return ProfileResponse.model_validate(await MyProfileService().overview(actor), from_attributes=True)


@router.patch(
    '/clock',
    response_model=ProfileResponse,
    summary='Set your timezone and 12/24-hour clock',
    description=(
        "Changes only the fields you send; null goes back to following your device. Fails with "
        "400 for a zone that isn't an IANA name or a clock other than `12` or `24`.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def set_clock(body: ClockUpdate, actor: User = Depends(require_write_actor)) -> ProfileResponse:
    if 'timezone' in body.model_fields_set:
        actor.timezone = await TimezoneService.set_user_timezone(actor, body.timezone)
    if 'hour_cycle' in body.model_fields_set:
        actor.hour_cycle = await TimezoneService.set_user_hour_cycle(actor, body.hour_cycle)
    return ProfileResponse.model_validate(await MyProfileService().overview(actor), from_attributes=True)


@router.get(
    '/accounts',
    response_model=List[ConnectedAccountResponse],
    summary='Your connected accounts',
    description=(
        "Each outside account this community can link (Challonge, start.gg, Twitch, "
        "racetime.gg) and whether you've linked it. Linking runs on the website: open "
        "`link_path` there.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_accounts(actor: User = Depends(require_api_actor)) -> List[ConnectedAccountResponse]:
    return [ConnectedAccountResponse.model_validate(a, from_attributes=True) for a in await MyProfileService().accounts(actor)]


@router.delete(
    '/accounts/{provider}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Unlink one of your connected accounts',
    description=(
        "Forgets the link. Unlinking one that isn't linked changes nothing.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def unlink_account(provider: Provider, actor: User = Depends(require_write_actor)) -> None:
    await MyProfileService.unlink_account(actor, provider)


@router.get(
    '/followable-tournaments',
    response_model=List[FollowableTournamentResponse],
    summary='Tournaments you can follow',
    description=(
        "The running tournaments, each with whether you play in it and how much you hear about "
        "its matches. Change a level with `PUT /notifications/preferences`.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def followable(actor: User = Depends(require_api_actor)) -> List[FollowableTournamentResponse]:
    return [
        FollowableTournamentResponse.model_validate(t, from_attributes=True)
        for t in await MyProfileService.followable_tournaments(actor)
    ]


@router.get(
    '/staff-alerts',
    response_model=StaffAlertsResponse,
    summary='Your staff alerts',
    description=(
        "Your alert-receiving duty shifts, and the tournaments you run with whether player "
        "comments and reschedule requests on them reach you. 404 when you have neither.\n\n"
        "**Who can call this:** Tournament admins and people with alert-receiving shifts."
    ),
)
async def my_staff_alerts(actor: User = Depends(require_api_actor)) -> StaffAlertsResponse:
    alerts = await MyProfileService.staff_alerts(actor)
    if alerts is None:
        raise NotFoundError('You have no staff alerts here.')
    return StaffAlertsResponse.model_validate(alerts, from_attributes=True)


@router.put(
    '/staff-alerts/{tournament_id}',
    response_model=StaffAlertsResponse,
    summary="Turn one tournament's staff alerts on or off",
    description=(
        "Fails with 400 for a tournament you don't run.\n\n"
        "**Who can call this:** That tournament's admins."
    ),
)
async def set_staff_alert(
    tournament_id: int, body: StaffAlertUpdate, actor: User = Depends(require_write_actor),
) -> StaffAlertsResponse:
    await StaffAlertService.set_tournament_alerts(actor, tournament_id, body.on)
    return await my_staff_alerts(actor)


def _accommodation(request) -> AccommodationResponse:
    status_value = request.status.value if request is not None else None
    return AccommodationResponse(
        requested=request is not None and status_value != 'withdrawn',
        details=request.details if request is not None else None,
        status=status_value,
        changed_since_arranged=bool(request is not None and request.changed_since_arranged),
        max_length=DETAILS_MAX_LENGTH,
    )


@router.get(
    '/accommodation',
    response_model=AccommodationResponse,
    summary='Your ADA accommodation request',
    description=(
        "Whether you've asked for accommodation here, what you wrote, and where staff are with it.\n\n"
        "**Who can call this:** Any member, when the community takes accommodation requests."
    ),
)
async def my_accommodation(actor: User = Depends(require_api_actor)) -> AccommodationResponse:
    return _accommodation(await AccommodationService().get_mine(actor))


@router.put(
    '/accommodation',
    response_model=AccommodationResponse,
    summary='Ask for accommodation, change it, or withdraw',
    description=(
        "Staff get a DM when a request opens or an arranged one changes. Fails with 400 when "
        "the details are too long.\n\n"
        "**Who can call this:** Any member, when the community takes accommodation requests."
    ),
)
async def set_accommodation(body: AccommodationUpdate, actor: User = Depends(require_write_actor)) -> AccommodationResponse:
    return _accommodation(await AccommodationService().set_my_request(actor, body.requested, body.details))


def _contact(contact) -> EmergencyContactResponse:
    if contact is None:
        return EmergencyContactResponse()
    viewer = contact.last_viewed_by if contact.last_viewed_at else None
    return EmergencyContactResponse(
        name=contact.name, relationship=contact.relationship, phone=contact.phone,
        last_viewed_at=contact.last_viewed_at,
        last_viewed_by=(viewer.preferred_name if viewer else ('a former staff member' if contact.last_viewed_at else None)),
    )


@router.get(
    '/emergency-contact',
    response_model=EmergencyContactResponse,
    summary='Your emergency contact',
    description=(
        "Who staff should call if something happens to you at an event, and when staff last "
        "opened it. Every field is null when you haven't added one.\n\n"
        "**Who can call this:** Any member, when the community keeps emergency contacts."
    ),
)
async def my_contact(actor: User = Depends(require_api_actor)) -> EmergencyContactResponse:
    return _contact(await EmergencyContactService().get_mine(actor))


@router.put(
    '/emergency-contact',
    response_model=EmergencyContactResponse,
    summary='Add or change your emergency contact',
    description=(
        "Fails with 400 without a name, or with a phone number that doesn't look like one.\n\n"
        "**Who can call this:** Any member, when the community keeps emergency contacts."
    ),
)
async def save_contact(body: EmergencyContactUpdate, actor: User = Depends(require_write_actor)) -> EmergencyContactResponse:
    await EmergencyContactService().save_mine(actor, body.name, body.relationship, body.phone)
    return _contact(await EmergencyContactService().get_mine(actor))


@router.delete(
    '/emergency-contact',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Remove your emergency contact',
    description=(
        "Deletes it outright; nothing of it is kept.\n\n"
        "**Who can call this:** Any member, when the community keeps emergency contacts."
    ),
)
async def remove_contact(actor: User = Depends(require_write_actor)) -> None:
    await EmergencyContactService().remove_mine(actor)
