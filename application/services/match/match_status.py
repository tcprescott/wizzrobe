"""One derived status vocabulary shared by the schedule, the bracket, and Discord.

Before this module there were three: ``Match`` had no enum at all (five nullable
timestamps read newest-first, in two places), ``BracketMatch`` had
:class:`~models.BracketMatchState`, and ``BracketMatchGame`` had
:class:`~models.BracketMatchGameState`. Each surface showed whichever it happened
to hold, so a bracket card said "Scheduled" for a match the schedule was calling
"In Progress".

:class:`MatchStatus` is the single vocabulary all three project into. It is
**derived, never stored** (docs/features/brackets.md): the
timestamps stay the source of truth and this module is a pure projection of them.

Pure and ORM-free, like its peers ``match_source_guard`` / ``match_request_guard``
— no queries, no NiceGUI — so services, the REST API, the Discord builders, and
the presentation layer can all call it on data they already hold.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional, Protocol, Sequence, runtime_checkable

from models import BracketMatchGameState, BracketMatchState, RaceRoomStatus


class MatchStatus(str, Enum):
    """Where one match — or one bracket matchup — currently stands.

    ``(str, Enum)`` like the model enums: render ``.value`` in f-strings, never
    the bare member.
    """

    PENDING = 'pending'
    UNSCHEDULED = 'unscheduled'
    SCHEDULED = 'scheduled'
    CHECKED_IN = 'checked_in'
    LIVE = 'live'
    AWAITING_RESULT = 'awaiting_result'
    COMPLETE = 'complete'
    CANCELLED = 'cancelled'
    NEEDS_RESCHEDULE = 'needs_reschedule'


@runtime_checkable
class MatchLike(Protocol):
    """The five timestamps :func:`resolve` reads off a ``Match``.

    A Protocol rather than the model so this module stays ORM-free and the truth
    table in ``tests/services/test_match_status.py`` can drive it with plain
    objects.
    """

    seated_at: Any
    started_at: Any
    finished_at: Any
    confirmed_at: Any


STATUS_LABELS = {
    MatchStatus.PENDING: 'Pending',
    MatchStatus.UNSCHEDULED: 'Unscheduled',
    MatchStatus.SCHEDULED: 'Scheduled',
    MatchStatus.CHECKED_IN: 'Checked In',
    MatchStatus.LIVE: 'Live',
    MatchStatus.AWAITING_RESULT: 'Awaiting result',
    MatchStatus.COMPLETE: 'Complete',
    MatchStatus.CANCELLED: 'Cancelled',
    MatchStatus.NEEDS_RESCHEDULE: 'Needs reschedule',
}

# Compared by value in filter chips, saved filters and Vue slots: don't rename.
LEGACY_STATE_LABELS = {
    MatchStatus.SCHEDULED: 'Scheduled',
    MatchStatus.CHECKED_IN: 'Checked In',
    MatchStatus.LIVE: 'Started',
    MatchStatus.AWAITING_RESULT: 'Finished',
    MatchStatus.COMPLETE: 'Confirmed',
}

STATUS_TONES = {
    MatchStatus.PENDING: 'neutral',
    MatchStatus.UNSCHEDULED: 'info',
    MatchStatus.SCHEDULED: 'info',
    MatchStatus.CHECKED_IN: 'imminent',
    MatchStatus.LIVE: 'live',
    MatchStatus.AWAITING_RESULT: 'done',
    MatchStatus.COMPLETE: 'settled',
    MatchStatus.CANCELLED: 'cancelled',
    MatchStatus.NEEDS_RESCHEDULE: 'attention',
}

_SERIES_RANK = (
    MatchStatus.LIVE,
    MatchStatus.CHECKED_IN,
    MatchStatus.AWAITING_RESULT,
    MatchStatus.SCHEDULED,
)


def resolve(
    *,
    match: Optional[MatchLike],
    game_state: Optional[BracketMatchGameState] = None,
    bracket_match_state: Optional[BracketMatchState] = None,
    room_status: Optional[RaceRoomStatus] = None,
) -> MatchStatus:
    """The status of one game / one scheduled match.

    Precedence, most-specific first:

    1. **A settled or cancelled game state wins over the match timestamps.**
       Staff can force a result the ``Match`` never recorded (``report_result``
       on the bracket), and a clinched series cancels games whose ``Match`` is
       still sitting there unconfirmed. In both cases the game row is the later,
       truer statement.
    2. **The match timestamps, newest-first** — the ladder
       ``MatchDisplayService._get_match_state`` has always walked.
    3. **The racetime room is a tiebreaker only.** An ``IN_PROGRESS`` room on a
       match with no ``started_at`` still reads ``LIVE``: the room is the truthier
       signal for a viewer, and the timestamp lags the bot by however long the
       result callback takes.

    ``match`` is ``None`` for a matchup with no game booked yet, which is why
    ``bracket_match_state`` is consulted last rather than first — it is the
    fallback, not the authority (D2: the schedule is the hub).
    """
    if game_state == BracketMatchGameState.COMPLETE:
        return MatchStatus.COMPLETE
    if game_state == BracketMatchGameState.CANCELLED:
        return MatchStatus.CANCELLED

    if match is not None:
        if getattr(match, 'confirmed_at', None):
            return MatchStatus.COMPLETE
        if getattr(match, 'finished_at', None):
            return MatchStatus.AWAITING_RESULT
        if getattr(match, 'started_at', None):
            return MatchStatus.LIVE
        if room_status == RaceRoomStatus.IN_PROGRESS:
            return MatchStatus.LIVE
        if getattr(match, 'seated_at', None):
            return MatchStatus.CHECKED_IN
        return MatchStatus.SCHEDULED

    if bracket_match_state == BracketMatchState.COMPLETE:
        return MatchStatus.COMPLETE
    if bracket_match_state == BracketMatchState.OPEN:
        return MatchStatus.UNSCHEDULED
    return MatchStatus.PENDING


def resolve_matchup(
    *,
    bracket_match_state: Optional[BracketMatchState],
    game_statuses: Sequence[MatchStatus] = (),
    series_underway: bool = False,
) -> MatchStatus:
    """The status of a whole matchup, from its games' already-resolved statuses.

    A COMPLETE or PENDING matchup is exactly that. An OPEN one takes the most
    interesting status among its live games (:data:`_SERIES_RANK`) — a Bo3 whose
    game 2 is being raced reads ``LIVE`` even though game 1 is ``COMPLETE``.

    ``NEEDS_RESCHEDULE`` is the OPEN matchup with **nothing bookable pending and
    a series already underway**: game 1 is played, game 2 is not booked, so the
    matchup is waiting on someone rather than merely un-started. A matchup whose
    only game was released back (U3a) on a best-of-1 has no surviving trace to
    distinguish it from never-booked, and correctly reads ``UNSCHEDULED``; its
    entrants are told by DM either way (U5).
    """
    if bracket_match_state == BracketMatchState.COMPLETE:
        return MatchStatus.COMPLETE
    if bracket_match_state != BracketMatchState.OPEN:
        return MatchStatus.PENDING

    live = set(game_statuses)
    for status in _SERIES_RANK:
        if status in live:
            return status
    if series_underway:
        return MatchStatus.NEEDS_RESCHEDULE
    return MatchStatus.UNSCHEDULED


def label(status: MatchStatus) -> str:
    """Display label for a status."""
    return STATUS_LABELS.get(status, status.value.replace('_', ' ').title())


def tone(status: MatchStatus) -> str:
    """Semantic colour name for a status (see :data:`STATUS_TONES`)."""
    return STATUS_TONES.get(status, 'neutral')


def legacy_label(status: MatchStatus) -> str:
    """The schedule table's historical state string for a status."""
    return LEGACY_STATE_LABELS.get(status, 'Scheduled')


def is_live(statuses: Iterable[MatchStatus]) -> bool:
    """Whether any of ``statuses`` means "being played right now"."""
    return MatchStatus.LIVE in set(statuses)


def has_recorded_result(players: Iterable[Any]) -> bool:
    """Whether a result is on the board, from an already-loaded ``players`` list.

    One predicate, two callers, deliberately: ``MatchScheduleService.confirm_match``
    refuses a match without a result, and ``MatchDisplayService`` puts the same
    answer on every row as ``has_result`` so the boards stop offering a Confirm
    whose only outcome is that refusal. Two spellings of "is there a result" drift
    into a board that hides a Confirm the service would have accepted.

    Reads *any* rank rather than a rank of 1, because a racetime sync maps
    ``entrant.place`` straight through (``race_room_service._map_results``): a match
    whose place-1 entrant has no linked user carries only a rank 2, and confirming
    it is legitimate.
    """
    return any(getattr(p, 'finish_rank', None) for p in players)


WINNER_MAX_PLAYERS = 4


def closes_without_winner(players: Iterable[Any]) -> bool:
    """Whether a match is an open race, from an already-loaded ``players`` list.

    ``MatchScheduleService.finish_match`` confirms such a match as it finishes
    it, and ``confirm_match`` accepts it with no result recorded.
    """
    return len(list(players)) > WINNER_MAX_PLAYERS
