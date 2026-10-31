"""Shared standings & tiebreakers — pure functions over opaque entrant refs.

Round robin uses this now; Swiss and stage-completion ranking will consume the
same API to write each entry's ``final_rank`` (docs/features/brackets.md). There is
no ORM here: entrants are plain ``int`` refs (the service passes ``BracketEntry``
ids) and results are plain :class:`ResultRow` records. The module computes match
points and a configurable chain of tiebreakers, then assigns 1-based competition
ranks, leaving genuinely-unresolved ties sharing a rank and pointing at each
other via :attr:`Standing.tied_with` (the service surfaces those for a staff
override).

Tiebreakers (config-selectable, applied in the listed order until a tie breaks):

* ``buchholz`` — strength of schedule: the sum of each played opponent's total
  match points (byes contribute no opponent).
* ``omw`` — opponent match-win percentage (MTG's OMW%): the average, over
  opponents actually played, of each opponent's ``wins / matches_played``, with
  each opponent's percentage floored at :attr:`StandingsConfig.omw_floor`. Byes
  count as a win and a played match when computing an opponent's percentage, but
  a bye is not itself an opponent and is excluded from the average.
* ``head_to_head`` — evaluated *within a tied group only*: order by the number of
  wins each tied member has against the rest of the tied set. Decisive only when
  those internal results yield distinct win counts; a cycle (everyone 1-1) leaves
  the group tied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

KNOWN_TIEBREAKERS: Tuple[str, ...] = ('buchholz', 'omw', 'head_to_head')


@dataclass(frozen=True)
class ResultRow:
    """One recorded match outcome between opaque entrant refs.

    ``ref2 is None`` denotes a **bye** awarded to ``ref1`` (``winner`` is then
    ``ref1``). With both refs set, ``winner`` is the winning ref, or ``None`` for
    a draw.
    """

    ref1: int
    ref2: Optional[int] = None
    winner: Optional[int] = None


@dataclass(frozen=True)
class StandingsConfig:
    """Points values and the ordered tiebreaker chain."""

    win_points: float = 1.0
    draw_points: float = 0.5
    loss_points: float = 0.0
    bye_points: float = 1.0
    tiebreakers: Tuple[str, ...] = ('buchholz', 'omw', 'head_to_head')
    omw_floor: float = 1.0 / 3.0


@dataclass
class Standing:
    """One entrant's computed placement.

    ``rank`` is 1-based competition ranking (equal rank for entrants still tied
    after every configured tiebreaker; the next distinct rank skips the tied
    count, i.e. ``1, 2, 2, 4``). ``tied_with`` lists the other refs sharing this
    rank (empty when the entrant is alone at its rank).
    """

    ref: int
    points: float
    tiebreakers: Dict[str, float]
    rank: int
    tied_with: Tuple[int, ...] = ()
    wins: int = 0
    draws: int = 0
    losses: int = 0
    byes: int = 0


def standings_config_from(config: Optional[Dict[str, Any]]) -> StandingsConfig:
    """Build a :class:`StandingsConfig` from a stage's stored ``Bracket.config``.

    The one place the blob is read into scoring parameters, shared by the ranking
    pass and the read-only views. Unknown tiebreaker keys are **dropped** rather
    than passed through: ``bracket_config`` rejects them on write, so only a row
    persisted before that validation existed can carry one, and letting it reach
    :func:`compute_standings` would raise on every read of a stage that can no
    longer be edited (config edits are DRAFT-only).
    """
    config = config or {}
    kwargs: Dict[str, Any] = {}
    for key in ('win_points', 'draw_points', 'loss_points', 'bye_points', 'omw_floor'):
        value = config.get(key)
        if value is not None:
            kwargs[key] = value
    tiebreakers = config.get('tiebreakers')
    if tiebreakers:
        known = tuple(tb for tb in tiebreakers if tb in KNOWN_TIEBREAKERS)
        if known:
            kwargs['tiebreakers'] = known
    return StandingsConfig(**kwargs)


_GLOBAL_TIEBREAKERS = ('buchholz', 'omw')


@dataclass
class _Record:
    ref: int
    points: float = 0.0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    byes: int = 0
    opponents: List[int] = field(default_factory=list)  # played opponents (byes excluded)

    @property
    def played(self) -> int:
        # Byes count as a played match for match-win-% purposes (MTG convention).
        return self.wins + self.draws + self.losses + self.byes

    @property
    def match_win_pct(self) -> float:
        p = self.played
        return (self.wins + self.byes) / p if p else 0.0


def compute_standings(
    refs: List[int],
    results: List[ResultRow],
    config: StandingsConfig,
) -> List[Standing]:
    """Rank ``refs`` from ``results`` under ``config``.

    Every ref in ``refs`` gets exactly one :class:`Standing`; refs with no
    results place last on zero points. Results referencing a ref absent from
    ``refs`` raise :class:`ValueError`. The output is ordered by ``rank`` then
    ``ref`` (deterministic). Ties unresolved after all configured tiebreakers
    share a ``rank`` and list one another in ``tied_with``.
    """
    ref_set = set(refs)
    if len(ref_set) != len(refs):
        raise ValueError("refs must be unique")

    records: Dict[int, _Record] = {r: _Record(ref=r) for r in refs}

    def rec(ref: int) -> _Record:
        if ref not in records:
            raise ValueError(f"result references unknown ref {ref!r}")
        return records[ref]

    for row in results:
        r1 = rec(row.ref1)
        if row.ref2 is None:
            if row.winner not in (None, row.ref1):
                raise ValueError("a bye's winner must be ref1 (or None)")
            r1.byes += 1
            r1.points += config.bye_points
            continue
        if row.ref1 == row.ref2:
            raise ValueError("a result cannot pair a ref against itself")
        r2 = rec(row.ref2)
        r1.opponents.append(row.ref2)
        r2.opponents.append(row.ref1)
        if row.winner is None:
            r1.draws += 1
            r2.draws += 1
            r1.points += config.draw_points
            r2.points += config.draw_points
        elif row.winner == row.ref1:
            r1.wins += 1
            r2.losses += 1
            r1.points += config.win_points
            r2.points += config.loss_points
        elif row.winner == row.ref2:
            r2.wins += 1
            r1.losses += 1
            r2.points += config.win_points
            r1.points += config.loss_points
        else:
            raise ValueError("winner must be one of the paired refs, or None")

    buchholz = {
        r: sum(records[o].points for o in records[r].opponents) for r in refs
    }

    def omw(ref: int) -> float:
        opps = records[ref].opponents
        if not opps:
            return 0.0
        return sum(
            max(records[o].match_win_pct, config.omw_floor) for o in opps
        ) / len(opps)

    omw_values = {r: omw(r) for r in refs}

    tb_values: Dict[str, Dict[int, float]] = {
        'buchholz': buchholz,
        'omw': omw_values,
    }

    def h2h_scores(group: List[int]) -> Dict[int, int]:
        member = set(group)
        scores = {r: 0 for r in group}
        for row in results:
            if (
                row.ref2 is not None
                and row.winner is not None
                and row.ref1 in member
                and row.ref2 in member
            ):
                scores[row.winner] += 1
        return scores

    ordered_tbs = tuple(config.tiebreakers)

    def order_group(group: List[int], tbs: Tuple[str, ...]) -> List[List[int]]:
        if len(group) <= 1 or not tbs:
            return [list(group)]
        tb, rest = tbs[0], tbs[1:]
        if tb == 'head_to_head':
            scores: Dict[int, float] = dict(h2h_scores(group))
        elif tb in tb_values:
            scores = {r: tb_values[tb][r] for r in group}
        else:
            raise ValueError(f"unknown tiebreaker {tb!r}")
        buckets: List[List[int]] = []
        for score in sorted(set(scores.values()), reverse=True):
            sub = [r for r in group if scores[r] == score]
            buckets.extend(order_group(sub, rest))
        return buckets

    all_buckets: List[List[int]] = []
    point_totals = sorted({records[r].points for r in refs}, reverse=True)
    for pts in point_totals:
        group = [r for r in refs if records[r].points == pts]
        all_buckets.extend(order_group(group, ordered_tbs))

    standings: List[Standing] = []
    rank = 1
    for bucket in all_buckets:
        tied = tuple(sorted(bucket))
        for ref in bucket:
            rc = records[ref]
            tb_out = {
                name: tb_values[name][ref]
                for name in ordered_tbs
                if name in _GLOBAL_TIEBREAKERS
            }
            standings.append(
                Standing(
                    ref=ref,
                    points=rc.points,
                    tiebreakers=tb_out,
                    rank=rank,
                    tied_with=tuple(t for t in tied if t != ref),
                    wins=rc.wins,
                    draws=rc.draws,
                    losses=rc.losses,
                    byes=rc.byes,
                )
            )
        rank += len(bucket)

    standings.sort(key=lambda s: (s.rank, s.ref))
    return standings


__all__ = [
    'KNOWN_TIEBREAKERS',
    'ResultRow',
    'Standing',
    'StandingsConfig',
    'compute_standings',
    'standings_config_from',
]
