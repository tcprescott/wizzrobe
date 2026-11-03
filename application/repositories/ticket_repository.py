"""
Ticket Repository - Data Access Layer

Badge sales: a community's connected stores, the badge types an event sells,
orders and the badges in them.
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional

from tortoise.expressions import Q
from tortoise.functions import Count, Max

from application.event_writes import guarded_update
from application.repositories._base import EventScopedRepository, TenantScopedRepository
from application.repositories._event import event_scoped
from application.repositories._save import save_fields
from application.repositories._tenant import scoped
from models import (
    Ticket,
    TicketOrder,
    TicketOrderStatus,
    TicketProvider,
    TicketStatus,
    TicketStore,
    TicketType,
)


class TicketStoreRepository(TenantScopedRepository[TicketStore]):
    """A community's connected store accounts (community-owned, not per event)."""

    model = TicketStore

    @staticmethod
    async def list_all() -> List[TicketStore]:
        return await scoped(TicketStore.all()).order_by('provider')

    @staticmethod
    async def get_by_provider(provider: TicketProvider) -> Optional[TicketStore]:
        return await scoped(TicketStore.filter(provider=provider)).first()

    @staticmethod
    async def get_by_webhook_token(token: str) -> Optional[TicketStore]:
        """The store a provider webhook names, from any community — **unscoped** on purpose.

        A webhook carries no community: the opaque token in its URL picks the
        row, and the caller binds that row's tenant before reading anything else.
        """
        return await TicketStore.get_or_none(webhook_token=token)


class TicketTypeRepository(EventScopedRepository[TicketType]):
    """The badge types the current event sells."""

    model = TicketType

    @staticmethod
    async def list_all(*, active_only: bool = False) -> List[TicketType]:
        filters = {'is_active': True} if active_only else {}
        return await event_scoped(TicketType.filter(**filters)).order_by('sort_order', 'price_cents', 'id')

    @staticmethod
    async def get_many(type_ids: Iterable[int]) -> Dict[int, TicketType]:
        rows = await event_scoped(TicketType.filter(id__in=list(type_ids)))
        return {row.id: row for row in rows}

    @staticmethod
    async def lock(type_ids: Iterable[int]) -> List[TicketType]:
        """The named types, row-locked so two checkouts can't both take the last badge."""
        return await event_scoped(
            TicketType.filter(id__in=sorted(set(type_ids))),
        ).order_by('id').select_for_update()

    @staticmethod
    async def comp_type() -> Optional[TicketType]:
        return await event_scoped(TicketType.filter(covers_comp=True)).order_by('id').first()

    @staticmethod
    async def clear_comp_flag(except_id: Optional[int]) -> None:
        qs = event_scoped(TicketType.filter(covers_comp=True))
        if except_id is not None:
            qs = qs.exclude(id=except_id)
        await guarded_update(qs, covers_comp=False)

    @staticmethod
    async def has_tickets(type_id: int) -> bool:
        return await scoped(Ticket.filter(ticket_type_id=type_id)).exists()


class TicketOrderRepository(EventScopedRepository[TicketOrder]):
    """Checkouts. Webhooks find theirs by the store's reference, within the community."""

    model = TicketOrder

    @staticmethod
    async def get_by_provider_ref(provider: TicketProvider, ref: str) -> Optional[TicketOrder]:
        # event-scope: exempt — a store webhook finds its order before any event is bound; the order then binds its own.
        return await scoped(TicketOrder.filter(provider=provider, provider_ref=ref)).first()

    @staticmethod
    async def get_by_payment_ref(provider: TicketProvider, ref: str) -> Optional[TicketOrder]:
        # event-scope: exempt — a store webhook finds its order before any event is bound; the order then binds its own.
        return await scoped(TicketOrder.filter(provider=provider, payment_ref=ref)).first()

    @staticmethod
    async def list_for_event(*, statuses: Optional[List[TicketOrderStatus]] = None, limit: int = 200) -> List[TicketOrder]:
        filters = {'status__in': statuses} if statuses else {}
        return await event_scoped(TicketOrder.filter(**filters)).prefetch_related('buyer', 'tickets__ticket_type', 'tickets__owner') \
            .order_by('-created_at').limit(limit)

    @staticmethod
    async def get_with_tickets(order_id: int) -> Optional[TicketOrder]:
        return await scoped(TicketOrder.filter(id=order_id)).prefetch_related(
            'buyer', 'tickets__ticket_type', 'tickets__owner',
        ).first()

    @staticmethod
    async def lapsed_pending(now: datetime) -> List[TicketOrder]:
        return await event_scoped(TicketOrder.filter(status=TicketOrderStatus.PENDING, hold_expires_at__lte=now))

    @staticmethod
    async def transition(order_id: int, from_statuses: List[TicketOrderStatus], **fields: object) -> bool:
        """Move an order on only if it is still in one of ``from_statuses``.

        A conditional update rather than read-then-save, so a store retrying
        the same webhook, or two arriving together, settle the order once.
        """
        changed = await guarded_update(
            scoped(TicketOrder.filter(id=order_id, status__in=from_statuses)), **fields,
        )
        return bool(changed)

    @staticmethod
    async def save(order: TicketOrder, *fields: str) -> None:
        await save_fields(order, *fields)


class TicketRepository(EventScopedRepository[Ticket]):
    """Badges. Sold and held counts are worked out here, never kept as a counter."""

    model = Ticket

    @staticmethod
    async def taken_counts(type_ids: Iterable[int], now: datetime) -> Dict[int, int]:
        """Badges per type that are sold, or held by a checkout that hasn't lapsed."""
        ids = list(type_ids)
        valid = await event_scoped(Ticket.filter(ticket_type_id__in=ids, status=TicketStatus.VALID)) \
            .annotate(n=Count('id')).group_by('ticket_type_id').values('ticket_type_id', 'n')
        held = await event_scoped(Ticket.filter(
            ticket_type_id__in=ids, status=TicketStatus.PENDING, order__hold_expires_at__gt=now,
        )).annotate(n=Count('id')).group_by('ticket_type_id').values('ticket_type_id', 'n')
        counts: Dict[int, int] = {}
        for row in [*valid, *held]:
            counts[row['ticket_type_id']] = counts.get(row['ticket_type_id'], 0) + row['n']
        return counts

    @staticmethod
    async def sold_counts(type_ids: Iterable[int]) -> Dict[int, int]:
        rows = await event_scoped(Ticket.filter(ticket_type_id__in=list(type_ids), status=TicketStatus.VALID)) \
            .annotate(n=Count('id')).group_by('ticket_type_id').values('ticket_type_id', 'n')
        return {row['ticket_type_id']: row['n'] for row in rows}

    @staticmethod
    async def for_order(order_id: int) -> List[Ticket]:
        return await scoped(Ticket.filter(order_id=order_id)).prefetch_related('ticket_type', 'owner').order_by('id')

    @staticmethod
    async def owned_by(user_id: int) -> List[Ticket]:
        return await event_scoped(Ticket.filter(owner_id=user_id, status__in=[TicketStatus.VALID, TicketStatus.REFUNDED])) \
            .prefetch_related('ticket_type', 'order', 'owner').order_by('id')

    @staticmethod
    async def extras_bought_by(user_id: int) -> List[Ticket]:
        """Badges ``user_id`` bought that someone else holds or nobody has claimed yet."""
        return await event_scoped(
            Ticket.filter(Q(owner_id__isnull=True) | ~Q(owner_id=user_id), order__buyer_id=user_id, status=TicketStatus.VALID),
        ).prefetch_related('ticket_type', 'owner').order_by('id')

    @staticmethod
    async def for_check_in(event_id: int) -> List[Ticket]:
        """Every owned badge, held or refunded, in the event a check-in roster belongs to."""
        # event-scope: exempt — the check-in event names its own event, whatever event the caller is in.
        return await scoped(Ticket.filter(
            event_id=event_id, owner_id__isnull=False, status__in=[TicketStatus.VALID, TicketStatus.REFUNDED],
        )).prefetch_related('ticket_type', 'order', 'owner').order_by('id')

    @staticmethod
    async def badge_changes_by_event() -> Dict[int, datetime]:
        """When an owned badge (held or refunded) last changed, per event, across every tenant.

        The check-in sync worker's scan: an event with badges syncs on its own
        interval, and sooner once one of them has changed since its last sync.
        """
        # event-scope: exempt — a cross-tenant worker scan; each event then syncs in its own scope.
        rows = await Ticket.filter(
            owner_id__isnull=False, status__in=[TicketStatus.VALID, TicketStatus.REFUNDED],
        ).annotate(changed=Max('updated_at')).group_by('event_id').values('event_id', 'changed')
        return {row['event_id']: row['changed'] for row in rows}

    @staticmethod
    async def get_by_claim_lookup(lookup: str) -> Optional[Ticket]:
        # event-scope: exempt — a claim link names one badge, and the badge decides its own event.
        return await scoped(Ticket.filter(claim_lookup=lookup)).prefetch_related('ticket_type', 'order__buyer').first()

    @staticmethod
    async def get_by_code(code: str) -> Optional[Ticket]:
        # event-scope: exempt — a badge code is unique; the badge decides its own event.
        return await scoped(Ticket.filter(code=code)).first()

    @staticmethod
    async def own_badges_of(user_ids: Iterable[int]) -> List[Ticket]:
        """Valid badges each of ``user_ids`` bought for themselves, oldest first."""
        ids = list(user_ids)
        if not ids:
            return []
        rows = await event_scoped(Ticket.filter(owner_id__in=ids, status=TicketStatus.VALID)) \
            .prefetch_related('ticket_type', 'order', 'owner').order_by('id')
        return [t for t in rows if t.order.buyer_id == t.owner_id]

    @staticmethod
    async def get_many(ticket_ids: Iterable[int]) -> List[Ticket]:
        return await event_scoped(Ticket.filter(id__in=list(ticket_ids))) \
            .prefetch_related('ticket_type', 'order', 'owner').order_by('id')

    @staticmethod
    async def void_pending(order_id: int) -> None:
        await guarded_update(
            scoped(Ticket.filter(order_id=order_id, status=TicketStatus.PENDING)), status=TicketStatus.VOID,
        )

    @staticmethod
    async def set_status_for_order(order_id: int, status: TicketStatus, **extra: object) -> None:
        await guarded_update(scoped(Ticket.filter(order_id=order_id)), status=status, **extra)

    @staticmethod
    async def save(ticket: Ticket, *fields: str) -> None:
        await save_fields(ticket, *fields)
