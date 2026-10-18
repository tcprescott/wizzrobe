"""Bring everyone in a community's Discord server into Wizzrobe at once.

Admin → Users › Import from Discord. Login and role sync each make an account
for one person at the moment they need it; this makes one for every human in
the linked server ahead of time, and adds them all to the community, so staff
can enroll and map people (a Challonge entrant, a bracket seat) who have never
signed in. Safe to run again: it matches accounts on Discord id, so a second
run only picks up people who joined the server since.
"""

import logging
from dataclasses import dataclass
from typing import List

from application.events import EventType
from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.repositories.user_repository import UserRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord.discord_guild_ops import GuildMember
from application.services.discord.discord_service import DiscordService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tenant_service import TenantService
from application.tenant_context import require_tenant_id
from models import MembershipSource, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordImportPreview:
    """What an import would do right now, for the confirm dialog."""

    server_name: str
    people: int
    new_accounts: int
    new_members: int
    already_members: int
    bots: int
    removed: int


@dataclass(frozen=True)
class DiscordImportResult:
    accounts_created: int
    members_added: int
    already_members: int
    bots: int
    removed: int


@dataclass(frozen=True)
class _Plan:
    server_name: str
    humans: List[GuildMember]
    bots: int
    existing: dict
    members: set
    blocked: set
    unusable: set

    @property
    def new_accounts(self) -> List[GuildMember]:
        return [m for m in self.humans if m.id not in self.existing]

    @property
    def new_members(self) -> int:
        skip = self.members | self.blocked | self.unusable
        return len(self.new_accounts) + sum(
            1 for user in self.existing.values() if user.id not in skip
        )


class DiscordMemberImportService:
    """Staff-only, scoped to the community in context and its linked server."""

    def __init__(self) -> None:
        self.audit_service = AuditService()

    async def preview(self, actor: User) -> DiscordImportPreview:
        plan = await self._plan(actor)
        return DiscordImportPreview(
            server_name=plan.server_name,
            people=len(plan.humans),
            new_accounts=len(plan.new_accounts),
            new_members=plan.new_members,
            already_members=len(plan.members),
            bots=plan.bots,
            removed=len(plan.blocked),
        )

    async def run(self, actor: User) -> DiscordImportResult:
        plan = await self._plan(actor)
        await UserRepository.bulk_create_from_discord(
            (m.id, m.username, m.avatar) for m in plan.new_accounts
        )
        accounts = await UserRepository.get_by_discord_ids(m.id for m in plan.humans)
        created = [u for did, u in accounts.items() if did not in plan.existing]
        added = await TenantMembershipService().bulk_add_members(
            actor, accounts.values(), MembershipSource.DISCORD_IMPORT,
        )
        result = DiscordImportResult(
            accounts_created=len(created),
            members_added=len(added),
            already_members=len(plan.members),
            bots=plan.bots,
            removed=len(plan.blocked),
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.TENANT_MEMBERS_IMPORTED,
            {
                'source': 'discord',
                'accounts_created': result.accounts_created,
                'members_added': result.members_added,
                'skipped_bots': result.bots,
                'skipped_removed': result.removed,
                'created_user_ids': [u.id for u in created],
                'added_user_ids': [u.id for u in added],
            },
            EventType.TENANT_MEMBERS_IMPORTED,
        )
        return result

    async def _plan(self, actor: User) -> _Plan:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            'Only Staff can import people from Discord',
        )
        tenant_id = require_tenant_id()
        tenant = await TenantService.get_by_id(tenant_id)
        if tenant is None or not tenant.discord_guild_id:
            raise ValueError(
                'This community has no Discord server linked. '
                'Connect one under Settings → Integrations first.'
            )
        service = DiscordService()
        ok, payload = await service.list_members(tenant.discord_guild_id)
        if not ok or isinstance(payload, str):
            raise ValueError(f"Couldn't read the Discord server: {payload}")
        server_name = await self._server_name(service, tenant.discord_guild_id)

        humans = [m for m in payload if not m.bot]
        existing = await UserRepository.get_by_discord_ids(m.id for m in humans)
        user_ids = [u.id for u in existing.values()]
        return _Plan(
            server_name=server_name,
            humans=humans,
            bots=len(payload) - len(humans),
            existing=existing,
            members=await TenantMembershipRepository.member_ids(user_ids, tenant_id),
            blocked=await TenantMembershipRepository.blocked_ids(user_ids, tenant_id),
            unusable={u.id for u in existing.values() if not u.is_active or u.is_system},
        )

    @staticmethod
    async def _server_name(service: DiscordService, guild_id: int) -> str:
        ok, summary = await service.get_guild_summary(guild_id)
        name = summary.get('name') if ok and isinstance(summary, dict) else None
        return str(name) if name else 'Discord'
