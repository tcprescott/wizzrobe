"""SpeedGaming sync endpoints — tenant event links, staging episodes, on-demand sync.

All routes delegate to :class:`SpeedGamingSyncService`, which gates every method
on ``AuthService.can_manage_sync`` (STAFF / super-admin / ``SYNC_ADMIN``). The
HTTP dep only distinguishes read (any token) from write (rejects read-only
tokens); authorization proper is enforced in-service.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.speedgaming import (
    SpeedGamingEpisodeResponse,
    SpeedGamingLinkCreateRequest,
    SpeedGamingLinkResponse,
    SpeedGamingLinkUpdateRequest,
    SyncResultResponse,
)
from application.errors import require_found
from application.services import SpeedGamingSyncService
from application.tenant_context import require_tenant_id
from models import SpeedGamingEventLink, User

router = APIRouter(prefix="/speedgaming", tags=["SpeedGaming"], route_class=ServiceErrorRoute, dependencies=[Depends(event_binding(link_id=SpeedGamingEventLink))])


async def _load_link_or_404(link_id: int) -> SpeedGamingEventLink:
    return require_found(
        await SpeedGamingEventLink.get_or_none(id=link_id, tenant_id=require_tenant_id()),
        "SpeedGaming event link",
    )


# --- Links ----------------------------------------------------------------

@router.get(
    "/links",
    response_model=List[SpeedGamingLinkResponse],
    summary="List SpeedGaming event links",
    description=(
        "Lists this community's SpeedGaming event links: the SpeedGaming events whose "
        "schedule is pulled into a tournament as matches, with how each link's last sync "
        "went.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def list_links(actor: User = Depends(require_api_actor)):
    return await SpeedGamingSyncService().list_links(actor)


@router.get(
    "/links/{link_id}/episodes",
    response_model=List[SpeedGamingEpisodeResponse],
    summary="List staged episodes for a link",
    description=(
        "Lists the episodes a link has pulled from SpeedGaming and where each one stands.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def list_episodes(link_id: int, actor: User = Depends(require_api_actor)):
    await _load_link_or_404(link_id)
    return await SpeedGamingSyncService().list_episodes(actor, link_id)


@router.post(
    "/links",
    response_model=SpeedGamingLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a SpeedGaming event link",
    description=(
        "Links a SpeedGaming event to a tournament. While the link is active, the background "
        "sync pulls its episodes on the link's interval. Fails with 400 when the event slug "
        "is empty or already linked to that tournament.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def create_link(body: SpeedGamingLinkCreateRequest, actor: User = Depends(require_write_actor)):
    return await SpeedGamingSyncService().create_link(actor, **body.model_dump())


@router.patch(
    "/links/{link_id}",
    response_model=SpeedGamingLinkResponse,
    summary="Update a SpeedGaming event link",
    description=(
        "Edits a link; omitted fields keep their value. Fails with 400 when the event slug is "
        "set to empty or is already linked to the same tournament.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def update_link(
    link_id: int,
    body: SpeedGamingLinkUpdateRequest,
    actor: User = Depends(require_write_actor),
):
    return await SpeedGamingSyncService().update_link(actor, link_id, **body.model_dump(exclude_unset=True))


@router.delete(
    "/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a SpeedGaming event link",
    description=(
        "Removes a link. Matches it already imported stay on the schedule.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def delete_link(link_id: int, actor: User = Depends(require_write_actor)):
    await SpeedGamingSyncService().delete_link(actor, link_id)


@router.post(
    "/links/{link_id}/sync",
    response_model=SyncResultResponse,
    summary="Run an on-demand sync for a link",
    description=(
        "Syncs one link now instead of waiting for its interval, and returns what changed. "
        "New or changed episodes become or update matches; matches that are finished, under "
        "way or have a race room are left alone; episodes gone from SpeedGaming are detached; "
        "imported matches more than four hours past their start are marked finished. A failed "
        "fetch shows up in `errors` and on the link rather than as an error response.\n\n"
        "**Who can call this:** Staff or Sync admin."
    ),
)
async def sync_link(link_id: int, actor: User = Depends(require_write_actor)):
    result = await SpeedGamingSyncService().sync_now(actor, link_id)
    return result.as_dict()
