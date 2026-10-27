"""
Survey Participation Repository - Data Access Layer

What each person did at one event, read in bulk when a survey is sent: the
matches they played, the shifts they worked, the matches they crewed, whether
the desk checked them in, and the reschedules they asked for. One query per
kind of participation, whatever the size of the event.
"""

from typing import Dict, List, Set, Tuple

from application.repositories._event import event_scoped
from application.repositories._tenant import scoped
from models import (
    Broadcaster,
    CheckInEntrant,
    Commentator,
    Match,
    MatchPlayers,
    MatchRescheduleRequest,
    Tracker,
    User,
    VolunteerAssignment,
)


class SurveyParticipationRepository:
    """Bulk reads of one event's participation, keyed by user id."""

    @staticmethod
    async def match_rows(event_id: int) -> List[dict]:
        """One row per player per match in the event's real (non-test) tournaments."""
        match_ids = await event_scoped(Match.filter(tournament__is_test=False), event_id).values_list('id', flat=True)
        if not match_ids:
            return []
        return await scoped(MatchPlayers.filter(match_id__in=list(match_ids))).values(
            'user_id',
            tournament_id='match__tournament_id',
            tournament_name='match__tournament__name',
            tournament_short='match__tournament__short_name',
            uses_room='match__tournament__uses_tournament_room',
            stage_id='match__stage_id',
        )

    @staticmethod
    async def shift_rows(event_id: int) -> List[dict]:
        """One row per volunteer assignment in the event, with its position."""
        return await scoped(VolunteerAssignment.filter(shift__event_id=event_id)).values(
            'user_id', position_id='shift__position_id', position_name='shift__position__name',
        )

    @staticmethod
    async def crew_user_ids(event_id: int) -> Set[int]:
        """Everyone approved to commentate, track or broadcast a match in the event."""
        match_ids = list(await event_scoped(Match.all(), event_id).values_list('id', flat=True))
        if not match_ids:
            return set()
        users: Set[int] = set()
        for model in (Commentator, Tracker, Broadcaster):
            users.update(await scoped(model.filter(match_id__in=match_ids, approved=True))
                         .values_list('user_id', flat=True))
        return users

    @staticmethod
    async def checked_in_user_ids(event_id: int) -> Set[int]:
        """Everyone the check-in desk checked in at the event, matched to an account."""
        return set(await scoped(CheckInEntrant.filter(
            event__event_id=event_id, checked_in_at__isnull=False, user_id__isnull=False,
            withdrawn_at__isnull=True,
        )).values_list('user_id', flat=True))

    @staticmethod
    async def reschedule_pairs(event_id: int) -> Set[Tuple[int, int]]:
        """``(user id, tournament id)`` for every reschedule a player asked for in the event."""
        rows = await scoped(MatchRescheduleRequest.filter(match__event_id=event_id)).values_list(
            'requested_by_id', 'match__tournament_id',
        )
        return {(int(u), int(t)) for u, t in rows}

    @staticmethod
    async def reachable_users(user_ids: Set[int]) -> Dict[int, User]:
        """The active, real accounts among ``user_ids`` (no system user, no deactivated account)."""
        if not user_ids:
            return {}
        users = await User.filter(id__in=list(user_ids), is_active=True, is_system=False)
        return {u.id: u for u in users}
