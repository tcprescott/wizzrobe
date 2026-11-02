"""Schemas for the app's Me screen: the settings list and each section's own reads and writes."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProfileSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., description=(
        "Which section: `about`, `notifications`, `availability`, `accounts`, `staff-alerts`, "
        "`equipment`, `accommodation`, `emergency-contact`, `feedback`, `webhooks`, `apps` or `tokens`."
    ))
    title: str
    summary: str = Field(..., description="One line on where the section stands, ready to print.")


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    display_name: Optional[str] = Field(None, description="The name you set here, if any.")
    preferred_name: str = Field(..., description="The name everyone sees: your display name, else your Discord name.")
    pronouns: Optional[str] = None
    discord_username: Optional[str] = None
    avatar_url: Optional[str] = None
    timezone: Optional[str] = Field(None, description="Your saved IANA zone, or null to follow your device.")
    hour_cycle: Optional[str] = Field(None, description="`12`, `24`, or null to follow your device.")
    community_zone: str = Field(..., description="The community's clock.")
    zone_pinned: bool = Field(..., description="The community shows every time on its own clock, so your zone only matters elsewhere.")
    matcherino_username: Optional[str] = Field(None, description="Where prize money goes. Only when the community runs payouts.")
    payouts: bool = Field(..., description="Whether the community runs payouts, so the Matcherino handle applies.")
    dm_notifications: bool
    sections: List[ProfileSectionResponse] = Field(..., description="The sections this community has, in display order.")


class ClockUpdate(BaseModel):
    timezone: Optional[str] = Field(None, description="An IANA zone, or null to follow your device. Omit to leave it.")
    hour_cycle: Optional[str] = Field(None, description="`12`, `24`, or null to follow your device. Omit to leave it.")


class ConnectedAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., description="`challonge`, `startgg`, `twitch` or `racetime`.")
    title: str
    description: str
    linked: bool
    username: Optional[str] = None
    linked_at: Optional[datetime] = None
    link_path: str = Field(..., description="The website path that starts linking. Open it in a browser signed in to Wizzrobe.")
    sign_in_first_url: Optional[str] = Field(None, description="Sign in to the site here first, or linking fails.")
    unlink_warning: Optional[str] = Field(None, description="What unlinking changes, to confirm before it happens.")


class FollowableTournamentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    playing: bool = Field(..., description="You're signed up to play in it.")
    level: str = Field(..., description="How much you hear about its matches: `none`, `all` and so on, as `PUT /notifications/preferences` takes.")


class StaffAlertTournamentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    on: bool


class StaffAlertsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    duty_position: Optional[str] = Field(None, description="The alert-receiving position you have shifts on.")
    on_duty_now: bool
    next_duty_start: Optional[datetime] = None
    tournaments: List[StaffAlertTournamentResponse] = Field(..., description="Tournaments you run, each with whether its alerts reach you.")


class StaffAlertUpdate(BaseModel):
    on: bool


class AccommodationResponse(BaseModel):
    requested: bool = Field(..., description="You have an open request (not withdrawn).")
    details: Optional[str] = None
    status: Optional[str] = Field(None, description="`new`, `acknowledged`, `arranged` or `withdrawn`; null if you never asked.")
    changed_since_arranged: bool = Field(False, description="You changed the details after staff arranged it; they're checking.")
    max_length: int


class AccommodationUpdate(BaseModel):
    requested: bool = Field(..., description="false withdraws the request and clears the details.")
    details: Optional[str] = None


class EmergencyContactResponse(BaseModel):
    name: Optional[str] = None
    relationship: Optional[str] = None
    phone: Optional[str] = None
    last_viewed_at: Optional[datetime] = Field(default=None, description="When staff last opened it.")
    last_viewed_by: Optional[str] = Field(default=None, description="Who opened it last.")


class EmergencyContactUpdate(BaseModel):
    name: str
    relationship: Optional[str] = None
    phone: str


class ChannelsResponse(BaseModel):
    discord: bool = Field(..., description="You signed in with Discord, so DMs can reach you.")
    dm_notifications: bool
    push_configured: bool = Field(..., description="This server can send device notifications.")
    push_public_key: Optional[str] = Field(None, description="The VAPID key a browser subscribes with.")
    devices: List['PushDeviceResponse']
    channels_enabled: bool = Field(..., description="The community offers email and Telegram.")
    email_available: bool
    email: Optional[str] = None
    email_notifications: bool
    telegram_available: bool
    telegram_username: Optional[str] = None
    telegram_connected: bool
    telegram_notifications: bool


class PushDeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_agent: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    endpoint_host: str = Field(..., description="The push service's host, to tell devices apart.")


class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: Optional[str] = None


class EmailStartRequest(BaseModel):
    email: str


class EmailConfirmRequest(BaseModel):
    code: str


class EnabledUpdate(BaseModel):
    enabled: bool


class TelegramLinkResponse(BaseModel):
    url: str = Field(..., description="Open it in Telegram and press Start; the connection shows up a few seconds later.")


class ChannelTestRequest(BaseModel):
    channels: List[str] = Field(..., description="Any of `discord`, `devices`, `email`, `telegram`.")


class ChannelTestResponse(BaseModel):
    results: dict[str, bool] = Field(..., description="Each channel tried, and whether it went out.")


class OAuthAppResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    homepage_url: Optional[str] = None
    client_type: str
    client_id: str
    secret_prefix: Optional[str] = None
    redirect_uris: List[str]
    allowed_scopes: List[str]
    disabled_reason: Optional[str] = None
    created_at: datetime


class OAuthAppCreate(BaseModel):
    name: str
    client_type: str = Field(..., description="`public` (can't keep a secret) or `confidential`.")
    redirect_uris: List[str]
    allowed_scopes: List[str]
    description: Optional[str] = None
    homepage_url: Optional[str] = None


class OAuthAppUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    homepage_url: Optional[str] = None
    redirect_uris: Optional[List[str]] = None
    allowed_scopes: Optional[List[str]] = None


class OAuthAppCreated(BaseModel):
    app: OAuthAppResponse
    client_secret: Optional[str] = Field(None, description="Shown this once. Null for a public app.")


class SecretResponse(BaseModel):
    secret: str = Field(..., description="Shown this once.")


class ConnectedCountResponse(BaseModel):
    count: int


class WebhookAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    hint: str


class WebhookEventResponse(BaseModel):
    event_type: str
    area: str
    label: str
    mine_only: bool


class PersonalWebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url_masked: str = Field(..., description="Where it posts, with any secret part hidden. The full URL is never sent back.")
    discord: bool = Field(..., description="Posts to a Discord channel webhook, so it isn't signed.")
    event_types: List[str]
    only_mine: bool
    is_active: bool
    paused_reason: Optional[str] = None
    last_delivery_at: Optional[datetime] = None
    last_status: Optional[int] = None
    last_error: Optional[str] = None
    delivered_count: int
    failed_count: int


class PersonalWebhooksResponse(BaseModel):
    areas: List[WebhookAreaResponse]
    events: List[WebhookEventResponse]
    webhooks: List[PersonalWebhookResponse]
    max_webhooks: int


class PersonalWebhookCreate(BaseModel):
    name: str
    url: str
    event_types: List[str]
    only_mine: bool = True


class PersonalWebhookUpdate(BaseModel):
    name: str
    url: Optional[str] = Field(None, description="Omit to keep the stored URL.")
    event_types: List[str]
    only_mine: bool


class PersonalWebhookCreated(BaseModel):
    webhook: PersonalWebhookResponse
    secret: Optional[str] = Field(None, description="The signing secret, shown this once. Null for a Discord webhook.")


ChannelsResponse.model_rebuild()
