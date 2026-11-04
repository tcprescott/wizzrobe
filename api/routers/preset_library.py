"""Public preset library: browse, read, roll and copy presets from every space.

Reads and rolls are open to any member; an unlisted preset is visible only to
the staff of the community that owns it. Copying into a community needs Staff
or Preset manager there.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.preset_library import (
    LibraryCopyRequest,
    LibraryPresetDetailResponse,
    LibraryPresetResponse,
    LibraryRollResponse,
)
from api.schemas.presets import PresetResponse
from application.services import LibraryPreset, PresetLibraryService
from models import User

router = APIRouter(prefix="/preset-library", tags=["Preset library"], route_class=ServiceErrorRoute)


def _row(view: LibraryPreset) -> dict:
    p = view.preset
    original = p.copied_from
    return {
        'id': p.id,
        'reference': view.reference,
        'name': p.name,
        'randomizer': p.randomizer,
        'randomizer_name': view.randomizer_name,
        'rollable': view.rollable,
        'can_manage': view.can_manage,
        'description': p.description,
        'space': view.space_slug,
        'space_kind': view.space_kind.value,
        'space_name': view.space_name,
        'mine': view.mine,
        'listed': p.listed,
        'roll_count': p.roll_count,
        'updated_at': p.updated_at,
        'copied_from': f"{original.space.slug}/{original.name}" if original is not None and original.space else None,
        'copied_from_id': original.id if original is not None else None,
        'changed_since_copy': view.changed_since_copy,
    }


@router.get(
    "",
    response_model=List[LibraryPresetResponse],
    summary="List library presets",
    description=(
        "Every preset shown in the library, from every community and player, most rolled "
        "first. Your own community's hidden presets are included for Staff and Preset "
        "managers.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_library(
    randomizer: Optional[str] = Query(None, description="Return only this randomizer's presets."),
    actor: User = Depends(require_api_actor),
):
    return [_row(v) for v in await PresetLibraryService().list_presets(actor, randomizer=randomizer)]


@router.get(
    "/{preset_id}",
    response_model=LibraryPresetDetailResponse,
    summary="Get a library preset",
    description=(
        "One library preset with its settings.\n\n"
        "**Who can call this:** Any member, for a preset shown in the library; Staff or "
        "Preset manager for their own community's hidden ones."
    ),
)
async def get_library_preset(preset_id: int, actor: User = Depends(require_api_actor)):
    view = await PresetLibraryService().get_preset(actor, preset_id)
    return {**_row(view), 'settings': view.preset.settings}


@router.post(
    "/{preset_id}/roll",
    response_model=LibraryRollResponse,
    summary="Roll a seed from a library preset",
    description=(
        "Rolls one seed with this community's randomizer keys. Limited to 10 rolls every "
        "10 minutes per person, except for Staff and Preset managers.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def roll_library_preset(preset_id: int, actor: User = Depends(require_write_actor)):
    roll = await PresetLibraryService().roll(actor, preset_id)
    return LibraryRollResponse(**roll.__dict__)


@router.post(
    "/{preset_id}/copy",
    response_model=PresetResponse,
    summary="Copy a library preset into this community",
    description=(
        "Copies the preset into this community's Seed Presets, hidden from the library, "
        "remembering the original so it can be updated later.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def copy_library_preset(
    preset_id: int, body: LibraryCopyRequest, actor: User = Depends(require_write_actor),
):
    return await PresetLibraryService().copy_to_community(actor, preset_id, name=body.name)


@router.post(
    "/{preset_id}/update-copy",
    response_model=PresetResponse,
    summary="Update a copy from its original",
    description=(
        "Replaces a copied preset's settings and note with its original's current ones.\n\n"
        "**Who can call this:** Staff or Preset manager."
    ),
)
async def update_library_copy(preset_id: int, actor: User = Depends(require_write_actor)):
    return await PresetLibraryService().update_copy(actor, preset_id)
