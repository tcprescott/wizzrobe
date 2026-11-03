"""Schemas for tournament endpoints."""

from datetime import date, datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class TournamentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The tournament's ID.")
    name: str = Field(..., description="The tournament's name.")
    short_name: Optional[str] = Field(
        None, description="Abbreviation shown where the full name won't fit, such as run sheets. Null means use the name.",
    )
    description: Optional[str] = Field(None, description="Long description shown to players, as markdown.")
    seed_generator: Optional[str] = Field(
        None,
        description=(
            "Key of the randomizer that rolls this tournament's seeds. When a preset is "
            "attached it is the preset's randomizer. Null when the tournament rolls no seeds."
        ),
    )
    is_active: bool = Field(..., description="Whether the tournament is running. Inactive tournaments are hidden from players.")
    players_per_match: int = Field(..., description="How many players a match in this tournament has.")
    bracket_url: Optional[str] = Field(None, description="Link to the tournament's external bracket page.")
    rules_url: Optional[str] = Field(None, description="Link to the tournament's rules.")
    tournament_format: Optional[str] = Field(None, description="Short free-text format label, such as \"Double elimination\".")
    triforce_access_message: Optional[str] = Field(
        None,
        description="Markdown shown to players who may not submit triforce texts, explaining how to get access.",
    )
    randomizer_notes: Optional[str] = Field(
        None, description="The randomizer settings, preset and build the tournament races on, as markdown.",
    )
    average_match_duration: Optional[int] = Field(
        None, description="Typical match length in minutes, used to plan the schedule and suggest start times.",
    )
    max_match_duration: Optional[int] = Field(None, description="Longest a match is expected to run, in minutes.")
    required_commentators: int = Field(
        ...,
        description=(
            "Approved commentators a streamed match needs before the coverage reports count it "
            "as covered. 0 means the tournament doesn't use the role."
        ),
    )
    required_trackers: int = Field(
        ...,
        description=(
            "Approved trackers a streamed match needs before the coverage reports count it "
            "as covered. 0 means the tournament doesn't use the role."
        ),
    )
    required_broadcasters: int = Field(
        1,
        description=(
            "Approved broadcasters a streamed match needs, in an event with broadcasters "
            "switched on. 0 means the tournament doesn't use the role."
        ),
    )
    allow_player_match_requests: bool = Field(
        ...,
        description=(
            "Whether players may request their own matches with POST /matches/request. False once "
            "a bracket is attached, since the tournament then schedules only its own matchups."
        ),
    )
    event_start_date: Optional[date] = Field(
        None, description="First day of this tournament's event window. Null inherits the community's setting.",
    )
    event_end_date: Optional[date] = Field(
        None, description="Last day of this tournament's event window. Null inherits the community's setting.",
    )
    tournament_hours: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            'Per-day match hours as {"YYYY-MM-DD": ["HH:MM open", "HH:MM close"]}, on the '
            "community's clock. Null inherits the community's setting."
        ),
    )
    signups_open_at: Optional[datetime] = Field(
        None, description="When player self-signup opens (UTC). Null means signups are open now.",
    )
    signups_close_at: Optional[datetime] = Field(
        None, description="When player self-signup closes (UTC). Null means signups stay open.",
    )
    is_test: bool = Field(
        False, description="A test tournament: usable, but left off the public event-info tournament list.",
    )
    players_release_seed: bool = Field(
        False, description="Players release their own seed: after check-in each taps Ready, and the seed rolls when all have.",
    )
    uses_tournament_room: bool = Field(
        True, description="Whether its off-stream matches are played in, and count toward, the tournament room.",
    )
    preset_id: Optional[int] = Field(None, description="ID of the seed preset matches roll from, if any.")
    hard_preset_id: Optional[int] = Field(
        None, description="ID of the harder preset players can opt into instead, if any.",
    )
    stage_reminder_minutes: int = Field(
        30, description="Minutes before a match that the stage reminder DM goes out. 0 means off.",
    )
    racetime_bot_id: Optional[int] = Field(None, description="ID of the racetime bot that opens rooms, if any.")
    race_room_profile_id: Optional[int] = Field(None, description="ID of the race room profile new rooms use, if any.")
    racetime_auto_create_rooms: bool = Field(False, description="Whether racetime rooms open automatically.")
    room_open_minutes_before: int = Field(30, description="Minutes before the match an automatic room opens.")
    require_racetime_link: bool = Field(False, description="Whether players need a linked racetime account.")
    racetime_default_goal: Optional[str] = Field(None, description="Racetime goal new rooms use.")
    challonge_tournament_url: Optional[str] = Field(
        None, description="The linked Challonge bracket's page, when the tournament mirrors one.",
    )
    discord_events_enabled: bool = Field(
        False, description="Whether matches appear as scheduled events in the community's Discord server.",
    )
    created_at: datetime = Field(..., description="When the tournament was created (UTC).")
    updated_at: datetime = Field(..., description="When the tournament was last changed (UTC).")
