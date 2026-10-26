"""
Stage Repository - Data Access Layer

Handles database operations for stages.
"""

from typing import List, Optional

from application.repositories._base import EventScopedRepository
from application.repositories._event import current_event_id, event_scoped
from application.repositories._tenant import current_tenant_id
from models import Stage


class StageRepository(EventScopedRepository[Stage]):
    """Repository for stage data access: the current event's stages."""

    model = Stage

    @staticmethod
    async def get_all() -> List[Stage]:
        """
        Get all stages.
        
        Returns:
            List of Stage objects
        """
        return await event_scoped(Stage.all()).order_by('name')
    
    @staticmethod
    async def any_exists() -> bool:
        """Whether the current event has any stage at all."""
        return await event_scoped(Stage.all()).exists()

    @staticmethod
    async def get_all_as_dict() -> dict[int, str]:
        """
        Get all stages as a dict mapping ID to name.
        Useful for dropdown/select options.
        
        Returns:
            Dict mapping stage ID to name
        """
        stages = await event_scoped(Stage.all())
        return {sr.id: sr.name for sr in stages}
    
    @staticmethod
    async def create(
        name: str,
        stream_url: Optional[str] = None,
        is_active: bool = True
    ) -> Stage:
        """
        Create a new stage.
        
        Args:
            name: Stage name
            stream_url: Optional stream URL
            is_active: Whether the stage is active (default: True)
            
        Returns:
            Created Stage object
        """
        return await Stage.create(
            tenant_id=current_tenant_id(),
            event_id=current_event_id(),
            name=name,
            stream_url=stream_url,
            is_active=is_active
        )
    
    @staticmethod
    async def name_taken(name: str, exclude_id: Optional[int] = None) -> bool:
        """Whether another of the current event's stages already has this name."""
        query = event_scoped(Stage.filter(name=name))
        if exclude_id is not None:
            query = query.exclude(id=exclude_id)
        return await query.exists()

    @staticmethod
    async def count_matches(stage: Stage) -> int:
        """Return how many matches reference this stage."""
        return await stage.matches.all().count()
