"""Door Randomizer seeds, hosted ROM patches, and Admin → Patches.

The token routes open for anyone with the link (``require_reader``): a seed's or
patch's token is the secret that names it, and the community comes from the
header. No ROM ever reaches the server; these hand out the patches a player's
browser applies to their own.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_reader, require_write_actor
from api.schemas.rom_patches import (
    AdminPatchesResponse,
    AdminPatchResponse,
    DoorSeedResponse,
    HostedPatchResponse,
    PatchTournamentOption,
    PatchUpdateRequest,
    SpriteResponse,
)
from application.services import DoorSeedService, RomPatchService, TenantService
from application.services.rom_patch_service import output_base
from application.tenant_context import require_tenant_id
from models import RomPatch, User

__all__ = ['admin_router', 'door_router', 'patch_router']

door_router = APIRouter(prefix='/door-seeds', tags=['Door seeds'], route_class=ServiceErrorRoute)
patch_router = APIRouter(prefix='/patches', tags=['ROM patches'], route_class=ServiceErrorRoute)
admin_router = APIRouter(
    prefix='/admin/patches', tags=['Admin: patches'], route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding())],
)

MAX_PATCH_BYTES = 16 * 1024 * 1024

_LINK = (
    "**Who can call this:** Anyone with the link, signed in or not. Name the community in the "
    "`X-Wizzrobe-Community` header."
)
_MANAGER = "**Who can call this:** Staff or Preset manager."
_IMMUTABLE = {'Cache-Control': 'public, max-age=31536000, immutable', 'X-Content-Type-Options': 'nosniff'}


@door_router.get(
    '/sprites', response_model=List[SpriteResponse], summary='List ALttP sprites',
    description="The alttpr.com sprite list, cached for a few hours; empty when alttpr.com can't be reached.\n\n" + _LINK,
)
async def sprites(_reader: Optional[User] = Depends(require_reader)):
    return await DoorSeedService.sprite_list()


@door_router.get(
    '/base/{version}', summary="Download a Door Randomizer build's base patch",
    response_class=Response, responses={200: {'content': {'application/octet-stream': {}}}},
    description="The BPS every seed of that build starts from, applied to a Japanese 1.0 ROM.\n\n" + _LINK,
)
async def base_patch(version: str, _reader: Optional[User] = Depends(require_reader)):
    return Response(await DoorSeedService().base_patch(version), media_type='application/octet-stream', headers=_IMMUTABLE)


@door_router.get(
    '/{token}', response_model=DoorSeedResponse, summary='Get a door seed',
    description="The seed's build, code and whether you may download its spoiler.\n\n" + _LINK,
)
async def door_seed(token: str, reader: Optional[User] = Depends(require_reader)):
    service = DoorSeedService()
    seed = await service.get_seed(token)
    return DoorSeedResponse(
        token=seed.token, version=seed.version, seed_code=seed.seed_code, created_at=seed.created_at,
        spoiler=await service.can_read_spoiler(seed, reader),
        spoiler_staff_only=seed.spoiler_access == 'staff',
        filename=f'DR_{seed.token}.sfc',
    )


@door_router.get(
    '/{token}/patch', summary="Download a door seed's writes",
    response_class=Response, responses={200: {'content': {'application/json': {}}}},
    description="JSON byte writes on top of the build's base patch.\n\n" + _LINK,
)
async def door_seed_patch(token: str, _reader: Optional[User] = Depends(require_reader)):
    return Response(await DoorSeedService().patch(token), media_type='application/json', headers=_IMMUTABLE)


@door_router.get(
    '/{token}/spoiler', summary="Download a door seed's spoiler log",
    response_class=Response, responses={200: {'content': {'application/json': {}}}},
    description=(
        "404 unless the preset made it public, or it's staff-only and you're this community's staff.\n\n" + _LINK
    ),
)
async def door_seed_spoiler(token: str, reader: Optional[User] = Depends(require_reader)):
    data = await DoorSeedService().spoiler_by_token(token, reader)
    return Response(data, media_type='application/json', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f'attachment; filename="DR_{token}_spoiler.json"',
    })


async def _community() -> str:
    tenant = await TenantService.get_by_id(require_tenant_id())
    return tenant.name if tenant else ''


def _hosted(patch: RomPatch, community: str) -> HostedPatchResponse:
    return HostedPatchResponse(
        token=patch.token, title=patch.title, game=patch.game, description=patch.description,
        format=patch.format, source_crc=patch.source_crc,
        tournament=patch.tournament.name if patch.tournament_id and patch.tournament else None,
        community=community, updated_at=patch.updated_at,
        filename_base=output_base(patch.title),
    )


@patch_router.get(
    '/{token}', response_model=HostedPatchResponse, summary='Get a hosted patch',
    description="What the patch is and the ROM it needs.\n\n" + _LINK,
)
async def hosted_patch(token: str, _reader: Optional[User] = Depends(require_reader)):
    return _hosted(await RomPatchService.get_by_token(token), await _community())


@patch_router.get(
    '/{token}/file', summary="Download a hosted patch's file",
    response_class=Response, responses={200: {'content': {'application/octet-stream': {}}}},
    description="The BPS, IPS or UPS file itself.\n\n" + _LINK,
)
async def hosted_patch_file(token: str, _reader: Optional[User] = Depends(require_reader)):
    patch, data = await RomPatchService().patch_file(token)
    return Response(data, media_type='application/octet-stream', headers={
        'Cache-Control': 'private, no-cache', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f'attachment; filename="{patch.token}.{patch.format}"',
    })


@patch_router.post(
    '/{token}/downloaded', status_code=status.HTTP_204_NO_CONTENT, summary='Count a download',
    description="Called after a player saves a patched ROM, for the download count staff see.\n\n" + _LINK,
)
async def hosted_patch_downloaded(token: str, _reader: Optional[User] = Depends(require_reader)):
    await RomPatchService.record_download(token)


def _admin(patch: RomPatch, hosted: HostedPatchResponse) -> AdminPatchResponse:
    return AdminPatchResponse(
        **hosted.model_dump(), id=patch.id, filename=patch.filename, size=patch.size,
        tournament_id=patch.tournament_id, download_count=patch.download_count,
        uploaded_by=patch.uploaded_by.username if patch.uploaded_by_id and patch.uploaded_by else None,
        created_at=patch.created_at,
    )


async def _admin_one(service: RomPatchService, actor: User, patch_id: int) -> AdminPatchResponse:
    patch = await service.get_for_manager(actor, patch_id)
    await patch.fetch_related('tournament', 'uploaded_by')
    return _admin(patch, _hosted(patch, await _community()))


@admin_router.get(
    '', response_model=AdminPatchesResponse, summary='List hosted patches',
    description="Every patch this community hosts, and the tournaments one can be tagged with.\n\n" + _MANAGER,
)
async def list_patches(actor: User = Depends(require_api_actor)):
    service = RomPatchService()
    patches = await service.list_patches(actor)
    tournaments = await service.tournament_choices(actor)
    community = await _community()
    return AdminPatchesResponse(
        patches=[_admin(p, _hosted(p, community)) for p in patches],
        tournaments=[PatchTournamentOption(id=t.id, name=t.name) for t in tournaments],
    )


async def _body(request: Request) -> bytes:
    data = await request.body()
    if len(data) > MAX_PATCH_BYTES:
        raise ValueError('Patches can be up to 16 MB.')
    return data


@admin_router.post(
    '', response_model=AdminPatchResponse, status_code=status.HTTP_201_CREATED, summary='Upload a patch',
    description=(
        "The request body is the patch file itself (BPS, IPS or UPS, up to 16 MB); the rest comes in the "
        "query. BPS and UPS name their ROM's CRC32 themselves; `source_crc` is read for IPS only. "
        "Fails with 400 on a file that isn't a patch.\n\n" + _MANAGER
    ),
)
async def upload_patch(
    request: Request,
    title: str = Query(..., max_length=120),
    game: str = Query(..., max_length=120),
    filename: str = Query(..., max_length=200),
    description: Optional[str] = Query(None, max_length=2000),
    tournament_id: Optional[int] = Query(None),
    source_crc: Optional[str] = Query(None, max_length=8),
    actor: User = Depends(require_write_actor),
):
    service = RomPatchService()
    patch = await service.create_patch(
        actor, title=title, game=game, filename=filename, data=await _body(request),
        description=description, tournament_id=tournament_id, source_crc=source_crc,
    )
    return await _admin_one(service, actor, patch.id)


@admin_router.put(
    '/{patch_id}', response_model=AdminPatchResponse, summary='Edit a patch',
    description="Title, game, description, tournament, and an IPS patch's CRC32. The link stays the same.\n\n" + _MANAGER,
)
async def update_patch(patch_id: int, body: PatchUpdateRequest, actor: User = Depends(require_write_actor)):
    service = RomPatchService()
    await service.update_patch(
        actor, patch_id, title=body.title, game=body.game, description=body.description,
        tournament_id=body.tournament_id, source_crc=body.source_crc,
    )
    return await _admin_one(service, actor, patch_id)


@admin_router.put(
    '/{patch_id}/file', response_model=AdminPatchResponse, summary="Replace a patch's file",
    description="The request body is the new file. The player link keeps working.\n\n" + _MANAGER,
)
async def replace_patch_file(
    patch_id: int, request: Request,
    filename: str = Query(..., max_length=200),
    actor: User = Depends(require_write_actor),
):
    service = RomPatchService()
    await service.replace_file(actor, patch_id, filename=filename, data=await _body(request))
    return await _admin_one(service, actor, patch_id)


@admin_router.delete(
    '/{patch_id}', status_code=status.HTTP_204_NO_CONTENT, summary='Delete a patch',
    description="Its link stops working.\n\n" + _MANAGER,
)
async def delete_patch(patch_id: int, actor: User = Depends(require_write_actor)):
    await RomPatchService().delete_patch(actor, patch_id)
