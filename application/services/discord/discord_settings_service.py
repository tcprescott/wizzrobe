"""The Discord pane of Admin → Settings: one read model over the link, the role mappings and the events mirror.

The app's pane shows three cards that each act straight away. This service
gathers what they show from the services that own the writes
(``DiscordLinkService``, ``DiscordRoleMappingService``, ``DiscordEventSyncService``),
so the checks and audit rows stay the ones the website uses.
"""

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from application.repositories.discord_scheduled_event_repository import DiscordScheduledEventRepository
from application.services.auth_service import AuthService
from application.services.discord.discord_event_sync_service import DiscordEventSyncService
from application.services.discord.discord_link_service import DiscordLinkService
from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService
from application.services.discord.discord_service import DiscordService
from application.services.event_service import EventService
from application.services.tenant_service import TenantService
from application.services.tournament_service import TournamentService
from application.tenant_context import require_tenant_id
from models import DiscordRoleMapping, EventStatus, Role, Tenant, TournamentGrant, User

NOT_LINKED = "Connect a Discord server first."

GRANT_LABELS = {g: g.value.replace('_', ' ').title() for g in TournamentGrant}


def mapping_labels(mapping: DiscordRoleMapping) -> Tuple[str, str]:
    """(what it grants, where it applies) for one mapping row."""
    if mapping.app_role is not None:
        label = mapping.app_role.label
        if not mapping.app_role.is_per_event:
            return label, 'Community-wide'
        event = mapping.event
        if event is None:
            return label, 'No event: grants nothing'
        suffix = {EventStatus.DRAFT: ' (Draft)', EventStatus.ARCHIVED: ' (Archived)'}.get(event.status, '')
        return label, f'{event.name}{suffix}'
    grant = GRANT_LABELS.get(mapping.tournament_grant, mapping.tournament_grant.value)
    tournament = mapping.tournament.name if mapping.tournament else f'#{mapping.tournament_id}'
    return grant, tournament


@dataclass
class DiscordLinkStatus:
    guild_id: Optional[int]
    server_name: Optional[str]
    can_manage_roles: bool
    can_manage_events: bool


@dataclass
class GrantOption:
    value: str
    label: str
    kind: str  # 'community', 'event' or 'tournament'
    tournament_grant: bool


@dataclass
class MappingOptions:
    discord_roles: list[dict]
    grants: list[GrantOption]
    tournaments: list[Tuple[int, str]]
    events: list[Tuple[int, str]]
    default_event_id: Optional[int]


@dataclass
class TournamentMirror:
    id: int
    name: str
    enabled: bool
    mirrored: int


@dataclass
class EventsOverview:
    problem: Optional[str]
    tournaments: list[TournamentMirror] = field(default_factory=list)


class DiscordSettingsService:
    """What the app's Discord pane reads, and the writes that need the linked server."""

    def __init__(self) -> None:
        self.mappings = DiscordRoleMappingService()
        self.events = DiscordEventSyncService()

    @staticmethod
    async def _tenant() -> Tenant:
        tenant = await TenantService.get_by_id(require_tenant_id())
        if tenant is None:
            raise ValueError('Select a community first.')
        return tenant

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor), 'Only Staff can manage Discord role mappings',
        )

    async def _guild_id(self) -> int:
        tenant = await self._tenant()
        if tenant.discord_guild_id is None:
            raise ValueError(NOT_LINKED)
        return tenant.discord_guild_id

    async def link_status(self, actor: User) -> DiscordLinkStatus:
        can_roles = await AuthService.can_grant_roles(actor)
        can_events = await AuthService.can_manage_sync(actor)
        await AuthService.ensure(can_roles or can_events, 'You need Staff or Sync admin to open Discord settings.')
        tenant = await self._tenant()
        name = None
        if tenant.discord_guild_id is not None:
            ok, summary = await DiscordService().get_guild_summary(tenant.discord_guild_id)
            if ok and isinstance(summary, dict):
                name = str(summary.get('name'))
        return DiscordLinkStatus(tenant.discord_guild_id, name, can_roles, can_events)

    async def disconnect(self, actor: User) -> None:
        tenant = await self._tenant()
        if tenant.discord_guild_id is None:
            raise ValueError('No Discord server is connected.')
        await DiscordLinkService.disconnect(actor, tenant)

    async def list_mappings(self, actor: User) -> list[DiscordRoleMapping]:
        await self._ensure_staff(actor)
        tenant = await self._tenant()
        if tenant.discord_guild_id is None:
            return []
        return await self.mappings.list_mappings(tenant.discord_guild_id)

    async def mapping_options(self, actor: User) -> MappingOptions:
        await self._ensure_staff(actor)
        guild_id = await self._guild_id()
        ok, payload = await DiscordService().list_guild_roles(guild_id)
        if not ok:
            raise ValueError(str(payload))
        roles = sorted(
            (r for r in payload if r.get('mappable', True)),  # type: ignore[union-attr]
            key=lambda r: str(r['name']).casefold(),
        )
        grants = [
            GrantOption(r.value, r.label, 'event' if r.is_per_event else 'community', False)
            for r in Role.tenant_grantable()
        ] + [GrantOption(g.value, label, 'tournament', True) for g, label in GRANT_LABELS.items()]
        tournaments = await TournamentService().get_all_tournaments(active_only=True)
        events = await EventService.events_for_tenant(require_tenant_id())
        return MappingOptions(
            discord_roles=[
                {'id': int(r['id']), 'name': str(r['name']), 'color': int(r.get('color') or 0)} for r in roles
            ],
            grants=grants,
            tournaments=sorted(((t.id, t.name) for t in tournaments), key=lambda t: t[1].casefold()),
            events=[(e.id, e.name + (' (Draft)' if e.is_draft else '')) for e in events if not e.is_archived],
            default_event_id=next((e.id for e in events if e.is_default), None),
        )

    async def add_mappings(
        self,
        actor: User,
        discord_roles: Sequence[Tuple[int, str]],
        app_roles: Sequence[Role],
        tournament_grants: Sequence[TournamentGrant],
        tournament_id: Optional[int],
        event_id: Optional[int],
    ) -> Tuple[int, int]:
        await self._ensure_staff(actor)
        return await self.mappings.add_mappings(
            guild_id=await self._guild_id(),
            discord_roles=discord_roles,
            app_roles=app_roles,
            tournament_grants=tournament_grants,
            tournament_id=tournament_id if tournament_grants else None,
            event_id=event_id if any(r.is_per_event for r in app_roles) else None,
            actor=actor,
        )

    async def sync_everyone(self, actor: User) -> dict:
        await self._guild_id()
        return await self.mappings.sync_all_users(actor)

    async def events_overview(self, actor: User) -> EventsOverview:
        problem = await self.events.setup_problem(actor)
        tournaments = await self.events.list_tournaments(actor)
        counts = await DiscordScheduledEventRepository().counts_by_tournament()
        return EventsOverview(
            problem=problem,
            tournaments=[
                TournamentMirror(t.id, t.name, t.discord_events_enabled, counts.get(t.id, 0))
                for t in sorted(tournaments, key=lambda t: t.name.casefold())
            ],
        )
