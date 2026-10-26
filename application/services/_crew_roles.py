"""The crew roles a match can take, and how many of each its tournament asks for.

Broadcasters are the third role and an event opt-in, so their count reads as 0
on an event without them.
"""

from application.services.event_service import EventService
from models import Match

#: Every crew role, by the singular the services, handlers and audit rows use.
CREW_ROLES = ('commentator', 'tracker', 'broadcaster')
INVALID_ROLE = "Invalid role: {}. Must be 'commentator', 'tracker' or 'broadcaster'"


def check_role(role: str) -> None:
    if role not in CREW_ROLES:
        raise ValueError(INVALID_ROLE.format(role))


def crew_list(match: Match, role: str) -> list:
    """``match``'s prefetched signups for ``role``."""
    return getattr(match, f'{role}s')


async def required_crew(match: Match, role: str) -> int:
    """How many approved ``role`` the tournament asks for; 0 means it doesn't use the role.

    Broadcasters are an event opt-in: in an event without them the count reads
    as 0 whatever the tournament says.
    """
    tournament = match.tournament
    if role == 'broadcaster':
        if not await EventService.broadcasters_on(match.event_id):
            return 0
        return tournament.required_broadcasters if tournament else 1
    if tournament is None:
        return 1
    return tournament.required_commentators if role == 'commentator' else tournament.required_trackers
