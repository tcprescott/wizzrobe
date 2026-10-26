"""Schemas for staff-managed outbound webhook endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

_EVENT_TYPES = (
    "Event names this webhook receives, such as `match.created`, or `*` for every event."
)
_EVENT_IDS = (
    "Ids of the community's events (see `GET /api/events`) whose updates it sends, or null for every "
    "event's, including events added later. Community-wide updates (members, OAuth grants) are sent "
    "either way."
)


class WebhookResponse(BaseModel):
    """A webhook as returned by list/GET — never includes the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="ID of the webhook.")
    name: str = Field(..., description="Label shown in the admin list.")
    url: str = Field(..., description="Endpoint that receives each delivery as a POST.")
    event_types: List[str] = Field(..., description=_EVENT_TYPES)
    event_ids: Optional[List[int]] = Field(None, description=_EVENT_IDS)
    is_active: bool = Field(..., description="Whether the webhook is receiving deliveries. Paused webhooks get none.")
    created_at: datetime = Field(..., description="When the webhook was created (UTC).")
    updated_at: datetime = Field(..., description="When the webhook was last changed (UTC).")


class WebhookCreatedResponse(WebhookResponse):
    """Returned once on create; carries the signing secret."""

    secret: str = Field(
        ...,
        description=(
            "Key for verifying that a delivery came from this app. It's only shown in this response "
            "and when the secret is rotated, so store it now."
        ),
    )


class WebhookSecretResponse(BaseModel):
    secret: str = Field(
        ...,
        description=(
            "The new key for verifying deliveries. Deliveries are signed with it from now on, and it "
            "isn't shown again."
        ),
    )


class WebhookCreate(BaseModel):
    name: str = Field(..., description="Label shown in the admin list. Can't be blank.")
    url: str = Field(..., description="HTTPS endpoint that receives each delivery as a POST.")
    event_types: List[str] = Field(
        ..., description="Event names to deliver, such as `match.created`, or `[\"*\"]` for every event. At least one."
    )
    is_active: bool = Field(True, description="Whether the webhook starts receiving deliveries straight away.")
    event_ids: Optional[List[int]] = Field(None, description=_EVENT_IDS + " Leave it out for every event.")


class WebhookUpdate(BaseModel):
    name: Optional[str] = Field(None, description="New label. Can't be blank. Omit to keep the current one.")
    url: Optional[str] = Field(None, description="New HTTPS endpoint. Omit to keep the current one.")
    event_types: Optional[List[str]] = Field(
        None, description="Replacement list of event names, or `[\"*\"]` for every event. Omit to keep the current list."
    )
    is_active: Optional[bool] = Field(None, description="Pause (`false`) or resume (`true`) deliveries. Omit to leave as is.")
    event_ids: Optional[List[int]] = Field(
        None, description=_EVENT_IDS + " Send null for every event; omit to keep the current filter.",
    )


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="ID of the delivery record.")
    webhook_id: int = Field(..., description="ID of the webhook it was sent to.")
    event_type: str = Field(..., description="Name of the event that was delivered.")
    response_status: Optional[int] = Field(
        None, description="HTTP status the endpoint returned on the last attempt; null when no response came back."
    )
    attempt_count: int = Field(..., description="POST attempts made, up to 3.")
    success: bool = Field(..., description="Whether an attempt got a 2xx response.")
    error: Optional[str] = Field(
        None, description="Why the last attempt failed, such as `HTTP 500` or a connection error; null on success."
    )
    created_at: datetime = Field(..., description="When the delivery finished, successful or not (UTC).")
    delivered_at: Optional[datetime] = Field(None, description="When the endpoint accepted it (UTC); null when it never did.")
