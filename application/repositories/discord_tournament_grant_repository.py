"""
DiscordTournamentGrant Repository - Data Access Layer

Provenance rows for per-tournament grants the guild-role sync made. The grant
itself lives on ``Tournament.admins`` / ``.crew_coordinators``; these rows record
which of those the sync is allowed to take back.
"""

from typing import List, Optional

from application.event_writes import guarded_delete
from application.repositories._tenant import current_tenant_id, scoped
from models import DiscordTournamentGrant, Tournament, TournamentGrant, User


class DiscordTournamentGrantRepository:
    """Repository for DiscordTournamentGrant data access."""

    @staticmethod
    async def list_for_user(user: User) -> List[DiscordTournamentGrant]:
        return await scoped(DiscordTournamentGrant.filter(user=user))

    @staticmethod
    async def get_match(
        user: User, tournament_id: int, grant: TournamentGrant
    ) -> Optional[DiscordTournamentGrant]:
        return await DiscordTournamentGrant.get_or_none(
            user=user, tournament_id=tournament_id, grant=grant,
            tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def add(
        user: User, tournament: Tournament, grant: TournamentGrant
    ) -> DiscordTournamentGrant:
        instance, _ = await DiscordTournamentGrant.get_or_create(
            user=user, tournament=tournament, grant=grant,
            defaults={'tenant_id': current_tenant_id()},
        )
        return instance

    @staticmethod
    async def remove(user: User, tournament_id: int, grant: TournamentGrant) -> None:
        await guarded_delete(scoped(
            DiscordTournamentGrant.filter(user=user, tournament_id=tournament_id, grant=grant)
        ))
