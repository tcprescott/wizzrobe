"""System configuration endpoints (Staff only)."""

from typing import List

from fastapi import APIRouter, Depends

from api.dependencies import ServiceErrorRoute, require_staff, require_staff_write
from api.schemas.config import ConfigEntryResponse, ConfigValueUpdate
from application.errors import require_found
from application.services import SystemConfigService
from models import User

router = APIRouter(prefix="/config", tags=["System Config"], route_class=ServiceErrorRoute)


@router.get(
    "",
    response_model=List[ConfigEntryResponse],
    summary="List all configuration entries",
    description=(
        "Returns every setting this community has stored, sorted by key. A setting that's never "
        "been saved isn't listed.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_config(actor: User = Depends(require_staff)):
    # The community's rows plus the default event's own settings (capacity,
    # hours, dates…), which live on the event now.
    return [ConfigEntryResponse(name=name, value=value) for name, value in await SystemConfigService.list_entries(actor)]


@router.get(
    "/{key}",
    response_model=ConfigEntryResponse,
    summary="Get a configuration entry",
    description=(
        "Returns one stored setting by key. A key that's never been saved returns 404, even when "
        "it's a valid setting.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def get_config(key: str, actor: User = Depends(require_staff)):
    entries = dict(await SystemConfigService.list_entries(actor))
    return require_found(
        ConfigEntryResponse(name=key, value=entries[key]) if key in entries else None,
        "Config key",
    )


@router.put(
    "/{key}",
    response_model=ConfigEntryResponse,
    summary="Set a configuration entry (Staff only)",
    description=(
        "Validates the value for the key, normalises it (for example `yes` becomes `true` on a switch), "
        "stores it and records the old and new values in the audit log.\n\n"
        "Fails with 400 when the key isn't a known setting or the value doesn't fit it: a date that "
        "isn't YYYY-MM-DD, a count that isn't a whole number of at least 1, a switch that isn't true or "
        "false, or a malformed comp-tier list, station format, Watch page mode, tournament-hours object "
        "or Discord invite link.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def set_config(key: str, body: ConfigValueUpdate, actor: User = Depends(require_staff_write)):
    return await SystemConfigService.set_validated(key, body.value, actor)
