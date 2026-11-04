"""Schemas for door seeds, hosted ROM patches and Admin → Patches."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DoorSeedResponse(BaseModel):
    token: str
    version: str = Field(..., description="The Door Randomizer build that rolled it; its base patch is the same build's.")
    seed_code: Optional[List[str]] = Field(None, description="The file-select code, item names in order.")
    created_at: datetime
    spoiler: bool = Field(..., description="Whether the caller may download the spoiler log.")
    spoiler_staff_only: bool = Field(..., description="The spoiler is open to this community's staff only.")
    filename: str = Field(..., description="What to name the built ROM.")


class SpriteResponse(BaseModel):
    name: str
    author: Optional[str] = None
    file: Optional[str] = Field(None, description="The .zspr URL; it allows cross-origin reads.")
    preview: Optional[str] = None


class HostedPatchResponse(BaseModel):
    token: str
    title: str
    game: str = Field(..., description="The ROM a player needs, in words.")
    description: Optional[str] = None
    format: str = Field(..., description="bps, ips or ups.")
    source_crc: Optional[str] = Field(None, description="CRC32 (8 hex digits) of the ROM it was made for; IPS may have none.")
    tournament: Optional[str] = None
    community: str
    updated_at: datetime
    filename_base: str = Field(..., description="Filename stem for the patched ROM; the ROM's own extension is added.")


class AdminPatchResponse(HostedPatchResponse):
    id: int
    filename: str
    size: int
    tournament_id: Optional[int] = None
    download_count: int
    uploaded_by: Optional[str] = None
    created_at: datetime


class PatchTournamentOption(BaseModel):
    id: int
    name: str


class AdminPatchesResponse(BaseModel):
    patches: List[AdminPatchResponse]
    tournaments: List[PatchTournamentOption] = Field(..., description="Tournaments a patch can be tagged with.")


class PatchUpdateRequest(BaseModel):
    title: str = Field(..., max_length=120)
    game: str = Field(..., max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    tournament_id: Optional[int] = None
    source_crc: Optional[str] = Field(None, description="IPS only: the CRC32 of the ROM it needs.")
