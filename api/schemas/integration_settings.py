"""Schemas for the Integrations pane of Admin → Settings (api/routers/integration_settings.py)."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from models import AiProvider


class IntegrationCardsResponse(BaseModel):
    """Which cards of the Integrations pane the caller sees."""

    challonge: bool = Field(..., description="Challonge sign-in (Staff, Challonge feature on).")
    startgg: bool = Field(..., description="start.gg sign-in (Staff, start.gg feature on).")
    webhooks: bool = Field(..., description="Outbound webhooks (Staff).")
    randomizer_keys: bool = Field(..., description="Randomizer API keys (Staff or Preset manager).")
    ai: bool = Field(..., description="AI writing help for surveys (Staff, Surveys feature on).")


class ChallongeCommunityItem(BaseModel):
    slug: str = Field(..., description="The Challonge community's subdomain.")
    tournaments: int = Field(..., description="Linked tournaments on it. A community with any can't be removed.")


class ChallongeOverviewResponse(BaseModel):
    """The community's Challonge sign-in, as the Challonge card shows it."""

    configured: bool = Field(..., description="Whether this server has Challonge sign-in set up at all.")
    connected: bool = Field(..., description="Whether a Challonge account is signed in for this community.")
    username: Optional[str] = Field(None, description="The signed-in Challonge account. Null when none is.")
    can_write_signups: bool = Field(
        ..., description="Whether the sign-in may add players who sign up. False means it needs reconnecting."
    )
    linked_tournaments: int = Field(..., description="Tournaments in any event linked to a Challonge bracket.")
    request_usage: int = Field(..., description="Challonge API requests made this month.")
    auto_sync_minutes: int = Field(..., description="How often unfinished brackets sync on their own, in minutes. 0 is off.")
    auto_sync_choices: List[int] = Field(..., description="The intervals `auto_sync_minutes` may take.")
    auto_sync_brackets: int = Field(..., description="Unfinished linked brackets an automatic sync would cover.")
    auto_sync_requests_per_day: int = Field(..., description="Roughly how many API requests a day the current interval costs.")
    communities: List[ChallongeCommunityItem] = Field(..., description="Challonge communities asked for at each sign-in.")


class ChallongeAutoSyncUpdate(BaseModel):
    minutes: int = Field(..., description="One of `auto_sync_choices`. 0 turns automatic sync off.")


class StartggAccessItem(BaseModel):
    tournament_id: int = Field(..., description="Tournament id.")
    name: str = Field(..., description="Tournament name.")
    event_url: str = Field(..., description="The linked start.gg event's page.")
    reports_results: bool = Field(..., description="Whether confirmed results are reported to start.gg.")
    can_report: Optional[bool] = Field(
        None, description="Whether the signed-in account may report there. Null when start.gg couldn't be asked."
    )


class StartggOverviewResponse(BaseModel):
    """The community's start.gg sign-in, as the start.gg card shows it."""

    configured: bool = Field(..., description="Whether this server has start.gg sign-in set up at all.")
    platform_token: bool = Field(..., description="Whether the server has its own start.gg token for reading brackets.")
    connected: bool = Field(..., description="Whether a start.gg account is signed in for this community.")
    username: Optional[str] = Field(None, description="The signed-in start.gg account.")
    expired: bool = Field(..., description="True when the sign-in lapsed and couldn't be renewed.")
    renewed_at: Optional[datetime] = Field(None, description="When the sign-in was last renewed (UTC).")
    linked: List[StartggAccessItem] = Field(..., description="Linked tournaments, and whether the account can report on each.")


class WebhookRow(BaseModel):
    """One webhook with its last day's delivery health."""

    id: int = Field(..., description="ID of the webhook. Change it through `/api/webhooks/{webhook_id}`.")
    name: str = Field(..., description="Label.")
    url: str = Field(..., description="Endpoint that receives each delivery.")
    event_types: List[str] = Field(..., description="Events it gets. `*` means every event.")
    event_ids: Optional[List[int]] = Field(None, description="Community events it sends from. Null means all of them.")
    is_active: bool = Field(..., description="Whether it's receiving deliveries.")
    delivered: int = Field(..., description="Deliveries that landed in the last 24 hours.")
    failed: int = Field(..., description="Deliveries that failed in the last 24 hours.")
    last_sent_at: Optional[datetime] = Field(None, description="When the last delivery in that window finished (UTC).")


class WebhookEventPick(BaseModel):
    id: int = Field(..., description="Event id.")
    name: str = Field(..., description="Event name, with (Draft) or (Archived) when it is one.")


class WebhookOptionsResponse(BaseModel):
    event_types: List[str] = Field(..., description="Every event a webhook can get, by name. `*` is every event.")
    events: List[WebhookEventPick] = Field(..., description="The community's events, for limiting a webhook to some.")


class RandomizerKeyRow(BaseModel):
    randomizer: str = Field(..., description="Randomizer slug.")
    key: str = Field(..., description="Which credential of that randomizer.")
    label: str = Field(..., description="The credential's name.")
    help_text: str = Field(..., description="Where to get it and what it unlocks.")
    configured: bool = Field(..., description="Whether one is stored. The value itself is never sent back.")
    updated_at: Optional[datetime] = Field(None, description="When it was last set (UTC).")


class RandomizerKeyUpdate(BaseModel):
    value: str = Field(..., min_length=1, description="The new key. Replaces the stored one.")


class AiProviderResponse(BaseModel):
    configured: bool = Field(..., description="Whether a provider is set up.")
    provider: AiProvider = Field(..., description="Which API the key talks to.")
    model: str = Field(..., description="The model asked for.")
    base_url: Optional[str] = Field(None, description="The provider's address, for an OpenAI-compatible service.")
    daily_limit: int = Field(..., description="Requests a day, all staff together.")
    key_readable: bool = Field(..., description="False when a stored key can't be read any more and must be entered again.")
    updated_at: Optional[datetime] = Field(None, description="When it was last saved (UTC).")
    default_models: dict[str, str] = Field(..., description="The model each provider starts with.")


class AiProviderUpdate(BaseModel):
    provider: AiProvider = Field(..., description="Which API the key talks to.")
    model: str = Field(..., description="The model to ask for. Can't be blank.")
    base_url: Optional[str] = Field(None, description="Needed for an OpenAI-compatible service.")
    api_key: Optional[str] = Field(None, description="The key. Leave out to keep the stored one.")
    daily_limit: int = Field(..., description="Requests a day, 1 to 200.")
