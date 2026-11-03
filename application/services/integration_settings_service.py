"""The Integrations pane of Admin → Settings: one read model over each service a community signs in to or sends to.

The app's pane is one stack of cards (Challonge, start.gg, webhooks,
randomizer keys, AI writing help). The writes stay with the services that own
them (``ChallongeService``, ``StartggConnectionService``, ``WebhookService``,
``RandomizerCredentialService``, ``AiProviderService``), so checks and audit
rows match the website. This service decides which cards a caller sees and
gathers what the two sign-in cards show.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from application.repositories.challonge_repository import ChallongeRepository
from application.services._challonge_auto_sync import AUTO_SYNC_CHOICES
from application.services.admin_nav_service import AdminNavService
from application.services.auth_service import AuthService
from application.services.challonge_service import ChallongeService
from application.services.feature_flag_service import FeatureFlagService
from application.services.startgg_connection_service import StartggConnectionService, TournamentAccess
from models import FeatureFlag, User


@dataclass(frozen=True)
class IntegrationCards:
    challonge: bool
    startgg: bool
    webhooks: bool
    randomizer_keys: bool
    ai: bool
    patreon: bool = False


@dataclass(frozen=True)
class ChallongeCommunityRow:
    slug: str
    tournaments: int


@dataclass
class ChallongeOverview:
    configured: bool
    connected: bool
    username: Optional[str]
    can_write_signups: bool
    linked_tournaments: int
    request_usage: int
    auto_sync_minutes: int
    auto_sync_choices: tuple[int, ...]
    auto_sync_brackets: int
    auto_sync_requests_per_day: int
    communities: List[ChallongeCommunityRow]


@dataclass
class StartggOverview:
    configured: bool
    platform_token: bool
    connected: bool
    username: Optional[str]
    expired: bool
    renewed_at: Any
    linked: List[TournamentAccess]


class IntegrationSettingsService:
    """Which Integrations cards a caller sees, and what the sign-in cards show."""

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only Staff can manage integrations')

    async def cards(self, actor: User) -> IntegrationCards:
        access = await AdminNavService.access(actor)
        await AuthService.ensure(
            access.is_staff or access.is_preset_manager,
            'You need Staff or Preset manager to open Integrations.',
        )
        live = await FeatureFlagService().enabled_flags()
        staff = access.is_staff
        return IntegrationCards(
            challonge=staff and FeatureFlag.CHALLONGE in live,
            startgg=staff and FeatureFlag.STARTGG in live,
            webhooks=staff,
            randomizer_keys=staff or access.is_preset_manager,
            ai=staff and FeatureFlag.SURVEYS in live,
            patreon=staff and FeatureFlag.PATREON in live,
        )

    async def challonge(self, actor: User) -> ChallongeOverview:
        await self._ensure_staff(actor)
        service = ChallongeService()
        status: Dict[str, Any] = await service.get_connection_status()
        minutes = await service.auto_sync_minutes()
        estimate = await service.auto_sync_estimate(minutes or 60)
        communities = await service.remembered_communities()
        connected = bool(status.get('connected'))
        return ChallongeOverview(
            configured=bool(status.get('configured')),
            connected=connected,
            username=status.get('challonge_username'),
            can_write_signups=connected and await service.can_add_participants(),
            linked_tournaments=await ChallongeRepository.linked_tournament_count(),
            request_usage=int(status.get('request_usage') or 0),
            auto_sync_minutes=minutes,
            auto_sync_choices=AUTO_SYNC_CHOICES,
            auto_sync_brackets=estimate.tournaments,
            auto_sync_requests_per_day=estimate.requests_per_day if minutes else 0,
            communities=[ChallongeCommunityRow(c.slug, c.tournament_count) for c in communities],
        )

    async def startgg(self, actor: User) -> StartggOverview:
        await self._ensure_staff(actor)
        service = StartggConnectionService()
        status: Dict[str, Any] = await service.get_status()
        connected = bool(status.get('connected'))
        return StartggOverview(
            configured=bool(status.get('configured')),
            platform_token=bool(status.get('platform_token')),
            connected=connected,
            username=status.get('username'),
            expired=bool(status.get('expired')),
            renewed_at=status.get('renewed_at'),
            linked=await service.access_for_linked(actor) if connected else [],
        )
