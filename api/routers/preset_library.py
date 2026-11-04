"""Public preset library: browse, read, roll and copy presets from every space.

Reads and rolls are open to any member; an unlisted preset is visible only to
the staff of the community that owns it. Copying into a community needs Staff
or Preset manager there. Any member keeps presets of their own in a personal
space, which they and their co-editors change.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, require_api_actor, require_write_actor
from api.schemas.preset_library import (
    LibraryCopyRequest,
    LibraryCopyToMineRequest,
    LibraryPresetDetailResponse,
    LibraryPresetResponse,
    LibraryRandomizerResponse,
    LibraryRollResponse,
    PersonalPresetCreateRequest,
    PersonalPresetUpdateRequest,
    PresetSpaceEditorRequest,
    PresetSpaceMember,
    PresetSpaceResponse,
    PresetSpaceUpdateRequest,
    SahasrahBotImportRequest,
    SahasrahBotImportResult,
    SahasrahBotPresetResponse,
)
from api.schemas.presets import PresetResponse
from application.services import LibraryPreset, PersonalPresetService, PresetLibraryService, SpaceView
from application.services.personal_preset_service import MAX_EDITORS_PER_SPACE, MAX_PRESETS_PER_SPACE
from models import User

_SPACE_WHO = "**Who can call this:** The space's owner or a co-editor."
_OWNER_WHO = "**Who can call this:** The space's owner."

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
        'can_edit': view.can_edit,
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


def _space(view: SpaceView) -> dict:
    owner = view.space.owner
    return {
        'id': view.space.id,
        'slug': view.space.slug,
        'owner': {'id': view.space.owner_id, 'name': view.owner_name if owner is not None else ''},
        'is_owner': view.is_owner,
        'preset_count': view.preset_count,
        'max_presets': MAX_PRESETS_PER_SPACE,
        'editors': [{'id': u.id, 'name': u.preferred_name} for u in view.editors],
        'max_editors': MAX_EDITORS_PER_SPACE,
    }


async def _detail(actor: User, preset_id: int) -> dict:
    view = await PresetLibraryService().get_preset(actor, preset_id)
    return {**_row(view), 'settings': view.preset.settings}


@router.get(
    "/randomizers",
    response_model=List[LibraryRandomizerResponse],
    summary="Randomizers a preset can be written for",
    description=(
        "Every randomizer, whether this community can roll it, and whether its settings are a "
        "pasted value rather than JSON.\n\n**Who can call this:** Any member."
    ),
)
async def list_randomizers(actor: User = Depends(require_api_actor)):
    return [r.__dict__ for r in await PresetLibraryService().randomizers(actor)]


@router.get(
    "/spaces",
    response_model=List[PresetSpaceResponse],
    summary="Your preset spaces",
    description=(
        "The personal space you own, if you've saved a preset, and any you co-edit, yours first.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_my_spaces(actor: User = Depends(require_api_actor)):
    return [_space(v) for v in await PersonalPresetService().my_spaces(actor)]


@router.get(
    "/members",
    response_model=List[PresetSpaceMember],
    summary="Find members to add as co-editors",
    description=(
        "Up to ten members of this community whose name contains the search, not counting you. "
        "Needs two letters at least.\n\n**Who can call this:** Any member."
    ),
)
async def find_members(q: str = Query('', description="Part of a name."), actor: User = Depends(require_api_actor)):
    return [{'id': u.id, 'name': u.preferred_name} for u in await PersonalPresetService().find_members(actor, q)]


@router.patch(
    "/spaces/{space_id}",
    response_model=PresetSpaceResponse,
    summary="Rename your preset space",
    description=(
        "Changes the part before the slash in every preset in the space. Copies others made keep "
        "working.\n\n" + _OWNER_WHO
    ),
)
async def rename_space(space_id: int, body: PresetSpaceUpdateRequest, actor: User = Depends(require_write_actor)):
    return _space(await PersonalPresetService().rename_space(actor, space_id, body.slug))


@router.post(
    "/spaces/{space_id}/editors",
    response_model=PresetSpaceResponse,
    summary="Add a co-editor",
    description=(
        "Lets a member of this community add, edit and delete the space's presets. Ten at most.\n\n" + _OWNER_WHO
    ),
)
async def add_space_editor(space_id: int, body: PresetSpaceEditorRequest, actor: User = Depends(require_write_actor)):
    return _space(await PersonalPresetService().add_editor(actor, space_id, body.user_id))


@router.delete(
    "/spaces/{space_id}/editors/{user_id}",
    response_model=PresetSpaceResponse,
    summary="Remove a co-editor",
    description=(
        "The owner removes a co-editor, or a co-editor leaves.\n\n" + _SPACE_WHO
    ),
)
async def remove_space_editor(space_id: int, user_id: int, actor: User = Depends(require_write_actor)):
    return _space(await PersonalPresetService().remove_editor(actor, space_id, user_id))


@router.post(
    "/personal",
    response_model=LibraryPresetDetailResponse,
    status_code=201,
    summary="Save a preset in a personal space",
    description=(
        "Saves a preset in your space (made the first time, named after you) or one you co-edit. "
        "It shows in the library straight away. A space holds 50 presets.\n\n"
        "**Who can call this:** Any member, in their own space or one they co-edit."
    ),
)
async def create_personal_preset(body: PersonalPresetCreateRequest, actor: User = Depends(require_write_actor)):
    preset = await PersonalPresetService().create_preset(
        actor, name=body.name, randomizer=body.randomizer, settings=body.settings,
        description=body.description, space_id=body.space_id,
    )
    return await _detail(actor, preset.id)


@router.patch(
    "/personal/{preset_id}",
    response_model=LibraryPresetDetailResponse,
    summary="Edit a personal preset",
    description="Changes a preset's name, note or settings. Its game can't change.\n\n" + _SPACE_WHO,
)
async def update_personal_preset(
    preset_id: int, body: PersonalPresetUpdateRequest, actor: User = Depends(require_write_actor),
):
    await PersonalPresetService().update_preset(
        actor, preset_id, name=body.name, settings=body.settings, description=body.description,
    )
    return await _detail(actor, preset_id)


@router.delete(
    "/personal/{preset_id}",
    status_code=204,
    summary="Delete a personal preset",
    description="Copies others made keep their settings.\n\n" + _SPACE_WHO,
)
async def delete_personal_preset(preset_id: int, actor: User = Depends(require_write_actor)):
    await PersonalPresetService().delete_preset(actor, preset_id)


@router.get(
    "/sahasrahbot/{namespace}",
    response_model=List[SahasrahBotPresetResponse],
    summary="List a SahasrahBot namespace's presets",
    description=(
        "Asks SahasrahBot for the ALTTPR, Super Metroid and SMZ3 presets in one namespace. "
        "Empty when the namespace has none. Fails with 400 when SahasrahBot can't be reached.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_sahasrahbot_presets(namespace: str, actor: User = Depends(require_api_actor)):
    return [p.__dict__ for p in await PersonalPresetService().sahasrahbot_presets(actor, namespace)]


@router.post(
    "/sahasrahbot/{namespace}/import",
    response_model=List[SahasrahBotImportResult],
    summary="Import presets from SahasrahBot",
    description=(
        "Copies the picked presets into your space (made the first time) or one you co-edit, "
        "named in Wizzrobe's style. Door Randomizer presets and ones that place items themselves "
        "can't be rolled here, so they come back with a problem instead; the rest still import.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def import_sahasrahbot_presets(
    namespace: str, body: SahasrahBotImportRequest, actor: User = Depends(require_write_actor),
):
    results = await PersonalPresetService().import_from_sahasrahbot(
        actor, namespace, [(p.randomizer, p.name) for p in body.presets], space_id=body.space_id,
    )
    return [r.__dict__ for r in results]


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
    return await _detail(actor, preset_id)


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
    "/{preset_id}/copy-to-mine",
    response_model=LibraryPresetDetailResponse,
    status_code=201,
    summary="Copy a library preset into a personal space",
    description=(
        "Copies the preset into your space (made the first time) or one you co-edit, remembering "
        "the original so it can be updated later.\n\n**Who can call this:** Any member."
    ),
)
async def copy_to_mine(preset_id: int, body: LibraryCopyToMineRequest, actor: User = Depends(require_write_actor)):
    copy = await PersonalPresetService().copy_to_space(actor, preset_id, name=body.name, space_id=body.space_id)
    return await _detail(actor, copy.id)


@router.post(
    "/{preset_id}/update-copy",
    response_model=PresetResponse,
    summary="Update a copy from its original",
    description=(
        "Replaces a copied preset's settings and note with its original's current ones.\n\n"
        "**Who can call this:** Staff or Preset manager for a community copy; the space's owner "
        "or a co-editor for a personal one."
    ),
)
async def update_library_copy(preset_id: int, actor: User = Depends(require_write_actor)):
    return await PresetLibraryService().update_copy(actor, preset_id)
