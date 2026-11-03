"""Badges bought in Wizzrobe, on an event's check-in roster.

Each owned :class:`~models.Ticket` in the check-in event's event becomes a
:class:`~models.CheckInPass` (its own code, so the desk finds it by scanning
the QR or typing the code) under a :class:`~models.CheckInTier` that mirrors
its badge type, held by the owner's roster row. A buyer is linked to their
account from the start, so there is nothing to match.

:meth:`CheckInService.sync_event` runs :func:`apply_tickets` after the
Matcherino sales and before the comps. The rules:

- An owner with no roster row gets a ``WIZZROBE`` row. One already on it (a
  Matcherino buyer, a walk-up, a comp) keeps their row and gains the badge; a
  ``COMP`` row becomes ``WIZZROBE``, so it outlives the comp.
- A ``WIZZROBE`` row whose owner holds no badge any more is withdrawn, unless
  they're comped, when it goes back to being a ``COMP`` row for
  :func:`~application.services.check_in_comps.apply_comps` to look after.
- A refunded badge keeps its pass, marked refunded; a claimed one moves to its
  new owner's row.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from application.repositories import (
    CheckInEntrantRepository,
    CheckInPassRepository,
    CheckInTierRepository,
    TicketRepository,
)
from application.services.feature_flag_service import FeatureFlagService
from models import (
    CheckInEntrantSource,
    CheckInEvent,
    CheckInLinkMethod,
    CheckInPass,
    CheckInTier,
    FeatureFlag,
    Ticket,
    TicketStatus,
)


@dataclass
class TicketResult:
    added: int = 0
    withdrawn: int = 0
    rejoined: int = 0
    badges: int = 0


async def ticket_badges(event: CheckInEvent) -> List[Ticket]:
    """Every owned badge in the check-in event's event; empty with ticket sales off."""
    if not await FeatureFlagService().is_enabled(FeatureFlag.TICKET_SALES):
        return []
    return await TicketRepository.for_check_in(event.event_id)


def holders(tickets: List[Ticket]) -> set[int]:
    return {t.owner_id for t in tickets if t.status is TicketStatus.VALID and t.owner_id is not None}


async def _tiers(repo: CheckInTierRepository, event: CheckInEvent, tickets: List[Ticket]) -> Dict[int, CheckInTier]:
    tiers = await repo.by_ticket_type_id(event)
    for ticket in tickets:
        kind = ticket.ticket_type
        fields: Dict[str, object] = {'title': kind.name[:255], 'amount_cents': kind.price_cents, 'lanyard': kind.lanyard}
        tier = tiers.get(kind.id)
        if tier is None:
            tiers[kind.id] = await repo.create(event=event, ticket_type=kind, **fields)
            continue
        changes = [key for key, value in fields.items() if getattr(tier, key) != value]
        if changes:
            tier.update_from_dict({key: fields[key] for key in changes})
            await repo.save(tier, [*changes, 'updated_at'])
    return tiers


async def apply_tickets(
    entrants: CheckInEntrantRepository,
    tier_repo: CheckInTierRepository,
    pass_repo: CheckInPassRepository,
    event: CheckInEvent,
    tickets: List[Ticket],
    comped: Dict[int, List[str]],
    now: datetime,
) -> TicketResult:
    """Bring the roster's Wizzrobe rows and passes in line with ``tickets``."""
    result = TicketResult(badges=sum(1 for t in tickets if t.status is TicketStatus.VALID))
    holding = holders(tickets)
    rows = await entrants.rows_by_user_id(event)

    for ticket in tickets:
        user_id, owner = ticket.owner_id, ticket.owner
        if user_id is None or owner is None or user_id not in holding or user_id in rows:
            continue
        rows[user_id] = await entrants.create(
            event=event, source=CheckInEntrantSource.WIZZROBE, display_name=owner.preferred_name[:255],
            user_id=user_id, link_method=CheckInLinkMethod.TICKET, registered_at=ticket.order.paid_at,
            comp_reasons=comped.get(user_id),
        )
        result.added += 1

    for user_id, row in rows.items():
        changes: List[str] = []
        if user_id in holding:
            if row.source == CheckInEntrantSource.COMP:
                row.source = CheckInEntrantSource.WIZZROBE
                changes.append('source')
            if row.source == CheckInEntrantSource.WIZZROBE and row.withdrawn_at is not None:
                row.withdrawn_at = None  # type: ignore[assignment]
                changes.append('withdrawn_at')
                result.rejoined += 1
        elif row.source == CheckInEntrantSource.WIZZROBE:
            if comped.get(user_id):
                row.source = CheckInEntrantSource.COMP
                changes.append('source')
            elif row.withdrawn_at is None:
                row.withdrawn_at = now
                changes.append('withdrawn_at')
                result.withdrawn += 1
        if changes:
            await entrants.save(row, [*changes, 'updated_at'])

    tiers = await _tiers(tier_repo, event, tickets)
    stored = await pass_repo.by_ticket_id(event)
    created: List[CheckInPass] = []
    for ticket in tickets:
        owner_row = rows.get(ticket.owner_id) if ticket.owner_id is not None else None
        refunded = ticket.status is TicketStatus.REFUNDED
        fields: Dict[str, object] = {
            'tier_id': tiers[ticket.ticket_type_id].id,
            'entrant_id': owner_row.id if owner_row else None,
            'code': ticket.code,
            'purchased_at': ticket.order.paid_at,
            'refunded_at': (ticket.refunded_at or now) if refunded else None,
            'removed_at': None,
        }
        badge = stored.get(ticket.id)
        if badge is None:
            if not refunded:
                created.append(CheckInPass(event=event, ticket_id=ticket.id, **fields))
            continue
        if refunded and badge.refunded_at is not None:
            continue
        changes = [key for key, value in fields.items() if getattr(badge, key) != value]
        if changes:
            badge.update_from_dict({key: fields[key] for key in changes})
            await pass_repo.save(badge, [*changes, 'updated_at'])
    if created:
        await pass_repo.bulk_create(created)
    seen = {t.id for t in tickets}
    for ticket_id, badge in stored.items():
        if ticket_id not in seen and badge.removed_at is None:
            badge.removed_at = now
            await pass_repo.save(badge, ['removed_at', 'updated_at'])
    return result
