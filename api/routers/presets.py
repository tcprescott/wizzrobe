"""Preset endpoints — tenant-authored seed-rolling presets.

Reads are open to any authenticated token (the management gate lives in the
service), but the two picker reads blank each preset's settings for anyone who
can't manage presets: a tournament editor needs the name, and a preset can be
meant to stay secret until race day. Writes reject read-only tokens at the HTTP layer and are re-gated by
``PresetService`` (``can_manage_presets``: STAFF / SUPER_ADMIN / PRESET_MANAGER).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.presets import (
    PresetCreateRequest,
    PresetResponse,
    PresetUpdateRequest,
)
from application.services import AuthService, PresetService
from models import Preset, User

router = APIRouter(prefix="/presets", tags=["Presets"], route_class=ServiceErrorRoute)


@router.get(
    "",
    response_model=List[PresetResponse],
    summary="List presets",
    description=(
        "Lists this community's seed presets. With `randomizer` set, returns only that "
        "randomizer's presets.\n\n"
        "**Who can call this:** Any member when `randomizer` is set, with `settings` left "
        "empty unless you're Staff or a Preset manager; otherwise Staff or Preset manager."
    ),
)
async def list_presets(
    randomizer: Optional[str] = Query(None, description="Return only this randomizer's presets. Open to any member."),
    actor: User = Depends(require_api_actor),
):
    service = PresetService()
    if randomizer is not None:
        return await _for_viewer(actor, await service.list_by_randomizer(randomizer))
    return await service.list_presets(actor)


@router.get(
    "/selectable",
    response_model=List[PresetResponse],
    summary="List selectable presets",
    description=(
        "Lists every preset in this community, for choosing one on a tournament. The same "
        "rows as `GET /presets`, with `settings` left empty unless you're Staff or a Preset "
        "manager.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_selectable(actor: User = Depends(require_api_actor)):
    return await _for_viewer(actor, await PresetService().list_selectable())


async def _for_viewer(actor: User, presets: List[Preset]) -> List[PresetResponse]:
    rows = [PresetResponse.model_validate(p) for p in presets]
    if not await AuthService.can_manage_presets(actor):
        for row in rows:
            row.settings = {}
    return rows


@router.get(
    "/{preset_id}",
    response_model=PresetResponse,
    summary="Get a preset",
    description=(
        "Returns one preset with its settings.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def get_preset(preset_id: int, actor: User = Depends(require_api_actor)):
    return await PresetService().get_preset(actor, preset_id)


@router.post(
    "",
    response_model=PresetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a preset",
    description=(
        "Creates a preset. Final Fantasy, Zelda 1 and Wind Waker settings are checked and "
        "tidied before saving. Fails with 400 when the name is empty or over 255 characters, "
        "the randomizer is unknown, the settings don't suit the randomizer, or the randomizer "
        "already has a preset with that name.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def create_preset(body: PresetCreateRequest, actor: User = Depends(require_write_actor)):
    return await PresetService().create_preset(
        actor,
        name=body.name,
        randomizer=body.randomizer,
        settings=body.settings,
        description=body.description,
    )


@router.patch(
    "/{preset_id}",
    response_model=PresetResponse,
    summary="Update a preset",
    description=(
        "Edits a preset; omitted fields keep their value. New settings, or a new randomizer, "
        "are checked as they would be on create. Fails with 400 for the same reasons as "
        "creating one.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def update_preset(preset_id: int, body: PresetUpdateRequest, actor: User = Depends(require_write_actor)):
    return await PresetService().update_preset(
        actor, preset_id, **body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a preset",
    description=(
        "Deletes a preset. Tournaments that rolled from it are left with no preset, and "
        "randomizers that need one won't roll for them until another is attached.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def delete_preset(preset_id: int, actor: User = Depends(require_write_actor)):
    await PresetService().delete_preset(actor, preset_id)


@router.post(
    "/import-builtins",
    response_model=List[PresetResponse],
    summary="Import built-in presets",
    description=(
        "Adds the presets that ship with Wizzrobe, skipping any this community already has "
        "with the same randomizer and name, so it's safe to run again. Returns only the "
        "presets it created.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def import_builtins(actor: User = Depends(require_write_actor)):
    return await PresetService().import_builtins(actor)
