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
    can_edit: bool = Field(False, description="True for a preset in a personal space you own or co-edit.")
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


class LibraryCopyToMineRequest(BaseModel):
    name: Optional[str] = Field(None, description="Name for the copy. Omit to use the original's, made plain.")
    space_id: Optional[int] = Field(None, description="A space you co-edit. Omit for your own, made if needed.")


class LibraryRandomizerResponse(BaseModel):
    slug: str
    name: str
    available: bool = Field(..., description="False when this community hasn't added the randomizer's key, so rolls fail here.")
    paste_key: Optional[str] = Field(None, description="Edit its settings as one pasted value saved under this key, not JSON.")
    paste_label: Optional[str] = None
    paste_hint: Optional[str] = None


class PresetSpaceMember(BaseModel):
    id: int
    name: str


class PresetSpaceResponse(BaseModel):
    id: int
    slug: str = Field(..., description="The part before the slash in this space's preset names.")
    owner: PresetSpaceMember
    is_owner: bool = Field(..., description="False when you co-edit someone else's space.")
    preset_count: int
    max_presets: int
    editors: List[PresetSpaceMember]
    max_editors: int


class PresetSpaceUpdateRequest(BaseModel):
    slug: str = Field(..., description="2 to 32 lowercase letters, numbers, - or _, unique across Wizzrobe.")


class PresetSpaceEditorRequest(BaseModel):
    user_id: int = Field(..., description="A member of this community.")


class PersonalPresetCreateRequest(BaseModel):
    name: str = Field(..., description="2 to 32 lowercase letters, numbers, - or _.")
    randomizer: str
    settings: Dict[str, Any] = Field(default_factory=dict, description="The settings sent to the randomizer, 64 KB at most.")
    description: Optional[str] = Field(None, description="A note shown with the preset, 2000 characters at most.")
    space_id: Optional[int] = Field(None, description="A space you co-edit. Omit for your own, made if needed.")


class PersonalPresetUpdateRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    description: Optional[str] = None


class SahasrahBotPresetResponse(BaseModel):
    randomizer: str = Field(..., description="`alttpr`, `sm` or `smz3`, as SahasrahBot files it.")
    name: str = Field(..., description="The preset's name in SahasrahBot.")
    exists: bool = Field(..., description="Your space already has a preset by this name.")


class SahasrahBotPick(BaseModel):
    randomizer: str
    name: str


class SahasrahBotImportRequest(BaseModel):
    presets: List[SahasrahBotPick] = Field(..., description="Up to 25 presets from the namespace.")
    space_id: Optional[int] = Field(None, description="A space you co-edit. Omit for your own, made if needed.")


class SahasrahBotImportResult(BaseModel):
    randomizer: str
    name: str
    preset_id: Optional[int] = Field(None, description="The new preset, when it imported.")
    problem: Optional[str] = Field(None, description="Why it didn't import.")
