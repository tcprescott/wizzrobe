"""
DiscordRoleSync Service - Business Logic Layer

The outbound half of Discord role sync. :class:`DiscordRoleMappingService` reads
a member's Discord roles into app roles; this writes Discord roles out from what
Wizzrobe knows: who is enrolled in any tournament, who is in a match right now,
who is enrolled in a given tournament, and who holds a VIP, Base or Day pass
badge.

Three rules shape it:

* **Only take back what we gave.** :class:`~models.DiscordRoleGrant` records
  each role the bot added. A role someone already held (a moderator handed it
  out) is never recorded, so the sync never removes it.
* **Never write a role the bot can't manage.** Every role is checked against the
  bot's own permissions and position before it's saved and again on every sync;
  a rule that breaks later is marked with the reason and skipped.
* **Discord being down changes nothing.** A run that can't read the server
  records the error and stops before any write; the ledger is written only after
  Discord confirms each add or removal, so the next run picks up where it left.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional, Set, Tuple, Union, cast

from application.errors import require_found
from application.event_context import require_event_id
from application.feature_flags import requires_feature
from application.repositories.discord_role_mapping_repository import DiscordRoleMappingRepository
from application.repositories.discord_role_sync_repository import DiscordRoleSyncRepository
from application.repositories.tournament_repository import TournamentRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord.discord_service import DiscordService
from application.services.feature_flag_service import FeatureFlagService
from application.services.tenant_service import TenantService
from application.tenant_context import require_tenant_id
from application.utils.shared_lock import LockBusy, shared_lock
from models import (
    CheckInTierLanyard,
    DiscordRoleRule,
    DiscordRoleRuleKind,
    DiscordRoleSyncState,
    FeatureFlag,
    User,
)

logger = logging.getLogger(__name__)

LIVE_MATCH_WINDOW = timedelta(hours=12)

UNREACHABLE_CAP = 300

AUDIT_REASON = 'Wizzrobe role sync'

NO_DISCORD = 'no_discord'
NOT_IN_SERVER = 'not_in_server'

INBOUND_CONFLICT = (
    "Role Mappings already uses this role to grant something in Wizzrobe. Giving it "
    "out here would hand that to everyone it covers, so pick a different role."
)
BADGES_OFF = "Event check-in is turned off for this community, so no one has a badge."



def rule_key(
    kind: DiscordRoleRuleKind,
    tournament_id: Optional[int] = None,
    lanyard: Optional[CheckInTierLanyard] = None,
) -> str:
    if kind == DiscordRoleRuleKind.TOURNAMENT:
        return f'tournament:{tournament_id}'
    if kind == DiscordRoleRuleKind.BADGE and lanyard is not None:
        return f'badge:{lanyard.value}'
    return kind.value


def role_problem(role: Optional[Mapping], access: Mapping) -> Optional[str]:
    """Why the bot can't hand ``role`` out, or None when it can.

    ``role`` is one entry of ``list_guild_roles`` (None when it's gone from the
    server); ``access`` is ``bot_role_access``. The order is the order to fix
    them in: a missing permission makes the rest moot.
    """
    if role is None:
        return 'This role no longer exists in the Discord server.'
    if not role.get('mappable', True):
        return (
            "Discord manages this role (it belongs to a bot, server boosts or an "
            "integration), so no one can hand it out."
        )
    if not access.get('manage_roles'):
        return (
            "The bot doesn't have Manage Roles. Turn it on for the Wizzrobe role in "
            "Server Settings → Roles."
        )
    if int(role.get('position', 0)) >= int(access.get('top_position', 0)):
        return (
            "This role is above the bot's own role. Drag the Wizzrobe role above it "
            "in Server Settings → Roles."
        )
    return None


def _bot_gone(message: str) -> bool:
    """A failure that will repeat for every person: stop the run rather than walk the list."""
    return message.startswith('Discord bot not') or 'not connected' in message


@dataclass(frozen=True)
class RoleChoice:
    """One Discord role as the checklist's picker shows it."""

    id: int
    name: str
    color: int
    problem: Optional[str]


@dataclass
class RoleSyncOverview:
    rules: Dict[str, DiscordRoleRule]
    state: Optional[DiscordRoleSyncState]
    roles: List[RoleChoice]
    roles_error: Optional[str]
    badges_available: bool


@dataclass
class RoleSyncResult:
    added: int = 0
    removed: int = 0
    in_place: int = 0
    unreachable: int = 0
    error: Optional[str] = None
    skipped: bool = False
    problems: List[str] = field(default_factory=list)


ServerRead = Union[Tuple[int, List[dict], dict], str]


class DiscordRoleSyncService:
    """Rules, checks and the sync itself for Discord roles Wizzrobe gives."""

    def __init__(self) -> None:
        self.repository = DiscordRoleSyncRepository()
        self.mapping_repository = DiscordRoleMappingRepository()
        self.tournament_repository = TournamentRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_can_manage(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can manage the Discord roles Wizzrobe gives",
        )

    @requires_feature(FeatureFlag.DISCORD_ROLE_SYNC)
    async def overview(self, actor: User) -> RoleSyncOverview:
        """Everything the settings card shows: rules, last sync, and the server's roles."""
        await self._ensure_can_manage(actor)
        rules = {rule.key: rule for rule in await self.repository.list_rules()}
        state = await self.repository.get_state()
        badges_available = await FeatureFlagService().is_enabled(FeatureFlag.EVENT_CHECK_IN)
        roles: List[RoleChoice] = []
        roles_error: Optional[str] = None
        server = await self._read_server()
        if isinstance(server, str):
            roles_error = server
        else:
            _guild_id, guild_roles, access = server
            inbound = await self._inbound_role_ids()
            for role in sorted(guild_roles, key=lambda r: -int(r.get('position', 0))):
                if not role.get('mappable', True):
                    continue
                problem = role_problem(role, access)
                if problem is None and int(role['id']) in inbound:
                    problem = INBOUND_CONFLICT
                roles.append(RoleChoice(
                    id=int(role['id']), name=str(role['name']),
                    color=int(role.get('color') or 0), problem=problem,
                ))
            roles.sort(key=lambda r: r.problem is not None)
        return RoleSyncOverview(rules, state, roles, roles_error, badges_available)

    @requires_feature(FeatureFlag.DISCORD_ROLE_SYNC)
    async def set_rule(
        self,
        actor: User,
        kind: DiscordRoleRuleKind,
        discord_role_id: Optional[int],
        *,
        tournament_id: Optional[int] = None,
        lanyard: Optional[CheckInTierLanyard] = None,
    ) -> Optional[DiscordRoleRule]:
        """Give ``discord_role_id`` to everyone the rule covers, or None to turn the rule off.

        Turning a rule off takes the role back on the next sync from everyone
        Wizzrobe gave it to.
        """
        await self._ensure_can_manage(actor)
        if kind == DiscordRoleRuleKind.TOURNAMENT:
            if tournament_id is None:
                raise ValueError("Pick the tournament this role is for")
            tournament = require_found(await self.tournament_repository.get_by_id(tournament_id), "Tournament")
            if tournament.event_id != require_event_id():
                raise ValueError("That tournament belongs to another event")
            lanyard = None
        elif kind == DiscordRoleRuleKind.BADGE:
            if lanyard is None:
                raise ValueError("Pick the badge this role is for")
            tournament_id = None
        else:
            tournament_id = lanyard = None
        key = rule_key(kind, tournament_id, lanyard)
        details: Dict[str, object] = {'rule': key}

        if discord_role_id is None:
            rule = await self.repository.get_rule(key)
            if rule is None:
                return None
            await self.repository.delete_rule(rule)
            details.update(discord_role_id=rule.discord_role_id, discord_role_name=rule.discord_role_name)
            await self.audit_service.write_log(actor, AuditActions.DISCORD_ROLE_RULE_CLEARED, details)
            request_sync(require_tenant_id())
            return None

        server = await self._read_server()
        if isinstance(server, str):
            raise ValueError(f"Couldn't check that role with Discord: {server}")
        _guild_id, guild_roles, access = server
        role = next((r for r in guild_roles if int(r['id']) == discord_role_id), None)
        problem = role_problem(role, access)
        if problem is None and discord_role_id in await self._inbound_role_ids():
            problem = INBOUND_CONFLICT
        if problem is not None or role is None:
            raise ValueError(problem)
        rule = await self.repository.upsert_rule(
            key, kind, discord_role_id, str(role['name']),
            tournament_id=tournament_id, lanyard=lanyard,
        )
        details.update(discord_role_id=discord_role_id, discord_role_name=str(role['name']))
        await self.audit_service.write_log(actor, AuditActions.DISCORD_ROLE_RULE_SET, details)
        request_sync(require_tenant_id())
        return rule

    @requires_feature(FeatureFlag.DISCORD_ROLE_SYNC)
    async def clear_all(self, actor: User) -> RoleSyncResult:
        """Turn every rule off and take back every role Wizzrobe gave, now."""
        await self._ensure_can_manage(actor)
        count = await self.repository.delete_all_rules()
        await self.audit_service.write_log(actor, AuditActions.DISCORD_ROLE_RULES_RESET, {'rules': count})
        return await self._sync(wait=True)

    @requires_feature(FeatureFlag.DISCORD_ROLE_SYNC)
    async def sync_now(self, actor: User) -> RoleSyncResult:
        await self._ensure_can_manage(actor)
        return await self._sync(wait=True)

    @requires_feature(FeatureFlag.DISCORD_ROLE_SYNC)
    async def sync_tenant(self) -> RoleSyncResult:
        """The worker's run: skipped, not queued, when one is already going."""
        return await self._sync(wait=False)

    async def _sync(self, wait: bool) -> RoleSyncResult:
        try:
            async with shared_lock('discord.role_sync', require_tenant_id(), wait=wait):
                return await self._run()
        except LockBusy:
            return RoleSyncResult(skipped=True)

    async def _read_server(self) -> ServerRead:
        """``(guild_id, roles, bot_access)`` for this community's server, or why not."""
        tenant = await TenantService.get_by_id(require_tenant_id())
        if tenant is None or not tenant.discord_guild_id:
            return "No Discord server is linked to this community."
        discord = DiscordService()
        ok, roles = await discord.list_guild_roles(tenant.discord_guild_id)
        if not ok or isinstance(roles, str):
            return str(roles)
        ok, access = await discord.bot_role_access(tenant.discord_guild_id)
        if not ok or isinstance(access, str):
            return str(access)
        return tenant.discord_guild_id, list(roles), dict(access)

    async def _inbound_role_ids(self) -> Set[int]:
        return {m.discord_role_id for m in await self.mapping_repository.get_all()}

    async def _qualifying(self, rules: List[DiscordRoleRule], badges_on: bool) -> Dict[int, Set[int]]:
        """``rule.id -> user ids`` it covers right now, each within its own event."""
        any_tournament: Dict[int, Set[int]] = {}
        live: Dict[int, Set[int]] = {}
        badges: Dict[int, Dict[CheckInTierLanyard, Set[int]]] = {}
        by_tournament: Optional[Dict[int, Set[int]]] = None
        live_after = datetime.now(timezone.utc) - LIVE_MATCH_WINDOW

        found: Dict[int, Set[int]] = {}
        for rule in rules:
            event_id = cast(int, rule.event_id)
            if rule.kind == DiscordRoleRuleKind.ANY_TOURNAMENT:
                if event_id not in any_tournament:
                    any_tournament[event_id] = await self.repository.players_in_active_tournaments(event_id)
                found[rule.id] = any_tournament[event_id]
            elif rule.kind == DiscordRoleRuleKind.LIVE_MATCH:
                if event_id not in live:
                    live[event_id] = await self.repository.players_in_live_matches(event_id, live_after)
                found[rule.id] = live[event_id]
            elif rule.kind == DiscordRoleRuleKind.TOURNAMENT:
                if by_tournament is None:
                    by_tournament = await self.repository.players_by_tournament()
                found[rule.id] = by_tournament.get(rule.tournament_id or 0, set())
            elif rule.lanyard is not None and badges_on:
                if event_id not in badges:
                    badges[event_id] = await self.repository.badge_holders(event_id)
                found[rule.id] = badges[event_id].get(rule.lanyard, set())
        return found

    async def _fail(self, message: str) -> RoleSyncResult:
        await self.repository.save_state(last_error=message)
        return RoleSyncResult(error=message)

    async def _run(self) -> RoleSyncResult:
        rules = await self.repository.list_live_rules()
        grants = await self.repository.list_grants()
        if not rules and not grants:
            await self.repository.save_state(
                last_synced_at=datetime.now(timezone.utc), last_error=None,
                in_place=0, added=0, removed=0, unreachable=[],
            )
            return RoleSyncResult()
        server = await self._read_server()
        if isinstance(server, str):
            return await self._fail(server)
        guild_id, guild_roles, access = server
        roles_by_id = {int(r['id']): r for r in guild_roles}

        badges_on = await FeatureFlagService().is_enabled(FeatureFlag.EVENT_CHECK_IN)
        inbound = await self._inbound_role_ids()
        problems: Dict[int, Optional[str]] = {}
        for role_id in {r.discord_role_id for r in rules} | {rid for _, rid in grants}:
            problem = role_problem(roles_by_id.get(role_id), access)
            if problem is None and role_id in inbound:
                problem = INBOUND_CONFLICT
            problems[role_id] = problem

        qualifying = await self._qualifying(rules, badges_on)
        desired: Dict[int, Set[int]] = {}
        giver: Dict[Tuple[int, int], Optional[int]] = {}
        for rule in rules:
            if problems.get(rule.discord_role_id) is None:
                for user_id in qualifying.get(rule.id, set()):
                    desired.setdefault(user_id, set()).add(rule.discord_role_id)
                    giver.setdefault((user_id, rule.discord_role_id), rule.event_id)

        people = await self.repository.people(set(desired) | {uid for uid, _ in grants})
        discord = DiscordService()
        ok, snapshot = await discord.member_role_snapshot(
            guild_id, {did for did, _ in people.values() if did},
        )
        if not ok or isinstance(snapshot, str):
            return await self._fail(str(snapshot))
        held: Dict[int, Set[int]] = {did: set(roles) for did, roles in snapshot.items()}

        result = RoleSyncResult()
        unreachable: Dict[int, Dict[str, object]] = {}
        failures: List[str] = []
        for user_id in sorted(desired):
            discord_id, name = people.get(user_id, (None, f'#{user_id}'))
            if not discord_id:
                unreachable[user_id] = {'user_id': user_id, 'name': name, 'reason': NO_DISCORD}
                continue
            if discord_id not in held:
                unreachable[user_id] = {'user_id': user_id, 'name': name, 'reason': NOT_IN_SERVER}
                continue
            for role_id in sorted(desired[user_id] - held[discord_id]):
                ok, message = await discord.add_role_to_user(guild_id, discord_id, role_id, reason=AUDIT_REASON)
                if not ok:
                    if _bot_gone(message):
                        return await self._fail(message)
                    failures.append(message)
                    continue
                await self.repository.add_grant(user_id, role_id, giver.get((user_id, role_id)))
                held[discord_id].add(role_id)
                result.added += 1

        for user_id, role_id in grants:
            if role_id in desired.get(user_id, set()):
                continue
            if role_id not in roles_by_id:
                await self.repository.delete_grant(user_id, role_id)
                continue
            if problems.get(role_id) is not None:
                continue
            discord_id = people.get(user_id, (None, ''))[0]
            if not discord_id or role_id not in held.get(discord_id, set()):
                await self.repository.delete_grant(user_id, role_id)
                continue
            ok, message = await discord.remove_role_from_user(guild_id, discord_id, role_id, reason=AUDIT_REASON)
            if not ok:
                if _bot_gone(message):
                    return await self._fail(message)
                failures.append(message)
                continue
            await self.repository.delete_grant(user_id, role_id)
            held[discord_id].discard(role_id)
            result.removed += 1

        for rule in rules:
            problem = problems.get(rule.discord_role_id)
            if problem is None and rule.kind == DiscordRoleRuleKind.BADGE and not badges_on:
                problem = BADGES_OFF
            holding = sum(
                1 for user_id in qualifying.get(rule.id, set())
                if rule.discord_role_id in held.get(people.get(user_id, (None, ''))[0] or 0, set())
            )
            result.in_place += holding
            if problem:
                result.problems.append(f'{rule.discord_role_name}: {problem}')
            await self.repository.set_rule_health(rule.id, problem, holding)

        result.unreachable = len(unreachable)
        if failures:
            result.error = f"{len(failures)} role change{'s' if len(failures) != 1 else ''} failed: {failures[-1]}"
        await self.repository.save_state(
            last_synced_at=datetime.now(timezone.utc),
            last_error=result.error,
            in_place=result.in_place,
            added=result.added,
            removed=result.removed,
            unreachable=sorted(unreachable.values(), key=lambda p: str(p['name']).lower())[:UNREACHABLE_CAP],
        )
        if result.added or result.removed or failures:
            logger.info(
                'Discord role sync for tenant %s: +%s -%s, %s failed',
                require_tenant_id(), result.added, result.removed, len(failures),
            )
        return result


# Module-level on purpose: one worker process, and it holds tenant ids, never per-user state.
_pending: Set[int] = set()


def request_sync(tenant_id: Optional[int]) -> None:
    if tenant_id is not None:
        _pending.add(tenant_id)


def take_pending() -> Set[int]:
    pending = set(_pending)
    _pending.clear()
    return pending
