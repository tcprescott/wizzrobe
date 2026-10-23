"""Request schemas for tournament write actions."""

from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field

# Per-tournament "tournament days" override. The mapping keys off each day
# (``YYYY-MM-DD``) to an ``[open, close]`` pair of ``HH:MM`` strings; its
# ``model_dump()`` yields the ``{date: (open, close)}`` shape the service's
# ``validate_hours_mapping`` consumes directly. ``None`` (or omission on update)
# leaves the tournament inheriting the tenant setting.
_HOURS_DESC = (
    'Per-day match hours as {"YYYY-MM-DD": ["HH:MM open", "HH:MM close"]}. '
    'Omit or null to inherit the community setting.'
)
_DATE_DESC = 'Overrides the community event-window bound; null inherits it.'
_SIGNUP_DESC = (
    'Bound of the player self-signup window (UTC). Null on either side is '
    'permissive: no open date means signups are open now, no close date means '
    'they stay open. Staff enrolment ignores the window entirely.'
)
_CREW_DESC = (
    'Approved crew a streamed match needs before the coverage reports count it '
    'as covered. 0 means this tournament does not use the role. Does not '
    'restrict signups.'
)
_UPDATE_NOTE = ' Omit to leave it unchanged.'
_CLEAR_NOTE = ' Omit to leave it unchanged; null clears it.'
_TEST_DESC = (
    'Marks a test tournament: it stays usable but is left off the public '
    'event-info tournament list.'
)
_ROOM_DESC = (
    'Whether its matches are played in the tournament room. False leaves its '
    'off-stream matches out of room capacity: the capacity forecast, the '
    'room-full check and suggested times.'
)
_REQUESTS_DESC = 'Whether players may request their own matches.'
_PRESET_DESC = (
    'ID of the seed preset matches roll from. Attaching one also sets '
    '`seed_generator` to the preset\'s randomizer.'
)
_HARD_PRESET_DESC = (
    'ID of a harder preset for the same randomizer, offered as an alternative '
    'roll. Needs a standard preset.'
)
_BOT_DESC = 'ID of the racetime bot that opens race rooms (online tournaments).'
_PROFILE_DESC = 'ID of the race room profile new rooms use (online tournaments).'
_AUTO_ROOMS_DESC = 'Open racetime rooms automatically before each match. Needs a racetime bot.'
_ROOM_OPEN_DESC = 'How many minutes before the match an automatic room opens.'
_REQUIRE_LINK_DESC = 'Require players to have linked a racetime account.'
_SHORT_NAME_DESC = (
    "Abbreviation of up to 40 characters, shown where the full name won't fit (run sheets, "
    "the timeline). Leave it out to use the name."
)
_GOAL_DESC = 'Racetime goal new rooms use.'
_STAGE_REMINDER_DESC = 'How many minutes before a match the stage reminder DM goes out. 0 turns it off.'


class TournamentCreateRequest(BaseModel):
    name: str = Field(..., description="The tournament's name. Can't be blank.")
    short_name: Optional[str] = Field(None, description=_SHORT_NAME_DESC)
    description: Optional[str] = Field(None, description="Long description shown to players, as markdown.")
    seed_generator: Optional[str] = Field(
        None, description="Key of the randomizer that rolls this tournament's seeds, or null for none.",
    )
    bracket_url: Optional[str] = Field(None, description="Link to the external bracket page. Must be http or https.")
    rules_url: Optional[str] = Field(None, description="Link to the rules. Must be http or https.")
    tournament_format: Optional[str] = Field(None, description="Short free-text format label, such as \"Double elimination\".")
    triforce_access_message: Optional[str] = Field(
        None, description="Markdown shown to players who may not submit triforce texts.",
    )
    randomizer_notes: Optional[str] = Field(
        None, description="The randomizer settings, preset and build the tournament races on, as markdown.",
    )
    average_match_duration: Optional[int] = Field(None, description="Typical match length in minutes.")
    max_match_duration: Optional[int] = Field(None, description="Longest a match is expected to run, in minutes.")
    is_active: bool = Field(True, description="Whether the tournament starts out active.")
    players_per_match: int = Field(2, description="How many players a match in this tournament has.")
    required_commentators: int = Field(default=1, ge=0, description=_CREW_DESC)
    required_trackers: int = Field(default=1, ge=0, description=_CREW_DESC)
    event_start_date: Optional[date] = Field(default=None, description=_DATE_DESC)
    event_end_date: Optional[date] = Field(default=None, description=_DATE_DESC)
    tournament_hours: Optional[Dict[date, Tuple[str, str]]] = Field(
        default=None, description=_HOURS_DESC,
    )
    signups_open_at: Optional[datetime] = Field(default=None, description=_SIGNUP_DESC)
    signups_close_at: Optional[datetime] = Field(default=None, description=_SIGNUP_DESC)
    is_test: bool = Field(False, description=_TEST_DESC)
    uses_tournament_room: bool = Field(True, description=_ROOM_DESC)
    allow_player_match_requests: bool = Field(True, description=_REQUESTS_DESC)
    preset_id: Optional[int] = Field(None, description=_PRESET_DESC)
    hard_preset_id: Optional[int] = Field(None, description=_HARD_PRESET_DESC)
    racetime_bot_id: Optional[int] = Field(None, description=_BOT_DESC)
    race_room_profile_id: Optional[int] = Field(None, description=_PROFILE_DESC)
    racetime_auto_create_rooms: bool = Field(False, description=_AUTO_ROOMS_DESC)
    room_open_minutes_before: int = Field(30, ge=0, description=_ROOM_OPEN_DESC)
    require_racetime_link: bool = Field(False, description=_REQUIRE_LINK_DESC)
    racetime_default_goal: Optional[str] = Field(None, description=_GOAL_DESC)
    stage_reminder_minutes: int = Field(30, ge=0, description=_STAGE_REMINDER_DESC)


class TournamentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="New name. Can't be blank." + _UPDATE_NOTE)
    short_name: Optional[str] = Field(
        None, description=_SHORT_NAME_DESC + ' An empty string clears it.' + _UPDATE_NOTE,
    )
    description: Optional[str] = Field(
        None, description="Long description, as markdown. An empty string clears it." + _UPDATE_NOTE,
    )
    seed_generator: Optional[str] = Field(
        None, description="Key of the randomizer that rolls this tournament's seeds." + _UPDATE_NOTE,
    )
    bracket_url: Optional[str] = Field(
        None, description="Link to the external bracket page. Must be http or https." + _UPDATE_NOTE,
    )
    rules_url: Optional[str] = Field(None, description="Link to the rules. Must be http or https." + _UPDATE_NOTE)
    tournament_format: Optional[str] = Field(None, description="Short free-text format label." + _UPDATE_NOTE)
    triforce_access_message: Optional[str] = Field(
        None, description="Markdown shown to players who may not submit triforce texts." + _UPDATE_NOTE,
    )
    randomizer_notes: Optional[str] = Field(
        None, description="The randomizer settings, preset and build, as markdown." + _UPDATE_NOTE,
    )
    average_match_duration: Optional[int] = Field(None, description="Typical match length in minutes." + _UPDATE_NOTE)
    max_match_duration: Optional[int] = Field(
        None, description="Longest a match is expected to run, in minutes." + _UPDATE_NOTE,
    )
    is_active: Optional[bool] = Field(
        None, description="Set false to deactivate, which hides the tournament from players." + _UPDATE_NOTE,
    )
    players_per_match: Optional[int] = Field(None, description="How many players a match has." + _UPDATE_NOTE)
    required_commentators: Optional[int] = Field(default=None, ge=0, description=_CREW_DESC + _UPDATE_NOTE)
    required_trackers: Optional[int] = Field(default=None, ge=0, description=_CREW_DESC + _UPDATE_NOTE)
    event_start_date: Optional[date] = Field(default=None, description=_DATE_DESC)
    event_end_date: Optional[date] = Field(default=None, description=_DATE_DESC)
    tournament_hours: Optional[Dict[date, Tuple[str, str]]] = Field(
        default=None, description=_HOURS_DESC,
    )
    signups_open_at: Optional[datetime] = Field(default=None, description=_SIGNUP_DESC)
    signups_close_at: Optional[datetime] = Field(default=None, description=_SIGNUP_DESC)
    is_test: Optional[bool] = Field(None, description=_TEST_DESC + _UPDATE_NOTE)
    uses_tournament_room: Optional[bool] = Field(None, description=_ROOM_DESC + _UPDATE_NOTE)
    allow_player_match_requests: Optional[bool] = Field(None, description=_REQUESTS_DESC + _UPDATE_NOTE)
    preset_id: Optional[int] = Field(None, description=_PRESET_DESC + _CLEAR_NOTE)
    hard_preset_id: Optional[int] = Field(None, description=_HARD_PRESET_DESC + _CLEAR_NOTE)
    racetime_bot_id: Optional[int] = Field(None, description=_BOT_DESC + _CLEAR_NOTE)
    race_room_profile_id: Optional[int] = Field(None, description=_PROFILE_DESC + _CLEAR_NOTE)
    racetime_auto_create_rooms: Optional[bool] = Field(None, description=_AUTO_ROOMS_DESC + _UPDATE_NOTE)
    room_open_minutes_before: Optional[int] = Field(None, ge=0, description=_ROOM_OPEN_DESC + _UPDATE_NOTE)
    require_racetime_link: Optional[bool] = Field(None, description=_REQUIRE_LINK_DESC + _UPDATE_NOTE)
    racetime_default_goal: Optional[str] = Field(None, description=_GOAL_DESC + _CLEAR_NOTE)
    stage_reminder_minutes: Optional[int] = Field(None, ge=0, description=_STAGE_REMINDER_DESC + _UPDATE_NOTE)


class MembershipRequest(BaseModel):
    user_id: int = Field(..., description="ID of the user to add as tournament admin or crew coordinator.")


class ChallongeLinkRequest(BaseModel):
    challonge: str = Field(
        ..., description="The Challonge bracket's URL (https://challonge.com/...) or its ID.",
    )


class ChallongeSyncResponse(BaseModel):
    counts: Dict[str, Any] = Field(
        ..., description="What the sync found or changed, such as participant and match counts.",
    )
