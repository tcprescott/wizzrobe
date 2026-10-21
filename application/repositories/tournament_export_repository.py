"""Reads behind the tournament export: one tournament's roster, bracket and matches.

Each method reads one tournament's rows in a single query, so the export
worker can build tournament by tournament and pause between them.
"""

from typing import Dict, List, Set, Tuple

from tortoise.expressions import Q
from tortoise.functions import Count

from application.repositories._tenant import scoped
from models import (
    Bracket,
    BracketEntrant,
    BracketEntry,
    BracketMatch,
    ChallongeMatch,
    ChallongeParticipant,
    Match,
    StartggEntrant,
    StartggPool,
    StartggSet,
    Tournament,
    TournamentPlayers,
)


class TournamentExportRepository:

    @staticmethod
    async def tournaments(ids: List[int]) -> List[Tournament]:
        return await scoped(Tournament.filter(id__in=ids)).order_by('name', 'id')

    @staticmethod
    async def player_counts(ids: List[int]) -> Dict[int, int]:
        rows = await scoped(TournamentPlayers.filter(tournament_id__in=ids)).annotate(
            n=Count('id'),
        ).group_by('tournament_id').values('tournament_id', 'n')
        return {r['tournament_id']: r['n'] for r in rows}

    @staticmethod
    async def match_counts(ids: List[int]) -> Dict[int, Tuple[int, int]]:
        """``{tournament_id: (matches, finished)}``."""
        rows = await scoped(Match.filter(tournament_id__in=ids)).annotate(
            n=Count('id'), done=Count('id', _filter=Q(finished_at__isnull=False)),
        ).group_by('tournament_id').values('tournament_id', 'n', 'done')
        return {r['tournament_id']: (r['n'], r['done']) for r in rows}

    @staticmethod
    async def with_native_bracket(ids: List[int]) -> Set[int]:
        rows = await scoped(Bracket.filter(tournament_id__in=ids)).distinct().values_list('tournament_id', flat=True)
        return set(rows)

    @staticmethod
    async def enrolled(tournament_id: int) -> List[TournamentPlayers]:
        return await scoped(
            TournamentPlayers.filter(tournament_id=tournament_id)
        ).prefetch_related('user').order_by('id')

    @staticmethod
    async def native_brackets(tournament_id: int) -> List[Bracket]:
        return await scoped(Bracket.filter(tournament_id=tournament_id)).order_by('stage_order', 'id')

    @staticmethod
    async def native_entrants(tournament_id: int) -> List[BracketEntrant]:
        return await scoped(
            BracketEntrant.filter(tournament_id=tournament_id)
        ).prefetch_related('user').order_by('id')

    @staticmethod
    async def native_entries(tournament_id: int) -> List[BracketEntry]:
        return await scoped(
            BracketEntry.filter(bracket__tournament_id=tournament_id)
        ).order_by('id')

    @staticmethod
    async def native_matches(tournament_id: int) -> List[BracketMatch]:
        return await scoped(
            BracketMatch.filter(bracket__tournament_id=tournament_id)
        ).prefetch_related('games').order_by('bracket_id', 'round', 'position')

    @staticmethod
    async def challonge_participants(tournament_id: int) -> List[ChallongeParticipant]:
        return await scoped(
            ChallongeParticipant.filter(tournament_id=tournament_id)
        ).prefetch_related('user').order_by('id')

    @staticmethod
    async def challonge_matches(tournament_id: int) -> List[ChallongeMatch]:
        return await scoped(
            ChallongeMatch.filter(tournament_id=tournament_id)
        ).order_by('round', 'id')

    @staticmethod
    async def startgg_entrants(tournament_id: int) -> List[StartggEntrant]:
        return await scoped(
            StartggEntrant.filter(tournament_id=tournament_id)
        ).prefetch_related('user').order_by('id')

    @staticmethod
    async def startgg_pools(tournament_id: int) -> List[StartggPool]:
        return await scoped(
            StartggPool.filter(tournament_id=tournament_id)
        ).prefetch_related('phase').order_by('id')

    @staticmethod
    async def startgg_sets(tournament_id: int) -> List[StartggSet]:
        return await scoped(
            StartggSet.filter(tournament_id=tournament_id)
        ).order_by('round', 'id')

    @staticmethod
    async def matches(tournament_id: int) -> List[Match]:
        return await scoped(
            Match.filter(tournament_id=tournament_id)
        ).prefetch_related('players__user', 'stage', 'generated_seed').order_by('scheduled_at', 'id')
