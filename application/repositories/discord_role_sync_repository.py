"""
DiscordRoleSync Repository - Data Access Layer

The rules, the ledger of roles Wizzrobe gave, the last-sync state, and the
reads that say who qualifies for each rule. Every read is tenant-scoped except
:meth:`DiscordRoleSyncRepository.tenant_ids_with_work`, the worker's scan.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from application.repositories._tenant import current_tenant_id, scoped
from models import (
    CheckInEventStatus,
    CheckInPass,
    CheckInTierLanyard,
    DiscordRoleGrant,
    DiscordRoleRule,
    DiscordRoleRuleKind,
    DiscordRoleSyncState,
    MatchPlayers,
    TournamentPlayers,
    User,
)


class DiscordRoleSyncRepository:
    """Data access for outbound Discord role sync."""

    @staticmethod
    async def list_rules() -> List[DiscordRoleRule]:
        return await scoped(DiscordRoleRule.all()).prefetch_related('tournament').order_by('id')

    @staticmethod
    async def get_rule(key: str) -> Optional[DiscordRoleRule]:
        return await DiscordRoleRule.get_or_none(tenant_id=current_tenant_id(), key=key)

    @staticmethod
    async def upsert_rule(
        key: str,
        kind: DiscordRoleRuleKind,
        discord_role_id: int,
        discord_role_name: str,
        tournament_id: Optional[int] = None,
        lanyard: Optional[CheckInTierLanyard] = None,
    ) -> DiscordRoleRule:
        rule = await DiscordRoleRule.get_or_none(tenant_id=current_tenant_id(), key=key)
        if rule is None:
            return await DiscordRoleRule.create(
                tenant_id=current_tenant_id(), key=key, kind=kind,
                tournament_id=tournament_id, lanyard=lanyard,
                discord_role_id=discord_role_id, discord_role_name=discord_role_name,
            )
        await scoped(DiscordRoleRule.filter(id=rule.id)).update(
            discord_role_id=discord_role_id, discord_role_name=discord_role_name,
            problem=None, holding=None,
        )
        await rule.refresh_from_db()
        return rule

    @staticmethod
    async def delete_rule(rule: DiscordRoleRule) -> None:
        await scoped(DiscordRoleRule.filter(id=rule.id)).delete()

    @staticmethod
    async def delete_all_rules() -> int:
        return await scoped(DiscordRoleRule.all()).delete()

    @staticmethod
    async def set_rule_health(rule_id: int, problem: Optional[str], holding: Optional[int]) -> None:
        await scoped(DiscordRoleRule.filter(id=rule_id)).update(problem=problem, holding=holding)

    @staticmethod
    async def rule_role_ids() -> Set[int]:
        return set(cast(List[int], await scoped(DiscordRoleRule.all()).values_list('discord_role_id', flat=True)))

    @staticmethod
    async def list_grants() -> List[Tuple[int, int]]:
        """``(user_id, discord_role_id)`` for every role Wizzrobe gave here."""
        return [
            (user_id, role_id) for user_id, role_id in
            await scoped(DiscordRoleGrant.all()).values_list('user_id', 'discord_role_id')
        ]

    @staticmethod
    async def add_grant(user_id: int, discord_role_id: int) -> None:
        await DiscordRoleGrant.get_or_create(
            tenant_id=current_tenant_id(), user_id=user_id, discord_role_id=discord_role_id,
        )

    @staticmethod
    async def delete_grant(user_id: int, discord_role_id: int) -> None:
        await scoped(DiscordRoleGrant.filter(user_id=user_id, discord_role_id=discord_role_id)).delete()

    @staticmethod
    async def get_state() -> Optional[DiscordRoleSyncState]:
        return await DiscordRoleSyncState.get_or_none(tenant_id=current_tenant_id())

    @staticmethod
    async def save_state(**values: Any) -> None:
        state, _ = await DiscordRoleSyncState.get_or_create(tenant_id=current_tenant_id())
        await scoped(DiscordRoleSyncState.filter(id=state.id)).update(**values)

    @staticmethod
    async def tenant_ids_with_work() -> Set[int]:
        """Tenants with a rule or a grant still to take back, across every tenant.

        Deliberately cross-tenant: the worker's scan, which then syncs each one
        inside its own ``tenant_scope``.
        """
        ids = set(cast(List[int], await DiscordRoleRule.all().distinct().values_list('tenant_id', flat=True)))
        ids.update(cast(List[int], await DiscordRoleGrant.all().distinct().values_list('tenant_id', flat=True)))
        return ids

    @staticmethod
    async def players_in_active_tournaments() -> Set[int]:
        return set(cast(List[int], await scoped(
            TournamentPlayers.filter(tournament__is_active=True)
        ).values_list('user_id', flat=True)))

    @staticmethod
    async def players_by_tournament() -> Dict[int, Set[int]]:
        found: Dict[int, Set[int]] = {}
        for tournament_id, user_id in await scoped(TournamentPlayers.all()).values_list(
            'tournament_id', 'user_id',
        ):
            found.setdefault(tournament_id, set()).add(user_id)
        return found

    @staticmethod
    async def players_in_live_matches(started_after: datetime) -> Set[int]:
        """Players of a match that has started, hasn't finished, and started after the cutoff."""
        return set(cast(List[int], await scoped(MatchPlayers.filter(
            match__started_at__isnull=False,
            match__started_at__gte=started_after,
            match__finished_at__isnull=True,
        )).values_list('user_id', flat=True)))

    @staticmethod
    async def badge_holders() -> Dict[CheckInTierLanyard, Set[int]]:
        """Linked accounts holding an unrefunded badge in an open check-in event, by lanyard."""
        found: Dict[CheckInTierLanyard, Set[int]] = {}
        rows = await scoped(CheckInPass.filter(
            event__status=CheckInEventStatus.OPEN,
            refunded_at__isnull=True,
            removed_at__isnull=True,
            entrant__user_id__isnull=False,
        )).values_list('tier__lanyard', 'entrant__user_id')
        for lanyard, user_id in rows:
            found.setdefault(CheckInTierLanyard(lanyard), set()).add(user_id)
        return found

    @staticmethod
    async def people(user_ids: Set[int]) -> Dict[int, Tuple[Optional[int], str]]:
        """``user_id -> (discord_id, name)`` for the accounts a sync touches.

        ``User`` is global; the ids come from this tenant's own rows.
        """
        if not user_ids:
            return {}
        rows = await User.filter(id__in=list(user_ids)).values_list(
            'id', 'discord_id', 'display_name', 'username',
        )
        return {
            user_id: (discord_id, display_name or username or f'#{user_id}')
            for user_id, discord_id, display_name, username in rows
        }
