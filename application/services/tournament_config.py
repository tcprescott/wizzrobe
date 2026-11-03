"""Tournament config substrate - Business Logic Layer.

The hybrid-config decision (docs/online-tournaments) keeps worker-queried knobs
as typed columns on :class:`~models.Tournament` and stores the rest — messaging
templates, scoring parameters, and strategy choices — as a schema-validated JSON
blob in ``Tournament.config``. This module owns that schema.

``validate_tournament_config`` is the single entry point the service layer calls
before persisting the blob. It rejects unknown keys (``extra='forbid'``) so a
typo or a stale client field surfaces as a user-facing ``ValueError`` instead of
silently landing dead data. Feature PRs extend :class:`TournamentConfig` with the
concrete keys their strategies read; PR 0 ships the substrate with only the
cross-cutting messaging-templates facet.
"""

from datetime import datetime, time
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from application.utils.config_validation import validate_config_blob


class RoundScheduleEntry(BaseModel):
    """When one stage of a linked bracket may be booked (see ``round_windows``).

    ``opens_at``/``closes_at`` are UTC ISO-8601 instants; ``daily_from`` and
    ``daily_until`` are ``HH:MM`` on the community's clock and come as a pair.
    """

    model_config = ConfigDict(extra='forbid')

    opens_at: Optional[str] = None
    closes_at: Optional[str] = None
    daily_from: Optional[str] = None
    daily_until: Optional[str] = None
    best_of: Optional[int] = None

    @field_validator('best_of')
    @classmethod
    def _best_of(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v % 2 == 0 or v > 7):
            raise ValueError('best_of must be an odd number from 1 to 7')
        return v

    @field_validator('opens_at', 'closes_at')
    @classmethod
    def _instant(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v)
        return v

    @field_validator('daily_from', 'daily_until')
    @classmethod
    def _clock(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            time.fromisoformat(v)
        return v

    @model_validator(mode='after')
    def _consistent(self) -> 'RoundScheduleEntry':
        if self.opens_at and self.closes_at and (
            datetime.fromisoformat(self.closes_at) <= datetime.fromisoformat(self.opens_at)
        ):
            raise ValueError('A stage has to close after it opens.')
        if (self.daily_from is None) != (self.daily_until is None):
            raise ValueError('Daily hours need both a start and an end.')
        if self.daily_from is not None and self.daily_from == self.daily_until:
            raise ValueError('Daily hours have to end at a different time than they start.')
        return self


class TournamentConfig(BaseModel):
    """Validated shape of ``Tournament.config``.

    ``extra='forbid'`` is load-bearing: it is what turns an unknown key into a
    validation error, keeping the config a closed, reviewed vocabulary rather
    than a free-form bag. Every field is optional so an online tournament opts
    into only the behavior it uses.
    """

    model_config = ConfigDict(extra='forbid')

    # ``str.format``-style ``{placeholder}`` substitution only, never ``eval``.
    messaging_templates: Optional[Dict[str, str]] = None
    round_schedule: Optional[Dict[str, RoundScheduleEntry]] = None


def validate_tournament_config(
    config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Validate and normalize a ``Tournament.config`` blob.

    Returns ``None`` unchanged (config is optional). Otherwise validates the dict
    against :class:`TournamentConfig` and returns the normalized dict with unset
    keys dropped. Raises :class:`ValueError` on any unknown key or bad value, so
    the service layer can surface it the same as every other user error.
    """
    return validate_config_blob(config, TournamentConfig, "tournament")
