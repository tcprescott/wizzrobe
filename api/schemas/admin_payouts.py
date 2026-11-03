"""Schemas for the app's Admin → Payouts screen. Money is a string, as on `/payouts`."""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Attrs(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PayoutRowResponse(_Attrs):
    id: int
    name: str
    status: str = Field(..., description="`none` (no split yet), `unnamed` (a place has no winner), or `ready`.")
    status_label: str
    total: Decimal = Field(..., description="Pool plus bonus.")
    has_pool: bool = Field(..., description="Whether a pool or bonus is set at all.")
    place_count: int
    sglive_slug: Optional[str] = Field(None, description="The sglive page the pool follows, or null.")


class PayoutBoardResponse(_Attrs):
    tournaments: List[PayoutRowResponse] = Field(..., description="Every tournament the caller manages payouts for.")
    zone: Optional[str] = Field(None, description="The IANA zone to show times in for this viewer, or null for the device's.")


class PayoutEditLineResponse(_Attrs):
    place: int
    percentage: Optional[Decimal] = Field(None, description="The share, or null on a fixed-amount line.")
    amount: Decimal = Field(..., description="The money this line pays right now.")
    is_fixed: bool
    entrant_id: Optional[int] = None
    note: Optional[str] = None


class PayoutEntrantResponse(_Attrs):
    id: int
    name: str


class PayoutDetailResponse(_Attrs):
    id: int
    name: str
    status: str
    status_label: str
    pool: Optional[Decimal] = Field(None, description="The pool, or null when nobody has set one.")
    bonus: Optional[Decimal] = Field(None, description="The bonus, or null.")
    lines: List[PayoutEditLineResponse]
    entrants: List[PayoutEntrantResponse] = Field(..., description="The tournament's players, plus anyone already named in the split.")
    sglive_slug: Optional[str] = Field(None, description="The sglive page the pool follows; the pool can't be typed while set.")
    synced_at: Optional[datetime] = Field(None, description="When the pool last synced from sglive (UTC).")


class PayoutExportResponse(BaseModel):
    text: str = Field(..., description="The payout block to paste into Matcherino's thread.")


class SgliveGameLineResponse(_Attrs):
    slug: str
    name: str
    total: Decimal
    tournament_id: Optional[int] = Field(None, description="The linked tournament, or the suggested one when `suggested`.")
    suggested: bool = Field(..., description="True when `tournament_id` is a name match, not a saved link.")


class SgliveLinksResponse(_Attrs):
    games: List[SgliveGameLineResponse]
    tournaments: List[PayoutEntrantResponse] = Field(..., description="Tournaments the caller can link.")


class SgliveLinksRequest(BaseModel):
    links: Dict[str, Optional[int]] = Field(
        ..., description="sglive slug → tournament ID, or null to leave that game unlinked. Slugs left out keep their link.",
    )


class SgliveSyncResponse(BaseModel):
    missing: List[str] = Field(..., description="Linked slugs sglive no longer lists; their pools kept their last amount.")
