"""Schema for the build-version endpoint."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VersionResponse(BaseModel):
    version: str = Field(
        ...,
        description="Release tag when built from one, else the commit's UTC date as `YYYY.MM.DD`; `dev` for a local checkout.",
    )
    commit: Optional[str] = Field(None, description="Full git commit hash the build came from; null for a local checkout.")
    commit_short: Optional[str] = Field(None, description="First seven characters of `commit`.")
    commit_date: Optional[datetime] = Field(None, description="When that commit was made (UTC).")
    source_url: str = Field(..., description="The source code at that commit.")
