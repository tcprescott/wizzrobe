"""REST for the app's Admin → Stream Layouts screen.

Every stage with what its overlay shows now, every layout, and the box editor's
reads and writes, all over `StreamLayoutService`.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.admin_stream_layouts import (
    BackgroundRequest,
    CreateLayoutRequest,
    EditorOptionsResponse,
    LayoutDetailResponse,
    ObsLinkResponse,
    ObsScenesResponse,
    OverlayMatchResponse,
    OverlayPreviewResponse,
    PinMatchRequest,
    PreviewValuesRequest,
    PreviewValuesResponse,
    RenameLayoutRequest,
    SaveBoxesRequest,
    SetArtResponse,
    SetStageLayoutRequest,
    StreamLayoutBoardResponse,
)
from application.services import AdminStreamLayoutsService
from application.services.stream_layout_service import MAX_ART_BYTES
from models import Stage, StreamLayout, User

router = APIRouter(
    prefix='/admin/stream-layouts',
    tags=['Admin: stream layouts'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(stage_id=Stage, layout_id=StreamLayout))],
)

_WHO = "**Who can call this:** Staff and Broadcast Techs."


@router.get('/board', response_model=StreamLayoutBoardResponse, summary='The Stream Layouts board',
            description="Every stage with what its overlay shows now and its OBS link, and every layout.\n\n" + _WHO)
async def board(actor: User = Depends(require_api_actor)):
    return await AdminStreamLayoutsService().board(actor)


@router.get('/editor-options', response_model=EditorOptionsResponse, summary="The box editor's choices",
            description="What a box can show, the fonts, starters and background swatches.\n\n" + _WHO)
async def editor_options(actor: User = Depends(require_api_actor)):
    return await AdminStreamLayoutsService().editor_options(actor)


@router.get('/preview-matches', response_model=List[OverlayMatchResponse], summary='Matches to preview a layout with',
            description="Every stage's matches around now.\n\n" + _WHO)
async def preview_matches(actor: User = Depends(require_api_actor)):
    return await AdminStreamLayoutsService().preview_matches(actor)


@router.get('/obs-scenes', response_model=ObsScenesResponse, summary='An OBS scene collection',
            description="For one stage (`stage_id`) or every stage with a layout.\n\n" + _WHO)
async def obs_scenes(stage_id: Optional[int] = None, actor: User = Depends(require_api_actor)):
    filename, content = await AdminStreamLayoutsService().obs_scenes(actor, stage_id)
    return ObsScenesResponse(filename=filename, content=content)


@router.get('/stages/{stage_id}/preview', response_model=OverlayPreviewResponse, summary="What a stage's overlay draws now",
            description="The layout's art, boxes and each box's current text.\n\n" + _WHO)
async def stage_preview(stage_id: int, actor: User = Depends(require_api_actor)):
    return await AdminStreamLayoutsService().stage_preview(actor, stage_id)


@router.get('/stages/{stage_id}/matches', response_model=List[OverlayMatchResponse], summary='Matches to pin on a stage',
            description="The stage's unfinished matches around now.\n\n" + _WHO)
async def stage_matches(stage_id: int, actor: User = Depends(require_api_actor)):
    return await AdminStreamLayoutsService().stage_matches(actor, stage_id)


@router.put('/stages/{stage_id}/pin', status_code=204, summary='Pin a match to a stage',
            description="The overlay shows this match until it's set back to automatic.\n\n" + _WHO)
async def pin(stage_id: int, body: PinMatchRequest, actor: User = Depends(require_write_actor)):
    await AdminStreamLayoutsService().pin(actor, stage_id, body.match_id)


@router.delete('/stages/{stage_id}/pin', status_code=204, summary='Back to automatic',
               description="The overlay follows the stage's schedule again.\n\n" + _WHO)
async def unpin(stage_id: int, actor: User = Depends(require_write_actor)):
    await AdminStreamLayoutsService().unpin(actor, stage_id)


@router.put('/stages/{stage_id}/layout', status_code=204, summary="Set a stage's layout",
            description="OBS picks it up within two seconds.\n\n" + _WHO)
async def set_stage_layout(stage_id: int, body: SetStageLayoutRequest, actor: User = Depends(require_write_actor)):
    await AdminStreamLayoutsService().set_stage_layout(actor, stage_id, body.layout_id)


@router.post('/stages/{stage_id}/renew-link', response_model=ObsLinkResponse, summary="Replace a stage's OBS link",
             description="The old link stops working at once.\n\n" + _WHO)
async def renew_link(stage_id: int, actor: User = Depends(require_write_actor)):
    return ObsLinkResponse(obs_url=await AdminStreamLayoutsService().renew_link(actor, stage_id))


@router.post('/layouts', response_model=LayoutDetailResponse, status_code=201, summary='Create a layout',
             description="Blank or from a starter, on a plain background if one is given.\n\n" + _WHO)
async def create_layout(body: CreateLayoutRequest, actor: User = Depends(require_write_actor)):
    return await AdminStreamLayoutsService().create_layout(actor, body.name, body.starter, body.background)


@router.get('/layouts/{layout_id}', response_model=LayoutDetailResponse, summary='One layout', description=_WHO)
async def layout(layout_id: int, actor: User = Depends(require_api_actor)):
    return await AdminStreamLayoutsService().layout(actor, layout_id)


@router.patch('/layouts/{layout_id}', response_model=LayoutDetailResponse, summary='Rename a layout', description=_WHO)
async def rename_layout(layout_id: int, body: RenameLayoutRequest, actor: User = Depends(require_write_actor)):
    return await AdminStreamLayoutsService().rename_layout(actor, layout_id, body.name)


@router.post('/layouts/{layout_id}/duplicate', response_model=LayoutDetailResponse, status_code=201,
             summary='Duplicate a layout', description=_WHO)
async def duplicate_layout(layout_id: int, actor: User = Depends(require_write_actor)):
    return await AdminStreamLayoutsService().duplicate_layout(actor, layout_id)


@router.delete('/layouts/{layout_id}', status_code=204, summary='Delete a layout',
               description="Stages using it show a blank overlay until they get another.\n\n" + _WHO)
async def delete_layout(layout_id: int, actor: User = Depends(require_write_actor)):
    await AdminStreamLayoutsService().delete_layout(actor, layout_id)


@router.put('/layouts/{layout_id}/boxes', response_model=LayoutDetailResponse, summary="Save a layout's boxes",
            description="Replaces every box. Refused when someone else saved since `expected_updated_at`.\n\n" + _WHO)
async def save_boxes(layout_id: int, body: SaveBoxesRequest, actor: User = Depends(require_write_actor)):
    return await AdminStreamLayoutsService().save_boxes(actor, layout_id, body.boxes, body.expected_updated_at)


@router.post('/layouts/{layout_id}/preview', response_model=PreviewValuesResponse, summary="Each box's text in the editor",
             description="Sample text, or a real match's, for the saved or the editor's unsaved boxes.\n\n" + _WHO)
async def preview_values(layout_id: int, body: PreviewValuesRequest, actor: User = Depends(require_write_actor)):
    values = await AdminStreamLayoutsService().preview_values(actor, layout_id, body.match_id, body.boxes)
    return PreviewValuesResponse(values=values)


@router.put(
    '/layouts/{layout_id}/art', response_model=SetArtResponse, summary="Upload a layout's art",
    description=(
        "The request body is the image itself (PNG, JPEG or WebP, up to 10 MB). The canvas takes the art's "
        "size and the boxes scale to match.\n\n" + _WHO
    ),
    openapi_extra={'requestBody': {'required': True, 'content': {
        'image/png': {'schema': {'type': 'string', 'format': 'binary'}},
        'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}},
        'image/webp': {'schema': {'type': 'string', 'format': 'binary'}},
    }}},
)
async def set_art(layout_id: int, request: Request, actor: User = Depends(require_write_actor)):
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_ART_BYTES:
            raise HTTPException(status_code=413, detail='Art can be up to 10 MB.')
    detail, transparent = await AdminStreamLayoutsService().set_art(actor, layout_id, bytes(data))
    return SetArtResponse(layout=detail.layout, boxes=detail.boxes, transparent=transparent)


@router.put('/layouts/{layout_id}/background', response_model=LayoutDetailResponse, summary='Use a plain background',
            description="Swaps the art for one colour; boxes and the old art's see-through areas stay.\n\n" + _WHO)
async def set_background(layout_id: int, body: BackgroundRequest, actor: User = Depends(require_write_actor)):
    return await AdminStreamLayoutsService().set_background(actor, layout_id, body.colour)


@router.delete('/layouts/{layout_id}/art', response_model=LayoutDetailResponse, summary="Remove a layout's art", description=_WHO)
async def remove_art(layout_id: int, actor: User = Depends(require_write_actor)):
    return await AdminStreamLayoutsService().remove_art(actor, layout_id)
