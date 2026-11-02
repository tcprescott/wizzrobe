"""Display labels for a bracket's format, state and configuration.

One home for chrome three surfaces need identically: the public stage index and
detail (``pages/brackets.py``), the anonymous browse tab
(``pages/home_tabs/brackets.py``), and the admin surface
(``pages/admin_tabs/admin_brackets/``), plus the app's admin over REST —
which had each grown its own copy.
"""

from typing import Dict, List

from application.services.bracket_engines.round_names import RoundNode, detect_finals_ids, round_label
from models import Bracket, BracketFormat, BracketMatch, BracketState

FORMAT_LABELS: dict[BracketFormat, str] = {
    BracketFormat.SINGLE_ELIM: 'Single elimination',
    BracketFormat.DOUBLE_ELIM: 'Double elimination',
    BracketFormat.SWISS: 'Swiss',
    BracketFormat.ROUND_ROBIN: 'Round robin',
}

# Keyed by the enum's value: ui.select options map raw values.
FORMAT_OPTIONS: dict[str, str] = {
    fmt.value: label for fmt, label in FORMAT_LABELS.items()
}

STATE_COLORS: dict[BracketState, str] = {
    BracketState.DRAFT: 'grey',
    BracketState.ACTIVE: 'positive',
    BracketState.COMPLETE: 'primary',
    BracketState.CANCELLED: 'negative',
}

STATE_LABELS: dict[BracketState, str] = {
    BracketState.DRAFT: 'Draft',
    BracketState.ACTIVE: 'Running',
    BracketState.COMPLETE: 'Complete',
    BracketState.CANCELLED: 'Cancelled',
}


def format_label(fmt: BracketFormat) -> str:
    return FORMAT_LABELS.get(fmt, getattr(fmt, 'value', str(fmt)))


def state_color(state: BracketState) -> str:
    return STATE_COLORS.get(state, 'grey')


def state_label(state: BracketState) -> str:
    return STATE_LABELS.get(state, getattr(state, 'value', str(state)).title())


def config_summary(bracket: Bracket) -> List[str]:
    """A stage's configuration in plain language, one phrase per decision.

    The Create dialog was the only writer of these values and nothing anywhere
    read them back, so a stage's rules — how many Swiss rounds, whether the grand
    final has a reset, how many entrants it draws in — were unverifiable the
    moment the dialog closed. Returns only what was actually configured, so an
    unconfigured stage renders nothing rather than a row of defaults.
    """
    config = bracket.config or {}
    parts: List[str] = []

    if bracket.format == BracketFormat.SWISS:
        rounds = config.get('swiss_rounds')
        parts.append(
            f'{rounds} rounds' if rounds
            else 'rounds derived from the field size'
        )
    elif bracket.format == BracketFormat.ROUND_ROBIN:
        groups = config.get('group_count')
        if groups:
            parts.append(f'{groups} groups')
    elif bracket.format == BracketFormat.DOUBLE_ELIM:
        parts.append(
            'grand final reset' if config.get('grand_final_reset', True)
            else 'no grand final reset'
        )

    default_best_of = config.get('default_best_of')
    if default_best_of:
        parts.append(f'best of {default_best_of} by default')

    rounds_cfg = config.get('rounds') or {}
    overrides = sum(1 for r in rounds_cfg.values() if (r or {}).get('best_of'))
    if overrides:
        parts.append(f'{overrides} round(s) with their own best-of')

    tiebreakers = config.get('tiebreakers')
    if tiebreakers:
        parts.append('tiebreak: ' + ' → '.join(tiebreakers))

    advancement = config.get('advancement') or {}
    count = advancement.get('count')
    if count:
        scope = 'per group' if advancement.get('per_group') else 'overall'
        seeding = advancement.get('seeding', 'snake')
        parts.append(
            f'draws the top {count} {scope} from {stage_label(bracket.stage_order - 1)}'
            + (f' ({seeding} seeding)' if advancement.get('per_group') else '')
        )
    return parts


def stage_label(stage_order: int) -> str:
    """Human stage numbering — 1-based, matching every public surface.

    ``stage_order`` is 0-based in the model and in the field an operator types,
    so the two must never be printed the same way: an operator taught "Stage 1"
    by the public page who then types 1 into "Stage order" creates a stage the
    advancement chain will not find.
    """
    return f'Stage {stage_order + 1}'


def distinct_rounds(matches: List[BracketMatch]) -> List[int]:
    """Rounds present, ordered winners (asc) then losers (by magnitude)."""
    positive = sorted({m.round for m in matches if m.round >= 0})
    negative = sorted({m.round for m in matches if m.round < 0}, key=abs)
    return positive + negative


def round_display_names(matches: List[BracketMatch], fmt: BracketFormat) -> Dict[int, str]:
    """``round number → the name the public bracket gives that round`` (Quarterfinals, Final…)."""
    rounds = distinct_rounds(matches)
    if not rounds:
        return {}
    if fmt not in (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM):
        return {r: f'Round {r}' for r in rounds}
    double = fmt == BracketFormat.DOUBLE_ELIM
    gf_round = reset_round = None
    if double:
        by_id = {m.id: m for m in matches}
        gf_id, reset_id = detect_finals_ids([
            RoundNode(id=m.id, round=m.round, winner_to_id=m.winner_to_id, loser_to_id=m.loser_to_id)  # type: ignore[attr-defined]
            for m in matches
        ])
        gf_round = by_id[gf_id].round if gf_id in by_id else None
        reset_round = by_id[reset_id].round if reset_id in by_id else None
    finals = {r for r in (gf_round, reset_round) if r is not None}
    max_winners = max((r for r in rounds if r > 0 and r not in finals), default=None)
    max_losers = max((abs(r) for r in rounds if r < 0), default=None)
    return {
        r: round_label(
            r,
            is_grand_final=r == gf_round,
            is_reset=r == reset_round,
            max_winners_round=max_winners,
            max_losers_magnitude=max_losers,
            double_elim=double,
        )
        for r in rounds
    }


def match_label(match: BracketMatch, round_names: Dict[int, str]) -> str:
    """One matchup's coordinates: round name, group and board, never the raw ``position``.

    Round-robin positions are offset by group to keep the uniqueness constraint, so
    group 2 board 1 is stored as ``100001``.
    """
    name = round_names.get(match.round, f'Round {match.round}')
    if match.group_number:
        board = match.position - (match.group_number - 1) * 100000
        return f'Group {match.group_number} · {name} · Board {board}'
    return f'{name} · Match {match.position}'
