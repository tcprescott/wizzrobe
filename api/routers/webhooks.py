"""Outbound webhook management endpoints (Staff only)."""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, require_staff, require_staff_write
from api.schemas.webhooks import (
    WebhookCreate,
    WebhookCreatedResponse,
    WebhookDeliveryResponse,
    WebhookResponse,
    WebhookSecretResponse,
    WebhookUpdate,
)
from application.services import WebhookService
from models import User

router = APIRouter(prefix="/webhooks", tags=["Webhooks"], route_class=ServiceErrorRoute)


@router.get(
    "",
    response_model=List[WebhookResponse],
    summary="List webhooks (Staff only)",
    description=(
        "Returns every webhook this community has set up, sorted by name. Signing secrets are never "
        "included.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_webhooks(actor: User = Depends(require_staff)):
    return await WebhookService().list_webhooks(actor)


@router.post(
    "",
    response_model=WebhookCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook (Staff only) — secret returned once",
    description=(
        "Creates a webhook that POSTs the chosen events to the URL, and records it in the audit log. The response "
        "carries the signing secret; it isn't shown again except by rotating it.\n\n"
        "Fails with 400 when the name is blank, the URL isn't https, has no host or points at a private "
        "or internal address, the event list is empty or names an unknown event, or `event_ids` is empty or "
        "names an archived event (404 for an event id that isn't this community's).\n\n"
        "**Who can call this:** Staff."
    ),
)
async def create_webhook(body: WebhookCreate, actor: User = Depends(require_staff_write)):
    return await WebhookService().create_webhook(
        actor,
        name=body.name,
        url=body.url,
        event_types=body.event_types,
        is_active=body.is_active,
        event_ids=body.event_ids,
    )


@router.get(
    "/{webhook_id}",
    response_model=WebhookResponse,
    summary="Get a webhook (Staff only)",
    description=(
        "Returns one webhook. The signing secret is never included.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def get_webhook(webhook_id: int, actor: User = Depends(require_staff)):
    return await WebhookService().get_webhook(actor, webhook_id)


@router.put(
    "/{webhook_id}",
    response_model=WebhookResponse,
    summary="Update a webhook (Staff only)",
    description=(
        "Changes only the fields you send and records the change in the audit log. Set `is_active` to false to "
        "pause deliveries without deleting the webhook.\n\n"
        "Fails with 400 when the name is blank, the URL isn't https, has no host or points at a private "
        "or internal address, the event list is empty or names an unknown event, or `event_ids` is empty or "
        "names an archived event (404 for an event id that isn't this community's).\n\n"
        "**Who can call this:** Staff."
    ),
)
async def update_webhook(
    webhook_id: int, body: WebhookUpdate, actor: User = Depends(require_staff_write)
):
    return await WebhookService().update_webhook(
        actor,
        webhook_id,
        name=body.name,
        url=body.url,
        event_types=body.event_types,
        is_active=body.is_active,
        **({'event_ids': body.event_ids} if 'event_ids' in body.model_fields_set else {}),
    )


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook (Staff only)",
    description=(
        "Deletes the webhook and its delivery history; no further events are sent to it. The deletion is "
        "recorded in the audit log.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def delete_webhook(webhook_id: int, actor: User = Depends(require_staff_write)):
    await WebhookService().delete_webhook(actor, webhook_id)


@router.post(
    "/{webhook_id}/regenerate-secret",
    response_model=WebhookSecretResponse,
    summary="Rotate a webhook's signing secret (Staff only) — returned once",
    description=(
        "Generates a new signing secret, returns it once and records the rotation in the audit log. Deliveries "
        "from now on are signed with the new secret, so update your receiver.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def regenerate_secret(webhook_id: int, actor: User = Depends(require_staff_write)):
    secret = await WebhookService().regenerate_secret(actor, webhook_id)
    return WebhookSecretResponse(secret=secret)


@router.get(
    "/{webhook_id}/deliveries",
    response_model=List[WebhookDeliveryResponse],
    summary="List a webhook's recent delivery attempts (Staff only)",
    description=(
        "Returns the 50 most recent deliveries to this webhook, newest first, with the response status, "
        "attempt count and error for each. A delivery is tried up to 3 times before it's marked failed.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_deliveries(webhook_id: int, actor: User = Depends(require_staff)):
    return await WebhookService().list_deliveries(actor, webhook_id)
