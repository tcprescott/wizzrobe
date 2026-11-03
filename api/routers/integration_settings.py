"""The Integrations pane of Admin → Settings: Challonge, start.gg, webhooks, randomizer keys and AI writing help.

Each route calls the service that owns the setting, so checks and audit rows
match the website. Signing in to Challonge or start.gg happens on the website,
because their sign-in comes back there. Webhooks are created, changed and
deleted through ``/api/webhooks``; this router adds the list with delivery
health and the choices the add form offers.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    ServiceErrorRoute,
    require_api_actor,
    require_feature,
    require_staff,
    require_staff_write,
    require_write_actor,
)
from api.schemas.integration_settings import (
    AiProviderResponse,
    AiProviderUpdate,
    ChallongeAutoSyncUpdate,
    ChallongeCommunityItem,
    ChallongeOverviewResponse,
    IntegrationCardsResponse,
    RandomizerKeyRow,
    RandomizerKeyUpdate,
    StartggAccessItem,
    StartggOverviewResponse,
    WebhookEventPick,
    WebhookOptionsResponse,
    WebhookRow,
)
from application.events import EventType
from application.services import (
    AiProviderService,
    ChallongeService,
    EventService,
    IntegrationSettingsService,
    RandomizerCredentialService,
    StartggConnectionService,
    WebhookService,
)
from application.tenant_context import require_tenant_id
from application.utils.clients.ai_client import DEFAULT_MODELS
from models import FeatureFlag, User

router = APIRouter(prefix="/settings/integrations", tags=["Integration settings"], route_class=ServiceErrorRoute)

STAFF_ONLY = "**Who can call this:** Staff."
PRESETS = "**Who can call this:** Staff or Preset manager."
CHALLONGE = [Depends(require_feature(FeatureFlag.CHALLONGE))]
STARTGG = [Depends(require_feature(FeatureFlag.STARTGG))]
SURVEYS = [Depends(require_feature(FeatureFlag.SURVEYS))]


@router.get(
    "",
    response_model=IntegrationCardsResponse,
    summary="Which Integrations cards the caller sees",
    description="Which cards of the Integrations pane the caller may open, by role and the community's features.\n\n" + PRESETS,
)
async def cards(actor: User = Depends(require_api_actor)):
    c = await IntegrationSettingsService().cards(actor)
    return IntegrationCardsResponse(
        challonge=c.challonge, startgg=c.startgg, webhooks=c.webhooks, randomizer_keys=c.randomizer_keys, ai=c.ai,
        patreon=c.patreon,
    )


@router.get(
    "/challonge",
    response_model=ChallongeOverviewResponse,
    dependencies=CHALLONGE,
    summary="Get the Challonge sign-in",
    description=(
        "Who is signed in to Challonge for this community, how many tournaments are linked, and how often "
        "brackets sync on their own. 404 when the Challonge feature is off.\n\n" + STAFF_ONLY
    ),
)
async def challonge(actor: User = Depends(require_staff)):
    o = await IntegrationSettingsService().challonge(actor)
    return ChallongeOverviewResponse(
        configured=o.configured, connected=o.connected, username=o.username, can_write_signups=o.can_write_signups,
        linked_tournaments=o.linked_tournaments, request_usage=o.request_usage,
        auto_sync_minutes=o.auto_sync_minutes, auto_sync_choices=list(o.auto_sync_choices),
        auto_sync_brackets=o.auto_sync_brackets, auto_sync_requests_per_day=o.auto_sync_requests_per_day,
        communities=[ChallongeCommunityItem(slug=c.slug, tournaments=c.tournaments) for c in o.communities],
    )


@router.put(
    "/challonge/auto-sync",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=CHALLONGE,
    summary="Set how often Challonge brackets sync",
    description=(
        "Sets how often unfinished linked brackets sync on their own, and records it in the audit log. "
        "Fails with 400 for an interval not in `auto_sync_choices`.\n\n" + STAFF_ONLY
    ),
)
async def set_challonge_auto_sync(body: ChallongeAutoSyncUpdate, actor: User = Depends(require_staff_write)):
    await ChallongeService().set_auto_sync_minutes(body.minutes, actor)


@router.delete(
    "/challonge/communities/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=CHALLONGE,
    summary="Stop asking for a Challonge community",
    description=(
        "Stops asking Challonge for this community at the next sign-in. Fails with 400 while a linked "
        "tournament is on it.\n\n" + STAFF_ONLY
    ),
)
async def remove_challonge_community(slug: str, actor: User = Depends(require_staff_write)):
    await ChallongeService().remove_community(actor, slug)


@router.post(
    "/challonge/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=CHALLONGE,
    summary="Disconnect Challonge",
    description="Signs the community out of Challonge. Linked tournaments stop syncing until someone signs in again.\n\n" + STAFF_ONLY,
)
async def disconnect_challonge(actor: User = Depends(require_staff_write)):
    await ChallongeService().disconnect(actor)


@router.get(
    "/startgg",
    response_model=StartggOverviewResponse,
    dependencies=STARTGG,
    summary="Get the start.gg sign-in",
    description=(
        "Who is signed in to start.gg for this community, and for each linked tournament whether that "
        "account can report results there. Asks start.gg, so it can take a moment. 404 when the start.gg "
        "feature is off.\n\n" + STAFF_ONLY
    ),
)
async def startgg(actor: User = Depends(require_staff)):
    o = await IntegrationSettingsService().startgg(actor)
    return StartggOverviewResponse(
        configured=o.configured, platform_token=o.platform_token, connected=o.connected, username=o.username,
        expired=o.expired, renewed_at=o.renewed_at,
        linked=[
            StartggAccessItem(
                tournament_id=a.tournament_id, name=a.name, event_url=a.event_url,
                reports_results=a.reports_results, can_report=a.can_report,
            )
            for a in o.linked
        ],
    )


@router.post(
    "/startgg/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=STARTGG,
    summary="Disconnect start.gg",
    description=(
        "Signs the community out of start.gg. Results stop being reported there, and syncs fall back to the "
        "server's token if it has one.\n\n" + STAFF_ONLY
    ),
)
async def disconnect_startgg(actor: User = Depends(require_staff_write)):
    await StartggConnectionService().disconnect(actor)


@router.get(
    "/webhooks",
    response_model=List[WebhookRow],
    summary="List webhooks with delivery health",
    description="The community's webhooks, each with how many deliveries landed or failed in the last 24 hours.\n\n" + STAFF_ONLY,
)
async def webhooks(actor: User = Depends(require_staff)):
    service = WebhookService()
    health = await service.recent_health(actor)
    rows = []
    for w in await service.list_webhooks(actor):
        h = health.get(w.id) or {}
        rows.append(WebhookRow(
            id=w.id, name=w.name, url=w.url, event_types=list(w.event_types or []), event_ids=w.event_ids,
            is_active=w.is_active, delivered=h.get('ok', 0), failed=h.get('failed', 0), last_sent_at=h.get('last'),
        ))
    return rows


@router.get(
    "/webhook-options",
    response_model=WebhookOptionsResponse,
    summary="What a webhook can subscribe to",
    description="Every event name a webhook can get, and the community's events it can be limited to.\n\n" + STAFF_ONLY,
)
async def webhook_options(actor: User = Depends(require_staff)):
    events = await EventService.events_for_tenant(require_tenant_id())
    return WebhookOptionsResponse(
        event_types=[EventType.WILDCARD, *sorted(EventType.ALL)],
        events=[
            WebhookEventPick(
                id=e.id, name=e.name + (' (Draft)' if e.is_draft else ' (Archived)' if e.is_archived else ''),
            )
            for e in events
        ],
    )


@router.get(
    "/randomizer-keys",
    response_model=List[RandomizerKeyRow],
    summary="List randomizer keys",
    description="Each randomizer credential this community can set, and whether it is set. Values are never sent back.\n\n" + PRESETS,
)
async def randomizer_keys(actor: User = Depends(require_api_actor)):
    return [RandomizerKeyRow(**row) for row in await RandomizerCredentialService().list_status(actor)]


@router.put(
    "/randomizer-keys/{randomizer}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a randomizer key",
    description="Stores a randomizer credential, replacing any stored one, and records it in the audit log.\n\n" + PRESETS,
)
async def set_randomizer_key(
    randomizer: str, key: str, body: RandomizerKeyUpdate, actor: User = Depends(require_write_actor),
):
    await RandomizerCredentialService().set_credential(actor, randomizer, key, body.value)


@router.delete(
    "/randomizer-keys/{randomizer}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a randomizer key",
    description=(
        "Removes a stored randomizer credential. Seeds for that randomizer can't roll until one is set again.\n\n"
        + PRESETS
    ),
)
async def clear_randomizer_key(randomizer: str, key: str, actor: User = Depends(require_write_actor)):
    await RandomizerCredentialService().clear_credential(actor, randomizer, key)


@router.get(
    "/ai",
    response_model=AiProviderResponse,
    dependencies=SURVEYS,
    summary="Get the AI writing help provider",
    description="The provider and model behind Suggest questions in the survey builder. The key is never sent back.\n\n" + STAFF_ONLY,
)
async def ai(actor: User = Depends(require_staff)):
    s = await AiProviderService().status(actor)
    return AiProviderResponse(**s, default_models={p.value: m for p, m in DEFAULT_MODELS.items()})


@router.put(
    "/ai",
    response_model=AiProviderResponse,
    dependencies=SURVEYS,
    summary="Set the AI writing help provider",
    description=(
        "Saves the provider, model, address and daily limit. Leaving `api_key` out keeps the stored key, "
        "and there must be one. Recorded in the audit log without the key.\n\n" + STAFF_ONLY
    ),
)
async def set_ai(body: AiProviderUpdate, actor: User = Depends(require_staff_write)):
    service = AiProviderService()
    await service.save(
        actor, provider=body.provider.value, model=body.model, base_url=body.base_url,
        api_key=body.api_key, daily_limit=body.daily_limit,
    )
    return AiProviderResponse(**await service.status(actor), default_models={p.value: m for p, m in DEFAULT_MODELS.items()})


@router.delete(
    "/ai",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=SURVEYS,
    summary="Remove the AI writing help provider",
    description="Forgets the provider and its key. Suggest questions disappears until one is set again.\n\n" + STAFF_ONLY,
)
async def clear_ai(actor: User = Depends(require_staff_write)):
    await AiProviderService().clear(actor)
