"""Patreon: the community's campaign card, your own link, the sign-in callback and the webhook.

Gated by the ``PATREON`` feature flag at the router and again in each service.
The callback and webhook are separate routers with no token: the callback is
tied to whoever started the sign-in by its single-use state, and the webhook
proves itself with Patreon's signature.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from api.dependencies import (
    ServiceErrorRoute,
    require_api_actor,
    require_staff,
    require_staff_write,
    require_write_actor,
)
from api.routers._patreon_done import done_page
from api.schemas.patreon import (
    MyPatreonResponse,
    MyPatreonUpdate,
    PatreonCampaignResponse,
    PatreonTierLabelsUpdate,
    SignInUrlResponse,
    SyncQueuedResponse,
)
from application.errors import NotFoundError
from application.services import (
    PatreonCampaignService,
    PatreonLinkService,
    PatreonOAuthService,
    PatreonSignatureError,
)
from models import User

router = APIRouter(prefix="/settings/integrations/patreon", tags=["Patreon"], route_class=ServiceErrorRoute)
me_router = APIRouter(prefix="/me/patreon", tags=["Patreon"], route_class=ServiceErrorRoute)
callback_router = APIRouter(prefix="/patreon", tags=["Patreon"], include_in_schema=False)
webhook_router = APIRouter(prefix="/patreon-webhooks", tags=["Patreon"], include_in_schema=False)

STAFF = "**Who can call this:** Staff."
MEMBER = "**Who can call this:** any member."


# ── The community's campaign (staff) ───────────────────────────────────

@router.get("", response_model=PatreonCampaignResponse, summary="The Patreon card",
            description=("Whether this community's Patreon campaign is connected, who connected it, the last sync, "
                         f"how many linked members are patrons, and the tier badge labels. Never a token or a pledge amount.\n\n{STAFF}"))
async def campaign(actor: User = Depends(require_staff)):
    return PatreonCampaignResponse.build(await PatreonCampaignService().overview(actor))


@router.post("/connect", response_model=SignInUrlResponse, summary="Start connecting the campaign",
             description=("Returns Patreon's sign-in page for the campaign's creator. After they approve, Patreon sends "
                          "the browser back to Settings → Integrations with `?patreon=connected` (or `failed`, "
                          f"`cancelled`, `expired`). Reconnecting replaces the old sign-in.\n\n{STAFF}"))
async def connect(actor: User = Depends(require_staff_write)):
    return SignInUrlResponse(authorize_url=await PatreonCampaignService().start_connect(actor))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect the campaign",
               description=f"Removes Wizzrobe's webhook from the campaign, forgets the sign-in and takes every patron badge here away.\n\n{STAFF}")
async def disconnect(actor: User = Depends(require_staff_write)):
    await PatreonCampaignService().disconnect(actor)


@router.put("/tiers", response_model=PatreonCampaignResponse, summary="Set the tier badge labels",
            description=("Each tier's badge label (empty for the default, `Patron`) and their order: someone in "
                         f"several tiers shows the first one's label.\n\n{STAFF}"))
async def set_tiers(body: PatreonTierLabelsUpdate, actor: User = Depends(require_staff_write)):
    labels = [(t.tier_id, t.badge_label) for t in body.tiers]
    return PatreonCampaignResponse.build(await PatreonCampaignService().set_tier_labels(actor, labels))


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED, response_model=SyncQueuedResponse, summary="Sync now",
             description=f"Queues a full pass over the campaign's members; it runs within five minutes.\n\n{STAFF}")
async def sync(actor: User = Depends(require_staff_write)):
    await PatreonCampaignService().request_sync(actor)
    return SyncQueuedResponse(queued=True)


# ── Your own link ──────────────────────────────────────────────────────

@me_router.get("", response_model=MyPatreonResponse, summary="Your Patreon link",
               description=f"Whether you've linked Patreon, your badge here and in your other communities, and whether you hide it.\n\n{MEMBER}")
async def mine(actor: User = Depends(require_api_actor)):
    return MyPatreonResponse.build(await PatreonLinkService().my_status(actor))


@me_router.post("/link", response_model=SignInUrlResponse, summary="Start linking Patreon",
                description=("Returns Patreon's sign-in page. After you approve, Patreon sends the browser back to "
                             "Me → Accounts with `?patreon=linked` (or `taken`, `failed`, `cancelled`, `expired`). "
                             f"Wizzrobe keeps only your Patreon id and name, not the sign-in.\n\n{MEMBER}"))
async def link(actor: User = Depends(require_write_actor)):
    return SignInUrlResponse(authorize_url=await PatreonLinkService().start_link(actor))


@me_router.patch("", response_model=MyPatreonResponse, summary="Show or hide your badge",
                 description=f"Hiding your badge keeps it from other people. Anything a patron gets still works.\n\n{MEMBER}")
async def update_mine(body: MyPatreonUpdate, actor: User = Depends(require_write_actor)):
    return MyPatreonResponse.build(await PatreonLinkService().set_badge_hidden(actor, body.badge_hidden))


@me_router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Unlink Patreon",
                  description=f"Forgets your Patreon account and takes your patron badge away in every community.\n\n{MEMBER}")
async def unlink(actor: User = Depends(require_write_actor)):
    await PatreonLinkService().unlink(actor)


# ── Sign-in callback and webhook (no token) ────────────────────────────

@callback_router.get("/callback", response_class=HTMLResponse)
async def patreon_callback(state: Optional[str] = None, code: Optional[str] = None, error: Optional[str] = None):
    result = await PatreonOAuthService().finish(state, code, error)
    return HTMLResponse(done_page(result.outcome, creator=result.creator), headers={'Cache-Control': 'no-store'})


@webhook_router.post("/{token}", status_code=status.HTTP_200_OK)
async def patreon_webhook(token: str, request: Request):
    body = await request.body()
    try:
        outcome = await PatreonCampaignService().handle_webhook(token, body, dict(request.headers))
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown webhook') from exc
    except PatreonSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Bad signature') from exc
    return {'ok': True, 'outcome': outcome}
