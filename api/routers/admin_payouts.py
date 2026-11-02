"""REST for the app's Admin → Payouts screen.

The tournaments the caller pays out, and one tournament's pool and split shaped
for editing in place. The pool and split themselves save through
`/tournaments/{id}/prize-pool` and `/tournaments/{id}/payouts`.
"""

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor
from api.schemas.admin_payouts import (
    PayoutBoardResponse,
    PayoutDetailResponse,
    PayoutExportResponse,
    SgliveLinksRequest,
    SgliveLinksResponse,
    SgliveSyncResponse,
)
from application.services import AdminPayoutsService
from models import Tournament, User

router = APIRouter(
    prefix='/admin/payouts',
    tags=['Admin: payouts'],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(tournament_id=Tournament))],
)

_WHO = "**Who can call this:** Staff, and tournament admins for their own tournaments."


@router.get(
    '/board', response_model=PayoutBoardResponse, summary='The Payouts board',
    description="Every tournament the caller pays out, each with what its split still needs.\n\n" + _WHO,
)
async def board(actor: User = Depends(require_api_actor)):
    return await AdminPayoutsService().board(actor)


@router.get(
    '/sglive', response_model=SgliveLinksResponse, summary='sglive games and their links',
    description="Every game on sglive.speedgaming.org with the tournament it pays into, or a name-matched suggestion.\n\n" + _WHO,
)
async def sglive_links(actor: User = Depends(require_api_actor)):
    return await AdminPayoutsService().sglive_links(actor)


@router.put(
    '/sglive', status_code=204, summary='Link sglive games to tournaments',
    description="Saves which tournament each sglive game pays into, then fills the newly linked pools.\n\n" + _WHO,
)
async def set_sglive_links(body: SgliveLinksRequest, actor: User = Depends(require_api_actor)):
    await AdminPayoutsService().set_sglive_links(actor, body.links)


@router.post(
    '/sglive/sync', response_model=SgliveSyncResponse, summary='Sync pools from sglive now',
    description="Reads sglive now instead of waiting for the 15-minute sync.\n\n" + _WHO,
)
async def sync_sglive(actor: User = Depends(require_api_actor)):
    return SgliveSyncResponse(missing=await AdminPayoutsService().sync_sglive(actor))


@router.get(
    '/{tournament_id}', response_model=PayoutDetailResponse, summary="One tournament's payouts",
    description="The pool, the bonus, every place in the split, and the players to name as winners.\n\n" + _WHO,
)
async def detail(tournament_id: int, actor: User = Depends(require_api_actor)):
    return await AdminPayoutsService().detail(actor, tournament_id)


@router.get(
    '/{tournament_id}/export', response_model=PayoutExportResponse, summary='The payout block for Matcherino',
    description="The split as text, in the shape the organizer asks admins to post.\n\n" + _WHO,
)
async def export(tournament_id: int, actor: User = Depends(require_api_actor)):
    return PayoutExportResponse(text=await AdminPayoutsService().export_block(actor, tournament_id))


@router.delete(
    '/{tournament_id}/sglive', status_code=204, summary='Stop syncing a pool from sglive',
    description="The pool keeps its last synced amount and can be typed again.\n\n" + _WHO,
)
async def unlink_sglive(tournament_id: int, actor: User = Depends(require_api_actor)):
    await AdminPayoutsService().unlink_sglive(actor, tournament_id)
