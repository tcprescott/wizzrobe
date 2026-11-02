"""Schemas for the app's Admin → Tournaments screen."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from application.utils.tournament_signup import SignupWindow
from models import ForfeitReason


class _Attrs(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StageActionResponse(_Attrs):
    key: str = Field(..., description="Pass back to the stage endpoint to run it.")
    label: str = Field(..., description="Button text, such as `Start the group stage`.")
    confirm: str = Field(..., description="What to show before running it: it changes the bracket on Challonge.")


class BracketResponse(_Attrs):
    site: str = Field(..., description="`challonge` or `startgg`.")
    label: str = Field(..., description="The site's name as people see it.")
    stage: Optional[str] = Field(None, description="Where a Challonge bracket stands, such as `Group stage`.")
    synced_at: Optional[datetime] = Field(None, description="Last sync (UTC), or null if never synced.")
    unmatched: int = Field(..., description="Entrants not yet matched to a member.")
    url: Optional[str] = Field(None, description="The bracket on the site.")
    stage_action: Optional[StageActionResponse] = Field(None, description="The next Challonge stage step, if any.")


class BoardRowResponse(_Attrs):
    id: int
    name: str
    tournament_format: Optional[str] = None
    seed_generator: Optional[str] = None
    is_active: bool
    is_test: bool = Field(..., description="Hidden from the event info page only.")
    players_per_match: int
    player_count: int
    average_match_duration: Optional[int] = Field(None, description="Minutes.")
    max_match_duration: Optional[int] = Field(None, description="Minutes.")
    signups_open_at: Optional[datetime] = None
    signups_close_at: Optional[datetime] = None
    signup_state: SignupWindow
    bracket: Optional[BracketResponse] = Field(None, description="The bracket site it follows, if linked.")
    bracket_url: Optional[str] = None
    rules_url: Optional[str] = None
    has_round_schedule: bool = Field(..., description="Whether Round schedule applies (a linked bracket).")
    can_manage_bracket: bool = Field(..., description="Sync, stage step and entrant matching are offered.")
    can_link: bool = Field(..., description="Link or unlink a bracket is offered (staff only).")
    can_delete: bool = Field(..., description="Delete is offered: staff, and the tournament is inactive.")


class BracketSiteResponse(_Attrs):
    key: str
    label: str


class BoardResponse(_Attrs):
    rows: List[BoardRowResponse]
    can_create: bool = Field(..., description="Add tournament is offered (staff only).")
    linkable_sites: List[BracketSiteResponse] = Field(..., description="Sites a tournament can be linked to here.")


class DeletionPreviewResponse(BaseModel):
    matches: int
    players: int
    brackets: int
    triforce_texts: int


class PersonResponse(_Attrs):
    id: int
    name: str
    username: Optional[str] = None


class PlayerResponse(_Attrs):
    user: PersonResponse
    enrolled_at: Optional[datetime] = None


class DropMatchResponse(_Attrs):
    id: int
    title: str
    scheduled_at: Optional[datetime] = None


class DropPlanResponse(_Attrs):
    head_to_head: List[DropMatchResponse] = Field(..., description="Two-player matches: forfeited to the opponent, or called off.")
    group: List[DropMatchResponse] = Field(..., description="Larger matches: the player comes off the line-up.")
    on_bracket_site: bool = Field(..., description="They're also dropped on the Challonge bracket.")


class DropRequest(BaseModel):
    reason: ForfeitReason = Field(ForfeitReason.WITHDREW, description="Recorded on forfeited matches.")
    forfeit_matches: bool = Field(True, description="Forfeit their head-to-head matches; false calls them off.")


class DropResultResponse(_Attrs):
    forfeited: int
    called_off: int
    removed_from: int
    on_bracket_site: bool


class EntrantResponse(_Attrs):
    id: int
    name: str
    status: Literal['manual', 'linked', 'suggested', 'unmatched']
    user: Optional[PersonResponse] = None
    suggested_user: Optional[PersonResponse] = Field(None, description="A likely member to confirm (start.gg only).")
    conflicting_user: Optional[PersonResponse] = Field(
        None, description="Matched by hand, but the entrant's Challonge account belongs to this member.",
    )


class EntrantListResponse(_Attrs):
    site: str
    label: str
    entrants: List[EntrantResponse]


class EntrantAssignRequest(BaseModel):
    user_id: int


class ConfirmedResponse(BaseModel):
    confirmed: int


class SyncResultResponse(_Attrs):
    tournament_id: int
    tournament_name: str
    source_label: str
    summary: str = Field(..., description="What changed, ready to print.")
    error: Optional[str] = None


class LinkRequest(BaseModel):
    site: Literal['challonge', 'startgg']
    bracket: str = Field(..., min_length=1, description="The bracket's link or id on that site.")


class SiteLabelResponse(BaseModel):
    site: str


class StageActionRequest(BaseModel):
    action: str = Field(..., description="The `stage_action.key` from the board row.")


class PhaseResponse(_Attrs):
    phase_id: str
    name: str
    state: Optional[str] = None


class StartggSettingsResponse(_Attrs):
    event_slug: Optional[str] = None
    event_name: Optional[str] = None
    bookable_phase_id: Optional[str] = Field(None, description="The phase players book from; null means every phase.")
    report_results: bool = Field(..., description="Results are written back to start.gg.")
    phases: List[PhaseResponse]


class StartggSettingsUpdate(BaseModel):
    bookable_phase_id: Optional[str] = Field(None, description="Send null for every phase. Omit to leave it.")
    report_results: Optional[bool] = Field(None, description="Omit to leave it.")


class RoundScheduleEntry(BaseModel):
    opens_at: Optional[datetime] = Field(None, description="When the stage opens for booking (UTC).")
    closes_at: Optional[datetime] = Field(None, description="When it closes (UTC).")
    daily_from: Optional[str] = Field(None, pattern=r'^\d{2}:\d{2}$', description="HH:MM on the community's clock.")
    daily_until: Optional[str] = Field(None, pattern=r'^\d{2}:\d{2}$', description="HH:MM on the community's clock.")
    best_of: Literal[1, 3, 5] = 1


class RoundScheduleUpdate(BaseModel):
    stages: dict[str, RoundScheduleEntry] = Field(
        ..., description="Every stage's window by stage key; a stage left out or blank has none.",
    )
