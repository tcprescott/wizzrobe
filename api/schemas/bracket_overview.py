"""The full-bracket payload: ``GET /tournaments/{id}/bracket`` and MCP ``get_tournament_bracket``.

One shape for native, start.gg and Challonge brackets, read from
``BracketOverviewService``'s dataclasses (``from_attributes``).
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

_SET_ID = (
    "The matchup's Wizzrobe id: a native bracket match, a start.gg set or a Challonge match row, "
    "the same `key` the unbooked-matchups list and the booking tools use."
)


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BracketSlotResponse(_Base):
    entrant_id: Optional[int] = Field(None, description="The entrant in this side (see `entrants`). Null while undecided.")
    name: Optional[str] = Field(None, description="The entrant's name. Null while undecided.")
    user_id: Optional[int] = Field(
        None, description="The entrant's Wizzrobe account. Only Staff and this tournament's admins get it; null otherwise.",
    )
    score: Optional[int] = Field(
        None, description="This side's score. Challonge's mirror has no score line, so there it comes from a confirmed booking.",
    )
    placeholder: Optional[str] = Field(None, description='What fills an undecided side, like "Winner of A3".')
    from_set_id: Optional[int] = Field(None, description="The set this side is fed from.")
    from_outcome: Optional[str] = Field(None, description="`winner` or `loser`: which result of `from_set_id` fills this side.")


class BracketFeedResponse(_Base):
    set_id: int = Field(..., description="The set the result moves on to.")
    slot: Optional[int] = Field(None, description="Which side of that set (1 or 2).")


class BracketBookingResponse(_Base):
    match_id: int = Field(..., description="The Wizzrobe match booked for this set (use `get_match` for detail).")
    status: str = Field(..., description="The match's status, e.g. `scheduled`, `live`, `awaiting_result`, `complete`.")
    scheduled_at: Optional[datetime] = Field(None, description="When it's booked for (UTC).")
    game: Optional[str] = Field(None, description='"Game 2 of 3" for a game of a best-of set.')
    stage: Optional[str] = Field(None, description="The stage (stream or table) it's on.")


class BracketSetResponse(_Base):
    id: int = Field(..., description=_SET_ID)
    source_id: Optional[str] = Field(None, description="The set's id on start.gg or Challonge. Null for native.")
    identifier: Optional[str] = Field(None, description='The set\'s short label, like "A3" (native: "round.position").')
    round: Optional[int] = Field(None, description="Round number; negative is a losers round on start.gg and Challonge.")
    round_label: str = Field(..., description='Round name, like "Winners Semi-Final".')
    state: str = Field(..., description="`pending` (a side is undecided), `open` (playable) or `complete`.")
    best_of: Optional[int] = Field(
        None, description="Games in the set. Null on start.gg and Challonge until a game is booked.",
    )
    slots: List[BracketSlotResponse] = Field(..., description="The two sides, in order.")
    winner_entrant_id: Optional[int] = Field(None, description="The winning entrant, once complete.")
    forfeit: bool = Field(False, description="Settled by forfeit (native only).")
    completed_at: Optional[datetime] = Field(None, description="When it was completed (start.gg only, UTC).")
    winner_to: Optional[BracketFeedResponse] = Field(None, description="Where the winner goes next.")
    loser_to: Optional[BracketFeedResponse] = Field(None, description="Where the loser goes next.")
    bookings: List[BracketBookingResponse] = Field([], description="Wizzrobe matches booked for this set, one per game.")


class BracketStandingResponse(_Base):
    entrant_id: int
    name: str
    placement: Optional[int] = Field(None, description="Placement or rank. Null until the site or stage sets one.")
    wins: int = 0
    losses: int = 0
    draws: int = 0
    points: Optional[float] = Field(None, description="Standings points (native round robin and Swiss only).")


class BracketPoolResponse(_Base):
    id: Optional[int] = Field(None, description="The pool's row id (start.gg). Null where a phase has one implicit pool.")
    name: str = Field(..., description='Pool name, like "A1" or "Group 2".')
    format: Optional[str] = None
    complete: bool = False
    sets_total: int = Field(0, description="Sets in the whole pool, whatever the filters dropped.")
    sets_complete: int = 0
    standings: List[BracketStandingResponse] = Field(
        [], description="Placement order. Challonge has none mirrored, so it's tallied from completed sets.",
    )
    sets: List[BracketSetResponse] = []


class BracketPhaseResponse(_Base):
    id: int = Field(..., description="Phase id: the native bracket stage id, the start.gg phase row id, or 1/2 for Challonge's group/final stage.")
    source_id: Optional[str] = Field(None, description="The phase's id on start.gg.")
    name: str
    order: int
    format: Optional[str] = None
    state: Optional[str] = None
    bookable: Optional[bool] = Field(None, description="start.gg: whether players book this phase's sets in Wizzrobe.")
    pools: List[BracketPoolResponse] = []


class BracketEntrantSummary(_Base):
    id: int
    name: str
    user_id: Optional[int] = Field(None, description="Wizzrobe account. Staff and this tournament's admins only.")
    status: Optional[str] = None


class TournamentBracketResponse(_Base):
    tournament_id: int
    tournament: str
    source: str = Field(..., description="`native`, `startgg` or `challonge`.")
    url: Optional[str] = Field(None, description="The bracket's page on its site (a Wizzrobe path for native).")
    last_synced_at: Optional[datetime] = Field(None, description="When the start.gg or Challonge mirror last synced (UTC).")
    filtered: bool = Field(False, description="True when filters trimmed the phases, pools or sets.")
    phases: List[BracketPhaseResponse]
    entrants: List[BracketEntrantSummary]
