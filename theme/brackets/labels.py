"""Display labels for a bracket's format, state and configuration.

One home for chrome three surfaces need identically: the public stage index and
detail (``pages/brackets.py``), the anonymous browse tab
(``pages/home_tabs/brackets.py``), and the admin surface
(``pages/admin_tabs/admin_brackets/``) — which had each grown its own copy.
"""

from typing import List

from models import Bracket, BracketFormat, BracketState

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
