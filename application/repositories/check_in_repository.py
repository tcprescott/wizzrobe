"""
Check-in Repository - Data Access Layer

Check-in events, the entrants on their rosters, and the Matcherino badge
types and badges behind them.
"""

from typing import Dict, Iterable, List, Optional

from tortoise.expressions import Q

from application.repositories._base import EventScopedRepository, TenantScopedRepository
from application.repositories._event import event_scoped
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    CheckInDmStatus,
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    CheckInLinkMethod,
    CheckInPass,
    CheckInTier,
    User,
)


class CheckInEventRepository(EventScopedRepository[CheckInEvent]):
    """The current event's check-in events (``CheckInEvent`` is a check-in desk, not an ``Event``)."""

    model = CheckInEvent

    @staticmethod
    async def list_all() -> List[CheckInEvent]:
        return await event_scoped(CheckInEvent.all()).order_by('-created_at')

    @staticmethod
    async def list_by_status(status: CheckInEventStatus) -> List[CheckInEvent]:
        return await event_scoped(CheckInEvent.filter(status=status)).order_by('-created_at')

    @staticmethod
    async def exists_with_status(status: CheckInEventStatus) -> bool:
        return await event_scoped(CheckInEvent.filter(status=status)).exists()

    @staticmethod
    async def record_sync(event: CheckInEvent, **fields: object) -> None:
        """Write only the sync bookkeeping columns.

        A sync holds its ``event`` across a slow network fetch; a full save
        would revert anything staff changed on the event meanwhile.
        """
        await scoped(CheckInEvent.filter(id=event.id)).update(**fields)
        event.update_from_dict(fields)

    @staticmethod
    async def get_by_venue(venue_id: int) -> Optional[CheckInEvent]:
        return await event_scoped(CheckInEvent.filter(matcherino_venue_id=venue_id)).first()

    @staticmethod
    async def list_syncable_all() -> List[CheckInEvent]:
        """Open events with a Matcherino venue or comp rules, across every tenant.

        Deliberately cross-tenant and cross-event: this is the sync worker's
        scan, which then runs each one inside its own tenant and event scope.
        """
        return [
            event for event in await CheckInEvent.filter(status=CheckInEventStatus.OPEN)
            if event.matcherino_venue_id is not None or event.comp_roles or event.comp_volunteers
        ]


class CheckInEntrantRepository(TenantScopedRepository[CheckInEntrant]):
    model = CheckInEntrant

    @staticmethod
    async def get_by_id(obj_id: int) -> Optional[CheckInEntrant]:
        return await CheckInEntrant.get_or_none(
            id=obj_id, tenant_id=current_tenant_id(),
        ).prefetch_related('user', 'event', 'checked_in_by', 'passes__tier')

    @staticmethod
    async def list_for_event(event: CheckInEvent) -> List[CheckInEntrant]:
        return await scoped(CheckInEntrant.filter(event=event)).order_by(
            'display_name',
        ).prefetch_related('user', 'checked_in_by', 'passes__tier')

    @staticmethod
    async def matcherino_rows_by_user_id(event: CheckInEvent) -> Dict[str, CheckInEntrant]:
        rows = await scoped(CheckInEntrant.filter(event=event, matcherino_user_id__isnull=False))
        return {row.matcherino_user_id: row for row in rows}

    @staticmethod
    async def rows_by_user_id(event: CheckInEvent) -> Dict[int, CheckInEntrant]:
        rows = await scoped(CheckInEntrant.filter(event=event, user_id__isnull=False))
        return {row.user_id: row for row in rows if row.user_id is not None}

    @staticmethod
    async def comp_rows_by_user_id(event: CheckInEvent) -> Dict[int, CheckInEntrant]:
        rows = await scoped(CheckInEntrant.filter(
            event=event, source=CheckInEntrantSource.COMP, user_id__isnull=False,
        ))
        return {row.user_id: row for row in rows if row.user_id is not None}

    @staticmethod
    async def linked_user_ids(event: CheckInEvent) -> set[int]:
        rows = await scoped(CheckInEntrant.filter(event=event, user_id__isnull=False)).values_list(
            'user_id', flat=True,
        )
        return {int(row) for row in rows}  # type: ignore[call-overload]

    @staticmethod
    async def get_for_user(event: CheckInEvent, user: User) -> Optional[CheckInEntrant]:
        return await scoped(CheckInEntrant.filter(event=event, user=user)).first()

    @staticmethod
    async def has_matcherino_rows(event: CheckInEvent) -> bool:
        return await scoped(CheckInEntrant.filter(event=event, matcherino_user_id__isnull=False)).exists()

    @staticmethod
    async def claim_check_in(entrant: CheckInEntrant, at, by: User, *, active_only: bool = True) -> bool:
        """Set the check-in only if nobody has yet; False when someone beat us to it.

        ``active_only`` also refuses a row withdrawn since it was read.
        """
        query = CheckInEntrant.filter(id=entrant.id, checked_in_at__isnull=True)
        if active_only:
            query = query.filter(withdrawn_at__isnull=True)
        updated = await scoped(query).update(
            checked_in_at=at, checked_in_by_id=by.id, updated_at=at,
        )
        return bool(updated)

    @staticmethod
    async def release_check_in(entrant: CheckInEntrant, at) -> bool:
        updated = await scoped(CheckInEntrant.filter(id=entrant.id, checked_in_at__isnull=False)).update(
            checked_in_at=None, checked_in_by_id=None, updated_at=at,
        )
        return bool(updated)

    @staticmethod
    async def record_dm_status(entrant_id: int, status: CheckInDmStatus, at) -> None:
        """Write only the test-DM columns, so a send finishing late can't revert a desk action."""
        await scoped(CheckInEntrant.filter(id=entrant_id)).update(dm_status=status, dm_checked_at=at)

    @staticmethod
    async def claim_auto_link(entrant: CheckInEntrant, user: User, method, at) -> bool:
        """Link a row only if it is still unlinked and staff haven't unlinked it on purpose."""
        updated = await scoped(
            CheckInEntrant.filter(id=entrant.id, user_id__isnull=True)
            .filter(Q(link_method__isnull=True) | Q(link_method__not=CheckInLinkMethod.MANUAL.value))
        ).update(user_id=user.id, link_method=method, updated_at=at)
        return bool(updated)

    @staticmethod
    async def bulk_create(entrants: List[CheckInEntrant]) -> None:
        tenant_id = current_tenant_id()
        for entrant in entrants:
            entrant.tenant_id = tenant_id
        await CheckInEntrant.bulk_create(entrants)

    @staticmethod
    async def save(entrant: CheckInEntrant, fields: Iterable[str]) -> None:
        await entrant.save(update_fields=list(fields))


class CheckInTierRepository(TenantScopedRepository[CheckInTier]):
    model = CheckInTier

    @staticmethod
    async def by_pass_id(event: CheckInEvent) -> Dict[int, CheckInTier]:
        return {tier.matcherino_pass_id: tier for tier in await scoped(CheckInTier.filter(event=event))}

    @staticmethod
    async def list_for_event(event: CheckInEvent) -> List[CheckInTier]:
        return await scoped(CheckInTier.filter(event=event)).order_by('-amount_cents', 'title')

    @staticmethod
    async def save(tier: CheckInTier, fields: Iterable[str]) -> None:
        await tier.save(update_fields=list(fields))


class CheckInPassRepository(TenantScopedRepository[CheckInPass]):
    model = CheckInPass

    @staticmethod
    async def by_purchase_id(event: CheckInEvent) -> Dict[int, CheckInPass]:
        return {p.matcherino_purchase_id: p for p in await scoped(CheckInPass.filter(event=event))}

    @staticmethod
    async def bulk_create(passes: List[CheckInPass]) -> None:
        tenant_id = current_tenant_id()
        for badge in passes:
            badge.tenant_id = tenant_id
        await CheckInPass.bulk_create(passes)

    @staticmethod
    async def save(badge: CheckInPass, fields: Iterable[str]) -> None:
        await badge.save(update_fields=list(fields))


class CheckInUserLookupRepository:
    """Global identity lookups for matching registrants to accounts.

    ``User`` has no tenant column; these are deliberately unscoped (global)
    exact-identifier lookups. Who may be *offered* as a manual match is a
    separate, community-scoped question answered by ``UserRepository``.
    """

    @staticmethod
    async def users_by_identifiers(
        *,
        discord_ids: Iterable[int],
        twitch_ids: Iterable[str],
        matcherino_ids: Iterable[str],
    ) -> List[User]:
        discord_ids, twitch_ids, matcherino_ids = list(discord_ids), list(twitch_ids), list(matcherino_ids)
        clauses: List[Q] = []
        if discord_ids:
            clauses.append(Q(discord_id__in=discord_ids))
        if twitch_ids:
            clauses.append(Q(twitch_user_id__in=twitch_ids))
        if matcherino_ids:
            clauses.append(Q(matcherino_user_id__in=matcherino_ids))
        if not clauses:
            return []
        return await User.filter(Q(*clauses, join_type='OR'), is_active=True, is_system=False)

    @staticmethod
    async def users_with_unverified_handle() -> List[User]:
        """Users who typed a Matcherino handle but have no linked id yet (global)."""
        return await User.filter(
            matcherino_username__isnull=False,
            matcherino_user_id__isnull=True,
            is_active=True,
            is_system=False,
        )

    @staticmethod
    async def user_with_matcherino_id(matcherino_id: str) -> Optional[User]:
        return await User.get_or_none(matcherino_user_id=matcherino_id)
