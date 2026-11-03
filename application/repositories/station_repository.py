"""Station Repository - Data Access Layer

Handles database operations for the venue's station pool.
"""

from typing import List

from application.repositories._base import EventScopedRepository
from application.repositories._event import event_scoped
from models import Station


class StationRepository(EventScopedRepository[Station]):
    """Repository for the current event's station pool."""

    model = Station

    @staticmethod
    async def get_all() -> List[Station]:
        """Every station the current event has defined, in display order."""
        return await event_scoped(Station.all()).order_by('sort_order', 'name')

    @staticmethod
    async def get_active() -> List[Station]:
        """The stations currently available to assign, in display order."""
        return await event_scoped(Station.filter(is_active=True)).order_by('sort_order', 'name')

    @staticmethod
    async def active_names() -> set[str]:
        """The labels a station assignment may use.

        An empty set means this event has not defined a pool, which keeps the
        historical free-text behaviour.
        """
        return set(
            await event_scoped(Station.filter(is_active=True)).values_list('name', flat=True)
        )
