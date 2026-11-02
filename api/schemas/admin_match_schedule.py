"""Schemas for the app's admin Match Schedule: the board and one match's pane."""

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Named(BaseModel):
    id: int
    name: str


class BoardPlayer(BaseModel):
    id: Optional[int] = Field(None, description="The player's match-player id: what `POST /matches/{id}/result` names as the winner.")
    user_id: Optional[int] = None
    name: str
    station: Optional[str] = Field(None, description="The player's station, once assigned.")
    ada: bool = Field(False, description="Staff arranged an ADA accommodation for this player.")


class CrewPerson(BaseModel):
    id: Optional[int] = Field(None, description="The crew signup id, for `POST /crew/{crew_type}/{crew_id}/approval`.")
    name: str
    approved: bool
    with_name: Optional[str] = Field(None, alias='with', description="The partner a commentator was invited with.")

    model_config = {'populate_by_name': True}


class CrewRole(BaseModel):
    key: str = Field(..., description="`commentators`, `trackers` or `broadcasters`.")
    label: str
    count: str = Field(..., description="Signups against need, ready to print (`1/2`).")
    short: bool = Field(..., description="Fewer signups than the match needs, while that can still be fixed.")
    unapproved: bool = Field(..., description="Enough signups, but some wait for approval.")
    approvable: bool = Field(..., description="The match is still open for crew approval.")
    people: List[CrewPerson]


class LateTag(BaseModel):
    label: str
    note: Optional[str] = None


class Winner(BaseModel):
    id: int
    name: str


class NextStep(BaseModel):
    event: Optional[str] = Field(None, description="`seat`, `roll`, `start`, `winner` or `finish`; none while a seed rolls.")
    label: str
    primary: bool
    rolling: bool = False


class SiteResult(BaseModel):
    site: str = Field(..., description="`Challonge` or `start.gg`.")
    winner: Optional[str] = None
    winner_user_id: Optional[int] = None
    no_winner: bool = False


class HeldResult(BaseModel):
    reason: str
    label: str
    detail: str
    url: Optional[str] = None


class RowCaps(BaseModel):
    edit: bool = Field(..., description="Edit, delete, rewind or call off the match.")
    run: bool = Field(..., description="Check in, roll the seed, start, finish, assign stations.")
    confirm: bool = Field(..., description="Record, change or confirm the result.")
    stage: bool = Field(..., description="Put the match on a stage or take it off.")
    crew: bool = Field(..., description="Approve or decline crew signups.")


class BoardRow(BaseModel):
    id: int
    tournament_id: Optional[int] = None
    tournament: str = Field(..., description="The tournament's short name, else its name.")
    tournament_name: str
    bracket: Optional[str] = Field(None, description="The bracket stage the match is a game of.")
    game: Optional[int] = None
    best_of: Optional[int] = None
    set_label: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    until_at: Optional[datetime] = Field(None, description="When it finished, or while it runs, the estimated end.")
    until_estimated: bool = False
    overrun: bool = Field(False, description="A live match already past its estimated end.")
    state: str = Field(..., description="`Scheduled`, `Checked In`, `Started`, `Finished` or `Confirmed`.")
    status: str = Field(..., description="The line's status word, ready to print (`Late`, `To confirm`, `Played on Challonge`).")
    tone: str = Field(..., description="The status chip's tone: `neutral`, `info`, `live`, `pending`, `ok` or `cancelled`.")
    group: str
    players: List[BoardPlayer]
    placeholders: List[str] = Field(default_factory=list, description="Undecided sides, such as `Winner of QF1`.")
    stage_id: Optional[int] = None
    stage: Optional[str] = None
    stream_candidate: bool = False
    seat_line: Optional[str] = Field(None, description="Where the players sit, in a few words.")
    crew: List[CrewRole]
    late: List[LateTag]
    comments: Dict[str, int] = Field(default_factory=dict, description="Comment counts by `player` and `staff`.")
    winners: List[Winner] = Field(default_factory=list, description="The one-tap winner buttons on a live head-to-head.")
    pending_result: Optional[str] = Field(None, description="A result held behind Undo, as its label.")
    next_step: Optional[NextStep] = None
    seed_url: Optional[str] = None
    seed_code: Optional[str] = None
    seed_rolling: bool = False
    seed_error: Optional[str] = None
    is_racetime: bool = False
    played_on_site: Optional[SiteResult] = None
    racetime_held: Optional[HeldResult] = None
    needs_review: bool = False
    can: RowCaps


class BoardGroup(BaseModel):
    label: str
    folded_by_default: bool
    rows: List[BoardRow]


class BoardResponse(BaseModel):
    zone: str = Field(..., description="The IANA zone the days are cut on and times print in.")
    days: List[date] = Field(..., description="The event's days, for the day switch; empty for a long event.")
    tournaments: List[Named]
    stages: List[Named]
    scoped_tournaments: Optional[int] = Field(None, description="Set when the board shows only the viewer's own tournaments.")
    can_create: bool
    groups: List[BoardGroup]


class ChecklistItem(BaseModel):
    key: str
    label: str
    ready: Optional[bool] = Field(None, description="True or false for what a match needs; null for information only.")
    detail: str


class RewindTarget(BaseModel):
    step: str
    explanation: str


class MatchPaneResponse(BoardRow):
    zone: str
    description: Optional[str] = None
    state_since: Optional[datetime] = None
    checklist: List[ChecklistItem]
    rewind_to: List[RewindTarget]
    seed_dm_blocked: List[str]
    stage_url: Optional[str] = None
    ada_notes: Dict[str, str] = Field(default_factory=dict, description="Staff ADA notes by user id; withheld from proctors.")


class RewindRequest(BaseModel):
    to_step: str = Field(..., description="A step from the pane's `rewind_to`: `scheduled`, `checked_in`, `started` or `finished`.")


class CancelRequest(BaseModel):
    reason: str = Field('', max_length=500, description="Told to the players and crew.")
