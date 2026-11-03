"""
Broadcaster Repository - Data Access Layer

Handles all database operations for Broadcaster model.
"""

from application.repositories._crew_repository import CrewRepository
from models import Broadcaster


class BroadcasterRepository(CrewRepository[Broadcaster]):
    """Repository for broadcaster-related data access."""

    model = Broadcaster
