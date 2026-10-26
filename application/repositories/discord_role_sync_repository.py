"""
DiscordRoleSync Repository - Data Access Layer

The rules, the ledger of roles Wizzrobe gave, the last-sync state, and the
reads that say who qualifies for each rule. Every read is tenant-scoped except
:meth:`DiscordRoleSyncRepository.tenant_ids_with_work`, the worker's scan.

Rules belong to an event (SGL 2026's "Racing now" role is set on SGL 2026);
a sync works through the rules of every live event at once, because the roles
live on one Discord server. Grants are the community's, tagged with the event
whose rule gave them.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from application.repositories._event import current_event_id, event_scoped, live_event_scoped
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
        """The current event's rules."""
        return await event_scoped(DiscordRoleRule.all()).prefetch_related('tournament').order_by('id')

    @staticmethod
    async def list_live_rules() -> List[DiscordRoleRule]:
        """Every live event's rules (cross-event), for a sync."""
        return await live_event_scoped(DiscordRoleRule.all()).prefetch_related('tournament').order_by('id')

    @staticmethod
    async def get_rule(key: str) -> Optional[DiscordRoleRule]:
        return await DiscordRoleRule.get_or_none(tenant_id=current_tenant_id(), event_id=current_event_id(), key=key)

    @staticmethod
    async def upsert_rule(
        key: str,
        kind: DiscordRoleRuleKind,
        discord_role_id: int,
        discord_role_name: str,
        tournament_id: Optional[int] = None,
        lanyard: Optional[CheckInTierLanyard] = None,
    ) -> DiscordRoleRule:
        rule = await DiscordRoleRule.get_or_none(tenant_id=current_tenant_id(), event_id=current_event_id(), key=key)
        if rule is None:
            return await DiscordRoleRule.create(
                tenant_id=current_tenant_id(), event_id=current_event_id(), key=key, kind=kind,
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
        """Delete the current event's rules."""
        return await event_scoped(DiscordRoleRule.all()).delete()

    @staticmethod
    async def set_rule_health(rule_id: int, problem: Optional[str], holding: Optional[int]) -> None:
        await scoped(DiscordRoleRule.filter(id=rule_id)).update(problem=problem, holding=holding)

    @staticmethod
    async def rule_role_ids() -> Set[int]:
        """Every role any event's rule gives (cross-event): roles are the server's, not an event's."""
        return set(cast(List[int], await scoped(DiscordRoleRule.all()).values_list('discord_role_id', flat=True)))

    @staticmethod
    async def list_grants() -> List[Tuple[int, int]]:
        """``(user_id, discord_role_id)`` for every role Wizzrobe gave here."""
        return [
            (user_id, role_id) for user_id, role_id in
            await scoped(DiscordRoleGrant.all()).values_list('user_id', 'discord_role_id')
        ]

    @staticmethod
    async def add_grant(user_id: int, discord_role_id: int, event_id: Optional[int]) -> None:
        """Record a role Wizzrobe gave, tagged with the event whose rule gave it."""
        await DiscordRoleGrant.get_or_create(
            tenant_id=current_tenant_id(), user_id=user_id, discord_role_id=discord_role_id,
            defaults={'event_id': event_id},
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

        Deliberately cross-tenant and cross-event: the worker's scan, which then
        syncs each one inside its own ``tenant_scope``.
        """
        ids = set(cast(List[int], await DiscordRoleRule.all().distinct().values_list('tenant_id', flat=True)))
        ids.update(cast(List[int], await DiscordRoleGrant.all().distinct().values_list('tenant_id', flat=True)))
        return ids

    @staticmethod
    async def players_in_active_tournaments(event_id: int) -> Set[int]:
        return set(cast(List[int], await scoped(
            TournamentPlayers.filter(tournament__is_active=True, tournament__event_id=event_id)
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
    async def players_in_live_matches(event_id: int, started_after: datetime) -> Set[int]:
        """Players of an event's match that has started, hasn't finished, and started after the cutoff."""
        return set(cast(List[int], await scoped(MatchPlayers.filter(
            match__event_id=event_id,
            match__started_at__isnull=False,
            match__started_at__gte=started_after,
            match__finished_at__isnull=True,
        )).values_list('user_id', flat=True)))

    @staticmethod
    async def badge_holders(event_id: int) -> Dict[CheckInTierLanyard, Set[int]]:
        """Linked accounts holding an unrefunded badge at an event's open check-in desk, by lanyard."""
        found: Dict[CheckInTierLanyard, Set[int]] = {}
        rows = await scoped(CheckInPass.filter(
            event__event_id=event_id,
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
