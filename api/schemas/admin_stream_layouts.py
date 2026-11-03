"""Schemas for the app's Admin → Stream Layouts screen.

A box is a plain object: `id`, `kind` (`field` or `hole`), `x`, `y`, `w`, `h` in
canvas pixels, then `radius`/`label` for a video hole, or `field`, `which`, `font`,
`size`, `weight`, `color`, `align`, `upper`, `shadow`, `text` for a text box. Every
write is checked and clamped onto the canvas by the server.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Attrs(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OverlayMatchResponse(_Attrs):
    id: int
    who: str = Field(..., description="The players (\"A vs B\"), or the match title for bigger fields.")
    tournament: str = Field(..., description="The tournament's short name.")
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None


class StageLineResponse(_Attrs):
    id: int
    name: str
    channel: Optional[str] = Field(None, description="The stage's Twitch channel, as `twitch.tv/name`.")
    layout_id: Optional[int] = None
    layout_name: Optional[str] = None
    showing: Optional[OverlayMatchResponse] = Field(None, description="The match the overlay shows now.")
    pinned: bool = Field(..., description="True when `showing` was pinned there rather than following the schedule.")
    up_next: Optional[OverlayMatchResponse] = None
    obs_url: str = Field(..., description="The stage's overlay URL, to paste into an OBS browser source.")


class LayoutLineResponse(_Attrs):
    id: int
    name: str
    width: int
    height: int
    box_count: int
    art_url: Optional[str] = Field(None, description="The layout's art image, or null when it has none.")
    stages: List[str] = Field(..., description="The stages using this layout (filled on the board only).")
    updated_at: datetime = Field(..., description="Send this back when saving boxes, so a save over someone else's is refused.")


class StreamLayoutBoardResponse(_Attrs):
    stages: List[StageLineResponse]
    layouts: List[LayoutLineResponse]
    zone: Optional[str] = Field(None, description="The IANA zone to show times in for this viewer, or null for the device's.")


class OverlayPreviewResponse(_Attrs):
    width: int
    height: int
    art_url: Optional[str] = None
    boxes: List[Dict[str, Any]]
    values: Dict[str, str] = Field(..., description="Each text box's current text, by box ID.")


class LayoutDetailResponse(_Attrs):
    layout: LayoutLineResponse
    boxes: List[Dict[str, Any]]


class FieldOptionResponse(_Attrs):
    key: str
    label: str
    icon: str
    group: str
    per_player: bool


class StarterOptionResponse(_Attrs):
    key: str
    label: str
    blurb: str
    group: str
    boxes: List[Dict[str, Any]]


class SwatchResponse(_Attrs):
    label: str
    colour: str


class EditorOptionsResponse(_Attrs):
    fields: List[FieldOptionResponse]
    fonts: List[str]
    weights: List[int]
    aligns: List[str]
    max_players: int
    max_boxes: int
    max_text: int
    max_label: int
    max_art_bytes: int
    starters: List[StarterOptionResponse]
    default_starter: str
    starter_width: int
    starter_height: int
    backgrounds: List[SwatchResponse]


class SetStageLayoutRequest(BaseModel):
    layout_id: Optional[int] = Field(None, description="The layout to show, or null for a blank overlay.")


class PinMatchRequest(BaseModel):
    match_id: int


class ObsLinkResponse(BaseModel):
    obs_url: str


class ObsScenesResponse(BaseModel):
    filename: str
    content: str = Field(..., description="The OBS scene collection, as JSON text to save and import.")


class CreateLayoutRequest(BaseModel):
    name: str
    starter: Optional[str] = Field(None, description="A starter's key; empty means blank.")
    background: Optional[str] = Field(None, description="A six-digit hex colour for a plain background.")


class RenameLayoutRequest(BaseModel):
    name: str


class SaveBoxesRequest(BaseModel):
    boxes: List[Dict[str, Any]]
    expected_updated_at: Optional[datetime] = Field(
        None, description="The `updated_at` the editor loaded. A save is refused when the layout changed since.",
    )


class PreviewValuesRequest(BaseModel):
    match_id: Optional[int] = Field(None, description="A match to fill the boxes from, or null for sample text.")
    boxes: Optional[List[Dict[str, Any]]] = Field(None, description="The editor's unsaved boxes; null uses the saved ones.")


class PreviewValuesResponse(BaseModel):
    values: Dict[str, str]


class BackgroundRequest(BaseModel):
    colour: str = Field(..., description="Six-digit hex, like #1E2A44.")


class SetArtResponse(_Attrs):
    layout: LayoutLineResponse
    boxes: List[Dict[str, Any]]
    transparent: bool = Field(..., description="Whether the art has see-through areas (the video can show through).")
