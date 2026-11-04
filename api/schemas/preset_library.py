"""Schemas for the public preset library."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LibraryPresetResponse(BaseModel):
    id: int = Field(..., description="Preset id.")
    reference: str = Field(..., description="The preset's full name, `space/preset`.")
    name: str = Field(..., description="Preset name, unique per randomizer in its space.")
    randomizer: str = Field(..., description="Randomizer slug the preset rolls for.")
    randomizer_name: str = Field(..., description="The randomizer's display name.")
    rollable: bool = Field(..., description="Whether this community has the keys to roll it.")
    can_manage: bool = Field(..., description="Whether you manage this community's presets, so may copy or update copies.")
    description: Optional[str] = Field(None, description="The owner's note on the preset.")
    space: str = Field(..., description="The space's slug, the part before the slash.")
    space_kind: str = Field(..., description="`community`, `user` or `builtin`.")
    space_name: str = Field(..., description="Who the space belongs to: a community or player name.")
    mine: bool = Field(..., description="True when the preset belongs to the community you're in.")
    listed: bool = Field(..., description="Whether the preset shows to everyone. Only your own community's can be false.")
    roll_count: int = Field(..., description="How many seeds have been rolled from it in the library.")
    updated_at: datetime = Field(..., description="When the preset last changed (UTC).")
    copied_from: Optional[str] = Field(None, description="`space/preset` this one was copied from, if it's a copy.")
    copied_from_id: Optional[int] = Field(None, description="Id of the original, if it's a copy and still exists.")
    changed_since_copy: bool = Field(False, description="True when the original's settings changed after the copy was made.")


class LibraryPresetDetailResponse(LibraryPresetResponse):
    settings: Dict[str, Any] = Field(..., description="The settings the preset sends to its randomizer.")


class LibraryRollResponse(BaseModel):
    preset_id: int = Field(..., description="The preset that was rolled.")
    randomizer: str = Field(..., description="Randomizer slug.")
    seed_url: str = Field(..., description="Link to the seed.")
    code: Optional[List[str]] = Field(None, description="The file-select code, where the randomizer has one.")
    roll_count: int = Field(..., description="The preset's roll count, including this one.")


class LibraryCopyRequest(BaseModel):
    name: Optional[str] = Field(None, description="Name for the copy. Omit to keep the original's name.")
