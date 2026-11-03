"""Your developer tools: OAuth apps you register and personal webhooks.

Creating, rotating or revealing a secret is personal-token or Wizzrobe-app only
(see ``api/scopes.py``): no third-party app may mint credentials.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, require_api_actor, require_feature, require_write_actor
from api.schemas.my_profile import (
    ConnectedCountResponse,
    EnabledUpdate,
    OAuthAppCreate,
    OAuthAppCreated,
    OAuthAppResponse,
    OAuthAppUpdate,
    PersonalWebhookCreate,
    PersonalWebhookCreated,
    PersonalWebhookResponse,
    PersonalWebhooksResponse,
    PersonalWebhookUpdate,
    SecretResponse,
    WebhookAreaResponse,
    WebhookEventResponse,
)
from application.services import OAuthAppService, PersonalWebhookService
from application.services.personal_webhook_service import EVENTS, MAX_PER_USER, is_discord_url, masked_url
from models import FeatureFlag, User

apps_router = APIRouter(
    prefix='/me/apps', tags=['My developer tools'], route_class=ServiceErrorRoute,
    dependencies=[Depends(require_feature(FeatureFlag.THIRD_PARTY_APPS))],
)
webhooks_router = APIRouter(prefix='/me/webhooks', tags=['My developer tools'], route_class=ServiceErrorRoute)


def _app(app) -> OAuthAppResponse:
    return OAuthAppResponse(
        id=app.id, name=app.name, description=app.description or '', homepage_url=app.homepage_url,
        client_type=getattr(app.client_type, 'value', str(app.client_type)), client_id=app.client_id,
        secret_prefix=app.secret_prefix, redirect_uris=list(app.redirect_uris or []),
        allowed_scopes=list(app.allowed_scopes or []),
        disabled_reason=app.disabled_reason if app.disabled_at else None, created_at=app.created_at,
    )


@apps_router.get(
    '',
    response_model=List[OAuthAppResponse],
    summary='Apps you registered',
    description="**Who can call this:** Any member, where the community allows outside apps.",
)
async def list_apps(actor: User = Depends(require_api_actor)) -> List[OAuthAppResponse]:
    return [_app(a) for a in await OAuthAppService().list_my_apps(actor)]


@apps_router.post(
    '',
    response_model=OAuthAppCreated,
    status_code=status.HTTP_201_CREATED,
    summary='Register an app',
    description=(
        "A confidential app gets a client secret, shown in this answer only. Fails with 400 "
        "for a bad redirect URI or scope, or past the limit on apps.\n\n"
        "**Who can call this:** Any member, where the community allows outside apps."
    ),
)
async def create_app(body: OAuthAppCreate, actor: User = Depends(require_write_actor)) -> OAuthAppCreated:
    app, secret = await OAuthAppService().create_app(
        actor, name=body.name, client_type=body.client_type, redirect_uris=body.redirect_uris,
        allowed_scopes=body.allowed_scopes, description=body.description, homepage_url=body.homepage_url,
    )
    return OAuthAppCreated(app=_app(app), client_secret=secret)


@apps_router.patch(
    '/{app_id}',
    response_model=OAuthAppResponse,
    summary='Edit one of your apps',
    description=(
        "Changes only what you send; an empty `homepage_url` clears it. Narrowing the scopes "
        "applies to new grants, not ones people already gave.\n\n"
        "**Who can call this:** The app's owner."
    ),
)
async def update_app(app_id: int, body: OAuthAppUpdate, actor: User = Depends(require_write_actor)) -> OAuthAppResponse:
    return _app(await OAuthAppService().update_app(
        actor, app_id, name=body.name, description=body.description, homepage_url=body.homepage_url,
        redirect_uris=body.redirect_uris, allowed_scopes=body.allowed_scopes,
    ))


@apps_router.get(
    '/{app_id}/connections',
    response_model=ConnectedCountResponse,
    summary='How many people use one of your apps',
    description="A count only, never who. **Who can call this:** The app's owner.",
)
async def app_connections(app_id: int, actor: User = Depends(require_api_actor)) -> ConnectedCountResponse:
    return ConnectedCountResponse(count=await OAuthAppService().connected_user_count(actor, app_id))


@apps_router.post(
    '/{app_id}/secret',
    response_model=SecretResponse,
    summary="Replace an app's client secret",
    description=(
        "The old secret keeps working for a short grace period. Fails with 400 for a public app.\n\n"
        "**Who can call this:** The app's owner."
    ),
)
async def rotate_secret(app_id: int, actor: User = Depends(require_write_actor)) -> SecretResponse:
    return SecretResponse(secret=await OAuthAppService().rotate_secret(actor, app_id))


@apps_router.delete(
    '/{app_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Delete one of your apps',
    description="Ends every grant people gave it. **Who can call this:** The app's owner.",
)
async def delete_app(app_id: int, actor: User = Depends(require_write_actor)) -> None:
    await OAuthAppService().delete_app(actor, app_id)


def _hook(w) -> PersonalWebhookResponse:
    return PersonalWebhookResponse(
        id=w.id, name=w.name, url_masked=masked_url(w.url), discord=is_discord_url(w.url),
        event_types=list(w.event_types or []), only_mine=w.only_mine, is_active=w.is_active,
        paused_reason=w.paused_reason, last_delivery_at=w.last_delivery_at, last_status=w.last_status,
        last_error=w.last_error, delivered_count=w.delivered_count, failed_count=w.failed_count,
    )


@webhooks_router.get(
    '',
    response_model=PersonalWebhooksResponse,
    summary='Your personal webhooks',
    description=(
        "Your webhooks, plus the areas and events this community lets you subscribe to.\n\n"
        "**Who can call this:** Any member, where the community allows personal webhooks."
    ),
)
async def list_hooks(actor: User = Depends(require_api_actor)) -> PersonalWebhooksResponse:
    service = PersonalWebhookService()
    areas = await service.available_areas()
    keys = {a.key for a in areas}
    return PersonalWebhooksResponse(
        areas=[WebhookAreaResponse.model_validate(a, from_attributes=True) for a in areas],
        events=[
            WebhookEventResponse(event_type=e.event_type, area=e.area, label=e.label, mine_only=e.mine_only)
            for e in EVENTS if e.area in keys
        ],
        webhooks=[_hook(w) for w in await service.list_mine(actor)],
        max_webhooks=MAX_PER_USER,
    )


@webhooks_router.post(
    '',
    response_model=PersonalWebhookCreated,
    status_code=status.HTTP_201_CREATED,
    summary='Add a personal webhook',
    description=(
        "A JSON destination gets a signing secret, shown in this answer only; a Discord channel "
        "webhook isn't signed. Fails with 400 for a URL that isn't public https or past the limit.\n\n"
        "**Who can call this:** Any member, where the community allows personal webhooks."
    ),
)
async def create_hook(body: PersonalWebhookCreate, actor: User = Depends(require_write_actor)) -> PersonalWebhookCreated:
    hook, secret = await PersonalWebhookService().create(
        actor, name=body.name, url=body.url, event_types=body.event_types, only_mine=body.only_mine,
    )
    return PersonalWebhookCreated(webhook=_hook(hook), secret=secret)


@webhooks_router.put(
    '/{webhook_id}',
    response_model=PersonalWebhookResponse,
    summary='Edit a personal webhook',
    description="Leave `url` out to keep it. **Who can call this:** The webhook's owner.",
)
async def update_hook(
    webhook_id: int, body: PersonalWebhookUpdate, actor: User = Depends(require_write_actor),
) -> PersonalWebhookResponse:
    return _hook(await PersonalWebhookService().update(
        actor, webhook_id, name=body.name, url=body.url, event_types=body.event_types, only_mine=body.only_mine,
    ))


@webhooks_router.put(
    '/{webhook_id}/active',
    response_model=PersonalWebhookResponse,
    summary='Pause or resume a personal webhook',
    description="Resuming clears a pause from failed deliveries. **Who can call this:** The webhook's owner.",
)
async def set_hook_active(
    webhook_id: int, body: EnabledUpdate, actor: User = Depends(require_write_actor),
) -> PersonalWebhookResponse:
    return _hook(await PersonalWebhookService().set_active(actor, webhook_id, body.enabled))


@webhooks_router.post(
    '/{webhook_id}/secret',
    response_model=SecretResponse,
    summary="Replace a webhook's signing secret",
    description="Fails with 400 for a Discord webhook. **Who can call this:** The webhook's owner.",
)
async def regenerate_hook_secret(webhook_id: int, actor: User = Depends(require_write_actor)) -> SecretResponse:
    return SecretResponse(secret=await PersonalWebhookService().regenerate_secret(actor, webhook_id))


@webhooks_router.post(
    '/{webhook_id}/test',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Send a test delivery',
    description=(
        "Posts a sample now and waits for the answer. Fails with 400 with the reason when the "
        "destination refuses it, or after 3 tests in 10 minutes.\n\n"
        "**Who can call this:** The webhook's owner."
    ),
)
async def test_hook(webhook_id: int, actor: User = Depends(require_write_actor)) -> None:
    await PersonalWebhookService().send_test(actor, webhook_id)


@webhooks_router.delete(
    '/{webhook_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Delete a personal webhook',
    description="**Who can call this:** The webhook's owner.",
)
async def delete_hook(webhook_id: int, actor: User = Depends(require_write_actor)) -> None:
    await PersonalWebhookService().delete(actor, webhook_id)
