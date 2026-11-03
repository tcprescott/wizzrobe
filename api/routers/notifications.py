"""Your own notifications: tournaments you follow, and how each message reaches you."""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.notifications import (
    MyNotificationPresetUpdate,
    MyNotificationResponse,
    MyNotificationsResponse,
    MyNotificationUpdate,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
)
from application.services import NotificationPreferenceService, TournamentNotificationService
from models import NotificationRecipient, User

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
    route_class=ServiceErrorRoute,
)


@router.get(
    "/preferences",
    response_model=List[NotificationPreferenceResponse],
    summary="List your tournament notification preferences",
    description=(
        "Returns each tournament you follow and how much you hear about its matches.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_preferences(actor: User = Depends(require_api_actor)):
    return await TournamentNotificationService().get_user_preferences(actor)


@router.put(
    "/preferences",
    response_model=NotificationPreferenceResponse,
    summary="Set your notification preference for a tournament",
    description=(
        "Creates or changes how much you hear about one tournament's matches. Following "
        "doesn't need you to be signed up. Notifications go to your devices, and as Discord DMs "
        "when your DM setting is on. Fails with 400 for an unknown level.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def upsert_preference(
    body: NotificationPreferenceUpdate, actor: User = Depends(require_write_actor),
):
    return await TournamentNotificationService().upsert_preference(
        user=actor,
        tournament_id=body.tournament_id,
        match_notifications=body.match_notifications,
    )


_CEILING = (
    "Your community's setting is a ceiling: you can make any message quieter, never louder."
)


@router.get(
    "/messages",
    response_model=MyNotificationsResponse,
    summary="List how each message reaches you",
    description=(
        "Every message that can reach you in this community, with your preset, your own choice "
        f"and the community's. {_CEILING} Staff alerts are listed only for people who can see "
        "the admin side.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_notifications(actor: User = Depends(require_api_actor)):
    return MyNotificationsResponse.from_mine(await NotificationPreferenceService.get_mine(actor))


@router.put(
    "/messages/preset",
    response_model=MyNotificationsResponse,
    summary="Choose your notification preset",
    description=(
        "`everything`, `needs_me` or `quiet`. Messages you changed one by one keep your "
        f"choice. {_CEILING}\n\n"
        "**Who can call this:** Any member."
    ),
)
async def set_my_preset(body: MyNotificationPresetUpdate, actor: User = Depends(require_write_actor)):
    return MyNotificationsResponse.from_mine(await NotificationPreferenceService.set_preset(actor, body.preset))


@router.put(
    "/messages/{notice}/{recipient}",
    response_model=MyNotificationResponse,
    summary="Choose how one message reaches you",
    description=(
        "Sets `off`, `silent` or `ping` for one message, or `null` to follow your preset again. "
        f"{_CEILING} Fails with 400 for a message or recipient that isn't in the list.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def set_my_notification(
    notice: str, recipient: NotificationRecipient, body: MyNotificationUpdate,
    actor: User = Depends(require_write_actor),
):
    return MyNotificationResponse.from_row(
        await NotificationPreferenceService.set_mode(actor, notice, recipient, body.mode),
    )


@router.post(
    "/messages/reset",
    response_model=MyNotificationsResponse,
    summary="Put every message back to your preset",
    description="Clears your per-message choices. Your preset stays.\n\n**Who can call this:** Any member.",
)
async def reset_my_notifications(actor: User = Depends(require_write_actor)):
    return MyNotificationsResponse.from_mine(await NotificationPreferenceService.reset(actor))
