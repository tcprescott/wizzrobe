"""Discord Events sync service (PR 8) — tenant-facing config + on-demand reconcile.

The management layer over the Discord Scheduled Events reconciler: per-tournament
opt-in (which tournaments mirror their schedule to the tenant guild) and an
on-demand "reconcile now". All mutations are gated by
:meth:`AuthService.can_manage_sync` (STAFF / super-admin / ``SYNC_ADMIN``) and
audited. The background worker calls the reconciler directly as the system user;
this service is the human-driven surface.
"""

from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.repositories import DiscordScheduledEventRepository, TournamentRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord.discord_event_reconciler_service import (
    DiscordEventReconcilerService,
    ReconcileResult,
)
from application.services.discord.discord_service import DiscordService
from application.services.tenant_service import TenantService
from application.tenant_context import require_tenant_id
from models import DiscordScheduledEvent, Tenant, Tournament, User

NO_GUILD = "Connect a Discord server before syncing events."


class DiscordEventSyncService:
    """Per-tournament opt-in + on-demand reconcile for the Discord events mirror."""

    def __init__(self) -> None:
        self.repository = DiscordScheduledEventRepository()
        self.audit = AuditService()

    async def get_tenant(self) -> Optional[Tenant]:
        """The ambient tenant (for the UI to show its guild-link status)."""
        return await TenantService.get_by_id(require_tenant_id())

    async def setup_problem(self, actor: Optional[User]) -> Optional[str]:
        """Why events can't sync to the ambient tenant's Discord server, or ``None``.

        Settings stay locked while this is set: a switched-on tournament would
        only fail every match on each pass.
        """
        await AuthService.ensure_can_manage_sync(actor)
        tenant = await TenantService.get_by_id(require_tenant_id())
        if tenant is None or tenant.discord_guild_id is None:
            return NO_GUILD
        return await DiscordService().bot_event_problem(tenant.discord_guild_id)

    async def list_tournaments(self, actor: Optional[User]) -> List[Tournament]:
        await AuthService.ensure_can_manage_sync(actor)
        return await TournamentRepository.get_all()

    async def list_events(self, actor: Optional[User]) -> List[DiscordScheduledEvent]:
        await AuthService.ensure_can_manage_sync(actor)
        return await self.repository.list_all()

    async def update_settings(
        self,
        actor: Optional[User],
        tournament_id: int,
        *,
        enabled: Optional[bool] = None,
        duration_minutes: Optional[int] = None,
        title_template: Optional[str] = None,
        description_template: Optional[str] = None,
    ) -> Tournament:
        """Set a tournament's Discord-events opt-in + templating."""
        await AuthService.ensure_can_manage_sync(actor)
        tournament = require_found(
            await TournamentRepository.get_by_id(tournament_id), "Tournament"
        )
        problem = await self.setup_problem(actor)
        if problem is not None:
            raise ValueError(problem)
        changes: Dict[str, Any] = {}
        if enabled is not None:
            changes['discord_events_enabled'] = enabled
        if duration_minutes is not None:
            changes['discord_event_duration_minutes'] = max(1, duration_minutes)
        if title_template is not None:
            changes['discord_event_title_template'] = (title_template or '').strip() or None
        if description_template is not None:
            changes['discord_event_description_template'] = (description_template or '').strip() or None
        if changes:
            await TournamentRepository.update(tournament, **changes)
        await self.audit.write_log(
            actor, AuditActions.DISCORD_EVENT_SETTINGS_UPDATED,
            {'tournament_id': tournament_id, 'changes': list(changes.keys())},
        )
        return tournament

    async def reconcile_now(self, actor: Optional[User]) -> ReconcileResult:
        """Reconcile the ambient tenant's Discord events on demand.

        Gated by ``can_manage_sync``; the human actor is passed through so the
        resulting audit/event rows attribute to them (the background worker uses
        the system user). Raises :class:`ValueError` when no Discord server is
        linked — the mirror needs a verified guild.
        """
        await AuthService.ensure_can_manage_sync(actor)
        tenant = await TenantService.get_by_id(require_tenant_id())
        if tenant is None:
            raise ValueError("No tenant in scope")
        if tenant.discord_guild_id is None:
            raise ValueError(NO_GUILD)
        return await DiscordEventReconcilerService().reconcile_tenant(tenant, actor=actor)
