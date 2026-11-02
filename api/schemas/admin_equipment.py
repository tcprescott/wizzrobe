"""Schemas for the app's Admin → Equipment screen."""

from typing import List, Optional

from pydantic import BaseModel, Field


class PersonPickResponse(BaseModel):
    id: int
    name: str
    username: str


class LabelAssetResponse(BaseModel):
    id: int
    asset_number: int
    name: str


class LabelSheetResponse(BaseModel):
    community: str
    qr_logo: Optional[str] = Field(None, description="The community's logo as a `data:` URI for the centre of each code, or null.")
    assets: List[LabelAssetResponse]
