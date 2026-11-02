"""Schemas for the app's Admin → Seed Presets screen."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Attrs(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PasteFieldResponse(_Attrs):
    key: str = Field(..., description="The settings key the pasted value is saved under.")
    label: str
    placeholder: str
    hint: str


class RandomizerEditorResponse(_Attrs):
    slug: str
    name: str
    available: bool = Field(..., description="False when the community hasn't added this randomizer's API key.")
    rolls_from_flags: bool = Field(..., description="A roll is a seed number beside a flag string.")
    paste_field: Optional[PasteFieldResponse] = Field(
        None, description="Edit this randomizer's preset as one pasted value instead of JSON.",
    )
    offers_remote_presets: bool = Field(..., description="Its own API publishes presets to import.")
    remote_branches: List[str] = Field(..., description="Catalogue variants to choose between; empty for one list.")


class PresetUsageResponse(_Attrs):
    preset_id: int
    tournaments: List[str] = Field(..., description="Tournaments rolling from it; deleting the preset detaches them.")


class PresetEditorResponse(_Attrs):
    randomizers: List[RandomizerEditorResponse]
    usage: List[PresetUsageResponse]


class PasteCheckRequest(BaseModel):
    randomizer: str
    value: str


class PasteCheckResponse(_Attrs):
    kind: str = Field(..., description="`site_version`, `example`, `build` or `none`.")
    site_version: Optional[str] = None
    example: Optional[str] = Field(None, description="What a player would be handed for these flags.")
    build: Optional[str] = None
    version: Optional[str] = None
    summary: Optional[List[str]] = Field(None, description="Headline settings; null when the build isn't known.")


class TestRollResponse(_Attrs):
    randomizer: str
    seed_url: str
    used_preset_settings: bool = Field(..., description="False when this randomizer ignores preset settings.")


class RemotePresetResponse(_Attrs):
    name: str
    description: Optional[str] = None


class RemoteImportRequest(BaseModel):
    randomizer: str
    names: List[str] = Field(..., min_length=1)
    branch: Optional[str] = None
