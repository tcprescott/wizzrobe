"""SpeedGaming read-only guard for ``MatchService.update_match`` (PR 7).

The per-field half of the hybrid read-only contract: a ``Match`` materialized
from an SG episode has ETL-owned fields (schedule, players, tournament) that only
the next sync may change. Extracted into its own module to keep ``match_service``
focused (and under the file-length guideline). Comparison is by *value* so the
edit dialog can resubmit the disabled fields unchanged; only a genuine change is
rejected. The UI also disables these fields, but this is the enforcement — a gap
here would be silently reverted on the next sync and look like a bug.
"""

from datetime import datetime
from typing import List, Optional

from application.utils.timezone import parse_local_datetime


def assert_sg_fields_unchanged(
    match,
    *,
    tournament_id: Optional[int],
    scheduled_date: Optional[str],
    scheduled_time: Optional[str],
    players_changed: bool,
    scheduled_at: Optional[datetime] = None,
) -> None:
    """Raise ``ValueError`` if a sourced match's ETL-owned fields are being edited.

    A no-op when the match is not SG-sourced (``speedgaming_episode_id`` unset or
    absent — mock objects in unit tests lack the attribute, so ``getattr`` guards
    it).
    """
    if getattr(match, 'speedgaming_episode_id', None) is None:
        return

    violations: List[str] = []
    if tournament_id is not None and tournament_id != match.tournament_id:
        violations.append('tournament')
    new_scheduled_at = scheduled_at
    if new_scheduled_at is None and scheduled_date and scheduled_time:
        new_scheduled_at = parse_local_datetime(scheduled_date, scheduled_time)
    if new_scheduled_at is not None:
        old = match.scheduled_at
        if old is None or abs((new_scheduled_at - old).total_seconds()) >= 60:
            violations.append('scheduled time')
    if players_changed:
        violations.append('players')
    if violations:
        raise ValueError(
            "This match is synced from SpeedGaming; "
            f"{', '.join(violations)} cannot be edited here."
        )
