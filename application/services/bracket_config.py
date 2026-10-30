"""Bracket config substrate — the schema-validated shape of ``Bracket.config``.

Native brackets keep their per-stage knobs — grand-final reset toggle, Swiss
round count, group count, the scoring points a round robin / Swiss standings pass
consumes, and the tiebreaker order — as a closed, ``extra='forbid'`` JSON blob so
a typo or a stale client field surfaces as a user-facing ``ValueError`` instead
of silently landing dead data. This module owns that schema; the service layer
calls :func:`validate_bracket_config` before persisting the blob.

Advancement config (how a later stage draws from a prior stage's final ranks) is
added by a later unit — this schema deliberately leaves room for it rather than
implementing it now.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from application.services.bracket_engines.standings import KNOWN_TIEBREAKERS
from application.utils.config_validation import validate_config_blob


def validate_best_of(value: Optional[int]) -> Optional[int]:
    """Positive-and-odd check for a best-of length (``None`` passes through).

    Shared by :class:`RoundConfig.best_of` (the per-round default) and
    ``BracketMatch.best_of`` (the per-matchup override) so the two can never
    disagree about what a legal series length is.
    """
    if value is None:
        return value
    if value < 1:
        raise ValueError("best_of must be at least 1")
    if value % 2 == 0:
        raise ValueError("best_of must be odd (a best-of has no draws)")
    return value


class AdvancementConfig(BaseModel):
    """How a stage draws its field from the prior stage's ``final_rank``.

    Only meaningful on a stage with ``stage_order > 0``; the service reads it
    when staff trigger :meth:`BracketService.advance_stage`. ``count`` entrants
    advance — ``count`` per source ``group_number`` when ``per_group``, else the
    ``count`` best overall. ``seeding`` chooses how the advancers seed into this
    stage: ``'snake'`` (default) spreads group winners and keeps two entrants
    from the same source group apart in the opening playoff round; ``'preserve'``
    seeds strictly in advancement (rank) order.
    """

    model_config = ConfigDict(extra='forbid')

    count: int
    per_group: bool = False
    seeding: str = 'snake'

    @field_validator('count')
    @classmethod
    def _count_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("advancement count must be at least 1")
        return v

    @field_validator('seeding')
    @classmethod
    def _seeding_known(cls, v: str) -> str:
        if v not in ('snake', 'preserve'):
            raise ValueError("advancement seeding must be 'snake' or 'preserve'")
        return v


class RoundConfig(BaseModel):
    """Per-round metadata (best-of, the window the round runs in) for one round.

    ``best_of`` is **semantic**: it is the default series length for every match
    in the round (a per-matchup ``BracketMatch.best_of`` overrides it), and the
    scheduler and clinch logic both read it.

    ``scheduled_at``/``scheduled_end`` are the round's window. They render through
    ``format_local_display``, and they **bound match-time suggestions**: a matchup
    in this round is only ever suggested a slot that fits wholly inside the window
    (see :class:`~application.services.match.match_suggestion_service.MatchSuggestionService`).
    Either half may stand alone — a start with no end is an "no earlier than"
    floor, an end with no start a deadline. Both are UTC ISO-8601 strings (all
    stored datetimes are UTC — see docs/timezone-handling.md).
    """

    model_config = ConfigDict(extra='forbid')

    best_of: Optional[int] = None
    scheduled_at: Optional[str] = None
    scheduled_end: Optional[str] = None

    @field_validator('best_of')
    @classmethod
    def _best_of_positive_odd(cls, v: Optional[int]) -> Optional[int]:
        return validate_best_of(v)

    @field_validator('scheduled_at', 'scheduled_end')
    @classmethod
    def _scheduled_iso(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        try:
            datetime.fromisoformat(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{info.field_name} must be an ISO-8601 datetime string"
            ) from exc
        return v

    @model_validator(mode='after')
    def _window_ordered(self) -> 'RoundConfig':
        if self.scheduled_at and self.scheduled_end:
            start = datetime.fromisoformat(self.scheduled_at)
            end = datetime.fromisoformat(self.scheduled_end)
            if end <= start:
                raise ValueError(
                    "scheduled_end must be after scheduled_at"
                )
        return self


class BracketConfig(BaseModel):
    """Validated shape of ``Bracket.config``.

    ``extra='forbid'`` is load-bearing: it is what turns an unknown key into a
    validation error, keeping the config a closed, reviewed vocabulary rather
    than a free-form bag. Every field is optional so a stage opts into only the
    behavior it uses.
    """

    model_config = ConfigDict(extra='forbid')

    grand_final_reset: bool = True
    default_best_of: Optional[int] = None
    swiss_rounds: Optional[int] = None
    group_count: Optional[int] = None
    win_points: float = 1.0
    draw_points: float = 0.5
    loss_points: float = 0.0
    bye_points: float = 1.0
    tiebreakers: Optional[List[str]] = None
    omw_floor: float = 1 / 3
    advancement: Optional[AdvancementConfig] = None
    rounds: Optional[Dict[str, RoundConfig]] = None

    @field_validator('default_best_of')
    @classmethod
    def _default_best_of_positive_odd(cls, v: Optional[int]) -> Optional[int]:
        return validate_best_of(v)

    @field_validator('tiebreakers')
    @classmethod
    def _tiebreakers_known(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Reject a tiebreaker key the standings pass cannot apply.

        Without this the blob accepts any string and the failure surfaces far
        away, as a ``ValueError`` raised inside ``compute_standings`` on *every*
        standings read of the stage — and config edits are DRAFT-only, so a stage
        started with a typo could never be repaired in-app.
        """
        if v is None:
            return v
        unknown = [tb for tb in v if tb not in KNOWN_TIEBREAKERS]
        if unknown:
            raise ValueError(
                f"unknown tiebreaker(s) {', '.join(repr(tb) for tb in unknown)}; "
                f"choose from {', '.join(KNOWN_TIEBREAKERS)}"
            )
        return v

    @field_validator('rounds')
    @classmethod
    def _round_keys_are_ints(
        cls, v: Optional[Dict[str, RoundConfig]]
    ) -> Optional[Dict[str, RoundConfig]]:
        if v is None:
            return v
        for key in v:
            try:
                int(key)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"rounds keys must be integer round numbers, got {key!r}"
                ) from exc
        return v


_FORMAT_ONLY_KEYS: Dict[str, str] = {
    'swiss_rounds': 'swiss',
    'group_count': 'round_robin',
    'grand_final_reset': 'double_elim',
}

_FORMAT_KEY_HINT: Dict[str, str] = {
    'swiss_rounds': 'Swiss',
    'group_count': 'round-robin',
    'grand_final_reset': 'double-elimination',
}


def validate_bracket_config(
    config: Optional[Dict[str, Any]],
    *,
    fmt: Optional[Any] = None,
    stage_order: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Validate and normalize a ``Bracket.config`` blob.

    Returns ``None`` unchanged (config is optional). Otherwise validates the dict
    against :class:`BracketConfig` and returns the normalized dict with unset
    keys dropped. Raises :class:`ValueError` on any unknown key or bad value, so
    the service layer can surface it the same as every other user error.

    ``fmt`` and ``stage_order`` enable the cross-field checks the schema itself
    cannot make — it never sees the stage they belong to. Both are optional so a
    caller validating a bare blob (a test, a migration) still gets the shape
    check; the service always passes them, which is what keeps a dead key out of
    the blob over REST as well as through the UI.
    """
    validated = validate_config_blob(config, BracketConfig, "bracket")
    if validated is None:
        return None

    fmt_value = getattr(fmt, 'value', fmt)
    if fmt_value is not None:
        for key, owner in _FORMAT_ONLY_KEYS.items():
            if owner == fmt_value:
                continue
            value = validated.get(key)
            # A key at its schema default must pass: the normalizer injects defaults, so a stored blob returns carrying keys nobody typed.
            if value is None or value == BracketConfig.model_fields[key].default:
                continue
            raise ValueError(
                f"{key!r} only applies to a {_FORMAT_KEY_HINT[key]} bracket"
            )

    if stage_order == 0 and 'advancement' in validated:
        raise ValueError(
            "An advancement rule belongs on the stage being drawn INTO, so the "
            "first stage cannot carry one — set it on the stage that follows this "
            "one"
        )
    return validated
