from typing import List, Optional

from application.errors import require_found
from application.event_context import event_scoped
from application.repositories import TournamentNotificationRepository, TournamentRepository
from models import MatchNotificationLevel, Tournament, TournamentNotificationPreference, User


class TournamentNotificationService:
    """Service for managing per-tournament notification preferences."""

    def __init__(self) -> None:
        self.repository = TournamentNotificationRepository()
        self.tournament_repository = TournamentRepository()

    async def get_preference(self, user: User, tournament_id: int) -> Optional[TournamentNotificationPreference]:
        tournament = await self.tournament_repository.get_by_id(tournament_id)
        if not tournament:
            return None
        return await self.repository.get_by_user_and_tournament(user, tournament)

    async def get_user_preferences(self, user: User) -> List[TournamentNotificationPreference]:
        return await self.repository.get_all_for_user(user)

    async def upsert_preference(
        self,
        user: User,
        tournament_id: int,
        match_notifications: str,
    ) -> TournamentNotificationPreference:
        valid_levels = {level.value for level in MatchNotificationLevel}
        if match_notifications not in valid_levels:
            raise ValueError(
                f"Invalid notification level '{match_notifications}'. "
                f"Must be one of: {', '.join(sorted(valid_levels))}"
            )
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id),
            f"Tournament {tournament_id}",
        )
        return await self.repository.upsert(
            user=user,
            tournament=tournament,
            match_notifications=MatchNotificationLevel(match_notifications),
        )

    async def get_active_tournaments(self) -> List[Tournament]:
        return await event_scoped(Tournament.filter(is_active=True)).order_by('name').all()
