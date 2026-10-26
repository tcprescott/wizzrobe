"""Data exports: queue a tournament or volunteer export, check on it, read the result.

Mirrors ``api/routers/exports.py``. Exports build in the background, so asking
for one returns at once with ``status='queued'``; poll ``get_export`` until it
is ``ready``. A ready tournament export comes back as its JSON document (the
same ``tournaments.json`` the ZIP holds), which is the form a model can read.
The service decides who may export what (staff: every tournament; a tournament
admin: their own; volunteer data: staff and volunteer coordinators), and every
caller can only see their own exports.
"""

import json
from datetime import timezone
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from api.schemas.exports import ExportInfo, ExportList, export_info
from application.services import DataExportService
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import TenantArg
from mcpserver.tools._args import window
from models import DataExportStatus


class ExportResult(BaseModel):
    export: ExportInfo
    document: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "For a ready tournament export: the JSON document (format `wizzrobe-tournament-export`), "
            "with each tournament's participants and matches. Null otherwise."
        ),
    )


async def list_my_exports(tenant: TenantArg = None) -> ExportList:
    """Your exports from the last 3 days, newest first, with how far each has got."""
    actor = current_actor().user
    return ExportList(items=[export_info(e) for e in await DataExportService().list_mine(actor)])


async def export_tournaments(
    tenant: TenantArg = None, tournament_ids: Optional[List[int]] = None,
) -> ExportInfo:
    """Queue an export of tournament participants and matches.

    Leave `tournament_ids` out for every tournament you can export (staff: all;
    a tournament admin: their own). Returns at once with `status='queued'`; poll
    `get_export` with the returned `id`. One export may build per person at a
    time, 10 an hour.
    """
    actor = current_actor().user
    return export_info(await DataExportService().request_tournaments(actor, tournament_ids))


async def export_volunteer_data(tenant: TenantArg = None, start: str = '', end: str = '') -> ExportInfo:
    """Queue the volunteer data ZIP (roster, availability, positions, shifts, assignments).

    `start` and `end` are ISO 8601 (UTC when no offset). Requires Staff or a
    volunteer coordinator. The ZIP downloads from the web app (Reports →
    Exports) or `GET /api/exports/{id}/download`; this tool reports its status only.
    """
    actor = current_actor().user
    start_dt, end_dt = window(start, end)
    start_dt = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=timezone.utc)
    end_dt = end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=timezone.utc)
    return export_info(await DataExportService().request_volunteers(actor, start_dt, end_dt))


async def get_export(tenant: TenantArg = None, export_id: int = 0, include_document: bool = True) -> ExportResult:
    """One of your exports. A ready tournament export includes its JSON document unless `include_document` is false."""
    actor = current_actor().user
    service = DataExportService()
    export = await service.get_mine(actor, export_id)
    info = export_info(export)
    document = None
    if include_document and info.has_json and export.status == DataExportStatus.READY:
        f = await service.download(actor, export_id, 'json')
        document = json.loads(f.content)
    return ExportResult(export=info, document=document)


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_my_exports, gate=Gate.ACTOR, title='List my exports')
    register(mcp, get_export, gate=Gate.ACTOR, title='Get an export')
    register(mcp, export_tournaments, gate=Gate.ACTOR, title='Export tournaments', write=True, event=True)
    register(mcp, export_volunteer_data, gate=Gate.ACTOR, title='Export volunteer data', write=True, event=True)
