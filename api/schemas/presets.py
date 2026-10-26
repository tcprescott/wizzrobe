"""Schemas for preset endpoints."""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

_RANDOMIZER = (
    "Randomizer slug the preset rolls for, as listed by `GET /seeds/randomizers` "
    "(for example `alttpr`, `avianart`, `ff1r`, `z1r`, `sm`, `smz3`, `smdash`, `smvaria`, `dk64r`, `wwr`)."
)
_SETTINGS = (
    "The randomizer's settings as a JSON object. Most randomizers take their own "
    "settings format unchanged; Final Fantasy takes `{\"url\": <flags link>}`, Zelda 1 "
    "`{\"flags\": ...}` and Wind Waker `{\"permalink\": ...}`, and those three are "
    "checked before saving."
)


class PresetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Preset id.")
    name: str = Field(..., description="Preset name, unique per randomizer in this community.")
    randomizer: str = Field(..., description=_RANDOMIZER)
    settings: Dict[str, Any] = Field(..., description=_SETTINGS)
    description: Optional[str] = Field(None, description="Optional note shown next to the preset.")
    created_at: datetime = Field(..., description="When the preset was created (UTC).")
    updated_at: datetime = Field(..., description="When the preset was last changed (UTC).")


class PresetCreateRequest(BaseModel):
    name: str = Field(..., description="Preset name, up to 255 characters. Must be unique for its randomizer.")
    randomizer: str = Field(..., description=_RANDOMIZER)
    settings: Dict[str, Any] = Field(..., description=_SETTINGS)
    description: Optional[str] = Field(None, description="Optional note shown next to the preset.")


class PresetUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="New name. Omit to keep the current one.")
    randomizer: Optional[str] = Field(None, description="New randomizer slug. Omit to keep the current one.")
    settings: Optional[Dict[str, Any]] = Field(
        None, description="Replacement settings object. Omit to keep the current settings."
    )
    description: Optional[str] = Field(
        None, description="New note. An empty string clears it; omit to keep the current one."
    )
