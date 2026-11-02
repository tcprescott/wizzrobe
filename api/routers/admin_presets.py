"""The app's Admin → Seed Presets screen: editor context, checking a pasted value,
test rolls and importing a randomizer's published presets.

Listing, creating, editing and deleting presets stay on ``/presets``.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.admin_presets import (
    PasteCheckRequest,
    PasteCheckResponse,
    PresetEditorResponse,
    RemoteImportRequest,
    RemotePresetResponse,
    TestRollResponse,
)
from api.schemas.presets import PresetResponse
from application.services import AdminPresetsService
from models import User

router = APIRouter(
    prefix='/admin/presets', tags=['Admin: seed presets'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding())],
)

_WHO = "**Who can call this:** Staff or Preset manager."


@router.get(
    '/editor', response_model=PresetEditorResponse, summary='What the preset editor needs',
    description=(
        "Every randomizer with its display name, whether it can be used here, how its settings "
        "are entered and whether it publishes presets, plus which tournaments roll from each preset.\n\n" + _WHO
    ),
)
async def editor(actor: User = Depends(require_api_actor)):
    return await AdminPresetsService().editor_context(actor)


@router.post(
    '/check-paste', response_model=PasteCheckResponse, summary='Check a pasted permalink or flag string',
    description=(
        "Reads a Wind Waker permalink, an FFR flags link or a flag string the way a roll will, "
        "without saving anything. Fails with 400 and the reason when it can't be read.\n\n" + _WHO
    ),
)
async def check_paste(body: PasteCheckRequest, actor: User = Depends(require_write_actor)):
    return await AdminPresetsService().check_paste(actor, body.randomizer, body.value)


@router.post(
    '/{preset_id}/test-roll', response_model=TestRollResponse, summary='Roll one test seed',
    description=(
        "Rolls a seed from the preset to check the randomizer works; it's audited but never "
        "attached to a match. Some randomizers take minutes.\n\n" + _WHO
    ),
)
async def test_roll(preset_id: int, actor: User = Depends(require_write_actor)):
    return await AdminPresetsService().test_roll(actor, preset_id)


@router.get(
    '/remote', response_model=List[RemotePresetResponse], summary="A randomizer's published presets",
    description="The catalogue its own API publishes, to pick from before importing. Needs its API key.\n\n" + _WHO,
)
async def remote(
    randomizer: str = Query(...), branch: Optional[str] = Query(None),
    actor: User = Depends(require_api_actor),
):
    return await AdminPresetsService().remote_presets(actor, randomizer, branch)


@router.post(
    '/import-remote', response_model=List[PresetResponse], status_code=status.HTTP_201_CREATED,
    summary="Import a randomizer's published presets",
    description="Saves the named presets as this community's own, fetched fresh from the randomizer.\n\n" + _WHO,
)
async def import_remote(body: RemoteImportRequest, actor: User = Depends(require_write_actor)):
    return await AdminPresetsService().import_remote(actor, body.randomizer, body.names, body.branch)
