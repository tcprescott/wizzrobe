"""Async-qualifier config substrate — validated JSON blob (PR 9).

Mirrors :mod:`application.services.tournament_config`: worker-/query-facing knobs
(``opens_at``/``closes_at``, ``runs_per_pool``, ``allowed_reattempts``) are typed
columns on :class:`~models.AsyncQualifier`; scoring parameters, the draw-fairness
threshold, and messaging templates live in the schema-validated ``config`` blob.
``extra='forbid'`` keeps it a closed, reviewed vocabulary.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from application.utils.config_validation import validate_config_blob


class AsyncQualifierConfig(BaseModel):
    """Validated shape of ``AsyncQualifier.config`` (every field optional)."""

    model_config = ConfigDict(extra='forbid')

    par_sample_size: Optional[int] = Field(default=None, ge=1)
    draw_imbalance_threshold: Optional[int] = Field(default=None, ge=1)
    run_time_limit_hours: Optional[int] = Field(default=None, ge=1, le=168)
    expiry_warning_minutes: Optional[int] = Field(default=None, ge=1)
    # Safe ``{placeholder}`` templates for DM notifications (never eval).
    messaging_templates: Optional[Dict[str, str]] = None


def validate_async_qualifier_config(
    config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Validate/normalize an ``AsyncQualifier.config`` blob (None passes through)."""
    return validate_config_blob(config, AsyncQualifierConfig, "qualifier")
