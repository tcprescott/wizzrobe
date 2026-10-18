"""
start.gg Repository - Data Access Layer

Pure data access for the read-only start.gg mirror (entrants, phases, pools,
sets), each tournament's event link, and the lookups that map entrants to
Wizzrobe users. No business logic.
"""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, cast

from tortoise.expressions import Q
from tortoise.functions import Lower

from application.repositories._tenant import current_tenant_id, scoped
from models import (
    Match,
    StartggEntrant,
    StartggPhase,
    StartggPool,
    StartggSet,
    StartggSetState,
    TenantMembership,
    Tournament,
    User,
)


class StartggRepository:
    """Repository for the start.gg mirror. Every mirror model is tenant-scoped."""

    # ------------------------------------------------------------------
    # Tournament link
    # ------------------------------------------------------------------
    @staticmethod
    async def set_event_link(
        tournament: Tournament, event_id: Optional[str], slug: Optional[str], name: Optional[str],
    ) -> None:
        await scoped(Tournament.filter(id=tournament.id)).update(
            startgg_event_id=event_id, startgg_event_slug=slug, startgg_event_name=name,
            startgg_last_synced_at=None, startgg_bookable_phase_id=None,
        )
        await tournament.refresh_from_db()

    @staticmethod
    async def record_sync(tournament: Tournament, when: datetime, name: Optional[str]) -> None:
        await scoped(Tournament.filter(id=tournament.id)).update(
            startgg_last_synced_at=when, startgg_event_name=name or tournament.startgg_event_name,
        )
        await tournament.refresh_from_db()

    @staticmethod
    async def set_bookable_phase(tournament: Tournament, phase_id: Optional[str]) -> None:
        await scoped(Tournament.filter(id=tournament.id)).update(startgg_bookable_phase_id=phase_id)
        await tournament.refresh_from_db()

    @staticmethod
    async def set_report_results(tournament: Tournament, enabled: bool) -> None:
        await scoped(Tournament.filter(id=tournament.id)).update(startgg_report_results=enabled)
        await tournament.refresh_from_db()

    @staticmethod
    async def linked_tournaments() -> List[Tournament]:
        return await scoped(Tournament.filter(startgg_event_slug__isnull=False))

    @staticmethod
    async def linked_tenant_ids() -> Set[int]:
        """Cross-tenant: every tenant with at least one linked event, for the sync worker."""
        rows = await Tournament.filter(startgg_event_slug__isnull=False).values_list('tenant_id', flat=True)
        return set(cast(List[int], rows))

    # ------------------------------------------------------------------
    # Identity lookups (global: users are not tenant-scoped)
    # ------------------------------------------------------------------
    @staticmethod
    async def users_by_startgg_ids(ids: Iterable[Optional[str]]) -> Dict[str, User]:
        wanted = list({i for i in ids if i})
        if not wanted:
            return {}
        return {u.startgg_user_id: u for u in await User.filter(startgg_user_id__in=wanted)}

    @staticmethod
    async def users_by_discord_ids(ids: Iterable[Optional[str]]) -> Dict[str, User]:
        wanted = list({int(i) for i in ids if i and str(i).isdigit()})
        if not wanted:
            return {}
        return {str(u.discord_id): u for u in await User.filter(discord_id__in=wanted)}

    @staticmethod
    async def members_by_lower_name(names: Iterable[str], tenant_id: int) -> Dict[str, List[User]]:
        """This community's members whose username, display name or linked
        start.gg gamer tag matches, lower-cased.

        Global user table filtered to one tenant's membership, for same-name
        suggestions. The gamer tag catches a player whose start.gg entry was added
        by name with no account behind it, so their link has no id to match.
        """
        wanted = list({n.lower() for n in names if n})
        if not wanted:
            return {}
        member_ids = await TenantMembership.filter(tenant_id=tenant_id).values_list('user_id', flat=True)
        users = await User.annotate(
            lu=Lower('username'), ld=Lower('display_name'), lg=Lower('startgg_username'),
        ).filter(
            Q(lu__in=wanted) | Q(ld__in=wanted) | Q(lg__in=wanted), id__in=list(member_ids), is_active=True,
        )
        found: Dict[str, List[User]] = {}
        for user in users:
            names = (user.username, user.display_name, user.startgg_username)
            for key in {(n or '').lower() for n in names}:
                if key in wanted:
                    found.setdefault(key, []).append(user)
        return found

    # ------------------------------------------------------------------
    # Entrants
    # ------------------------------------------------------------------
    @staticmethod
    async def list_entrants(tournament: Tournament) -> List[StartggEntrant]:
        return await scoped(StartggEntrant.filter(tournament=tournament)).prefetch_related(
            'user', 'suggested_user',
        ).order_by('name')

    @staticmethod
    async def count_entrants(tournament: Tournament) -> int:
        return await scoped(StartggEntrant.filter(tournament=tournament)).count()

    @staticmethod
    async def get_entrant(entrant_pk: int) -> Optional[StartggEntrant]:
        return await scoped(StartggEntrant.filter(id=entrant_pk)).prefetch_related(
            'user', 'suggested_user', 'tournament',
        ).first()

    @staticmethod
    async def upsert_entrant(tournament: Tournament, entrant_id: str, **values: Any) -> StartggEntrant:
        entrant, created = await StartggEntrant.get_or_create(
            tenant_id=current_tenant_id(), tournament=tournament, entrant_id=entrant_id,
            defaults=values,
        )
        if not created:
            await scoped(StartggEntrant.filter(id=entrant.id)).update(**values)
            await entrant.refresh_from_db()
        return entrant

    @staticmethod
    async def set_entrant_user(entrant: StartggEntrant, user: Optional[User], manual: bool) -> None:
        await scoped(StartggEntrant.filter(id=entrant.id)).update(
            user_id=user.id if user else None, user_assigned_manually=manual,
        )
        await entrant.refresh_from_db()

    @staticmethod
    async def dismiss_suggestion(entrant: StartggEntrant) -> None:
        await scoped(StartggEntrant.filter(id=entrant.id)).update(
            suggested_user_id=None, suggestion_dismissed=True,
        )
        await entrant.refresh_from_db()

    @staticmethod
    async def entrant_tournament_ids_for_user(user: User) -> Set[int]:
        rows = await scoped(StartggEntrant.filter(user=user)).values_list('tournament_id', flat=True)
        return set(cast(List[int], rows))

    # ------------------------------------------------------------------
    # Phases, pools, sets
    # ------------------------------------------------------------------
    @staticmethod
    async def upsert_phase(tournament: Tournament, phase_id: str, **values: Any) -> StartggPhase:
        phase, created = await StartggPhase.get_or_create(
            tenant_id=current_tenant_id(), tournament=tournament, phase_id=phase_id, defaults=values,
        )
        if not created:
            await scoped(StartggPhase.filter(id=phase.id)).update(**values)
        return phase

    @staticmethod
    async def upsert_pool(
        tournament: Tournament, phase: StartggPhase, phase_group_id: str, **values: Any,
    ) -> StartggPool:
        pool, created = await StartggPool.get_or_create(
            tenant_id=current_tenant_id(), tournament=tournament, phase_group_id=phase_group_id,
            defaults={'phase': phase, **values},
        )
        if not created:
            await scoped(StartggPool.filter(id=pool.id)).update(phase_id=phase.id, **values)
        return pool

    @staticmethod
    async def upsert_set(tournament: Tournament, pool: StartggPool, set_id: str, **values: Any) -> StartggSet:
        row, created = await StartggSet.get_or_create(
            tenant_id=current_tenant_id(), tournament=tournament, set_id=set_id,
            defaults={'pool': pool, **values},
        )
        if not created:
            await scoped(StartggSet.filter(id=row.id)).update(pool_id=pool.id, **values)
        return row

    @staticmethod
    async def list_phases(tournament: Tournament) -> List[StartggPhase]:
        return await scoped(StartggPhase.filter(tournament=tournament)).order_by('phase_order', 'id')

    @staticmethod
    async def list_pools(tournament: Tournament) -> List[StartggPool]:
        return await scoped(StartggPool.filter(tournament=tournament)).prefetch_related('phase')

    @staticmethod
    async def list_sets(tournament: Tournament) -> List[StartggSet]:
        return await scoped(StartggSet.filter(tournament=tournament)).prefetch_related(
            'tournament', 'pool__phase', 'entrant1__user', 'entrant2__user', 'winner', 'match',
        ).order_by('round', 'identifier', 'id')

    @staticmethod
    async def get_set(set_pk: int) -> Optional[StartggSet]:
        return await scoped(StartggSet.filter(id=set_pk)).prefetch_related(
            'tournament', 'pool__phase', 'entrant1__user', 'entrant2__user', 'match',
        ).first()

    @staticmethod
    async def sets_for_match(match: Match) -> List[StartggSet]:
        return await scoped(StartggSet.filter(match_id=match.id)).prefetch_related(
            'tournament', 'entrant1', 'entrant2', 'pool__phase',
        )

    @staticmethod
    async def link_match(row: StartggSet, match: Match) -> None:
        await scoped(StartggSet.filter(id=row.id)).update(match_id=match.id)
        row.match_id = match.id

    @staticmethod
    async def open_unbooked_sets_for_user(user: User) -> List[StartggSet]:
        return await scoped(StartggSet.filter(
            Q(entrant1__user=user) | Q(entrant2__user=user),
            state=StartggSetState.OPEN, match_id__isnull=True,
        )).prefetch_related('tournament', 'pool__phase', 'entrant1__user', 'entrant2__user')

    @staticmethod
    async def prune(
        tournament: Tournament, *, entrant_ids: Set[str], phase_ids: Set[str],
        pool_ids: Set[str], set_ids: Set[str],
    ) -> Dict[str, int]:
        """Drop mirror rows start.gg no longer has. A booked ``Match`` stays on the schedule."""
        return {
            'sets': await scoped(
                StartggSet.filter(tournament=tournament).exclude(set_id__in=list(set_ids))
            ).delete(),
            'pools': await scoped(
                StartggPool.filter(tournament=tournament).exclude(phase_group_id__in=list(pool_ids))
            ).delete(),
            'phases': await scoped(
                StartggPhase.filter(tournament=tournament).exclude(phase_id__in=list(phase_ids))
            ).delete(),
            'entrants': await scoped(
                StartggEntrant.filter(tournament=tournament).exclude(entrant_id__in=list(entrant_ids))
            ).delete(),
        }

    @staticmethod
    async def delete_mirror(tournament: Tournament) -> None:
        await scoped(StartggSet.filter(tournament=tournament)).delete()
        await scoped(StartggPool.filter(tournament=tournament)).delete()
        await scoped(StartggPhase.filter(tournament=tournament)).delete()
        await scoped(StartggEntrant.filter(tournament=tournament)).delete()
