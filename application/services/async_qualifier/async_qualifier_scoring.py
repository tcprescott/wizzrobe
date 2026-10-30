"""Async-qualifier par + score + leaderboard math (PR 9).

Pure, DB-free functions ported from SahasrahBot's async-tournament scoring so the
formulas are unit-testable in isolation; :class:`AsyncQualifierService` assembles
the inputs from repositories and applies the results.

- **par** = mean of the ``sample_size`` fastest *finished + approved* runs on a
  permalink (whole seconds).
- **score** = ``clamp(0, 105, (2 - elapsed/par) * 100)`` — 100 at par, 0 at twice
  par, capped at 105 for a run ~5% under par.
- **leaderboard** = per-pool ``runs_per_pool`` slots; a user's best scores fill
  their slots, unfilled slots count 0 (``actual``); a separate ``estimate``
  projects the user's mean realized-slot score across all slots (finished runs
  only, so a partial entrant isn't understated). No tie-break — Python's stable
  sort preserves the provided (deterministic) input order.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Collection, Dict, List, Optional, Sequence

# Score bounds and the reference multipliers behind ``(2 - elapsed/par) * 100``.
SCORE_MIN = 0.0
SCORE_MAX = 105.0
# Default number of fastest approved runs averaged into a permalink's par. Read
# from ``AsyncQualifier.config['par_sample_size']`` when set; this is the
# fallback so a freshly-authored qualifier scores sensibly with no config.
DEFAULT_PAR_SAMPLE_SIZE = 5


def compute_par(finished_elapsed_seconds: Sequence[int], sample_size: int = DEFAULT_PAR_SAMPLE_SIZE) -> Optional[int]:
    """Mean (rounded) of the ``sample_size`` fastest finished times, or None.

    ``finished_elapsed_seconds`` need not be pre-sorted. Returns None when there
    are no finished runs yet (par is undefined, so runs stay unscored).
    """
    times = sorted(t for t in finished_elapsed_seconds if t is not None and t > 0)
    if not times:
        return None
    n = max(1, sample_size)
    fastest = times[:n]
    return round(sum(fastest) / len(fastest))


def compute_score(elapsed_seconds: Optional[int], par_seconds: Optional[int]) -> Optional[float]:
    """``clamp(0, 105, (2 - elapsed/par) * 100)``; None if not computable."""
    if not elapsed_seconds or not par_seconds or elapsed_seconds <= 0 or par_seconds <= 0:
        return None
    raw = (2 - elapsed_seconds / par_seconds) * 100
    return max(SCORE_MIN, min(SCORE_MAX, raw))


class ScoreBand(str, Enum):
    """A score coarsened to three outcomes, for the active-window lockdown.

    An exact score is exactly solvable for par: ``score = (2 - elapsed/par) * 100``
    rearranges to ``par = elapsed / (2 - score/100)``, and the runner knows their
    own elapsed. Publishing it during the window hands every runner the par the
    lockdown exists to hide — measured on the real fixture, ``elapsed 5,400 s ·
    score 100.00`` solves to par 5,400 s exactly.

    The band still bounds par (``NEAR`` means within the tolerance either way),
    which is a deliberate, accepted trade: the runner learns roughly where they
    stand without being handed the number.
    """

    UNDER = 'under'   # comfortably faster than par
    NEAR = 'near'     # within the tolerance of par, either side
    ABOVE = 'above'   # slower than par


# The fast side is already banded by the ``SCORE_MAX`` cap — every run 5% or more
# under par scores exactly 105 — so the slow edge mirrors it rather than inventing
# a second tolerance.
NEAR_PAR_FLOOR = 2 * 100 - SCORE_MAX


def score_band(score: Optional[float]) -> Optional[ScoreBand]:
    """Which band a score falls in, or None when there is no score to band."""
    if score is None:
        return None
    if score >= SCORE_MAX:
        return ScoreBand.UNDER
    if score >= NEAR_PAR_FLOOR:
        return ScoreBand.NEAR
    return ScoreBand.ABOVE


@dataclass(frozen=True)
class ScoredRun:
    """One run's contribution to a user's leaderboard total.

    Not only an approved finisher. A slot the runner **spent** on a forfeit, an
    expiry, a rejection or a disqualification is a realised zero: they cannot
    refill it, so it counts against them exactly as if it had scored nothing.
    The service decides which runs qualify; this module only sees the score.
    """

    user_id: int
    username: str
    pool_id: int
    score: float


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int              # competition rank: equal totals share it, then it skips
    user_id: int
    username: str
    actual: float          # realized total: filled slots + zeros for unfilled
    estimate: float        # projected total at the user's mean realized score
    slots_filled: int
    slots_total: int


def build_leaderboard(
    *,
    pool_ids: Sequence[int],
    runs_per_pool: int,
    scored_runs: Sequence[ScoredRun],
    open_pool_ids: Optional[Collection[int]] = None,
) -> List[LeaderboardEntry]:
    """Rank users by ``actual`` (desc), emitting a competition rank.

    Per user and pool, the top ``runs_per_pool`` scores fill that pool's slots;
    unfilled slots score 0 in ``actual``. ``estimate`` fills every slot at the
    user's mean realized-slot score, so it never understates a partial entrant.
    Pass ``scored_runs`` in a deterministic order (e.g. sorted by username) so
    tie ordering is stable.

    ``open_pool_ids`` are the pools **any** entrant could fill — in practice the
    ones holding a self-paced permalink. A pool outside that set contributes slots
    only to the people who actually have a run in it, because a live-race-only
    pool is not a slot a self-paced runner failed to fill; it is a slot they were
    never offered. Counting it for everyone inflated both ``slots_total`` and
    ``estimate`` for the whole field. Omit it and every pool counts for everyone,
    which is the right answer when a qualifier has no live races.

    **Rank is emitted here** rather than inferred from position by each caller.
    Two pages and the MCP tool each derived it from an ``enumerate`` index and the
    REST schema had no rank at all — four derivations of one number, which is how
    a three-way tie came to render 1, 2, 3 in alphabetical order.
    """
    per_pool = max(1, runs_per_pool)
    pool_id_set = set(pool_ids)
    # None means "every pool is open to everyone" — the no-live-races case.
    open_ids = pool_id_set if open_pool_ids is None else (set(open_pool_ids) & pool_id_set)

    # Preserve first-seen order for stable tie handling.
    order: List[int] = []
    names: Dict[int, str] = {}
    by_user_pool: Dict[int, Dict[int, List[float]]] = {}
    for run in scored_runs:
        if run.pool_id not in pool_id_set:
            continue
        if run.user_id not in by_user_pool:
            by_user_pool[run.user_id] = {}
            order.append(run.user_id)
            names[run.user_id] = run.username
        by_user_pool[run.user_id].setdefault(run.pool_id, []).append(run.score)

    entries: List[LeaderboardEntry] = []
    for user_id in order:
        pools = by_user_pool[user_id]
        # This entrant's denominator: every open pool, plus any closed pool they
        # actually raced in.
        countable = [p for p in pool_ids if p in open_ids or p in pools]
        actual = 0.0
        filled = 0
        for pool_id in countable:
            top = sorted(pools.get(pool_id, []), reverse=True)[:per_pool]
            actual += sum(top)
            filled += len(top)
        slots_total = len(countable) * per_pool
        estimate = (actual / filled) * slots_total if filled else 0.0
        entries.append(LeaderboardEntry(
            rank=0,   # assigned below, once the field is ordered
            user_id=user_id,
            username=names[user_id],
            actual=round(actual, 2),
            estimate=round(estimate, 2),
            slots_filled=filled,
            slots_total=slots_total,
        ))

    entries.sort(key=lambda e: e.actual, reverse=True)
    return _with_competition_ranks(entries)


def _with_competition_ranks(entries: List[LeaderboardEntry]) -> List[LeaderboardEntry]:
    """Stamp 1, 1, 3 rather than 1, 2, 3 onto an already-ordered board.

    Equal totals share a rank and the next distinct total skips past them, so the
    rank a player reads is the number of people genuinely ahead of them plus one.
    """
    ranked: List[LeaderboardEntry] = []
    last_actual: Optional[float] = None
    last_rank = 0
    for position, entry in enumerate(entries, start=1):
        if last_actual is None or entry.actual != last_actual:
            last_rank = position
            last_actual = entry.actual
        ranked.append(replace(entry, rank=last_rank))
    return ranked
