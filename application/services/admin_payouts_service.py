"""The app's Admin → Payouts screen: every tournament the viewer pays out, and one
tournament's pool and split, shaped for editing in place.

Screen-shaped reads over :class:`PayoutService`, which keeps every permission check
and every write.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, Optional

from application.feature_flags import requires_feature
from application.services.payout_service import CENT, PayoutOverview, PayoutService
from application.services.player_home_service import PlayerHomeService
from application.services.tournament_service import TournamentService
from application.utils.clients.sglive_client import SgliveGame
from models import FeatureFlag, User


@dataclass
class PayoutRow:
    id: int
    name: str
    status: str
    status_label: str
    total: Decimal
    has_pool: bool
    place_count: int
    sglive_slug: Optional[str]


@dataclass
class PayoutBoard:
    tournaments: List[PayoutRow]
    zone: Optional[str]


@dataclass
class PayoutEditLine:
    place: int
    percentage: Optional[Decimal]
    amount: Decimal
    is_fixed: bool
    entrant_id: Optional[int]
    note: Optional[str]


@dataclass
class PayoutEntrant:
    id: int
    name: str


@dataclass
class PayoutDetail:
    id: int
    name: str
    status: str
    status_label: str
    pool: Optional[Decimal]
    bonus: Optional[Decimal]
    lines: List[PayoutEditLine]
    entrants: List[PayoutEntrant]
    sglive_slug: Optional[str]
    synced_at: Optional[datetime]


@dataclass
class SgliveGameLine:
    slug: str
    name: str
    total: Decimal
    tournament_id: Optional[int]
    suggested: bool


@dataclass
class SgliveLinks:
    games: List[SgliveGameLine]
    tournaments: List[PayoutEntrant]


def payout_status(place_count: int, is_complete: bool) -> tuple:
    """What is still missing on a tournament's split, as one chip."""
    if not place_count:
        return 'none', 'No split'
    if not is_complete:
        return 'unnamed', 'Winners not named'
    return 'ready', 'Ready'


def _name_key(name: str) -> str:
    words = re.sub(r'[^a-z0-9 ]', ' ', name.lower()).split()
    return ' '.join(w for w in words if w not in {'the', 'tournament', 'online'})


def suggest_sglive_links(games: Iterable[SgliveGame], tournaments: Mapping[int, str]) -> Dict[str, int]:
    """Pair sglive games with tournaments whose names match once normalised."""
    keys: Dict[str, List[int]] = {}
    for tid, name in tournaments.items():
        keys.setdefault(_name_key(name), []).append(tid)
    suggestions: Dict[str, int] = {}
    for game in games:
        match = keys.get(_name_key(game.name), [])
        if len(match) == 1 and match[0] not in suggestions.values():
            suggestions[game.slug] = match[0]
    return suggestions


def _cents(value: Optional[Decimal]) -> Optional[Decimal]:
    return None if value is None else Decimal(value).quantize(CENT)


def _row(o: PayoutOverview) -> PayoutRow:
    status, label = payout_status(o.place_count, o.is_complete)
    return PayoutRow(o.tournament_id, o.name, status, label, o.total, bool(o.pool or o.bonus), o.place_count, o.sglive_slug)


class AdminPayoutsService:
    def __init__(self) -> None:
        self.payouts = PayoutService()

    @requires_feature(FeatureFlag.PAYOUTS)
    async def board(self, actor: User) -> PayoutBoard:
        rows = [_row(o) for o in await self.payouts.list_overview(actor)]
        return PayoutBoard(rows, await PlayerHomeService.viewer_zone(actor))

    @requires_feature(FeatureFlag.PAYOUTS)
    async def detail(self, actor: User, tournament_id: int) -> PayoutDetail:
        split = await self.payouts.get_split(tournament_id, actor)
        t = split.tournament
        entrants: Dict[int, str] = {}
        for tp in await TournamentService().get_enrolled_players_by_tournament_id(t.id):
            entrants[tp.user_id] = tp.user.preferred_name
        for line in split.lines:
            if line.entrant is not None:
                entrants.setdefault(line.entrant.id, line.entrant.preferred_name)
        status, label = payout_status(len(split.lines), split.is_complete)
        return PayoutDetail(
            id=t.id,
            name=t.name,
            status=status,
            status_label=label,
            pool=_cents(t.prize_pool),
            bonus=_cents(t.prize_bonus),
            lines=[
                PayoutEditLine(
                    place=line.place,
                    percentage=line.percentage,
                    amount=line.amount,
                    is_fixed=line.is_fixed,
                    entrant_id=line.entrant.id if line.entrant else None,
                    note=line.payout.note,
                )
                for line in split.lines
            ],
            entrants=sorted((PayoutEntrant(i, n) for i, n in entrants.items()), key=lambda e: e.name.lower()),
            sglive_slug=t.sglive_slug,
            synced_at=t.prize_pool_synced_at,
        )

    @requires_feature(FeatureFlag.PAYOUTS)
    async def export_block(self, actor: User, tournament_id: int) -> str:
        return await self.payouts.export_block(tournament_id, actor)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def sglive_links(self, actor: User) -> SgliveLinks:
        """sglive's games, each with the tournament it pays into or a suggested one."""
        games = await self.payouts.fetch_sglive_games(actor)
        overviews = await self.payouts.list_overview(actor)
        names = {o.tournament_id: o.name for o in overviews}
        current = {o.sglive_slug: o.tournament_id for o in overviews if o.sglive_slug}
        suggested = suggest_sglive_links(
            [g for g in games if g.slug not in current],
            {tid: name for tid, name in names.items() if tid not in current.values()},
        )
        return SgliveLinks(
            games=[
                SgliveGameLine(
                    slug=g.slug,
                    name=g.name,
                    total=g.total,
                    tournament_id=current.get(g.slug, suggested.get(g.slug)),
                    suggested=g.slug not in current and g.slug in suggested,
                )
                for g in games
            ],
            tournaments=[PayoutEntrant(tid, name) for tid, name in names.items()],
        )

    @requires_feature(FeatureFlag.PAYOUTS)
    async def set_sglive_links(self, actor: User, links: Mapping[str, Optional[int]]) -> None:
        await self.payouts.set_sglive_links(links, actor)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def sync_sglive(self, actor: User) -> List[str]:
        return await self.payouts.sync_sglive_pools(actor)

    @requires_feature(FeatureFlag.PAYOUTS)
    async def unlink_sglive(self, actor: User, tournament_id: int) -> None:
        await self.payouts.unlink_sglive(tournament_id, actor)
