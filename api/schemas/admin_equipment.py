"""Schemas for the app's Admin → Equipment screen."""

from typing import List, Optional

from pydantic import BaseModel, Field

from api.schemas.equipment import EquipmentResponse


class PersonPickResponse(BaseModel):
    id: int
    name: str
    username: str


class LabelAssetResponse(BaseModel):
    id: int
    asset_number: int
    name: str
    qr: str = Field(..., description="The asset's QR code as an SVG `data:` URI, linking to its page on the website.")


class LabelSheetResponse(BaseModel):
    community: str
    qr_logo: Optional[str] = Field(None, description="The community's logo as a `data:` URI for the centre of each code, or null.")
    assets: List[LabelAssetResponse]


class EquipmentBoardResponse(BaseModel):
    assets: List[EquipmentResponse] = Field(..., description="Every asset, by asset number, with who holds it now.")
    zone: Optional[str] = Field(None, description="The zone to print times in; null means the device's own.")
