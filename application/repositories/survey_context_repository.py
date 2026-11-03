"""
Survey Context Repository - Data Access Layer

The event's shape and a few totals, read for the AI question helper. Every
read here returns structure staff set up (tournament, stage and position
names, formats, dates) or counts and times; nothing a player or volunteer
typed and no names of people. One query per kind of row.
"""

from typing import List, Optional

from application.repositories._event import event_scoped
from application.repositories._tenant import scoped
from models import Event, Match, MatchRescheduleRequest, Stage, Tournament, VolunteerAssignment, VolunteerPosition


class SurveyContextRepository:
    """Bulk, people-free reads of one event."""

    @staticmethod
    async def event(event_id: int) -> Optional[Event]:
        return await scoped(Event.filter(id=event_id)).first()

    @staticmethod
    async def tournaments(event_id: int) -> List[dict]:
        return await event_scoped(Tournament.filter(is_test=False), event_id).order_by('name').values(
            'id', 'name', 'short_name', 'tournament_format', 'players_per_match', 'average_match_duration',
            'uses_tournament_room', 'is_active',
        )

    @staticmethod
    async def match_times(event_id: int) -> List[dict]:
        """Per match: its tournament, whether it had a stage, and its scheduled, start and finish times."""
        return await event_scoped(Match.filter(tournament__is_test=False), event_id).values(
            'tournament_id', 'stage_id', 'scheduled_at', 'started_at', 'finished_at',
        )

    @staticmethod
    async def stage_names(event_id: int) -> List[str]:
        names = await event_scoped(Stage.all(), event_id).order_by('name').values_list('name', flat=True)
        return [str(n) for n in names]

    @staticmethod
    async def positions(event_id: int) -> List[dict]:
        return await event_scoped(VolunteerPosition.all(), event_id).order_by('display_order', 'name') \
            .values('id', 'name')

    @staticmethod
    async def assignment_times(event_id: int) -> List[dict]:
        """Per volunteer assignment: its position and shift times, and whether they checked in."""
        return await scoped(VolunteerAssignment.filter(shift__event_id=event_id)).values(
            'user_id', position_id='shift__position_id', starts_at='shift__starts_at', ends_at='shift__ends_at',
            checked_in_at='checked_in_at',
        )

    @staticmethod
    async def reschedule_counts(event_id: int) -> List[dict]:
        """Reschedule requests by kind and status (never their reasons, which players wrote)."""
        return await scoped(MatchRescheduleRequest.filter(match__event_id=event_id)).values('kind', 'status')
