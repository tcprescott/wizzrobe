"""Bracket engine contract — pure structural types, no ORM (docs/features/brackets.md).

An engine turns a seeded field + a validated config into a description of a
bracket's match graph. It never touches the database: it returns plain
dataclasses that :class:`~application.services.bracket_service.BracketService`
maps onto ``BracketEntry`` / ``BracketMatch`` rows and persists. There are two
engine shapes, both registered under the ``'bracket_format'`` strategy kind:

* **Generative** (single/double elimination, round robin) — ``generate()`` emits
  the *entire* match graph up front, with ``winner_to`` / ``loser_to`` pointers
  and seed placements. Elimination advancement afterwards is plain
  pointer-following over the persisted rows; the engine is never re-run.
* **Pairing** (Swiss) — ``pair_round()`` is a stateless per-round call: given the
  current standings and pairing history it returns the next round's pairings,
  which the service persists as new ``BracketMatch`` rows.

Seed indices are **1-based positions into the seeded entrant list** (seed 1 = top
seed). The engine speaks only in these indices and ``(round, position, slot)``
coordinates; the service owns the mapping to real entry ids.

Round convention: positive rounds are the winners bracket; **negative rounds are
the losers bracket** (start.gg's convention), used by double elimination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class Slot:
    """A destination slot in the match graph: which entry-slot of which match.

    ``slot`` is 1 or 2 (``entry1`` / ``entry2``). ``(round, position)`` addresses
    the target match uniquely within a stage.
    """

    round: int
    position: int
    slot: int


@dataclass
class GeneratedMatch:
    """One node of a generated match graph (pre-persistence).

    ``entry1_seed`` / ``entry2_seed`` are the 1-based seeds *initially* placed in
    this match at generation (``None`` for a slot that a feeder fills later, or a
    structural bye). ``winner_to`` / ``loser_to`` say where this match's
    winner/loser flow. ``is_bye`` marks a walkover (exactly one real entrant, the
    other slot a structural bye) that the service auto-completes without a played
    ``Match``. ``label`` is optional display text ("Grand Final", "Losers R1").
    ``is_reset`` marks the double-elimination grand-final reset match, which the
    service persists but activates only if the losers-bracket entrant wins the
    first grand final.
    """

    round: int
    position: int
    entry1_seed: Optional[int] = None
    entry2_seed: Optional[int] = None
    winner_to: Optional[Slot] = None
    loser_to: Optional[Slot] = None
    group_number: Optional[int] = None
    is_bye: bool = False
    is_reset: bool = False
    label: Optional[str] = None


@runtime_checkable
class GenerativeEngine(Protocol):
    """An engine that emits a complete match graph from a seeded field."""

    def generate(self, num_entries: int, config: dict) -> List[GeneratedMatch]:
        ...


@dataclass
class PairingPlayer:
    """One player's state handed to a pairing engine for the next round.

    ``ref`` is an opaque caller-chosen identifier (the service passes the
    ``BracketEntry`` id). ``points`` drives score-group pairing; ``opponents`` is
    the set of refs already played (no rematch while a legal pairing exists);
    ``received_bye`` / ``can_bye`` gate bye assignment; ``dropped`` players are
    excluded from new pairings.
    """

    ref: int
    points: float = 0.0
    opponents: frozenset = field(default_factory=frozenset)
    received_bye: bool = False
    can_bye: bool = True
    dropped: bool = False


@runtime_checkable
class PairingEngine(Protocol):
    """An engine that pairs one round at a time from live standings (Swiss)."""

    def pair_round(
        self, players: List[PairingPlayer], config: dict
    ) -> List[Tuple[int, Optional[int]]]:
        """Return ``(ref1, ref2)`` pairings; ``ref2 is None`` denotes a bye."""
        ...


def standard_seeding(size: int) -> List[int]:
    """Standard single-elimination seed order for a power-of-two ``size``.

    Returns the seed number occupying each bracket slot, so consecutive pairs
    ``(order[0], order[1]), (order[2], order[3]), …`` are the first-round
    matchups. Seeds 1 and 2 land on opposite halves, 1 vs the lowest seed, etc.
    e.g. ``standard_seeding(4) == [1, 4, 2, 3]``;
    ``standard_seeding(8) == [1, 8, 4, 5, 2, 7, 3, 6]``.
    """
    if size < 1 or (size & (size - 1)) != 0:
        raise ValueError(f"size must be a power of two, got {size}")
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        nxt: List[int] = []
        for s in order:
            nxt.append(s)
            nxt.append(n + 1 - s)
        order = nxt
    return order


def next_power_of_two(n: int) -> int:
    """Smallest power of two ``>= n`` (``n >= 1``)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    p = 1
    while p < n:
        p *= 2
    return p
