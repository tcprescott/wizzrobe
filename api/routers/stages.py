"""Stage endpoints."""

from typing import List

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor
from api.schemas.stages import StageResponse
from application.errors import require_found
from application.services import StageService
from models import Stage

router = APIRouter(
    prefix="/stages",
    tags=["Stages"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(stage_id=Stage)), Depends(require_api_actor)],
)


@router.get(
    "",
    response_model=List[StageResponse],
    summary="List stages",
    description=(
        "Returns this community's stages, the streams and setups matches are played on.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_stages(
    active_only: bool = Query(False, description="Return only active stages"),
):
    return await StageService().get_all_stages(active_only=active_only)


@router.get(
    "/{stage_id}",
    response_model=StageResponse,
    summary="Get a stage",
    description=(
        "Returns one of this community's stages.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def get_stage(stage_id: int):
    return require_found(
        await StageService().get_stage_by_id(stage_id), "Stage"
    )
