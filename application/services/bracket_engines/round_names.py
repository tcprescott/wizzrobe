"""Round naming — "Semifinals", "Losers Round 2", "Grand Finals (Reset)".

Pure and ORM-free, alongside :mod:`standings` (the established home for bracket
logic shared beyond one caller). This lived in ``theme/brackets/layout.py``,
which meant **only the rendered bracket could name a round** — a service, a
Discord DM, or a REST payload had a round *number* and no way to turn it into
the word a player uses. That is why the matchup-ready DM (U5) needs this module
before it can be written; ``layout.py`` now imports :func:`round_label` from here
and re-exports it, so the card layer is unchanged.

Naming needs three facts beyond the round number — which round is the winners
final, how deep the losers bracket runs, and which matches are the grand final
and its reset — and all three are structural. :func:`round_names` derives them
from the light :class:`RoundNode` projection of a stage's graph and returns the
whole ``{round_number: name}`` map in one pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class RoundNode:
    """The projection of a ``BracketMatch`` this module needs.

    Both progression pointers, unlike the layout walker's ``MatchNode`` (which
    draws winner links only): the grand final is located structurally *by* its
    incoming loser link.
    """

    id: int
    round: int
    winner_to_id: Optional[int] = None
    loser_to_id: Optional[int] = None


def round_label(
    round_number: int,
    *,
    is_grand_final: bool = False,
    is_reset: bool = False,
    max_winners_round: Optional[int] = None,
    max_losers_magnitude: Optional[int] = None,
    double_elim: bool = False,
) -> str:
    """Human round name from the round number and its role.

    Winners rounds count down to Semifinals/Final (Winners Finals for double
    elim, where a grand final follows); losers rounds are "Losers Round N" up to
    "Losers Finals". The finals columns are named explicitly. ``double_elim`` is
    an explicit signal so the winners side is named correctly even for a
    2-entrant double elim, which has no losers bracket (``max_losers_magnitude``
    is then ``None``).
    """
    if is_reset:
        return 'Grand Finals (Reset)'
    if is_grand_final:
        return 'Grand Finals'
    is_de = double_elim or max_losers_magnitude is not None
    if round_number < 0:
        magnitude = abs(round_number)
        if max_losers_magnitude is not None and magnitude == max_losers_magnitude:
            return 'Losers Finals'
        return f'Losers Round {magnitude}'
    if max_winners_round is not None:
        if round_number == max_winners_round:
            return 'Winners Finals' if is_de else 'Final'
        if round_number == max_winners_round - 1:
            return 'Winners Semifinals' if is_de else 'Semifinals'
        if round_number == max_winners_round - 2 and not is_de:
            return 'Quarterfinals'
    prefix = 'Winners Round ' if is_de else 'Round '
    return f'{prefix}{round_number}'


def detect_finals_ids(
    nodes: List[RoundNode],
) -> Tuple[Optional[int], Optional[int]]:
    """Structurally locate a double-elim grand final and its optional reset.

    * **Grand Final** — the positive-round match with an incoming feeder from a
      **negative** (losers-bracket) round.
    * **Reset** — the positive-round match the grand final itself feeds into.

    Returns their ids; either may be ``None`` for an empty or not-yet-generated
    finals stage. ``theme.brackets.render.detect_finals`` maps these back to the
    ORM rows it holds, so the structural rule is stated once.
    """
    by_id = {n.id: n for n in nodes}
    incoming_rounds: Dict[int, List[int]] = {}
    for n in nodes:
        for target_id in (n.winner_to_id, n.loser_to_id):
            if target_id is not None:
                incoming_rounds.setdefault(target_id, []).append(n.round)

    grand_final: Optional[RoundNode] = None
    for n in nodes:
        if n.round > 0 and any(r < 0 for r in incoming_rounds.get(n.id, ())):
            grand_final = n
            break

    if grand_final is None:
        # A 2-entrant double elim has no losers bracket: fall back to the lowest positive loser_to target.
        loser_targets = {n.loser_to_id for n in nodes if n.loser_to_id is not None}
        candidates = [n for n in nodes if n.round > 0 and n.id in loser_targets]
        if candidates:
            grand_final = min(candidates, key=lambda n: n.round)

    reset: Optional[RoundNode] = None
    if grand_final is not None:
        for target_id in (grand_final.winner_to_id, grand_final.loser_to_id):
            candidate = by_id.get(target_id) if target_id is not None else None
            if candidate is not None and candidate.round > 0:
                reset = candidate
                break
    return (
        grand_final.id if grand_final is not None else None,
        reset.id if reset is not None else None,
    )


def round_names(
    nodes: List[RoundNode],
    *,
    double_elim: bool = False,
    elimination: bool = True,
) -> Dict[int, str]:
    """``{round_number: name}`` for a whole stage graph, in one pass.

    The finals rounds are excluded from the winners-bracket depth before
    ``max_winners_round`` is taken, exactly as the renderer does it — otherwise a
    double elim's grand final would be counted as the winners final and every
    winners round would be named one step too early.

    ``elimination=False`` for the flat formats (Swiss, round robin). Their rounds
    are a schedule, not a ladder: the deepest round is nothing but the last one
    everybody plays, so counting down to Final/Semifinals/Quarterfinals off it
    names rounds after an elimination structure the stage does not have. A
    3-round group would otherwise open on "Quarterfinals".
    """
    if not nodes:
        return {}
    if not elimination:
        return {n.round: f'Round {n.round}' for n in nodes}

    gf_id, reset_id = detect_finals_ids(nodes) if double_elim else (None, None)
    by_id = {n.id: n for n in nodes}
    gf_round = by_id[gf_id].round if gf_id is not None else None
    reset_round = by_id[reset_id].round if reset_id is not None else None
    finals_rounds = {r for r in (gf_round, reset_round) if r is not None}

    winners = [n.round for n in nodes if n.round > 0 and n.round not in finals_rounds]
    losers = [abs(n.round) for n in nodes if n.round < 0]
    max_winners_round = max(winners, default=None)
    max_losers_magnitude = max(losers, default=None)

    return {
        n.round: round_label(
            n.round,
            is_grand_final=(gf_round is not None and n.round == gf_round),
            is_reset=(reset_round is not None and n.round == reset_round),
            max_winners_round=max_winners_round,
            max_losers_magnitude=max_losers_magnitude,
            double_elim=double_elim,
        )
        for n in nodes
    }
