"""Async qualifier live races endpoints.

Author, inspect, open, and cancel synchronous racetime qualifier races. All
authorization is enforced in :class:`AsyncQualifierLiveRaceService` (via
``can_admin_qualifier``); handlers only translate HTTP <-> service calls and do a
tenant-scoped load-or-404 for by-id routes so a missing/cross-tenant race yields
404 (not the service's 400).
"""

from typing import List

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.async_qualifier_live_races import (
    LiveRaceCreateRequest,
    LiveRaceResponse,
    ManualRecordRequest,
    PermalinkAssignRequest,
    RunResponse,
)
from application.errors import require_found
from application.services import AsyncQualifierLiveRaceService
from application.services.async_qualifier.async_qualifier_live_race_service import ManualResult
from application.tenant_context import require_tenant_id
from models import AsyncQualifierLiveRace, User

router = APIRouter(
    prefix="/async-qualifiers/live-races",
    tags=["Async qualifier live races"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(live_race_id=AsyncQualifierLiveRace))],
)


async def _load_live_race_or_404(live_race_id: int) -> AsyncQualifierLiveRace:
    return require_found(
        await AsyncQualifierLiveRace.get_or_none(id=live_race_id, tenant_id=require_tenant_id()),
        "Live race",
    )


@router.get(
    "", response_model=List[LiveRaceResponse], summary="List live races for a qualifier",
    description=(
        "Every live race scheduled in the qualifier's pools, with its status and racetime "
        'room.\n\n**Who can call this:** Staff, Qualifier admin, or admins of this '
        'qualifier.'
    ),
)
async def list_live_races(
    qualifier_id: int = Query(..., description="Qualifier to list live races for"),
    actor: User = Depends(require_api_actor),
):
    return await AsyncQualifierLiveRaceService().list_live_races(actor, qualifier_id)


@router.get(
    "/{live_race_id}", response_model=LiveRaceResponse, summary="Get a live race",
    description=(
        "One live race, including any racetime handles its last result capture couldn't "
        'match to a person.\n\n**Who can call this:** Staff, Qualifier admin, or admins '
        "of the race's qualifier."
    ),
)
async def get_live_race(live_race_id: int, actor: User = Depends(require_api_actor)):
    await _load_live_race_or_404(live_race_id)
    return await AsyncQualifierLiveRaceService().get_live_race(actor, live_race_id)


@router.get(
    "/{live_race_id}/runs",
    response_model=List[RunResponse],
    summary="List captured runs for a live race",
    description=(
        'The runs captured from a live race, in the order they were recorded. Live-race '
        "runs skip review and are approved as soon as they're recorded.\n\n**Who can call "
        "this:** Staff, Qualifier admin, or admins of the race's qualifier."
    ),
)
async def list_runs(live_race_id: int, actor: User = Depends(require_api_actor)):
    await _load_live_race_or_404(live_race_id)
    return await AsyncQualifierLiveRaceService().list_runs(actor, live_race_id)


@router.post(
    "",
    response_model=LiveRaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a live race",
    description=(
        "Schedule a live race on one of the qualifier's pools. Everyone in the race plays "
        'the same permalink in one racetime room. You can leave the permalink out and set '
        "it later, but results can't be recorded until one is set. Fails with 400 when "
        'the title is blank or the permalink belongs to another pool.\n\n**Who can call '
        "this:** Staff, Qualifier admin, or admins of the pool's qualifier."
    ),
)
async def create_live_race(payload: LiveRaceCreateRequest, actor: User = Depends(require_write_actor)):
    return await AsyncQualifierLiveRaceService().create_live_race(
        actor,
        payload.pool_id,
        match_title=payload.match_title,
        permalink_id=payload.permalink_id,
        episode_id=payload.episode_id,
    )


@router.post(
    "/{live_race_id}/permalink",
    response_model=LiveRaceResponse,
    summary="Set the permalink a live race is run on",
    description=(
        'Set the permalink the race is run on, which is the seed its results are scored '
        'against. Setting the permalink it already has changes nothing. Fails with 400 '
        'when the permalink belongs to another pool, or the race already has recorded '
        'results.\n\n**Who can call this:** Staff, Qualifier admin, or admins of the '
        "race's qualifier."
    ),
)
async def assign_permalink(
    live_race_id: int, payload: PermalinkAssignRequest, actor: User = Depends(require_write_actor),
):
    """Choose the pool permalink whose par the race's results are scored against.

    Refused once the race has runs: they were captured against the old permalink,
    and par is per permalink, so moving the race would score them against a seed
    nobody in it played.
    """
    await _load_live_race_or_404(live_race_id)
    return await AsyncQualifierLiveRaceService().assign_permalink(
        actor, live_race_id, payload.permalink_id,
    )


@router.post(
    "/{live_race_id}/open-room",
    response_model=LiveRaceResponse,
    summary="Open a racetime room for the live race",
    description=(
        "Open a racetime.gg room for the race, using the community's racetime bot, with "
        "the qualifier's name as the goal. Results are captured from the room "
        'automatically when the race finishes. A race that already has a room is returned '
        'unchanged. Fails with 400 when no racetime bot is authorized for this '
        'community.\n\n**Who can call this:** Staff, Qualifier admin, or admins of the '
        "race's qualifier."
    ),
)
async def open_room(live_race_id: int, actor: User = Depends(require_write_actor)):
    await _load_live_race_or_404(live_race_id)
    return await AsyncQualifierLiveRaceService().open_room(actor, live_race_id)


@router.post(
    "/{live_race_id}/record",
    response_model=List[RunResponse],
    summary="Record a live race's results by hand",
    description=(
        "Record a race's results by hand, for when racetime's finish never arrived. Each "
        'result is approved and scored just like a racetime capture, and the race moves '
        'to `finished`. A racer who has already used all their runs in the pool is '
        'recorded as voided. Fails with 400 when the list is empty, a racer appears '
        "twice, a status isn't `finished`, `forfeit` or `disqualified`, a finisher has no "
        'time or one over a week, or the race has no permalink.\n\n**Who can call this:** '
        "Staff, Qualifier admin, or admins of the race's qualifier."
    ),
)
async def record_manually(
    live_race_id: int, payload: ManualRecordRequest, actor: User = Depends(require_write_actor),
):
    """Capture results a human asserts, when racetime's own event never arrived.

    Everything downstream is the racetime path's: the permalink requirement, the
    per-pool cap (a racer over it is recorded voided), the par recompute, and the
    race moving to FINISHED. Audited as a manual record so the trail says who typed
    it; subscribers get the ordinary recorded event with ``manual: true``.
    """
    await _load_live_race_or_404(live_race_id)
    return await AsyncQualifierLiveRaceService().record_manual_finish(
        actor, live_race_id,
        [ManualResult(user_id=r.user_id, status=r.status, elapsed_seconds=r.elapsed_seconds)
         for r in payload.results],
    )


@router.delete(
    "/{live_race_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a live race",
    description=(
        'Delete a scheduled live race. Runs already recorded from it are kept. This '
        "doesn't close its racetime room if one was opened.\n\n**Who can call this:** "
        "Staff, Qualifier admin, or admins of the race's qualifier."
    ),
)
async def cancel_live_race(live_race_id: int, actor: User = Depends(require_write_actor)):
    await _load_live_race_or_404(live_race_id)
    await AsyncQualifierLiveRaceService().cancel_live_race(actor, live_race_id)
