"""Schemas for group race endpoints."""

from typing import List, Optional

from pydantic import BaseModel, Field


class PickOption(BaseModel):
    id: str = Field(..., description='The id to save: a Discord id as a string, or a bot or profile id.')
    name: str = Field(..., description='What to show for it.')


class RoomOptionsResponse(BaseModel):
    bots: List[PickOption] = Field(..., description='Racetime bots this community may open rooms with, named `category (bot name)`.')
    profiles: List[PickOption] = Field(..., description="The community's race room profiles.")
    channels: List[PickOption] = Field(
        ..., description="Text channels in the community's Discord server, in Discord's order. Empty when Discord can't be read.",
    )
    roles: List[PickOption] = Field(
        ..., description="Roles in the community's Discord server, by name, without @everyone. Empty when Discord can't be read.",
    )
    discord_problem: Optional[str] = Field(
        None, description="Why the channel and role lists are empty (no server connected, the bot offline), or null.",
    )
