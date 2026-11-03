"""Data exports: request one, check on it, download it."""

from datetime import timezone

from fastapi import APIRouter, Depends, Query, Response

from api.dependencies import ServiceErrorRoute, event_binding, require_api_actor, require_write_actor
from api.schemas.exports import (
    ExportInfo,
    ExportList,
    TournamentExportRequest,
    VolunteerExportRequest,
    export_info,
)
from application.services import DataExportService
from models import User

router = APIRouter(prefix="/exports", tags=["Exports"], route_class=ServiceErrorRoute)

_LIMITS = (
    "Exports build in the background, one at a time across the app: you may have one export "
    "building at once and request 10 an hour. Finished exports are deleted after 3 days."
)


@router.get(
    "",
    response_model=ExportList,
    summary="List your exports",
    description="Your exports from the last 3 days, newest first.\n\n**Who can call this:** Any member.",
)
async def list_exports(actor: User = Depends(require_api_actor)):
    return ExportList(items=[export_info(e) for e in await DataExportService().list_mine(actor)])


@router.post(
    "/tournaments",
    dependencies=[Depends(event_binding())],
    response_model=ExportInfo,
    status_code=202,
    summary="Export tournaments",
    description=(
        "Queues an export of each tournament's participants (status, seed, placement, wins, losses) "
        "and matches. Poll `GET /exports/{id}` until `status` is `ready`, then download the ZIP "
        "(a folder of CSVs per tournament plus `tournaments.json`) or `format=json`. "
        f"{_LIMITS}\n\n**Who can call this:** Staff (any tournament) and tournament admins (their own)."
    ),
)
async def export_tournaments(body: TournamentExportRequest, actor: User = Depends(require_write_actor)):
    return export_info(await DataExportService().request_tournaments(actor, body.tournament_ids))


@router.post(
    "/volunteers",
    dependencies=[Depends(event_binding())],
    response_model=ExportInfo,
    status_code=202,
    summary="Export volunteer data",
    description=(
        "Queues the volunteer data ZIP (roster, availability, positions, shifts, assignments and a "
        f"schedule grid) for the window. {_LIMITS}\n\n"
        "**Who can call this:** Staff and volunteer coordinators, when volunteer scheduling is on."
    ),
)
async def export_volunteers(body: VolunteerExportRequest, actor: User = Depends(require_write_actor)):
    start = body.start if body.start.tzinfo else body.start.replace(tzinfo=timezone.utc)
    end = body.end if body.end.tzinfo else body.end.replace(tzinfo=timezone.utc)
    return export_info(await DataExportService().request_volunteers(actor, start, end))


@router.get(
    "/{export_id}",
    response_model=ExportInfo,
    summary="Get an export",
    description="One of your exports and how far it has got.\n\n**Who can call this:** The person who requested it.",
)
async def get_export(export_id: int, actor: User = Depends(require_api_actor)):
    return export_info(await DataExportService().get_mine(actor, export_id))


@router.get(
    "/{export_id}/download",
    summary="Download an export",
    description=(
        "The finished file, or with `format=json` the JSON copy of a tournament export. "
        "400 until the export is ready.\n\n**Who can call this:** The person who requested it."
    ),
    response_class=Response,
    responses={200: {"content": {
        "application/zip": {},
        "text/csv": {},
        "application/json": {"schema": {
            "description": "tournaments.json; see docs/schemas/tournament-export-v1.schema.json",
            "properties": {
                "format": {"type": "string", "example": "wizzrobe-tournament-export"},
                "schema_version": {"type": "integer", "example": 1},
                "generated_at": {"type": "string", "format": "date-time"},
                "community": {"type": "object"},
                "tournaments": {"type": "array", "items": {"type": "object"}},
            },
        }},
    }}},
)
async def download_export(
    export_id: int,
    format: str = Query("file", pattern="^(file|json)$", description="`file` for the ZIP or CSV, `json` for the JSON copy."),
    actor: User = Depends(require_api_actor),
):
    f = await DataExportService().download(actor, export_id, format)
    return Response(
        content=f.content, media_type=f.media_type,
        headers={"Content-Disposition": f'attachment; filename="{f.filename}"'},
    )
