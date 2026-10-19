"""Prize payouts — the pool, the placement split, and the block to paste.

A payout row stores a *place* and a *percentage*; the money is computed from
the tournament's pool at read time. Nothing here writes an amount, so moving
the pool moves every placement's share with it and there is never a second
number to reconcile.

The whole surface is gated behind :attr:`~models.FeatureFlag.PAYOUTS`: prize
money is SpeedGaming-shaped and most communities never pay anyone.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional

from application.errors import require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories._save import save_fields
from application.repositories.tournament_payout_repository import (
    TournamentPayoutRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_membership_service import TenantMembershipService
from application.tenant_context import require_tenant_id
from application.utils.clients import sglive_client
from application.utils.clients.sglive_client import SgliveAPIError, SgliveGame
from models import FeatureFlag, Tournament, TournamentPayout, User

logger = logging.getLogger(__name__)

CENT = Decimal('0.01')
_HUNDRED = Decimal(100)


def format_percentage(value: Decimal) -> str:
    """``Decimal('50.00')`` -> ``'50'``, ``Decimal('12.50')`` -> ``'12.5'``.

    Not ``:g``, which renders a stored ``50.00`` as ``5e+1``.
    """
    return format(Decimal(value).normalize(), 'f')


def _ordinal(place: int) -> str:
    """``3`` -> ``3rd``. Matches how the block is written by hand today."""
    if 10 <= place % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(place % 10, 'th')
    return f'{place}{suffix}'


@dataclass(frozen=True)
class PayoutLine:
    """One placement's share, with the money worked out."""

    payout: TournamentPayout
    amount: Decimal

    @property
    def place(self) -> int:
        return self.payout.place

    @property
    def percentage(self) -> Decimal:
        return self.payout.percentage

    @property
    def entrant(self) -> Optional[User]:
        return self.payout.entrant

    @property
    def entrant_name(self) -> Optional[str]:
        return self.entrant.preferred_name if self.entrant else None

    @property
    def matcherino_username(self) -> Optional[str]:
        return self.entrant.matcherino_username if self.entrant else None


@dataclass(frozen=True)
class PayoutSplit:
    """A tournament's pool and every placement's share of it."""

    tournament: Tournament
    pool: Decimal
    bonus: Decimal
    lines: List[PayoutLine]

    @property
    def total(self) -> Decimal:
        """What the percentages apply to — the pool plus the bonus."""
        return self.pool + self.bonus

    @property
    def allocated_percentage(self) -> Decimal:
        return sum((line.percentage for line in self.lines), Decimal(0))

    @property
    def is_complete(self) -> bool:
        """Whether every place has someone's name on it."""
        return bool(self.lines) and all(line.entrant is not None for line in self.lines)


@dataclass(frozen=True)
class PayoutOverview:
    """One tournament as the Payouts tab lists it."""

    tournament_id: int
    name: str
    pool: Decimal
    bonus: Decimal
    total: Decimal
    place_count: int
    allocated_percentage: Decimal
    is_complete: bool
    sglive_slug: Optional[str] = None
    synced_at: Optional[datetime] = None


class PayoutService:
    """Prize pool and placement splits for a tournament."""

    def __init__(self) -> None:
        self.repository = TournamentPayoutRepository()
        self.audit_service = AuditService()

    # --- reads ---------------------------------------------------------------

    @requires_feature(FeatureFlag.PAYOUTS)
    async def list_overview(self, actor: User) -> List[PayoutOverview]:
        """Every tournament ``actor`` may manage payouts for, with its totals.

        Scoped to what the actor can act on, so the tab never lists a row whose
        own controls would refuse: staff see the community, a tournament admin
        sees their own events.
        """
        tournaments = await Tournament.filter(
            tenant_id=require_tenant_id()
        ).order_by('-is_active', 'name')
        overviews: List[PayoutOverview] = []
        for tournament in tournaments:
            if not await AuthService.can_manage_payouts(actor, tournament):
                continue
            rows = await self.repository.list_for_tournament(tournament.id)
            split = self._build_split(tournament, rows)
            overviews.append(PayoutOverview(
                tournament_id=tournament.id,
                name=tournament.name,
                pool=split.pool,
                bonus=split.bonus,
                total=split.total,
                place_count=len(rows),
                allocated_percentage=split.allocated_percentage,
                is_complete=split.is_complete,
                sglive_slug=tournament.sglive_slug,
                synced_at=tournament.prize_pool_synced_at,
            ))
        return overviews

    @requires_feature(FeatureFlag.PAYOUTS)
    async def get_split(self, tournament_id: int, actor: User) -> PayoutSplit:
        tournament = await self._require_manageable(tournament_id, actor)
        rows = await self.repository.list_for_tournament(tournament.id)
        return self._build_split(tournament, rows)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def export_block(self, tournament_id: int, actor: User) -> str:
        """The payout block, in the shape the organizer asks admins to post.

        Reads only, and deliberately publishes no event: pasting a block into a
        thread changes nothing, and an event on a read would fire every time
        someone looked.
        """
        split = await self.get_split(tournament_id, actor)
        lines = [
            f'Total prize pool: ${split.pool:.2f}',
            f'Bonus: ${split.bonus:.2f}',
            '',
        ]
        for line in split.lines:
            lines.append(
                f'{_ordinal(line.place)} place - {self._entrant_label(line)}: '
                f'{format_percentage(line.percentage)}% / ${line.amount:.2f}'
            )
        return '\n'.join(lines)

    # --- writes --------------------------------------------------------------

    @requires_feature(FeatureFlag.PAYOUTS)
    async def set_pool(
        self,
        tournament_id: int,
        pool: Optional[Decimal],
        bonus: Optional[Decimal],
        actor: User,
    ) -> Tournament:
        """Set the advertised pool and the leaderboard bonus.

        ``None`` clears a value, which is not the same as zero: an unset pool
        means nobody has decided yet, a zero means the decision was nothing.
        """
        tournament = await self._require_manageable(tournament_id, actor)
        pool = self._validate_money(pool, 'Prize pool')
        bonus = self._validate_money(bonus, 'Bonus')
        if tournament.sglive_slug and pool != self._quantized(tournament.prize_pool):
            raise ValueError(
                'This prize pool updates from sglive.speedgaming.org. Unlink it '
                'before typing a pool by hand.'
            )
        tournament.prize_pool = pool  # type: ignore[assignment]
        tournament.prize_bonus = bonus  # type: ignore[assignment]
        await save_fields(tournament, 'prize_pool', 'prize_bonus')
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.TOURNAMENT_PRIZE_POOL_UPDATED,
            {
                'tournament_id': tournament.id,
                'prize_pool': str(pool) if pool is not None else None,
                'prize_bonus': str(bonus) if bonus is not None else None,
            },
            EventType.TOURNAMENT_PRIZE_POOL_UPDATED,
        )
        return tournament

    @requires_feature(FeatureFlag.PAYOUTS)
    async def set_split(
        self,
        tournament_id: int,
        rows: Iterable[Mapping[str, Any]],
        actor: User,
    ) -> PayoutSplit:
        """Replace the whole split with ``rows``.

        Each row carries ``place``, ``percentage``, and optionally ``entrant_id``
        and ``note``. Replacing rather than patching keeps the split one
        document, which is how the person editing it thinks about it.
        """
        tournament = await self._require_manageable(tournament_id, actor)
        cleaned = self._validate_rows(rows)
        await TenantMembershipService.require_community_users(
            [row['entrant_id'] for row in cleaned], actor,
        )
        await self.repository.replace_split(tournament, cleaned)
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.TOURNAMENT_PAYOUT_UPDATED,
            {
                'tournament_id': tournament.id,
                'places': [
                    {
                        'place': row['place'],
                        'percentage': str(row['percentage']),
                        'entrant_id': row.get('entrant_id'),
                    }
                    for row in cleaned
                ],
            },
            EventType.TOURNAMENT_PAYOUT_UPDATED,
        )
        return await self.get_split(tournament.id, actor)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def set_entrant(
        self, payout_id: int, user_id: Optional[int], actor: User,
    ) -> TournamentPayout:
        """Name (or un-name) the winner on one drafted row."""
        payout = require_found(await self.repository.get_by_id(payout_id), 'Payout')
        tournament = await self._require_manageable(payout.tournament_id, actor)  # type: ignore[attr-defined]
        entrant = None
        if user_id is not None:
            await TenantMembershipService.require_community_users([user_id], actor)
            entrant = require_found(await User.get_or_none(id=user_id), 'User')
            # The (tournament, place, entrant) key would raise an IntegrityError
            # here, which reaches the caller as a 500 rather than as the thing
            # the reader did wrong.
            clash = await TournamentPayout.filter(
                tournament_id=payout.tournament_id, place=payout.place,  # type: ignore[attr-defined]
                entrant_id=user_id,
            ).exclude(id=payout.id).exists()
            if clash:
                raise ValueError(
                    f'{entrant.preferred_name} is already paid at place '
                    f'{payout.place}.'
                )
        payout.entrant = entrant
        await save_fields(payout, 'entrant')
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.TOURNAMENT_PAYOUT_UPDATED,
            {
                'tournament_id': tournament.id,
                'payout_id': payout.id,
                'place': payout.place,
                'entrant_id': user_id,
            },
            EventType.TOURNAMENT_PAYOUT_UPDATED,
        )
        return payout

    # --- sglive.speedgaming.org ----------------------------------------------

    @requires_feature(FeatureFlag.PAYOUTS)
    async def fetch_sglive_games(self, actor: User) -> List[SgliveGame]:
        """Every game on the sglive leaderboard, for the link screen."""
        await self._require_any_manageable(actor)
        try:
            return await sglive_client.fetch_leaderboard()
        except SgliveAPIError as e:
            raise ValueError(f"Couldn't read sglive.speedgaming.org: {e}") from e

    @requires_feature(FeatureFlag.PAYOUTS)
    async def set_sglive_links(
        self, links: Mapping[str, Optional[int]], actor: User,
    ) -> List[Tournament]:
        """Link each sglive slug in ``links`` to a tournament, or to none.

        ``links`` maps every slug on the screen to the tournament it pays out
        for (``None`` = not linked). A slug left out keeps whatever it had. A
        tournament is linked to at most one slug; linking it again moves it.
        Newly linked pools are filled straight away, so staff see the number
        on save rather than at the next tick.
        """
        wanted: Dict[int, str] = {}
        for slug, tournament_id in links.items():
            slug = self._validate_slug(slug)
            if tournament_id is None:
                continue
            if tournament_id in wanted:
                raise ValueError('One tournament can only follow one sglive game.')
            wanted[tournament_id] = slug
        slugs = {self._validate_slug(slug) for slug in links}
        changed: List[Tournament] = []
        for tournament in await self.repository.list_sglive_linked():
            if tournament.sglive_slug in slugs and wanted.get(tournament.id) != tournament.sglive_slug:
                await self._require_manageable(tournament.id, actor)
                changed.append(await self._write_link(tournament, None, actor))
        for tournament_id, slug in wanted.items():
            tournament = await self._require_manageable(tournament_id, actor)
            if tournament.sglive_slug != slug:
                changed.append(await self._write_link(tournament, slug, actor))
        if wanted:
            try:
                await self.sync_sglive_pools(actor)
            except ValueError as e:
                logger.info('sglive fill after linking failed: %s', e)
        return changed

    @requires_feature(FeatureFlag.PAYOUTS)
    async def unlink_sglive(self, tournament_id: int, actor: User) -> Tournament:
        """Stop syncing one tournament's pool. The last synced amount stays."""
        tournament = await self._require_manageable(tournament_id, actor)
        if not tournament.sglive_slug:
            return tournament
        return await self._write_link(tournament, None, actor)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def sync_sglive_pools(self, actor: User) -> List[str]:
        """Read sglive now and update this community's linked pools.

        Returns the linked slugs sglive no longer lists; their pools keep their
        last value.
        """
        try:
            games = await sglive_client.fetch_leaderboard()
        except SgliveAPIError as e:
            raise ValueError(f"Couldn't read sglive.speedgaming.org: {e}") from e
        return await self.apply_sglive_games(games, actor)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def apply_sglive_games(self, games: Iterable[SgliveGame], actor: User) -> List[str]:
        """Write ``games``' totals into this community's linked pools.

        The worker reads sglive once per tick and hands every community the same
        list. A pool that moved is audited as a prize-pool change by ``actor``;
        one that didn't only has its synced time stamped.
        """
        by_slug = {game.slug: game for game in games}
        missing: List[str] = []
        now = datetime.now(timezone.utc)
        for tournament in await self.repository.list_sglive_linked():
            game = by_slug.get(tournament.sglive_slug or '')
            if game is None:
                missing.append(tournament.sglive_slug or '')
                continue
            old = tournament.prize_pool
            tournament.prize_pool_synced_at = now
            if old is not None and self._quantized(old) == game.total:
                await save_fields(tournament, 'prize_pool_synced_at')
                continue
            tournament.prize_pool = game.total
            await save_fields(tournament, 'prize_pool', 'prize_pool_synced_at')
            await self.audit_service.write_and_publish(
                actor,
                AuditActions.TOURNAMENT_PRIZE_POOL_UPDATED,
                {
                    'tournament_id': tournament.id,
                    'prize_pool': str(game.total),
                    'previous_prize_pool': str(old) if old is not None else None,
                    'source': 'sglive',
                },
                EventType.TOURNAMENT_PRIZE_POOL_UPDATED,
            )
        return missing

    async def _write_link(
        self, tournament: Tournament, slug: Optional[str], actor: User,
    ) -> Tournament:
        previous = tournament.sglive_slug
        tournament.sglive_slug = slug  # type: ignore[assignment]
        if slug is None:
            tournament.prize_pool_synced_at = None  # type: ignore[assignment]
        await save_fields(tournament, 'sglive_slug', 'prize_pool_synced_at')
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.TOURNAMENT_SGLIVE_LINK_UPDATED,
            {'tournament_id': tournament.id, 'sglive_slug': slug, 'previous_sglive_slug': previous},
            EventType.TOURNAMENT_SGLIVE_LINK_UPDATED,
        )
        return tournament

    @staticmethod
    def _validate_slug(slug: str) -> str:
        slug = (slug or '').strip().strip('/').lower()
        if not slug or len(slug) > 64 or not all(c.isalnum() or c in '-_' for c in slug):
            raise ValueError(f'{slug!r} is not an sglive page.')
        return slug

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _quantized(value: Optional[Decimal]) -> Optional[Decimal]:
        return None if value is None else Decimal(value).quantize(CENT)

    async def _require_any_manageable(self, actor: User) -> None:
        if await AuthService.is_staff(actor):
            return
        tournaments = await Tournament.filter(tenant_id=require_tenant_id())
        for tournament in tournaments:
            if await AuthService.can_manage_payouts(actor, tournament):
                return
        raise PermissionError("You do not have permission to manage payouts.")

    @staticmethod
    def amount_for(total: Decimal, percentage: Decimal) -> Decimal:
        """``percentage`` of ``total``, rounded to the cent.

        Rounding is per row, so a split whose percentages sum to exactly 100 can
        still total a cent under the pool. That is the correct behaviour — every
        line pays what it says it pays — and the remainder is the organizer's to
        place, not this code's to fudge.
        """
        return (total * percentage / _HUNDRED).quantize(CENT)

    def _build_split(
        self, tournament: Tournament, rows: List[TournamentPayout],
    ) -> PayoutSplit:
        # Quantized on the way out: the DB backend decides the scale it hands
        # back (SQLite returns an unscaled Decimal), and money that renders as
        # ``1.1E+3`` in a payout export is money nobody trusts.
        pool = (tournament.prize_pool or Decimal(0)).quantize(CENT)
        bonus = (tournament.prize_bonus or Decimal(0)).quantize(CENT)
        total = pool + bonus
        return PayoutSplit(
            tournament=tournament,
            pool=pool,
            bonus=bonus,
            lines=[
                PayoutLine(payout=row, amount=self.amount_for(total, row.percentage))
                for row in rows
            ],
        )

    @staticmethod
    def _entrant_label(line: PayoutLine) -> str:
        """How one line names its winner in the export.

        A missing handle is called out rather than left blank: a silently absent
        handle is what stalls a payout run, and the reader of the block is the
        person who can go and ask for it.
        """
        if line.entrant is None:
            return '(unassigned)'
        handle = line.matcherino_username
        if handle:
            return f'{line.entrant_name} ({handle})'
        return f'{line.entrant_name} (no Matcherino handle)'

    async def _require_manageable(self, tournament_id: int, actor: User) -> Tournament:
        tournament = require_found(
            await Tournament.get_or_none(
                id=tournament_id, tenant_id=require_tenant_id()
            ),
            'Tournament',
        )
        if not await AuthService.can_manage_payouts(actor, tournament):
            raise PermissionError(
                "You do not have permission to manage this tournament's payouts."
            )
        return tournament

    @staticmethod
    def _validate_money(value: Optional[Decimal], label: str) -> Optional[Decimal]:
        if value is None:
            return None
        value = Decimal(value)
        if value < 0:
            raise ValueError(f'{label} cannot be negative.')
        return value.quantize(CENT)

    @staticmethod
    def _validate_rows(rows: Iterable[Mapping[str, Any]]) -> List[dict]:
        cleaned: List[dict] = []
        seen: set[tuple[int, Optional[int]]] = set()
        for row in rows:
            place = int(row['place'])
            if place < 1:
                raise ValueError('Place must be 1 or higher.')
            percentage = Decimal(row['percentage'])
            if percentage <= 0:
                raise ValueError('Each share must be above 0%.')
            entrant_id = row.get('entrant_id')
            key = (place, entrant_id)
            if entrant_id is not None and key in seen:
                raise ValueError(
                    f'One person cannot be paid twice at place {place}.'
                )
            seen.add(key)
            cleaned.append({
                'place': place,
                'percentage': percentage.quantize(CENT),
                'entrant_id': entrant_id,
                'note': (row.get('note') or None),
            })
        allocated = sum((row['percentage'] for row in cleaned), Decimal(0))
        # Above 100 is a mistake; below is not. An unallocated remainder is
        # real — an organizer holding back a share is a normal state of a split
        # part way through being agreed.
        if allocated > _HUNDRED:
            raise ValueError(
                f'The shares add up to {format_percentage(allocated)}%, which is '
                f'more than the pool.'
            )
        return cleaned
