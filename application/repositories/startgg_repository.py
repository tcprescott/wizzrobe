"""
start.gg Repository - Data Access Layer

Pure data access for the read-only start.gg mirror (entrants, phases, pools,
sets), each tournament's event link, the community's start.gg sign-in, and the
lookups that map entrants to Wizzrobe users. No business logic.
"""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, cast

from tortoise.expressions import Q
from tortoise.functions import Count, Lower

from application.event_writes import guarded_delete, guarded_update
from application.repositories._event import event_scoped, live_event_scoped
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    EventStatus,
    Match,
    StartggConnection,
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

    @staticmethod
    async def set_event_link(
        tournament: Tournament, event_id: Optional[str], slug: Optional[str], name: Optional[str],
    ) -> None:
        await guarded_update(scoped(Tournament.filter(id=tournament.id)), startgg_event_id=event_id, startgg_event_slug=slug, startgg_event_name=name, startgg_last_synced_at=None, startgg_bookable_phase_id=None)
        await tournament.refresh_from_db()

    @staticmethod
    async def record_sync(tournament: Tournament, when: datetime, name: Optional[str]) -> None:
        await guarded_update(scoped(Tournament.filter(id=tournament.id)), startgg_last_synced_at=when, startgg_event_name=name or tournament.startgg_event_name)
        await tournament.refresh_from_db()

    @staticmethod
    async def set_bookable_phase(tournament: Tournament, phase_id: Optional[str]) -> None:
        await guarded_update(scoped(Tournament.filter(id=tournament.id)), startgg_bookable_phase_id=phase_id)
        await tournament.refresh_from_db()

    @staticmethod
    async def set_report_results(tournament: Tournament, enabled: bool) -> None:
        await guarded_update(scoped(Tournament.filter(id=tournament.id)), startgg_report_results=enabled)
        await tournament.refresh_from_db()

    @staticmethod
    async def linked_tournaments() -> List[Tournament]:
        """The current event's tournaments linked to start.gg."""
        return await event_scoped(Tournament.filter(startgg_event_slug__isnull=False))

    @staticmethod
    async def live_linked_tournaments() -> List[Tournament]:
        """Every live event's start.gg-linked tournaments (cross-event: the sign-in is the community's)."""
        return await live_event_scoped(Tournament.filter(startgg_event_slug__isnull=False))

    @staticmethod
    async def linked_tenant_ids() -> Set[int]:
        """Cross-tenant and cross-event: every tenant with at least one linked event, for the sync worker."""
        rows = await Tournament.filter(startgg_event_slug__isnull=False).values_list('tenant_id', flat=True)
        return set(cast(List[int], rows))

    @staticmethod
    async def get_connection() -> Optional[StartggConnection]:
        return await scoped(StartggConnection.all()).first()

    @staticmethod
    async def save_connection(**values: Any) -> StartggConnection:
        connection, _ = await StartggConnection.update_or_create(
            tenant_id=current_tenant_id(), defaults=values,
        )
        return connection

    @staticmethod
    async def update_connection_tokens(
        connection: StartggConnection,
        access_token: str,
        refresh_token: Optional[str],
        token_expires_at: Optional[datetime],
    ) -> None:
        connection.access_token = access_token
        if refresh_token:
            connection.refresh_token = refresh_token
        connection.token_expires_at = token_expires_at  # type: ignore[assignment]  # null=True field
        await save_fields(connection, 'access_token', 'refresh_token', 'token_expires_at')

    @staticmethod
    async def clear_connection() -> int:
        return await scoped(StartggConnection.all()).delete()

    # Identity lookups (global: users are not tenant-scoped)
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

    @staticmethod
    async def list_entrants(tournament: Tournament) -> List[StartggEntrant]:
        return await scoped(StartggEntrant.filter(tournament=tournament)).prefetch_related(
            'user', 'suggested_user',
        ).order_by('name')

    @staticmethod
    async def count_unmatched_entrants(tournament_ids: List[int]) -> Dict[int, int]:
        """Entrants with no Wizzrobe user, per tournament; tournaments with none are left out."""
        if not tournament_ids:
            return {}
        rows = await scoped(StartggEntrant.filter(
            tournament_id__in=tournament_ids, user_id__isnull=True,
        )).annotate(n=Count('id')).group_by('tournament_id').values('tournament_id', 'n')
        return {r['tournament_id']: r['n'] for r in rows}

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
            await guarded_update(scoped(StartggEntrant.filter(id=entrant.id)), **values)
            await entrant.refresh_from_db()
        return entrant

    @staticmethod
    async def set_entrant_user(entrant: StartggEntrant, user: Optional[User], manual: bool) -> None:
        await guarded_update(scoped(StartggEntrant.filter(id=entrant.id)), user_id=user.id if user else None, user_assigned_manually=manual)
        await entrant.refresh_from_db()

    @staticmethod
    async def dismiss_suggestion(entrant: StartggEntrant) -> None:
        await guarded_update(scoped(StartggEntrant.filter(id=entrant.id)), suggested_user_id=None, suggestion_dismissed=True)
        await entrant.refresh_from_db()

    @staticmethod
    async def entrant_tournament_ids_for_user(user: User) -> Set[int]:
        rows = await scoped(StartggEntrant.filter(user=user)).values_list('tournament_id', flat=True)
        return set(cast(List[int], rows))

    @staticmethod
    async def upsert_phase(tournament: Tournament, phase_id: str, **values: Any) -> StartggPhase:
        phase, created = await StartggPhase.get_or_create(
            tenant_id=current_tenant_id(), tournament=tournament, phase_id=phase_id, defaults=values,
        )
        if not created:
            await guarded_update(scoped(StartggPhase.filter(id=phase.id)), **values)
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
            await guarded_update(scoped(StartggPool.filter(id=pool.id)), phase_id=phase.id, **values)
        return pool

    @staticmethod
    async def upsert_set(tournament: Tournament, pool: StartggPool, set_id: str, **values: Any) -> StartggSet:
        row, created = await StartggSet.get_or_create(
            tenant_id=current_tenant_id(), tournament=tournament, set_id=set_id,
            defaults={'pool': pool, **values},
        )
        if not created:
            await guarded_update(scoped(StartggSet.filter(id=row.id)), pool_id=pool.id, **values)
        return row

    @staticmethod
    async def sets_by_ids(tournament: Tournament, set_ids: Iterable[str]) -> List[StartggSet]:
        """Mirrored sets with their entrants, winner and any booked match's players loaded."""
        return await scoped(StartggSet.filter(tournament=tournament, set_id__in=list(set_ids))).prefetch_related(
            'tournament', 'pool__phase', 'entrant1__user', 'entrant2__user', 'winner', 'match__players',
        )

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
    async def booked_sets_for_entrant(entrant: StartggEntrant) -> List[StartggSet]:
        """Sets this entrant plays that have a Wizzrobe match attached."""
        return await scoped(StartggSet.filter(
            Q(entrant1=entrant) | Q(entrant2=entrant), match_id__isnull=False,
        )).prefetch_related('entrant1', 'entrant2', 'match')

    @staticmethod
    async def list_placeholder_bookings(tournament: Tournament) -> List[StartggSet]:
        """Sets booked before their players were known, with the booking attached."""
        rows = await scoped(StartggSet.filter(tournament=tournament, match_id__isnull=False)).prefetch_related(
            'tournament', 'entrant1__user', 'entrant2__user', 'match',
        )
        return [r for r in rows if r.match.bracket_placeholders is not None]

    @staticmethod
    async def pending_unbooked_sets(tournament_ids: Optional[List[int]] = None) -> List[StartggSet]:
        """Unbooked, unplayed sets in active tournaments whose players aren't both decided."""
        qs = scoped(StartggSet.filter(
            state=StartggSetState.PENDING, match_id__isnull=True, tournament__is_active=True,
        ))
        if tournament_ids is not None:
            qs = qs.filter(tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'tournament', 'pool__phase', 'entrant1__user', 'entrant2__user',
        ).order_by('tournament_id', 'round', 'identifier', 'id')

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
    async def completed_for_matches(match_ids: List[int]) -> List[StartggSet]:
        """Booked sets start.gg already has a result for."""
        if not match_ids:
            return []
        return await scoped(StartggSet.filter(
            match_id__in=match_ids, state=StartggSetState.COMPLETE,
        )).prefetch_related('winner', 'entrant1', 'entrant2')

    @staticmethod
    async def link_match(row: StartggSet, match: Match) -> None:
        await guarded_update(scoped(StartggSet.filter(id=row.id)), match_id=match.id)
        row.match_id = match.id

    @staticmethod
    async def open_unbooked_sets_for_user(user: User) -> List[StartggSet]:
        """Open, unbooked sets the user is in.

        Across every live event (cross-event: it feeds the player's own schedule).
        """
        return await scoped(StartggSet.filter(
            Q(entrant1__user=user) | Q(entrant2__user=user),
            state=StartggSetState.OPEN, match_id__isnull=True,
            tournament__event__status=EventStatus.LIVE,
        )).prefetch_related('tournament', 'pool__phase', 'entrant1__user', 'entrant2__user')

    @staticmethod
    async def open_unbooked_sets(tournament_ids: Optional[List[int]] = None) -> List[StartggSet]:
        """Every open, unbooked set in active tournaments; ``None`` means all of them."""
        qs = scoped(StartggSet.filter(
            state=StartggSetState.OPEN, match_id__isnull=True, tournament__is_active=True,
        ))
        if tournament_ids is not None:
            qs = qs.filter(tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'tournament', 'pool__phase', 'entrant1__user', 'entrant2__user',
        ).order_by('tournament_id', 'round', 'identifier', 'id')

    @staticmethod
    async def open_set_sets_for_user(user: User) -> List[StartggSet]:
        """Open sets the user is in whose best-of games are partly booked.

        Across every live event (cross-event: it feeds the player's own schedule).
        """
        return await scoped(StartggSet.filter(
            Q(entrant1__user=user) | Q(entrant2__user=user),
            state=StartggSetState.OPEN, match__set_best_of__gt=1,
            tournament__event__status=EventStatus.LIVE,
        )).prefetch_related('tournament', 'pool__phase', 'entrant1__user', 'entrant2__user')

    @staticmethod
    async def open_set_sets(tournament_ids: Optional[List[int]] = None) -> List[StartggSet]:
        """Every open set whose best-of games are partly booked; ``None`` means all of them."""
        qs = scoped(StartggSet.filter(
            state=StartggSetState.OPEN, match__set_best_of__gt=1, tournament__is_active=True,
        ))
        if tournament_ids is not None:
            qs = qs.filter(tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'tournament', 'pool__phase', 'entrant1__user', 'entrant2__user',
        ).order_by('tournament_id', 'round', 'identifier', 'id')

    @staticmethod
    async def move_match_link(old_match_id: int, new_match_id: int) -> None:
        await guarded_update(scoped(StartggSet.filter(match_id=old_match_id)), match_id=new_match_id)

    @staticmethod
    async def prune(
        tournament: Tournament, *, entrant_ids: Set[str], phase_ids: Set[str],
        pool_ids: Set[str], set_ids: Set[str],
    ) -> Dict[str, int]:
        """Drop mirror rows start.gg no longer has. A booked ``Match`` stays on the schedule."""
        return {
            'sets': await guarded_delete(scoped(
                StartggSet.filter(tournament=tournament).exclude(set_id__in=list(set_ids))
            )),
            'pools': await guarded_delete(scoped(
                StartggPool.filter(tournament=tournament).exclude(phase_group_id__in=list(pool_ids))
            )),
            'phases': await guarded_delete(scoped(
                StartggPhase.filter(tournament=tournament).exclude(phase_id__in=list(phase_ids))
            )),
            'entrants': await guarded_delete(scoped(
                StartggEntrant.filter(tournament=tournament).exclude(entrant_id__in=list(entrant_ids))
            )),
        }

    @staticmethod
    async def delete_mirror(tournament: Tournament) -> None:
        await guarded_delete(scoped(StartggSet.filter(tournament=tournament)))
        await guarded_delete(scoped(StartggPool.filter(tournament=tournament)))
        await guarded_delete(scoped(StartggPhase.filter(tournament=tournament)))
        await guarded_delete(scoped(StartggEntrant.filter(tournament=tournament)))
