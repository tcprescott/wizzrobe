"""Data export schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from application.services import DataExportService
from models import DataExport, DataExportKind


class ExportInfo(BaseModel):
    id: int = Field(..., description="ID of the export.")
    kind: str = Field(..., description="`tournaments`, `volunteers`, or `table_csv` (a table's Export CSV button in the web app).")
    title: str = Field(..., description="What was exported, e.g. `All 5 tournaments`.")
    status: str = Field(..., description="`queued`, `running`, `ready` or `failed`.")
    progress_done: int = Field(..., description="Tournaments (or parts) built so far.")
    progress_total: int = Field(..., description="Tournaments (or parts) to build.")
    error: Optional[str] = Field(None, description="Why it failed, for `failed`.")
    filename: Optional[str] = Field(None, description="Name of the main file once it's ready.")
    has_json: bool = Field(..., description="Whether `format=json` downloads a JSON copy.")
    size_bytes: int = Field(..., description="Size of the stored files once ready.")
    created_at: datetime = Field(..., description="When it was requested (UTC).")
    finished_at: Optional[datetime] = Field(None, description="When it finished building (UTC).")
    expires_at: datetime = Field(..., description="When it is deleted (UTC), 3 days after the request.")


class ExportList(BaseModel):
    items: List[ExportInfo]


class TournamentExportRequest(BaseModel):
    tournament_ids: Optional[List[int]] = Field(
        None,
        description="Tournaments to export. Leave out for every tournament you can export.",
    )


class VolunteerExportRequest(BaseModel):
    start: datetime = Field(..., description="Start of the window (ISO 8601; UTC if no offset).")
    end: datetime = Field(..., description="End of the window, exclusive (ISO 8601; UTC if no offset).")


def export_info(e: DataExport) -> ExportInfo:
    return ExportInfo(
        id=e.id, kind=e.kind.value, title=e.title, status=e.status.value,
        progress_done=e.progress_done, progress_total=e.progress_total, error=e.error,
        filename=e.filename, has_json=e.kind == DataExportKind.TOURNAMENTS,
        size_bytes=e.size_bytes, created_at=e.created_at, finished_at=e.finished_at,
        expires_at=DataExportService.expires_at(e),
    )
