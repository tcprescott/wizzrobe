"""Where your notifications reach you: Discord DMs, device push, email and Telegram.

What you hear about (presets, per-message settings, tournaments you follow)
lives under ``/notifications``; these routes are the channels themselves.
Email and Telegram answer 404 where the community hasn't turned them on.
"""

from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.my_profile import (
    ChannelsResponse,
    ChannelTestRequest,
    ChannelTestResponse,
    EmailConfirmRequest,
    EmailStartRequest,
    EnabledUpdate,
    NativeDeviceRegisterRequest,
    NativeDeviceResponse,
    PushDeviceResponse,
    PushSubscribeRequest,
    TelegramLinkResponse,
)
from application.errors import NotFoundError
from application.services import (
    FeatureFlagService,
    NativePushService,
    NotificationChannelService,
    UserService,
    WebPushService,
)
from application.services.notification_channel_service import Channel, email_available, telegram_available
from models import FeatureFlag, User

router = APIRouter(prefix='/me/channels', tags=['My notifications'], route_class=ServiceErrorRoute)


async def _fresh(actor: User) -> User:
    return await UserService().get_user_by_id(actor.id) or actor


async def _channels(actor: User) -> ChannelsResponse:
    user = await _fresh(actor)
    push = WebPushService()
    configured = push.is_configured()
    devices = await push.list_subscriptions(user) if configured else []
    native = NativePushService()
    native_tokens = await native.list_tokens(user)
    enabled = await FeatureFlagService().is_enabled(FeatureFlag.NOTIFICATION_CHANNELS)
    return ChannelsResponse(
        discord=bool(user.discord_id),
        dm_notifications=bool(user.dm_notifications),
        push_configured=configured,
        push_public_key=push.get_public_key() if configured else None,
        devices=[
            PushDeviceResponse(
                id=d.id, user_agent=d.user_agent, created_at=d.created_at, last_used_at=d.last_used_at,
                endpoint_host=urlparse(d.endpoint).hostname or '',
            )
            for d in devices
        ],
        native_push_platforms=native.configured_platforms(),
        native_devices=[NativeDeviceResponse.model_validate(d) for d in native_tokens],
        channels_enabled=enabled,
        email_available=enabled and email_available(),
        email=user.email if enabled else None,
        email_notifications=enabled and bool(user.email_notifications),
        telegram_available=enabled and telegram_available(),
        telegram_username=user.telegram_username if enabled else None,
        telegram_connected=enabled and user.telegram_chat_id is not None,
        telegram_notifications=enabled and bool(user.telegram_notifications),
    )


@router.get(
    '',
    response_model=ChannelsResponse,
    summary='Where your notifications go',
    description=(
        "Your Discord DM switch, the devices getting push notifications, and your email and "
        "Telegram channels where the community offers them.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def my_channels(actor: User = Depends(require_api_actor)) -> ChannelsResponse:
    return await _channels(actor)


@router.post(
    '/devices',
    response_model=ChannelsResponse,
    summary='Turn on notifications for this device',
    description=(
        "Saves the browser's push subscription so notifications reach this device. "
        "Subscribing the same device again just refreshes it. Fails with 400 when the "
        "endpoint isn't an https URL on a public host or the keys are malformed.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def add_device(body: PushSubscribeRequest, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    if not WebPushService.is_configured():
        raise NotFoundError("Device notifications aren't set up on this server.")
    await WebPushService().subscribe(
        actor, endpoint=body.endpoint, p256dh=body.p256dh, auth=body.auth, user_agent=body.user_agent,
    )
    return await _channels(actor)


@router.delete(
    '/devices/{device_id}',
    response_model=ChannelsResponse,
    summary='Stop notifications to one of your devices',
    description="**Who can call this:** Any member, for their own devices.",
)
async def remove_device(device_id: int, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await WebPushService().remove_subscription(actor, device_id)
    return await _channels(actor)


@router.post(
    '/native-devices',
    response_model=ChannelsResponse,
    summary='Turn on notifications in the store app',
    description=(
        "Saves the push token the iOS or Android app got from its platform, so notifications "
        "reach that phone. Registering the same token again refreshes it, and a token another "
        "account registered moves to you (someone else signed in on that phone). Answers 404 "
        "when this server can't send to that platform.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def add_native_device(
    body: NativeDeviceRegisterRequest, actor: User = Depends(require_write_actor),
) -> ChannelsResponse:
    row = await NativePushService().register(
        actor, platform=body.platform, token=body.token, device_name=body.device_name,
    )
    channels = await _channels(actor)
    channels.registered_device_id = row.id
    return channels


@router.delete(
    '/native-devices/{device_id}',
    response_model=ChannelsResponse,
    summary='Stop notifications to one of your phones',
    description="**Who can call this:** Any member, for their own phones.",
)
async def remove_native_device(device_id: int, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NativePushService().remove(actor, device_id)
    return await _channels(actor)


@router.post(
    '/email',
    response_model=ChannelsResponse,
    summary='Send a code to an email address',
    description=(
        "Mails a 6-digit code to confirm the address; confirm it with `POST /me/channels/email/confirm`. "
        "Fails with 400 when we can't send to it or you've asked for many codes in a short time.\n\n"
        "**Who can call this:** Any member, where the community offers email."
    ),
)
async def start_email(body: EmailStartRequest, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NotificationChannelService().start_email_verification(actor, body.email)
    return await _channels(actor)


@router.post(
    '/email/confirm',
    response_model=ChannelsResponse,
    summary='Confirm your email with the code',
    description=(
        "Stores the address and turns email notifications on. Fails with 400 for a wrong or "
        "expired code.\n\n"
        "**Who can call this:** Any member, where the community offers email."
    ),
)
async def confirm_email(body: EmailConfirmRequest, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NotificationChannelService().confirm_email(actor, body.code)
    return await _channels(actor)


@router.delete(
    '/email',
    response_model=ChannelsResponse,
    summary='Remove your email address',
    description="**Who can call this:** Any member, where the community offers email.",
)
async def remove_email(actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NotificationChannelService().remove_email(await _fresh(actor))
    return await _channels(actor)


@router.put(
    '/email/enabled',
    response_model=ChannelsResponse,
    summary='Turn email notifications on or off',
    description=(
        "Fails with 400 when turning on without a confirmed address.\n\n"
        "**Who can call this:** Any member, where the community offers email."
    ),
)
async def set_email(body: EnabledUpdate, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NotificationChannelService().set_email_enabled(await _fresh(actor), body.enabled)
    return await _channels(actor)


@router.post(
    '/telegram',
    response_model=TelegramLinkResponse,
    summary='Start connecting Telegram',
    description=(
        "Returns a link to open in Telegram; pressing Start there connects it. Read "
        "`GET /me/channels` a few seconds later to see it connected.\n\n"
        "**Who can call this:** Any member, where the community offers Telegram."
    ),
)
async def start_telegram(actor: User = Depends(require_write_actor)) -> TelegramLinkResponse:
    url: Optional[str] = await NotificationChannelService().create_telegram_link(actor)
    if url is None:
        raise ValueError('Telegram is already connected.')
    return TelegramLinkResponse(url=url)


@router.delete(
    '/telegram',
    response_model=ChannelsResponse,
    summary='Disconnect Telegram',
    description="**Who can call this:** Any member, where the community offers Telegram.",
)
async def disconnect_telegram(actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NotificationChannelService().disconnect_telegram(await _fresh(actor))
    return await _channels(actor)


@router.put(
    '/telegram/enabled',
    response_model=ChannelsResponse,
    summary='Turn Telegram notifications on or off',
    description=(
        "Fails with 400 when turning on before Telegram is connected.\n\n"
        "**Who can call this:** Any member, where the community offers Telegram."
    ),
)
async def set_telegram(body: EnabledUpdate, actor: User = Depends(require_write_actor)) -> ChannelsResponse:
    await NotificationChannelService().set_telegram_enabled(await _fresh(actor), body.enabled)
    return await _channels(actor)


@router.post(
    '/test',
    response_model=ChannelTestResponse,
    status_code=status.HTTP_200_OK,
    summary='Send yourself a test notification',
    description=(
        "Sends a test to each channel you pick and says which went out. You can send 3 tests "
        "every 10 minutes; more fails with 400.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def send_test(body: ChannelTestRequest, actor: User = Depends(require_write_actor)) -> ChannelTestResponse:
    try:
        chosen = [Channel(c) for c in body.channels]
    except ValueError:
        raise ValueError('Pick from discord, devices, email and telegram.') from None
    results = await NotificationChannelService().send_test(await _fresh(actor), chosen)
    return ChannelTestResponse(results={c.value: ok for c, ok in results.items()})
