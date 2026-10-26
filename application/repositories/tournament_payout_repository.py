"""Tournament payout repository — data access for prize splits."""

from typing import Any, Iterable, List, Mapping, Optional

from tortoise.transactions import in_transaction

from application.event_writes import guarded_delete
from application.repositories._base import TenantScopedRepository
from application.repositories._event import event_scoped
from application.repositories._tenant import current_tenant_id, scoped
from models import Tournament, TournamentPayout


class TournamentPayoutRepository(TenantScopedRepository[TournamentPayout]):
    """CRUD for :class:`~models.TournamentPayout` rows."""

    model = TournamentPayout

    @classmethod
    async def get_by_id(cls, obj_id: int) -> Optional[TournamentPayout]:
        return await TournamentPayout.get_or_none(
            id=obj_id, tenant_id=current_tenant_id()
        ).prefetch_related('entrant', 'tournament')

    @classmethod
    async def list_for_tournament(cls, tournament_id: int) -> List[TournamentPayout]:
        """One tournament's split, best place first.

        Ordered by ``place`` then ``id`` so joint placings keep a stable order
        between reads rather than shuffling with whatever the planner returns.
        """
        return await scoped(
            TournamentPayout.filter(tournament_id=tournament_id)
        ).order_by('place', 'id').prefetch_related('entrant')

    @classmethod
    async def replace_split(
        cls, tournament: Tournament, rows: Iterable[Mapping[str, Any]],
    ) -> List[TournamentPayout]:
        """Swap a tournament's whole split for ``rows`` in one transaction.

        Delete-then-create, because a split is one document: half of the old
        rows next to half of the new ones is worse than either, and that is
        exactly what a failure between two awaited calls would leave behind.
        """
        tenant_id = current_tenant_id()
        async with in_transaction():
            await guarded_delete(scoped(TournamentPayout.filter(tournament_id=tournament.id)))
            for row in rows:
                await TournamentPayout.create(
                    tenant_id=tenant_id, tournament=tournament, **row,
                )
        return await cls.list_for_tournament(tournament.id)

    @classmethod
    async def list_sglive_linked(cls) -> List[Tournament]:
        """The current event's tournaments whose pool syncs from sglive."""
        return await event_scoped(Tournament.filter(sglive_slug__isnull=False)).order_by('name')

    @staticmethod
    async def list_sglive_linked_all() -> List[Tournament]:
        """Every community's sglive-linked tournaments, for the sync worker.

        Deliberately unscoped and cross-event: the worker reads sglive once per
        tick and fans out per tenant and event itself.
        """
        return await Tournament.filter(sglive_slug__isnull=False).order_by('tenant_id', 'id')
