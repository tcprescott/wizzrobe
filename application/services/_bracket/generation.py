"""Bracket generation mixin: start + match-graph materialization.

Split out of ``bracket_service.py`` as pure code motion (see that module's
docstring). ``GenerationMixin`` is composed into :class:`BracketService`; its
methods reach siblings, ``self.repository`` and ``self.audit_service`` through
that composed class.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from application.events import EventType
from application.repositories._save import save_fields
from application.services._bracket._composed import ComposedBracketService
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.bracket_engines import get_bracket_engine
from application.services.bracket_engines.base import PairingPlayer
from models import (
    Bracket,
    BracketEntry,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    BracketState,
    User,
)


@dataclass(frozen=True)
class DrawPreview:
    """What Start *would* produce, computed without writing anything.

    ``matches`` are unsaved :class:`BracketMatch` instances carrying synthetic
    negative ids, shaped exactly like the rows generation would persist so the
    ordinary renderer can draw them. ``error`` is set when the field cannot start
    at all (an inconsistent seeding), in which case ``matches`` is empty —
    the preview reports the problem instead of the caller discovering it at Start.
    """

    entry_count: int
    seed_of: Dict[int, Optional[int]] = field(default_factory=dict)
    matches: List[BracketMatch] = field(default_factory=list)
    summary: List[str] = field(default_factory=list)
    error: Optional[str] = None


class GenerationMixin(ComposedBracketService):
    # -- draw preview (pure: the engine over the current seeding, no writes) ---
    async def preview_draw(self, bracket_id: int) -> DrawPreview:
        """Run the engine over the current seeding and return the projected draw.

        The read-only twin of :meth:`start_bracket`: the same engine, the same
        seeding rule, the same bye materialization — but in memory, so an
        organizer can see the first round before committing to it. Start is the
        draw, the publish and the notification at once, so without this the only
        way to find out what the seeding produces is to publish it.
        """
        bracket = await self._require_bracket(bracket_id)
        entries = await self.repository.list_active_entries(bracket_id)
        seed_of, error = self._projected_seeding(entries)
        if error is not None:
            return DrawPreview(entry_count=len(entries), seed_of=seed_of, error=error)
        if len(entries) < 2:
            return DrawPreview(
                entry_count=len(entries), seed_of=seed_of,
                summary=['Enroll at least 2 entrants to draw this stage.'],
            )

        seed_to_entry = {seed_of[e.id]: e for e in entries}
        config = dict(bracket.config or {})
        engine = get_bracket_engine(bracket.format.value)()
        try:
            if bracket.format == BracketFormat.SWISS:
                matches = self._preview_swiss(bracket, entries, engine, config)
            else:
                matches = self._preview_generative(
                    bracket, len(entries), seed_to_entry, engine, config,
                )
        except ValueError as exc:
            return DrawPreview(
                entry_count=len(entries), seed_of=seed_of, error=str(exc),
            )
        return DrawPreview(
            entry_count=len(entries),
            seed_of=seed_of,
            matches=matches,
            summary=self._preview_summary(bracket, len(entries), matches, config),
        )

    def _preview_generative(
        self,
        bracket: Bracket,
        num_entries: int,
        seed_to_entry: Dict[int, BracketEntry],
        engine: Any,
        config: Dict[str, Any],
    ) -> List[BracketMatch]:
        """The generated graph as unsaved rows, byes already resolved.

        Mirrors :meth:`_start_generative`'s three passes — shell rows, pointer
        resolution, bye propagation + the OPEN/PENDING state pass — against
        in-memory objects. Synthetic ids are negative so they can never collide
        with a persisted id if one of these ever reaches a lookup by accident.
        """
        graph = engine.generate(num_entries, config)

        by_coord: Dict[tuple, tuple] = {}
        rows: List[BracketMatch] = []
        for index, gm in enumerate(graph):
            position = gm.position
            if gm.group_number:
                position += (gm.group_number - 1) * 100000
            entry1 = seed_to_entry.get(gm.entry1_seed)
            entry2 = seed_to_entry.get(gm.entry2_seed)
            row = BracketMatch(
                id=-(index + 1),
                bracket_id=bracket.id,
                round=gm.round,
                position=position,
                group_number=gm.group_number,
                entry1_id=entry1.id if entry1 is not None else None,
                entry2_id=entry2.id if entry2 is not None else None,
                state=BracketMatchState.PENDING,
            )
            rows.append(row)
            by_coord[(gm.round, gm.position, gm.group_number)] = (row, gm)

        for row, gm in by_coord.values():
            if gm.winner_to is not None:
                target = by_coord[
                    (gm.winner_to.round, gm.winner_to.position, gm.group_number)
                ][0]
                row.winner_to_id = target.id
                row.winner_to_slot = gm.winner_to.slot
            if gm.loser_to is not None:
                target = by_coord[
                    (gm.loser_to.round, gm.loser_to.position, gm.group_number)
                ][0]
                row.loser_to_id = target.id
                row.loser_to_slot = gm.loser_to.slot

        by_id = {row.id: row for row in rows}
        processed: set = set()
        changed = True
        while changed:
            changed = False
            for row, gm in by_coord.values():
                if not gm.is_bye or row.id in processed:
                    continue
                winner = (
                    seed_to_entry.get(gm.entry1_seed) or seed_to_entry.get(gm.entry2_seed)
                )
                processed.add(row.id)
                if winner is None:
                    continue
                row.winner_id = winner.id
                row.state = BracketMatchState.COMPLETE
                target = by_id.get(row.winner_to_id) if row.winner_to_id else None
                if target is not None:
                    if row.winner_to_slot == 1:
                        target.entry1_id = winner.id
                    else:
                        target.entry2_id = winner.id
                changed = True

        for row in rows:
            if row.state == BracketMatchState.COMPLETE:
                continue
            row.state = (
                BracketMatchState.OPEN
                if row.entry1_id is not None and row.entry2_id is not None
                else BracketMatchState.PENDING
            )
        return rows

    def _preview_swiss(
        self,
        bracket: Bracket,
        entries: List[BracketEntry],
        engine: Any,
        config: Dict[str, Any],
    ) -> List[BracketMatch]:
        """Swiss round 1 as unsaved rows. Later rounds depend on results."""
        entry_by_id = {e.id: e for e in entries}
        pairings = engine.pair_round([PairingPlayer(ref=e.id) for e in entries], config)
        rows: List[BracketMatch] = []
        for position, (ref1, ref2) in enumerate(pairings, start=1):
            bye = ref2 is None
            rows.append(BracketMatch(
                id=-position,
                bracket_id=bracket.id,
                round=1,
                position=position,
                entry1_id=entry_by_id[ref1].id,
                entry2_id=None if bye else entry_by_id[ref2].id,
                winner_id=entry_by_id[ref1].id if bye else None,
                state=BracketMatchState.COMPLETE if bye else BracketMatchState.OPEN,
            ))
        return rows

    def _preview_summary(
        self,
        bracket: Bracket,
        num_entries: int,
        matches: List[BracketMatch],
        config: Dict[str, Any],
    ) -> List[str]:
        """Plain-language lines describing the projected draw."""
        lines: List[str] = [f'{num_entries} entrants']
        byes = sum(
            1 for m in matches
            if m.round == min((x.round for x in matches), default=1)
            and (m.entry1_id is None or m.entry2_id is None)
        )
        if bracket.format in self._ELIM_FORMATS:
            rounds = len({m.round for m in matches if m.round > 0})
            lines.append(f'{len(matches)} matches across {rounds} winners round(s)')
            if bracket.format == BracketFormat.DOUBLE_ELIM:
                losers = len({m.round for m in matches if m.round < 0})
                lines.append(f'{losers} losers round(s)')
                lines.append(
                    'Grand final reset enabled'
                    if config.get('grand_final_reset', True)
                    else 'No grand final reset — one grand final decides it'
                )
            if byes:
                lines.append(f'{byes} first-round bye(s)')
        elif bracket.format == BracketFormat.ROUND_ROBIN:
            groups = {m.group_number for m in matches}
            per_group = num_entries // max(1, len(groups))
            lines.append(
                f'{len(groups)} group(s) of about {per_group} · {len(matches)} matches'
            )
        else:
            target = config.get('swiss_rounds')
            derived = max(1, math.ceil(math.log2(num_entries))) if num_entries > 1 else 1
            lines.append(
                f'{target} rounds'
                if target
                else f'{derived} rounds (derived from the field size — set it explicitly to fix it)'
            )
            lines.append(f'{len(matches)} pairings in round 1')
            if byes:
                lines.append(f'{byes} bye(s) per round')
        return lines

    # -- start (generate + persist the match graph) -----------------------
    async def start_bracket(self, actor: Optional[User], bracket_id: int) -> Bracket:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        bracket = await self._require_bracket(bracket_id)
        if bracket.state != BracketState.DRAFT:
            raise ValueError("Only a DRAFT bracket can be started")

        if bracket.stage_order > 0:
            predecessor = await self.repository.get_stage(
                bracket.tournament_id, bracket.stage_order - 1
            )
            if predecessor is None or predecessor.state != BracketState.COMPLETE:
                raise ValueError(
                    "The previous stage must be complete before starting this stage"
                )

        entries = await self.repository.list_active_entries(bracket_id)
        if len(entries) < 2:
            raise ValueError("A bracket needs at least 2 active entries to start")

        seed_to_entry = await self._assign_seeds(entries)
        num_entries = len(entries)

        engine = get_bracket_engine(bracket.format.value)()
        config_dict: Dict[str, Any] = dict(bracket.config or {})

        if bracket.format == BracketFormat.SWISS:
            await self._start_swiss(bracket, entries, engine, config_dict)
        else:
            await self._start_generative(bracket, num_entries, seed_to_entry, engine, config_dict)

        bracket.state = BracketState.ACTIVE
        await save_fields(bracket, 'state')

        details = {
            'bracket_id': bracket.id,
            'tournament_id': bracket.tournament_id,
            'format': bracket.format.value,
            'num_entries': num_entries,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_STARTED, details, EventType.BRACKET_STARTED,
        )
        # Generation writes the opening round straight to OPEN rather than
        # transitioning into it, so ``_settle_match``'s per-transition DM never
        # sees round 1 (D7). Announce them here instead — after the state pass,
        # so every matchup this reads is settled.
        await self.notify_open_matchups(bracket)
        return bracket

    @staticmethod
    def _projected_seeding(
        entries: List[BracketEntry],
    ) -> Tuple[Dict[int, Optional[int]], Optional[str]]:
        """The seeding a field *would* resolve to, and why it would not. Pure.

        Existing seeds are kept; missing seeds are filled from the unused values
        in ``1..N``, assigned to seed-less entries in entry-id order — so the
        result is deterministic and independent of insertion timing.

        The result must be exactly the contiguous ``1..N`` with no duplicates: a
        duplicate or out-of-range value would otherwise collapse or strand an
        engine slot and silently drop an entrant, leaving matches stuck PENDING.
        Returned rather than raised so the DRAFT preview can *show* the problem
        while :meth:`_assign_seeds` still refuses to start on it.
        """
        n = len(entries)
        used = {e.seed for e in entries if e.seed is not None}
        available = [s for s in range(1, n + 1) if s not in used]
        missing = sorted((e for e in entries if e.seed is None), key=lambda e: e.id)
        filled = dict(zip((e.id for e in missing), available, strict=False))
        seed_of: Dict[int, Optional[int]] = {
            e.id: (e.seed if e.seed is not None else filled.get(e.id)) for e in entries
        }

        assigned = sorted(s for s in seed_of.values() if s is not None)
        if assigned == list(range(1, n + 1)):
            return seed_of, None
        shown = sorted(seed_of.values(), key=lambda s: (s is None, s))
        return seed_of, (
            f"Bracket seeds must be a contiguous 1..{n} with no duplicates, but "
            f"the current seeding is {shown}. Fix the duplicate or out-of-range "
            "seeds before starting."
        )

    async def _assign_seeds(self, entries: List[BracketEntry]) -> Dict[int, BracketEntry]:
        """Persist the projected seeding onto the field, or refuse.

        Validates before writing, so a field that cannot start is left exactly as
        the operator left it rather than half-renumbered.
        """
        seed_of, error = self._projected_seeding(entries)
        if error is not None:
            raise ValueError(error)
        for entry in entries:
            if entry.seed != seed_of[entry.id]:
                entry.seed = seed_of[entry.id]
                await save_fields(entry, 'seed')
        return {e.seed: e for e in entries}

    async def _start_generative(
        self,
        bracket: Bracket,
        num_entries: int,
        seed_to_entry: Dict[int, BracketEntry],
        engine: Any,
        config_dict: Dict[str, Any],
    ) -> None:
        graph = engine.generate(num_entries, config_dict)

        # Pass 1 — persist a shell BracketMatch per generated node. Grouped
        # formats (round robin) number positions per group, so offset the
        # persisted position by group to keep (bracket, round, position) unique;
        # these nodes carry no winner_to/loser_to pointers, so the offset only
        # affects storage, not routing.
        by_coord: Dict[tuple, tuple] = {}
        for gm in graph:
            position = gm.position
            if gm.group_number:
                position += (gm.group_number - 1) * 100000
            bm = await self.repository.create_match(
                bracket_id=bracket.id,
                round=gm.round,
                position=position,
                group_number=gm.group_number,
                entry1=seed_to_entry.get(gm.entry1_seed),
                entry2=seed_to_entry.get(gm.entry2_seed),
                state=BracketMatchState.PENDING,
            )
            by_coord[(gm.round, gm.position, gm.group_number)] = (bm, gm)

        # Pass 2 — resolve winner_to / loser_to pointers into the persisted graph.
        for bm, gm in by_coord.values():
            changed = False
            if gm.winner_to is not None:
                target = by_coord[
                    (gm.winner_to.round, gm.winner_to.position, gm.group_number)
                ][0]
                bm.winner_to = target
                bm.winner_to_slot = gm.winner_to.slot
                changed = True
            if gm.loser_to is not None:
                target = by_coord[
                    (gm.loser_to.round, gm.loser_to.position, gm.group_number)
                ][0]
                bm.loser_to = target
                bm.loser_to_slot = gm.loser_to.slot
                changed = True
            if changed:
                await save_fields(bm, 'winner_to', 'winner_to_slot', 'loser_to', 'loser_to_slot')

        # Materialize initial state: auto-complete every structural bye and
        # propagate its winner, iterating to a fixpoint (byes never create new
        # byes, so this terminates after each bye is processed once). A downstream
        # match that receives two propagated winners becomes a normal match and is
        # NOT auto-completed.
        processed: set = set()
        changed = True
        while changed:
            changed = False
            for bm, gm in by_coord.values():
                if not gm.is_bye or bm.id in processed:
                    continue
                winner_entry = seed_to_entry.get(gm.entry1_seed) or seed_to_entry.get(gm.entry2_seed)
                if winner_entry is None:
                    processed.add(bm.id)
                    continue
                await self._propagate_winner(bm, winner_entry)
                processed.add(bm.id)
                changed = True

        # Final state pass: both entries present → OPEN, else PENDING.
        for match in await self.repository.list_matches(bracket.id):
            if match.state == BracketMatchState.COMPLETE:
                continue
            if match.entry1_id is not None and match.entry2_id is not None:
                match.state = BracketMatchState.OPEN
            else:
                match.state = BracketMatchState.PENDING
            await save_fields(match, 'state')

    async def _start_swiss(
        self,
        bracket: Bracket,
        entries: List[BracketEntry],
        engine: Any,
        config_dict: Dict[str, Any],
    ) -> None:
        entry_by_id = {e.id: e for e in entries}
        players = [PairingPlayer(ref=e.id) for e in entries]
        pairings = engine.pair_round(players, config_dict)

        for position, (ref1, ref2) in enumerate(pairings, start=1):
            if ref2 is None:
                winner = entry_by_id[ref1]
                await self.repository.create_match(
                    bracket_id=bracket.id,
                    round=1,
                    position=position,
                    entry1=winner,
                    entry2=None,
                    winner=winner,
                    state=BracketMatchState.COMPLETE,
                )
            else:
                await self.repository.create_match(
                    bracket_id=bracket.id,
                    round=1,
                    position=position,
                    entry1=entry_by_id[ref1],
                    entry2=entry_by_id[ref2],
                    state=BracketMatchState.OPEN,
                )

    async def _propagate_winner(
        self, match: BracketMatch, winner_entry: BracketEntry
    ) -> Optional[BracketMatch]:
        """Complete ``match`` with ``winner_entry`` and push it into ``winner_to``.

        Sets the winner + COMPLETE state, saves, and — when a ``winner_to`` pointer
        exists — fills the target's ``entry1``/``entry2`` per ``winner_to_slot`` and
        saves the target. Returns the target match (or ``None``). Reused by B7's
        advancement, so it re-fetches the target rather than trusting an in-memory
        pointer object.
        """
        match.winner = winner_entry
        match.state = BracketMatchState.COMPLETE
        await save_fields(match, 'winner', 'state')

        if match.winner_to_id is None:
            return None
        target = await self.repository.get_match(match.winner_to_id)
        if target is None:
            return None
        if match.winner_to_slot == 1:
            target.entry1 = winner_entry
        else:
            target.entry2 = winner_entry
        await save_fields(target, 'entry1' if match.winner_to_slot == 1 else 'entry2')
        return target
