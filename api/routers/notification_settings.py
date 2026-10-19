"""Community notification settings: who gets each message and whether it pings (Staff only)."""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_staff, require_staff_write
from api.schemas.notification_settings import (
    NotificationSettingResponse,
    NotificationSettingsReset,
    NotificationSettingUpdate,
)
from application.services import NotificationSettingsService
from models import NotificationRecipient, User

router = APIRouter(
    prefix="/notification-settings", tags=["Notification settings"], route_class=ServiceErrorRoute,
)


@router.get(
    "",
    response_model=List[NotificationSettingResponse],
    summary="List notification settings",
    description=(
        "Every message Wizzrobe sends, once per kind of recipient, with how this community sends "
        "it: `off`, `silent` or `ping`. Settings apply to the Discord DM and every copy of it "
        "(device, email, Telegram); email can't be silent.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_notification_settings(actor: User = Depends(require_staff)):
    return [NotificationSettingResponse.from_row(r) for r in await NotificationSettingsService.list_settings(actor)]


@router.put(
    "/{notice}/{recipient}",
    response_model=NotificationSettingResponse,
    summary="Set how one message reaches one kind of recipient",
    description=(
        "Sets `off`, `silent` or `ping` and records the change in the audit log. Choosing the "
        "default clears the override. Fails with 400 for a message or recipient that isn't in "
        "the list.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_notification_setting(
    notice: str, recipient: NotificationRecipient, body: NotificationSettingUpdate,
    actor: User = Depends(require_staff_write),
):
    return NotificationSettingResponse.from_row(await NotificationSettingsService.set_mode(actor, notice, recipient, body.mode))


@router.post(
    "/reset",
    response_model=NotificationSettingsReset,
    summary="Put every notification back to its default",
    description="Clears every override this community saved.\n\n**Who can call this:** Staff.",
)
async def reset_notification_settings(actor: User = Depends(require_staff_write)):
    return NotificationSettingsReset(reset=await NotificationSettingsService.reset_all(actor))
