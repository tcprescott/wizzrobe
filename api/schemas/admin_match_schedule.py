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


class RescheduleNeed(BaseModel):
    id: int = Field(..., description="The request, for `POST /reschedule-requests/{id}/approve` or `/decline`.")
    kind: str = Field(..., description="`reschedule` or `cancel`.")
    requested_by: str
    players: List[str]
    title: Optional[str] = None
    tournament: str
    scheduled_at: Optional[datetime] = None
    proposed_at: Optional[datetime] = None
    reason: str
    opponent_agreed: bool
    created_at: datetime
    clashes: List[str] = Field(..., description="Why the proposed time might not work. Approving anyway is allowed.")


class Need(BaseModel):
    kind: str = Field(..., description=(
        "`request` (a player asked to move or call off a match), `site` (the bracket site already has a "
        "result), `held` (the racetime room bot held a result), `result` (finished, not confirmed) or "
        "`late` (past its time and nobody checked in)."
    ))
    match_id: int = Field(..., description="The match; for anything but `request` it is a row on this board.")
    has_result: Optional[bool] = Field(None, description="For `result`: a winner is recorded.")
    request: Optional[RescheduleNeed] = None


class BoardResponse(BaseModel):
    zone: str = Field(..., description="The IANA zone the days are cut on and times print in.")
    days: List[date] = Field(..., description="The event's days, for the day switch; empty for a long event.")
    tournaments: List[Named]
    stages: List[Named]
    scoped_tournaments: Optional[int] = Field(None, description="Set when the board shows only the viewer's own tournaments.")
    can_create: bool
    groups: List[BoardGroup]
    needs_you: List[Need] = Field(..., description="Work waiting on you, in the order to show it.")


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
    seed_rule: Optional[str] = Field(None, description="Why the seed rolled what it did, such as `Rule 3: Top 8 → Open Hard`.")
    stage_url: Optional[str] = None
    ada_notes: Dict[str, str] = Field(default_factory=dict, description="Staff ADA notes by user id; withheld from proctors.")


class RewindRequest(BaseModel):
    to_step: str = Field(..., description="A step from the pane's `rewind_to`: `scheduled`, `checked_in`, `started` or `finished`.")


class CancelRequest(BaseModel):
    reason: str = Field('', max_length=500, description="Told to the players and crew.")


class FormTournament(BaseModel):
    id: int
    name: str
    enrolled: List[int] = Field(..., description="User ids enrolled in it: the Players picker offers these unless told to offer everyone.")
    players_per_match: Optional[int] = None


class FormValues(BaseModel):
    tournament_id: Optional[int] = None
    stage_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    player_ids: List[int]
    commentator_ids: List[int]
    tracker_ids: List[int]
    broadcaster_ids: List[int]
    title: str
    description: str
    comment: str
    is_stream_candidate: bool


class MatchFormResponse(BaseModel):
    match_id: Optional[int] = None
    synced_from_speedgaming: bool = Field(..., description="Its tournament, time and players come from SpeedGaming and can't be changed here.")
    tournaments: List[FormTournament]
    stages: List[Named]
    people: List[Named] = Field(..., description="Community members, for the player and crew pickers.")
    broadcasters: bool = Field(..., description="Show the Broadcasters picker.")
    values: FormValues


class MatchFormSave(BaseModel):
    tournament_id: int
    stage_id: Optional[int] = None
    scheduled_at: datetime = Field(..., description="When, as an ISO 8601 instant with an offset.")
    player_ids: List[int] = Field(default_factory=list, description="Empty holds the slot; the title shows until players are set.")
    commentator_ids: List[int] = Field(default_factory=list)
    tracker_ids: List[int] = Field(default_factory=list)
    broadcaster_ids: Optional[List[int]] = Field(None, description="Leave out to keep the broadcasters as they are.")
    title: str = Field('', max_length=255)
    description: str = Field('', max_length=5000)
    comment: str = Field('', max_length=2000)
    is_stream_candidate: bool = False


class SavedMatch(BaseModel):
    match_id: int


class TimeCheckRequest(BaseModel):
    tournament_id: int
    player_ids: List[int] = Field(default_factory=list)
    scheduled_at: datetime
    match_id: Optional[int] = Field(None, description="The match being moved, so it doesn't clash with itself.")
    source: Optional[str] = Field(None, description="For a bracket matchup: `bracket`, `challonge` or `startgg`.")
    key: Optional[int] = Field(None, description="The matchup's id within its source.")


class WindowWarning(BaseModel):
    message: str
    can_override: bool = Field(..., description="You may book outside it anyway (`allow_outside_round`).")


class TimeCheck(BaseModel):
    window: Optional[WindowWarning] = Field(None, description="The round's booking window this time falls outside of.")
    clashes: List[str] = Field(..., description="Why the time might not work. Booking anyway is allowed.")


class SuggestRequest(BaseModel):
    tournament_id: int
    player_ids: List[int] = Field(default_factory=list)
    match_id: Optional[int] = None
    source: Optional[str] = None
    key: Optional[int] = None


class Suggestion(BaseModel):
    scheduled_at: datetime


class LinkCandidate(BaseModel):
    id: int
    scheduled_at: Optional[datetime] = None
    stage: Optional[str] = None


class UnbookedItem(BaseModel):
    source: str = Field(..., description="`bracket`, `challonge` or `startgg`.")
    source_label: str
    key: int
    tournament_id: int
    tournament: str
    round_label: str
    players: List[str]
    player_ids: List[int]
    state: str = Field(..., description="`ready`, `needs_account` (a player has no Wizzrobe account) or `tbd` (players not decided yet).")
    unlinked: List[str]
    window_text: Optional[str] = None
    best_of: int
    games_booked: int
    link_candidates: List[LinkCandidate] = Field(..., description="Matches already on the schedule that look like this matchup's.")
    bracket_url: Optional[str] = None


class BookRequest(BaseModel):
    scheduled_at: datetime
    allow_outside_round: bool = Field(False, description="Book outside the round's window (staff and tournament admins).")


class LinkRequest(BaseModel):
    match_id: int
