"""Schemas for the app's admin shell: which areas and settings panes a viewer may open."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class AdminAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str = Field(..., description="Path segment under `/admin/`, the same one the website uses.")
    label: str
    icon: str = Field(..., description="Material Symbols name.")
    group: str = Field(..., description="Menu heading: `Run the event`, `Online play`, `People`, `Community`, `Configuration` or `System`.")
    half: str = Field(..., description="`event` for areas that work inside one event, `community` for the rest.")
    aliases: List[str] = Field(default_factory=list, description="Older slugs that should open this area.")


class SettingsPaneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    label: str
    icon: str
    summary: str


class SetupStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    done: bool
    hint: str
    tab: str = Field(..., description="Label of the admin area that finishes this step.")
    required: bool


class AdminNavResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    areas: List[AdminAreaResponse] = Field(..., description="In menu order. Empty when you have no admin access here.")
    settings_panes: List[SettingsPaneResponse] = Field(..., description="Panes under Settings, in menu order.")
    event_settings_panes: List[SettingsPaneResponse] = Field(..., description="Panes under Event settings, in menu order.")
    setup: List[SetupStepResponse] = Field(..., description="The community's setup checklist, for staff and tournament admins.")
    setup_ready: bool = Field(..., description="Every required setup step is done.")
