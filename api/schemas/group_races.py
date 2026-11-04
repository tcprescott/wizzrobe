"""Schemas for group race endpoints."""

from datetime import datetime
from typing import List, Literal, Optional

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


class GroupRaceEntrantResponse(BaseModel):
    name: str = Field(..., description="Their Wizzrobe name when they've linked racetime here, else their racetime name.")
    linked: bool = Field(..., description='Whether this racetime account is linked to a Wizzrobe account.')
    place: Optional[int] = Field(None, description='Finishing place, once they finish.')
    finish_seconds: Optional[int] = Field(None, description='Finish time in whole seconds, once they finish.')
    status: Optional[str] = Field(None, description="racetime's entrant status (`ready`, `done`, `dnf` …), when known.")


class GroupRaceResponse(BaseModel):
    kind: Literal['match', 'qualifier'] = Field(
        ..., description='`match` for a race in a group race tournament, `qualifier` for a qualifier live race.',
    )
    id: int = Field(..., description='The match id or the live race id, per `kind`.')
    title: str
    series: str = Field(..., description='The tournament or qualifier it belongs to.')
    series_id: int
    from_speedgaming: bool = Field(..., description='Imported from the SpeedGaming schedule, rather than added by hand.')
    scheduled_at: Optional[datetime] = Field(None, description='Start time, UTC.')
    opens_at: Optional[datetime] = Field(None, description='When the bot opens the room, UTC.')
    seed_at: Optional[datetime] = Field(None, description='When the bot posts the seed, UTC.')
    state: Literal['scheduled', 'open', 'running', 'finished']
    lock_at_seed: bool = Field(..., description='Whether the room locks when the seed posts.')
    channel_id: Optional[str] = Field(None, description='Discord channel the room is posted in.')
    role_id: Optional[str] = Field(None, description='Discord role pinged with the post.')
    seed_note: str = Field(..., description='Where the seed comes from, in words.')
    room_url: Optional[str] = Field(None, description='The racetime room, once open.')
    opened_at: Optional[datetime] = None
    announced_at: Optional[datetime] = Field(None, description='When the Discord post went out.')
    seed_posted_at: Optional[datetime] = None
    locked_at: Optional[datetime] = None
    finishers: int = Field(0, description='How many finished, for a finished race.')


class GroupRaceDetailResponse(GroupRaceResponse):
    entrants: List[GroupRaceEntrantResponse] = Field(
        ..., description="Who's in the room (read live from racetime while it's open), or the results once it's finished.",
    )
    entrants_problem: Optional[str] = Field(None, description="Why the entrant list is empty when racetime couldn't be read.")


class GroupRaceBoardResponse(BaseModel):
    zone: Optional[str] = Field(None, description="The zone to print times in; null means the device's own.")
    races: List[GroupRaceResponse]


class SeriesPickResponse(BaseModel):
    kind: Literal['tournament', 'pool'] = Field(..., description='A group race tournament, or a qualifier pool.')
    id: int
    name: str
    permalinks: List[PickOption] = Field(
        default_factory=list, description="For a pool: its live race permalinks, one of which the race is raced on.",
    )


class GroupRaceCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    scheduled_at: datetime = Field(..., description='Start time. Send UTC (or with an offset).')
    tournament_id: Optional[int] = Field(None, description='A group race tournament. Send this or `pool_id`.')
    pool_id: Optional[int] = Field(None, description='A qualifier pool, for a qualifier live race.')
    permalink_id: Optional[int] = Field(None, description="With `pool_id`: the pool permalink it's raced on.")
