"""
Reporting shared constants and helpers.

:class:`ReportsService` (point-in-time snapshots) and :class:`AnalyticsService`
(trends/health) run the same underlying time math. These shared constants and
helpers keep their assumptions in lockstep — most importantly the
``ON_TIME_THRESHOLD_MIN`` that both "on-time %" figures depend on, so the Reports
and Insights dashboards can never silently disagree.
"""

from datetime import datetime
from typing import Optional

from application.utils.timezone import to_local
from models import Tournament, User

# Fallback match length (minutes) when a match has no finish time and its
# tournament defines no average duration.
DEFAULT_MATCH_DURATION_MIN = 90

# A match counts as "on time" when its start delay is within this many minutes
# (absolute) of the scheduled time. Shared so every on-time percentage agrees.
ON_TIME_THRESHOLD_MIN = 5

# A recorded length this many times the tournament's configured match length is
# a Finish clicked hours late (sgl26 #113, #276), not a long race.
OVERRUN_OUTLIER_FACTOR = 3


def is_outlier_duration(duration_min: Optional[int], tournament: Optional[Tournament]) -> bool:
    """Whether a start-to-finish length is a click artefact to leave out of averages.

    Zero minutes means start and finish were stamped together after the fact.
    """
    if duration_min is None:
        return False
    if duration_min <= 0:
        return True
    expected = tournament.average_match_duration if tournament else None
    return bool(expected) and duration_min > expected * OVERRUN_OUTLIER_FACTOR


def to_display(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a UTC-aware datetime to the display zone, passing ``None`` through."""
    return to_local(dt)


def window_hours(start: Optional[datetime], end: Optional[datetime]) -> float:
    """Non-negative hours spanned by ``[start, end]`` (0.0 for missing/inverted)."""
    if not start or not end:
        return 0.0
    return max(0.0, (end - start).total_seconds() / 3600.0)


# What a tournament asks for when it has not said. Matches the column defaults,
# and is what a match with no tournament at all is measured against.
DEFAULT_CREW_REQUIREMENT = (1, 1)


def crew_requirement(tournament: Optional[Tournament]) -> tuple[int, int]:
    """``(commentators, trackers)`` an approved crew needs for a stream candidate."""
    if tournament is None:
        return DEFAULT_CREW_REQUIREMENT
    return tournament.required_commentators, tournament.required_trackers


def is_crew_covered(
    tournament: Optional[Tournament],
    commentators_approved: int,
    trackers_approved: int,
) -> bool:
    """Whether approved crew meets ``tournament``'s requirement.

    The single definition of "covered" behind both the crew-coverage report and
    the tournament-health score, so the two dashboards cannot drift apart. A
    role the tournament requires none of is satisfied by having none.
    """
    need_commentators, need_trackers = crew_requirement(tournament)
    return commentators_approved >= need_commentators and trackers_approved >= need_trackers


async def ensure_report_reader(actor: Optional[User]) -> None:
    """The Reports tab's audience: staff, a tournament admin or a crew coordinator.

    Checked here as well as at each entry surface, so a new page, tool or
    handler that reaches a report inherits the gate. The system actor (workers)
    passes.
    """
    from application.services.auth_service import AuthService

    if AuthService.is_system(actor) or await AuthService.can_view_admin(actor):
        return
    raise PermissionError('Only staff, tournament admins and crew coordinators can read reports.')


async def ensure_volunteer_report_reader(actor: Optional[User]) -> None:
    """The volunteer reports also admit a volunteer coordinator."""
    from application.services.auth_service import AuthService

    if AuthService.is_system(actor) or await AuthService.can_view_volunteer_admin(actor):
        return
    raise PermissionError('Only staff and volunteer coordinators can read volunteer reports.')
