"""
DiscordRoleMapping Service - Business Logic Layer

Manages the mapping of Discord guild roles to application roles, performs the
sync that grants/revokes app roles from a user's Discord roles, and provisions
an account for a guild member who holds a mapped role but has never signed in.
"""

import asyncio
import logging
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from application.errors import require_found
from application.event_context import event_scope
from application.repositories.discord_role_mapping_repository import DiscordRoleMappingRepository
from application.repositories.discord_role_sync_repository import DiscordRoleSyncRepository
from application.repositories.discord_tournament_grant_repository import (
    DiscordTournamentGrantRepository,
)
from application.repositories.tournament_repository import TournamentRepository
from application.repositories.user_role_repository import UserRoleRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord.discord_guild_ops import GuildMember
from application.services.discord.discord_service import DiscordService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tenant_service import TenantService
from application.services.user_service import UserService
from application.services.volunteer.volunteer_role_mapping_service import (
    VolunteerRoleMappingService,
)
from application.tenant_context import get_current_tenant_id, require_tenant_id, tenant_scope
from models import (
    DiscordRoleMapping,
    MembershipSource,
    Role,
    RoleSource,
    Tenant,
    TournamentGrant,
    User,
)

logger = logging.getLogger(__name__)


def _grant_key(grant: Tuple[Role, Optional[int]]) -> Tuple[str, int]:
    return grant[0].value, grant[1] or 0


def _name(user: User) -> str:
    return getattr(user, 'display_name', None) or getattr(user, 'username', None) or f'#{user.id}'

# Audit vocabulary for a per-tournament grant, keyed by which relation it writes.
# Reuses the actions a staff member's hand-grant already emits so the tournament's
# audit trail reads as one story; ``source`` in the details says who made it.
_GRANT_AUDIT = {
    TournamentGrant.TOURNAMENT_ADMIN: (
        AuditActions.TOURNAMENT_ADMIN_GRANTED, AuditActions.TOURNAMENT_ADMIN_REVOKED,
    ),
    TournamentGrant.CREW_COORDINATOR: (
        AuditActions.TOURNAMENT_CREW_COORDINATOR_GRANTED,
        AuditActions.TOURNAMENT_CREW_COORDINATOR_REVOKED,
    ),
}


class DiscordRoleMappingService:
    """Service for Discord-role-to-app-role mappings and login-time sync."""

    def __init__(self) -> None:
        self.mapping_repository = DiscordRoleMappingRepository()
        self.role_repository = UserRoleRepository()
        self.grant_repository = DiscordTournamentGrantRepository()
        self.sync_repository = DiscordRoleSyncRepository()
        self.tournament_repository = TournamentRepository()
        self.audit_service = AuditService()

    async def list_all_mappings(self) -> List[DiscordRoleMapping]:
        return await self.mapping_repository.get_all()

    async def list_mappings(self, guild_id: int) -> List[DiscordRoleMapping]:
        return await self.mapping_repository.list_for_guild(guild_id)

    async def add_mapping(
        self,
        guild_id: int,
        discord_role_id: int,
        discord_role_name: str,
        actor: User,
        app_role: Optional[Role] = None,
        tournament_grant: Optional[TournamentGrant] = None,
        tournament_id: Optional[int] = None,
        event_id: Optional[int] = None,
    ) -> DiscordRoleMapping:
        """Map a guild role onto either a tenant-wide role or one tournament's grant.

        Exactly one of ``app_role`` / ``tournament_grant`` may be given, and a
        tournament grant must name the tournament it lands on — a guild role is
        guild-wide, so without one there is nothing to scope the grant to. A
        per-event ``app_role`` lands in ``event_id``, by default the community's
        default event as it is now.
        """
        await self._ensure_can_manage(actor)
        mapping = await self._add_one(
            guild_id, discord_role_id, discord_role_name, actor,
            app_role, tournament_grant, tournament_id,
            await self._mapping_event_id(app_role, event_id),
        )
        if mapping is None:
            raise ValueError(
                "That Discord role is already mapped to this app role" if app_role is not None
                else "That Discord role is already mapped to this tournament grant"
            )
        return mapping

    async def add_mappings(
        self,
        guild_id: int,
        discord_roles: Sequence[Tuple[int, str]],
        actor: User,
        app_roles: Sequence[Role] = (),
        tournament_grants: Sequence[TournamentGrant] = (),
        tournament_id: Optional[int] = None,
        event_id: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Map every ``discord_roles`` entry onto every grant; ``(created, already_mapped)``.

        A pair that already exists is skipped rather than failing the batch, so
        adding "these five roles get Proctor" works when two of them already do.
        Every check runs before the first write: a bad grant or tournament leaves
        nothing half-created.
        """
        await self._ensure_can_manage(actor)
        if not discord_roles:
            raise ValueError("Pick at least one Discord role")
        if not app_roles and not tournament_grants:
            raise ValueError("Pick at least one thing to grant")
        for role in app_roles:
            self._check_app_role(role)
        per_event = [role for role in app_roles if role.is_per_event]
        event_id = await self._mapping_event_id(per_event[0], event_id) if per_event else None
        if tournament_grants:
            if tournament_id is None:
                raise ValueError("Pick the tournament this grant applies to")
            require_found(await self.tournament_repository.get_by_id(tournament_id), "Tournament")

        grants: List[Tuple[Optional[Role], Optional[TournamentGrant]]] = [
            *((role, None) for role in app_roles),
            *((None, grant) for grant in tournament_grants),
        ]
        created = skipped = 0
        for discord_role_id, discord_role_name in discord_roles:
            for app_role, grant in grants:
                mapping = await self._add_one(
                    guild_id, discord_role_id, discord_role_name, actor,
                    app_role, grant, tournament_id if grant is not None else None,
                    event_id if app_role is not None and app_role.is_per_event else None,
                )
                if mapping is None:
                    skipped += 1
                else:
                    created += 1
        return created, skipped

    @staticmethod
    async def _ensure_can_manage(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can manage Discord role mappings",
        )

    @staticmethod
    def _check_app_role(app_role: Role) -> None:
        if app_role not in Role.tenant_grantable():
            # The other half of the same door ``UserService.grant_role``
            # closes: a mapping is a *standing* grant, so this one would have
            # handed platform authority to whoever holds a guild role the
            # community's own staff control, on their next login.
            raise ValueError(
                "Super Admin is a platform role and cannot be mapped from a "
                "Discord role."
            )

    @staticmethod
    async def _mapping_event_id(app_role: Optional[Role], event_id: Optional[int]) -> Optional[int]:
        """The event a mapping onto ``app_role`` grants in: none unless it's a
        per-event role, then ``event_id`` (one of this community's) or the default."""
        if app_role is None or not app_role.is_per_event:
            return None
        from application.services.event_service import EventService

        tenant_id = require_tenant_id()
        if event_id is None:
            event_id = await EventService.default_event_id(tenant_id)
        event = require_found(await EventService.ref(tenant_id, event_id), 'Event')
        if event.is_archived:
            raise ValueError(f'{event.name} is archived, so a Discord role can\'t grant anything in it.')
        return event.id

    async def _add_one(
        self,
        guild_id: int,
        discord_role_id: int,
        discord_role_name: str,
        actor: User,
        app_role: Optional[Role],
        tournament_grant: Optional[TournamentGrant],
        tournament_id: Optional[int],
        event_id: Optional[int] = None,
    ) -> Optional[DiscordRoleMapping]:
        """Validate and create one mapping; None when that exact pair already exists."""
        if (app_role is None) == (tournament_grant is None):
            raise ValueError("Choose either an application role or a tournament grant")

        if discord_role_id in await self.sync_repository.rule_role_ids():
            # The reverse of DiscordRoleSyncService's INBOUND_CONFLICT: a role
            # Wizzrobe hands out to every player would then grant this to them.
            raise ValueError(
                "Wizzrobe already gives this Discord role out (Discord roles from "
                "Wizzrobe). Mapping it here would grant this to everyone it covers, "
                "so pick a different role."
            )
        details = {
            'guild_id': guild_id,
            'discord_role_id': discord_role_id,
            'discord_role_name': discord_role_name,
        }
        if app_role is not None:
            self._check_app_role(app_role)
            if tournament_id is not None:
                raise ValueError("An application role applies community-wide, not to one tournament")
            if await self.mapping_repository.get_match(guild_id, discord_role_id, app_role, event_id):
                return None
            details['app_role'] = app_role.value
            if event_id is not None:
                details['event_id'] = event_id
        else:
            if tournament_id is None:
                raise ValueError("Pick the tournament this grant applies to")
            tournament = require_found(
                await self.tournament_repository.get_by_id(tournament_id), "Tournament"
            )
            if await self.mapping_repository.get_tournament_match(
                guild_id, discord_role_id, tournament_grant, tournament_id  # type: ignore[arg-type]
            ):
                return None
            details['tournament_grant'] = tournament_grant.value  # type: ignore[union-attr]
            details['tournament_id'] = tournament.id

        mapping = await self.mapping_repository.create(
            guild_id, discord_role_id, discord_role_name,
            app_role=app_role, tournament_grant=tournament_grant, tournament_id=tournament_id,
            event_id=event_id,
        )
        await self.audit_service.write_log(
            actor, AuditActions.DISCORD_ROLE_MAPPING_ADDED, details,
        )
        return mapping

    async def remove_mapping(self, mapping_id: int, actor: User) -> None:
        await self._ensure_can_manage(actor)
        mapping = require_found(await self.mapping_repository.get_by_id(mapping_id), "Mapping")
        await self._remove_one(mapping, actor)

    async def remove_mappings(self, mapping_ids: Sequence[int], actor: User) -> int:
        """Remove each mapping in ``mapping_ids``; returns how many were removed.

        An id that is already gone (another tab removed it) is skipped, so the
        rest of a multi-row removal still lands.
        """
        await self._ensure_can_manage(actor)
        removed = 0
        for mapping_id in mapping_ids:
            mapping = await self.mapping_repository.get_by_id(mapping_id)
            if mapping is None:
                continue
            await self._remove_one(mapping, actor)
            removed += 1
        return removed

    async def _remove_one(self, mapping: DiscordRoleMapping, actor: User) -> None:
        details = {
            'guild_id': mapping.guild_id,
            'discord_role_id': mapping.discord_role_id,
            'discord_role_name': mapping.discord_role_name,
        }
        if mapping.app_role is not None:
            details['app_role'] = mapping.app_role.value
            if getattr(mapping, 'event_id', None) is not None:
                details['event_id'] = mapping.event_id  # type: ignore[attr-defined]
        else:
            details['tournament_grant'] = mapping.tournament_grant.value
            details['tournament_id'] = mapping.tournament_id
        await self.mapping_repository.delete(mapping)
        await self.audit_service.write_log(
            actor, AuditActions.DISCORD_ROLE_MAPPING_REMOVED, details
        )

    async def sync_all_users(self, actor: User) -> dict:
        """Force a Discord-role sync for this community, now.

        Applies the current mappings immediately instead of waiting for each
        person to next log in. Covers this community's members plus every
        account holding one of its mapped roles in its server: not every
        account on the platform, whose other communities this staff member has
        no say over. Reuses the defensive per-user sync, so an unreachable
        Discord or a single bad user never aborts the run.

        First provisions an account for every member of the community's guild
        who holds a mapped role but has never signed in, so a role handed out
        before the mapping existed still reaches its holder.

        The summary names who it created and whose roles it changed (``created``
        and ``changed``, display names), so staff can see what one press did.
        """
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can sync Discord roles",
        )

        # Before the user list is read, so the accounts it creates are synced
        # (and handed their roles) by the loop below in the same run.
        created, holders = await self._provision_current_guild_members()
        tenant_id = get_current_tenant_id()
        tenant = await TenantService.get_by_id(tenant_id) if tenant_id is not None else None
        people: dict = {}
        for user in [*await TenantMembershipService().list_members(), *holders]:
            if user.discord_id and not getattr(user, 'is_system', False):
                people.setdefault(user.id, user)

        summary: dict = {
            'users_processed': len(people),
            'users_created': len(created),
            'granted': 0,
            'revoked': 0,
            'skipped': 0,
            'created': [_name(u) for u in created],
            'changed': [],
        }
        changed_ids: List[int] = []
        for user in people.values():
            if tenant is None:
                summary['skipped'] += 1
                continue
            result = await self.sync_user_roles_for_tenant(user, tenant)
            granted = list(result.get('granted') or []) + list(result.get('tournament_granted') or [])
            revoked = list(result.get('revoked') or []) + list(result.get('tournament_revoked') or [])
            summary['granted'] += len(granted)
            summary['revoked'] += len(revoked)
            if result.get('skipped'):
                summary['skipped'] += 1
            if granted or revoked:
                changed_ids.append(user.id)
                summary['changed'].append(
                    {'name': _name(user), 'granted': granted, 'revoked': revoked}
                )

        await self.audit_service.write_log(
            actor, AuditActions.ROLE_DISCORD_SYNC_BULK,
            {
                **{k: summary[k] for k in
                   ('users_processed', 'users_created', 'granted', 'revoked', 'skipped')},
                'created_user_ids': [u.id for u in created],
                'changed_user_ids': changed_ids,
            },
        )
        return summary

    async def provision_member(
        self, tenants: Iterable[Tenant], member: GuildMember,
    ) -> Optional[User]:
        """The account for a guild member whose roles one of ``tenants`` maps.

        ``None`` when none of the member's Discord roles confers anything in
        those tenants, so holding an unmapped role (or none) never creates an
        account. Creation is get-or-create, so a concurrent login is harmless.
        """
        if member.role_ids & await self._conferring_role_ids(tenants):
            user, _created = await UserService().provision_from_discord_role_sync(
                member.id, member.username, member.avatar,
            )
            return user
        return None

    async def _provision_current_guild_members(self) -> Tuple[List[User], List[User]]:
        """Accounts for the current guild's mapped-role holders. Never raises.

        Scoped to the community whose staff pressed the button: its guild, its
        mappings. Returns ``(created, holders)``: the accounts this made, and
        every holder's account, new or not.
        """
        tenant_id = get_current_tenant_id()
        try:
            tenant = await TenantService.get_by_id(tenant_id) if tenant_id is not None else None
            if tenant is None or not tenant.discord_guild_id:
                return [], []
            role_ids = await self._conferring_role_ids([tenant])
            if not role_ids:
                return [], []
            ok, payload = await DiscordService().list_members_with_roles(
                tenant.discord_guild_id, role_ids,
            )
        except Exception:
            logger.exception('Failed to list guild members to provision for tenant %s', tenant_id)
            return [], []
        if not ok or isinstance(payload, str):
            logger.warning(
                'Skipped provisioning guild members for tenant %s: %s', tenant.id, payload,
            )
            return [], []

        created: List[User] = []
        holders: List[User] = []
        for member in payload:
            try:
                user, was_created = await UserService().provision_from_discord_role_sync(
                    member.id, member.username, member.avatar,
                )
            except Exception:
                logger.exception(
                    'Failed to provision discord_id=%s for tenant %s', member.id, tenant.id,
                )
                continue
            holders.append(user)
            if was_created:
                created.append(user)
        return created, holders

    async def _conferring_role_ids(self, tenants: Iterable[Tenant]) -> Set[int]:
        """Discord role ids whose mapping would grant something in ``tenants``.

        The same filter the sync applies to a *held* role: a grantable app role,
        or a tournament grant on an active tournament. A mapping the sync would
        ignore must not create an account either.
        """
        grantable = set(Role.tenant_grantable())
        role_ids: Set[int] = set()
        for tenant in tenants:
            if not tenant.discord_guild_id:
                continue
            with tenant_scope(tenant.id):
                mappings = await self.mapping_repository.list_for_guild(tenant.discord_guild_id)
            role_ids.update(
                m.discord_role_id for m in mappings
                if m.app_role in grantable
                or (m.tournament_grant is not None
                    and m.tournament is not None and m.tournament.is_active)
            )
        return role_ids

    async def sync_user_roles(self, user: User) -> dict:
        """Login-time sync across **every** tenant whose Discord guild this user is in.

        In a multi-tenant deployment a user may belong to several tenants' guilds,
        so login-time sync fans out over all tenants that have a
        ``discord_guild_id`` and syncs each independently (each in its own tenant
        scope). Defensive by design: never raises; a failure in one tenant leaves
        its roles untouched and does not abort the others.
        """
        summary: dict = {
            'granted': [], 'revoked': [],
            'tournament_granted': [], 'tournament_revoked': [],
            'skipped': None,
        }
        try:
            tenants = [t for t in await TenantService.list_tenants() if t.discord_guild_id]
            if not tenants:
                summary['skipped'] = 'no_guild_configured'
                return summary
            # Sync every tenant concurrently: each per-tenant sync is independent
            # (own tenant scope, own Discord call) and never raises, so login
            # latency stays ~one Discord round-trip instead of scaling with the
            # tenant count. asyncio.gather runs each in its own context copy, so
            # the tenant_scope contextvar in one does not leak into another.
            results = await asyncio.gather(*(
                self.sync_user_roles_for_tenant(user, tenant) for tenant in tenants
            ))
            skips: Set[str] = set()
            for result in results:
                for key in ('granted', 'revoked', 'tournament_granted', 'tournament_revoked'):
                    summary[key] += result.get(key) or []
                if result.get('skipped'):
                    skips.add(result['skipped'])
            if not any(
                summary[k] for k in
                ('granted', 'revoked', 'tournament_granted', 'tournament_revoked')
            ) and skips:
                # Surface a single representative skip reason when nothing changed.
                summary['skipped'] = 'discord_unavailable' if 'discord_unavailable' in skips else next(iter(skips))
            return summary
        except Exception:
            logger.exception(
                'Unexpected error during Discord role sync for user %s',
                getattr(user, 'id', None),
            )
            summary['skipped'] = 'error'
            return summary

    @staticmethod
    async def _writable_event_ids(tenant_id: int) -> Set[int]:
        """This community's events a sync may grant in: every one not archived."""
        from application.services.event_service import EventService

        return {e.id for e in await EventService.events_for_tenant(tenant_id) if not e.is_archived}

    async def sync_user_roles_for_tenant(self, user: User, tenant: Tenant) -> dict:
        """Sync a user's Discord-sourced app roles for ONE tenant, scoped to it.

        Uses the tenant's own ``discord_guild_id`` (the routing key) and wraps all
        role reads/writes in ``tenant_scope(tenant.id)`` so grants/revokes land on
        that tenant's ``UserRole`` rows. Never raises (fail-open).
        """
        summary: dict = {
            'granted': [], 'revoked': [],
            'tournament_granted': [], 'tournament_revoked': [],
            'skipped': None,
        }
        guild_id = tenant.discord_guild_id
        if not guild_id:
            summary['skipped'] = 'no_guild_configured'
            return summary
        try:
            ok, payload = await DiscordService().get_member_role_ids(guild_id, user.discord_id)
            if not ok:
                logger.warning('Discord role sync skipped for user %s in tenant %s: %s', user.id, tenant.id, payload)
                summary['skipped'] = 'discord_unavailable'
                return summary
            member_role_ids: Set[int] = payload  # type: ignore[assignment]

            with tenant_scope(tenant.id):
                mappings = await self.mapping_repository.list_for_guild(guild_id)
                # Filtered here as well as at ``add_mapping``, because this side
                # is what a *stored* row reaches. A mapping written before the
                # grantable check existed — or restored from a backup — would
                # otherwise grant platform authority on the next login, silently,
                # with no one performing an action to notice.
                grantable = set(Role.tenant_grantable())
                # A grant is a role and the event it's held in (None for a
                # community-wide role). A per-event mapping grants in the event
                # it names; one naming none, or an archived event, grants
                # nothing, and the archived event's own grants are left alone.
                writable = await self._writable_event_ids(tenant.id)
                desired: Set[Tuple[Role, Optional[int]]] = set()
                for m in mappings:
                    if m.app_role not in grantable or m.discord_role_id not in member_role_ids:
                        continue
                    event_id = m.event_id if m.app_role.is_per_event else None  # type: ignore[attr-defined]
                    if m.app_role.is_per_event and event_id not in writable:
                        continue
                    desired.add((m.app_role, event_id))
                held = await self.role_repository.list_for_user(user)
                current_all = {(r.role, r.event_id) for r in held}  # type: ignore[attr-defined]
                current_discord = {
                    (r.role, r.event_id) for r in held  # type: ignore[attr-defined]
                    if r.source == RoleSource.DISCORD
                    and (r.event_id is None or r.event_id in writable)  # type: ignore[attr-defined]
                }

                granting = sorted(desired - current_all, key=_grant_key)
                closed = None
                if granting:
                    # A role in a tenant implies membership in it. Once per sync,
                    # not once per role — the write is idempotent either way.
                    # Deliberately *before* the grants and inside the existing
                    # try: if it fails, this tenant is skipped and retried on the
                    # next login (fail-open, as the docstring promises), which is
                    # self-healing. Granting first would instead leave roles with
                    # no membership behind — the state wave 4's gate locks out.
                    closed = await TenantMembershipService.ensure_member(
                        user, source=MembershipSource.DISCORD_ROLE,
                    )
                for role, event_id in granting:
                    await self.role_repository.add(
                        user, role, granted_by=None, source=RoleSource.DISCORD, event_id=event_id,
                    )
                    details: dict = {
                        'role': role.value, 'source': RoleSource.DISCORD.value,
                        'tenant_id': tenant.id,
                    }
                    if event_id is not None:
                        details['event_id'] = event_id
                    # On the first grant's row: that's the one that let them in.
                    if closed is not None:
                        details['closed_request_id'] = closed.id
                        closed = None
                    await self.audit_service.write_log(
                        user, AuditActions.ROLE_DISCORD_SYNC_GRANTED, details,
                    )
                    summary['granted'].append(role.value)

                revoked_events: Set[Optional[int]] = set()
                for role, event_id in sorted(current_discord - desired, key=_grant_key):
                    await self.role_repository.remove(user, role, event_id=event_id)
                    details = {'role': role.value, 'source': RoleSource.DISCORD.value, 'tenant_id': tenant.id}
                    if event_id is not None:
                        details['event_id'] = event_id
                    await self.audit_service.write_log(user, AuditActions.ROLE_DISCORD_SYNC_REVOKED, details)
                    summary['revoked'].append(role.value)
                    revoked_events.add(event_id)
                # The Discord grant may have been the one row standing in for a
                # role a volunteer assignment also confers; hand it back, in
                # each event the sync took a role from.
                for event_id in sorted(e for e in revoked_events if e is not None):
                    with event_scope(event_id, tenant.id):
                        await VolunteerRoleMappingService().reconcile_users(user, [user.id])

                await self._sync_tournament_grants(
                    user, tenant, mappings, member_role_ids, summary
                )

            return summary
        except Exception:
            logger.exception(
                'Unexpected error during Discord role sync for user %s in tenant %s',
                getattr(user, 'id', None), getattr(tenant, 'id', None),
            )
            summary['skipped'] = 'error'
            return summary

    async def _sync_tournament_grants(
        self,
        user: User,
        tenant: Tenant,
        mappings: List[DiscordRoleMapping],
        member_role_ids: Set[int],
        summary: dict,
    ) -> None:
        """Reconcile ``Tournament.admins`` / ``.crew_coordinators`` from the mappings.

        Runs inside the caller's ``tenant_scope``. The revoke half works off
        ``DiscordTournamentGrant`` rows rather than the join tables themselves,
        which is what keeps a staff member's hand-made Tournament Admin safe: no
        provenance row, no revocation.

        A mapping onto an inactive tournament is treated as absent, so ending an
        event takes back the authority its guild role conferred, and re-opening it
        hands that authority back on the next sync.
        """
        desired: Set[Tuple[int, TournamentGrant]] = {
            (m.tournament_id, m.tournament_grant)  # type: ignore[attr-defined]
            for m in mappings
            if m.tournament_grant is not None
            and m.discord_role_id in member_role_ids
            and m.tournament is not None
            and m.tournament.is_active
        }
        recorded = await self.grant_repository.list_for_user(user)
        current: Set[Tuple[int, TournamentGrant]] = {
            (row.tournament_id, row.grant) for row in recorded  # type: ignore[attr-defined]
        }

        for tournament_id, grant in sorted(desired - current):
            # Already held by hand: leave it be rather than recording provenance
            # over it, or a later sync would revoke a grant it never made.
            if await self.tournament_repository.has_tournament_grant(tournament_id, user, grant):
                continue
            tournament = await self.tournament_repository.get_by_id(tournament_id)
            if tournament is None:
                continue
            # Same ordering rationale as the role grants above: membership first,
            # so a failure leaves no grant stranded without one.
            closed = await TenantMembershipService.ensure_member(
                user, source=MembershipSource.DISCORD_ROLE,
            )
            await self.tournament_repository.set_tournament_grant(
                tournament, user, grant, granted=True
            )
            await self.grant_repository.add(user, tournament, grant)
            await self.audit_service.write_log(
                user,
                _GRANT_AUDIT[grant][0],
                {
                    'tournament_id': tournament_id, 'target_user_id': user.id,
                    'source': RoleSource.DISCORD.value, 'tenant_id': tenant.id,
                    **({'closed_request_id': closed.id} if closed is not None else {}),
                },
            )
            summary['tournament_granted'].append(f'{grant.value}:{tournament_id}')

        for tournament_id, grant in sorted(current - desired):
            tournament = await self.tournament_repository.get_by_id(tournament_id)
            if tournament is not None:
                await self.tournament_repository.set_tournament_grant(
                    tournament, user, grant, granted=False
                )
            await self.grant_repository.remove(user, tournament_id, grant)
            await self.audit_service.write_log(
                user,
                _GRANT_AUDIT[grant][1],
                {
                    'tournament_id': tournament_id, 'target_user_id': user.id,
                    'source': RoleSource.DISCORD.value, 'tenant_id': tenant.id,
                },
            )
            summary['tournament_revoked'].append(f'{grant.value}:{tournament_id}')
