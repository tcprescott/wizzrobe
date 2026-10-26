"""Bracket Repository — data access for the five native-bracket models.

Pure data access (no business logic, audit, or notifications). One repository
spans :class:`Bracket`, :class:`BracketEntrant`, :class:`BracketEntry`,
:class:`BracketMatch`, and :class:`BracketMatchGame` — they are one aggregate the
lifecycle drives together, so a single tenant-scoped repository keeps their
queries in one place. Reads scope through the ``_tenant`` helper; custom creates
stamp the ambient tenant.
"""

from typing import List, Optional

from tortoise.expressions import Q

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    Bracket,
    BracketEntrant,
    BracketEntry,
    BracketMatch,
    BracketMatchGame,
    Match,
)


class BracketRepository(TenantScopedRepository[Bracket]):
    """Data access for brackets, entrants, entries, matches, and series games."""

    model = Bracket

    # --- Bracket ---------------------------------------------------------
    async def get_bracket(self, bracket_id: int) -> Optional[Bracket]:
        return await scoped(Bracket.filter(id=bracket_id)).first()

    async def list_for_tournament(self, tournament_id: int) -> List[Bracket]:
        return await scoped(
            Bracket.filter(tournament_id=tournament_id)
        ).order_by('stage_order')

    async def list_all_with_tournament(self) -> List[Bracket]:
        """Every stage in the tenant with its tournament loaded.

        Backs the browse surface, which groups stages under their tournament —
        so the tournament is prefetched here rather than lazily per row.
        """
        return await scoped(Bracket.all()).prefetch_related('tournament').order_by(
            '-tournament__is_active', 'tournament__name', 'tournament_id', 'stage_order'
        )

    async def get_stage(self, tournament_id: int, stage_order: int) -> Optional[Bracket]:
        return await scoped(
            Bracket.filter(tournament_id=tournament_id, stage_order=stage_order)
        ).first()

    # --- BracketEntrant --------------------------------------------------
    async def create_entrant(self, **fields) -> BracketEntrant:
        return await BracketEntrant.create(tenant_id=current_tenant_id(), **fields)

    async def user_has_entry(self, bracket_id: int, user_id: int) -> bool:
        return await scoped(
            BracketEntry.filter(bracket_id=bracket_id, entrant__user_id=user_id)
        ).exists()

    async def get_entrant(self, entrant_id: int) -> Optional[BracketEntrant]:
        return await scoped(BracketEntrant.filter(id=entrant_id)).first()

    async def update_entrant(self, entrant: BracketEntrant, **fields) -> BracketEntrant:
        """Persist field changes on one entrant.

        Named rather than inherited: the generic ``update`` is typed against this
        repository's ``model`` (:class:`Bracket`), and this aggregate spans five
        models — so each writable one gets an explicit ``update_*`` beside its
        ``create_*``, all delegating to the one shared setattr-and-save.
        """
        return await self.update(entrant, **fields)

    async def list_entrants(self, tournament_id: int) -> List[BracketEntrant]:
        # ``user`` is prefetched for the linked account's Discord avatar, which
        # every bracket surface paints beside the name — one extra query for the
        # whole field beats one per entrant at render time.
        return await scoped(
            BracketEntrant.filter(tournament_id=tournament_id)
        ).prefetch_related('user').order_by('display_name')

    # --- BracketEntry ----------------------------------------------------
    async def create_entry(self, **fields) -> BracketEntry:
        return await BracketEntry.create(tenant_id=current_tenant_id(), **fields)

    async def get_entry(self, entry_id: int) -> Optional[BracketEntry]:
        return await scoped(BracketEntry.filter(id=entry_id)).first()

    async def update_entry(self, entry: BracketEntry, **fields) -> BracketEntry:
        """Persist field changes on one stage entry. See :meth:`update_entrant`."""
        return await self.update(entry, **fields)

    async def delete_entry(self, entry: BracketEntry) -> None:
        """Remove one stage entry outright (DRAFT un-enrollment)."""
        await entry.delete()

    async def set_entry_seeds(self, seeds: dict) -> None:
        """Apply ``{entry_id: seed}`` in one statement per distinct seed value.

        The seeding UI submits the whole field at once, so the naive loop was one
        UPDATE per entry — 64 sequential round-trips on the shared event loop for a
        64-entrant reseed. Grouping by seed collapses that to one statement per
        distinct value (and clearing seeds to a single ``seed=None`` update).
        Callers must have validated the set first: this writes what it is given.
        """
        if not seeds:
            return
        by_seed: dict = {}
        for entry_id, seed in seeds.items():
            by_seed.setdefault(seed, []).append(entry_id)
        for seed, entry_ids in by_seed.items():
            await scoped(BracketEntry.filter(id__in=entry_ids)).update(seed=seed)

    async def list_entries(self, bracket_id: int) -> List[BracketEntry]:
        return await scoped(
            BracketEntry.filter(bracket_id=bracket_id)
        ).order_by('seed', 'id')

    async def list_active_entries(self, bracket_id: int) -> List[BracketEntry]:
        from models import BracketEntryStatus
        return await scoped(
            BracketEntry.filter(bracket_id=bracket_id, status=BracketEntryStatus.ACTIVE)
        ).order_by('seed', 'id')

    async def get_entry_for_entrant(
        self, bracket_id: int, entrant_id: int
    ) -> Optional[BracketEntry]:
        return await scoped(
            BracketEntry.filter(bracket_id=bracket_id, entrant_id=entrant_id)
        ).first()

    # --- BracketMatch ----------------------------------------------------
    async def create_match(self, **fields) -> BracketMatch:
        return await BracketMatch.create(tenant_id=current_tenant_id(), **fields)

    async def get_match(self, match_id: int) -> Optional[BracketMatch]:
        return await scoped(BracketMatch.filter(id=match_id)).first()

    async def update_match(self, match: BracketMatch, **fields) -> BracketMatch:
        """Persist field changes on one matchup. See :meth:`update_entrant`."""
        return await self.update(match, **fields)

    async def list_matches(self, bracket_id: int) -> List[BracketMatch]:
        # ``games__match`` is prefetched so a whole round can render its series
        # state — including each game's scheduled time — without an N+1 across
        # the bracket view. ``__stage`` rides along for the live "Watch"
        # link (U2): a fixed number of queries for the whole field, whatever its
        # size, which is the rule the bracket view is held to.
        return await scoped(
            BracketMatch.filter(bracket_id=bracket_id)
        ).prefetch_related(
            'games__match__stage',
        ).order_by('round', 'position')

    async def round_links(self, bracket_id: int) -> List[dict]:
        """``(id, round, winner_to_id, loser_to_id)`` for a stage — one lean query.

        A ``.values()`` projection rather than whole rows: round *naming*
        (``bracket_engines.round_names``) needs the progression pointers and
        nothing else, and it runs on the notification path where a full
        ``list_matches`` would be wasteful.
        """
        return await scoped(BracketMatch.filter(bracket_id=bracket_id)).values(
            'id', 'round', 'winner_to_id', 'loser_to_id',
        )

    async def get_match_at(
        self, bracket_id: int, round: int, position: int
    ) -> Optional[BracketMatch]:
        return await scoped(
            BracketMatch.filter(bracket_id=bracket_id, round=round, position=position)
        ).first()

    async def list_matches_in_round(
        self, bracket_id: int, round: int
    ) -> List[BracketMatch]:
        return await scoped(
            BracketMatch.filter(bracket_id=bracket_id, round=round)
        ).order_by('position')

    async def list_open_matches(self, bracket_id: int) -> List[BracketMatch]:
        from models import BracketMatchState
        # ``games__match`` (not just ``games``) so callers can derive each
        # matchup's live status without an N+1 — one more batched query.
        return await scoped(
            BracketMatch.filter(bracket_id=bracket_id, state=BracketMatchState.OPEN)
        ).prefetch_related('games__match').order_by('round', 'position')

    async def get_match_with_games(self, match_id: int) -> Optional[BracketMatch]:
        """A bracket match with its series games loaded.

        Separate from the lean :meth:`get_match`, which the advancement recursion
        calls repeatedly and must not pay a join for. Used where the games are
        actually rendered or serialized.
        """
        return await scoped(
            BracketMatch.filter(id=match_id)
        ).prefetch_related('games__match').first()

    async def max_round(self, bracket_id: int) -> Optional[int]:
        row = await scoped(
            BracketMatch.filter(bracket_id=bracket_id)
        ).order_by('-round').first()
        return row.round if row is not None else None

    async def winner_feeders(self, match_id: int, slot: int) -> List[BracketMatch]:
        """Not-yet-COMPLETE matches whose ``winner`` feeds ``slot`` of ``match_id``."""
        from models import BracketMatchState
        return await scoped(
            BracketMatch.filter(winner_to_id=match_id, winner_to_slot=slot)
        ).exclude(state=BracketMatchState.COMPLETE)

    async def loser_feeders(self, match_id: int, slot: int) -> List[BracketMatch]:
        """Not-yet-COMPLETE matches whose ``loser`` feeds ``slot`` of ``match_id``."""
        from models import BracketMatchState
        return await scoped(
            BracketMatch.filter(loser_to_id=match_id, loser_to_slot=slot)
        ).exclude(state=BracketMatchState.COMPLETE)

    # --- scheduling seam (mirrors ChallongeRepository) -------------------
    async def get_match_with_bracket(self, bracket_match_id: int) -> Optional[BracketMatch]:
        """One matchup with its parent stage, for readers that need the stage config.

        Lighter than :meth:`get_match_with_entrants` — the suggestion path wants
        ``bracket.config['rounds']`` and the round number, not the entry chains.
        """
        return await scoped(
            BracketMatch.filter(id=bracket_match_id)
        ).prefetch_related('bracket').first()

    async def get_match_with_entrants(self, bracket_match_id: int) -> Optional[BracketMatch]:
        """A bracket match with both entry slots resolved to their linked users.

        Used by the scheduler, which needs ``entry.entrant.user`` on both sides.
        """
        return await scoped(
            BracketMatch.filter(id=bracket_match_id)
        ).prefetch_related(
            'bracket__tournament', 'entry1__entrant__user', 'entry2__entrant__user',
        ).first()

    async def get_game_for_match(self, match_id: int) -> Optional[BracketMatchGame]:
        """The series game backed by a scheduled ``Match`` (the scheduling seam).

        A ``Match`` backs at most one game (``BracketMatchGame.match`` is a
        OneToOne), so this is single-valued. The parent bracket match and both
        entry chains are prefetched — the settle path needs them to map a
        ``finish_rank`` winner onto a :class:`BracketEntry`.
        """
        return await scoped(
            BracketMatchGame.filter(match_id=match_id)
        ).prefetch_related(
            'bracket_match__bracket',
            'bracket_match__entry1__entrant__user',
            'bracket_match__entry2__entrant__user',
        ).first()

    async def get_bracket_match_for_match(self, match_id: int) -> Optional[BracketMatch]:
        """The bracket match whose series contains the game backed by ``match_id``."""
        game = await self.get_game_for_match(match_id)
        return game.bracket_match if game is not None else None

    async def open_matches_for_user(
        self, user_id: int, tournament_id: Optional[int] = None
    ) -> List[BracketMatch]:
        """OPEN bracket matches where ``user_id`` is one of the two entrants.

        Joins entry → entrant → user on either slot. Optionally narrowed to one
        tournament. ``games`` is prefetched so the service can drop the ones whose
        series has no free slot left — that filter needs the resolved ``best_of``,
        which is business logic and so cannot live here.

        Excludes matchups in a **cancelled** stage. Cancelling a stage flips only
        the bracket's own state; its matchups stay OPEN, so without this the
        abandoned stage kept inviting both players to schedule a game in it.
        """
        from models import BracketMatchState, BracketState
        qs = scoped(BracketMatch.filter(
            Q(entry1__entrant__user_id=user_id) | Q(entry2__entrant__user_id=user_id),
            state=BracketMatchState.OPEN,
        )).exclude(bracket__state=BracketState.CANCELLED)
        if tournament_id is not None:
            qs = qs.filter(bracket__tournament_id=tournament_id)
        return await qs.prefetch_related(
            'bracket__tournament', 'entry1__entrant__user', 'entry2__entrant__user',
            'games',
        ).distinct().order_by('round', 'position')

    async def open_matches_for_tournament(
        self, tournament_id: int
    ) -> List[BracketMatch]:
        """Every OPEN matchup across a tournament's stages, entrants resolved.

        Backs the admin editor's "link this match to a matchup" picker, so it
        prefetches the same relations ``open_matches_for_user`` does — and
        excludes a cancelled stage's matchups for the same reason it does.
        """
        from models import BracketMatchState, BracketState
        return await scoped(BracketMatch.filter(
            bracket__tournament_id=tournament_id,
            state=BracketMatchState.OPEN,
        )).exclude(bracket__state=BracketState.CANCELLED).prefetch_related(
            'bracket', 'entry1__entrant__user', 'entry2__entrant__user', 'games',
        ).order_by('bracket__stage_order', 'round', 'position')

    async def open_matches_for_tournaments(
        self, tournament_ids: Optional[List[int]] = None
    ) -> List[BracketMatch]:
        """Every OPEN matchup in active tournaments; ``None`` means all of them.

        Excludes a cancelled stage's matchups, as ``open_matches_for_tournament`` does.
        """
        from models import BracketMatchState, BracketState
        qs = scoped(BracketMatch.filter(
            state=BracketMatchState.OPEN, bracket__tournament__is_active=True,
        )).exclude(bracket__state=BracketState.CANCELLED)
        if tournament_ids is not None:
            qs = qs.filter(bracket__tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'bracket__tournament', 'entry1__entrant__user', 'entry2__entrant__user', 'games',
        ).order_by('bracket__tournament_id', 'bracket__stage_order', 'round', 'position')

    # --- BracketMatchGame -------------------------------------------------
    async def create_game(self, **fields) -> BracketMatchGame:
        return await BracketMatchGame.create(tenant_id=current_tenant_id(), **fields)

    async def get_game(self, game_id: int) -> Optional[BracketMatchGame]:
        return await scoped(BracketMatchGame.filter(id=game_id)).first()

    async def delete_game(self, game_id: int) -> int:
        """Delete one game row, freeing its slot. Returns the rows removed."""
        return await scoped(BracketMatchGame.filter(id=game_id)).delete()

    async def list_games(self, bracket_match_id: int, *, with_matches: bool = False) -> List[BracketMatchGame]:
        query = scoped(
            BracketMatchGame.filter(bracket_match_id=bracket_match_id)
        ).order_by('game_number')
        if with_matches:
            query = query.prefetch_related('match')
        return await query

    async def games_for_matches(self, match_ids: List[int]) -> List[BracketMatchGame]:
        """The game rows backed by any of ``match_ids`` — **unscoped**, one query.

        Deliberately tenant-agnostic, like ``matches_due_for_auto_open``: the
        auto-open worker's hold runs over that scan's cross-tenant candidate list
        *before* it binds each match's ``tenant_scope``, so a scoped read here
        would raise. Safe because the caller supplies match ids it already holds
        and the result only ever computes a hold decision — no row reaches a user.
        """
        if not match_ids:
            return []
        return await BracketMatchGame.filter(match_id__in=match_ids)

    async def games_for_series(
        self, bracket_match_ids: List[int]
    ) -> List[BracketMatchGame]:
        """Every game row of the given series, with each game's ``Match``.

        The auto-open hold reads ``match.finished_at`` off these, so the ``Match``
        is prefetched — this plus :meth:`games_for_matches` is what keeps the hold
        at two queries regardless of how many candidates the worker is weighing.
        **Unscoped** for the same reason, and safe on the same grounds.
        """
        if not bracket_match_ids:
            return []
        return await BracketMatchGame.filter(
            bracket_match_id__in=bracket_match_ids
        ).prefetch_related('match')

    async def series_matches_due(self, window_start, window_end) -> List[Match]:
        """Scheduled series games in a **wider** band than the auto-open poll's.

        The poll's scan starts at ``now - 15min``, so a game whose slot slipped
        further than that while the previous game ran long has dropped out of it
        for good. The push at game-end normally opens such a game immediately;
        this is the backstop for when the process died in between. **Unscoped**
        like its sibling scans — the worker binds each match's tenant itself.
        """
        from models import BracketMatchGameState

        games = await BracketMatchGame.filter(
            state=BracketMatchGameState.SCHEDULED,
            match_id__isnull=False,
            match__scheduled_at__gte=window_start,
            match__scheduled_at__lte=window_end,
            match__finished_at__isnull=True,
            match__tournament__racetime_auto_create_rooms=True,
        ).prefetch_related('match__tournament', 'match__players__user')
        return [g.match for g in games if g.match is not None]

    async def settle_game(self, game_id: int, **fields) -> bool:
        """Compare-and-swap a SCHEDULED game to its settled state.

        Returns True iff *this* call moved the row. Two settle paths race for the
        same game — ``RaceRoomService.record_finish`` on the racetimebot task and
        ``advance_if_linked`` on the serial ``discord_queue`` — and counting one
        game's win twice would let a Bo3 "clinch" 2-0 off a single game. The
        conditional UPDATE makes the loser a no-op without needing a transaction.
        """
        from models import BracketMatchGameState
        updated = await scoped(BracketMatchGame.filter(
            id=game_id, state=BracketMatchGameState.SCHEDULED,
        )).update(**fields)
        return bool(updated)
