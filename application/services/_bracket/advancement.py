"""Bracket advancement mixin: result reporting, pointer-following, walkovers.

Split out of ``bracket_service.py`` as pure code motion (see that module's
docstring). ``AdvancementMixin`` is composed into :class:`BracketService`; its
methods reach siblings (including ``_propagate_winner`` and
``_maybe_complete_stage`` defined on other mixins), ``self.repository`` and
``self.audit_service`` through that composed class.
"""

from typing import List, Optional

from application.errors import require_found
from application.events import EventType
from application.repositories._save import save_fields
from application.services._bracket._composed import ComposedBracketService
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from models import (
    Bracket,
    BracketEntry,
    BracketEntryStatus,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    BracketState,
    User,
)


class AdvancementMixin(ComposedBracketService):
    _ELIM_FORMATS = (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM)

    async def report_result(
        self,
        actor: Optional[User],
        match_id: int,
        winner_entry_id: int,
        *,
        entry1_score: Optional[int] = None,
        entry2_score: Optional[int] = None,
        forfeit: bool = False,
    ) -> BracketMatch:
        """Record ``winner_entry_id`` as the winner of an OPEN match, then advance.

        Staff-gated. The match must be OPEN and belong to an ACTIVE bracket, and
        the winner must be one of the match's two entries. Set scores may be
        reported optionally (positional to the entry slots); the winner must
        carry the strictly-higher score unless ``forfeit`` marks a DQ / walkover.
        After completing the match the winner and loser are pushed through the
        ``winner_to`` / ``loser_to`` pointers, downstream slots are settled
        (auto-advancing walkovers), and the stage is auto-completed when an
        elimination final resolves.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        return await self._record_result(
            actor, match_id, winner_entry_id,
            entry1_score=entry1_score, entry2_score=entry2_score, forfeit=forfeit,
        )

    @staticmethod
    def _validate_scores(
        match: BracketMatch,
        winner_entry_id: int,
        entry1_score: Optional[int],
        entry2_score: Optional[int],
        forfeit: bool,
    ) -> None:
        """Enforce the score-vs-winner rule (conditional on ``forfeit``).

        Scores are optional and all-or-nothing — win-only reporting stays valid.
        When both are given and it is not a forfeit, the reported winner must
        carry the strictly-higher score. A forfeit waives the rule so a `0`-score
        or lower-score win (a DQ / walkover) is accepted.
        """
        if entry1_score is None and entry2_score is None:
            return
        if entry1_score is None or entry2_score is None:
            raise ValueError("Provide both scores or neither")
        if entry1_score < 0 or entry2_score < 0:
            raise ValueError("Scores cannot be negative")
        if forfeit:
            return
        winner_first = winner_entry_id == match.entry1_id
        winner_score = entry1_score if winner_first else entry2_score
        loser_score = entry2_score if winner_first else entry1_score
        if winner_score <= loser_score:
            raise ValueError(
                "The winner must have the strictly-higher score "
                "(or mark the result a forfeit)"
            )

    async def _record_result(
        self,
        actor: Optional[User],
        match_id: int,
        winner_entry_id: int,
        *,
        entry1_score: Optional[int] = None,
        entry2_score: Optional[int] = None,
        forfeit: bool = False,
    ) -> BracketMatch:
        """Set the winner of an OPEN match and advance — WITHOUT the staff gate.

        The un-gated core of :meth:`report_result`: it validates match/bracket
        state, records the winner (and any set scores / forfeit flag), writes the
        audit log + event, advances through the progression pointers, settles
        downstream walkovers, and auto-completes the stage. The staff gate lives
        on the public :meth:`report_result`; the match-confirm auto-advance seam
        (``advance_if_linked``) reaches this directly so a non-Staff confirmer's
        result still advances the bracket (the Challonge peer ``push_match_result``
        has no such gate).
        """
        match = require_found(await self.repository.get_match(match_id), "Match")
        bracket = await self._require_bracket(match.bracket_id)
        if bracket.state != BracketState.ACTIVE:
            raise ValueError("Can only report results for an ACTIVE bracket")
        if match.state != BracketMatchState.OPEN:
            raise ValueError("Only an OPEN match can be reported")
        if winner_entry_id not in (match.entry1_id, match.entry2_id):
            raise ValueError("Winner must be one of the match's two entries")
        self._validate_scores(
            match, winner_entry_id, entry1_score, entry2_score, forfeit
        )

        winner_entry = require_found(
            await self.repository.get_entry(winner_entry_id), "Entry"
        )
        loser_id = (
            match.entry1_id if winner_entry_id == match.entry2_id else match.entry2_id
        )
        loser_entry = require_found(
            await self.repository.get_entry(loser_id), "Entry"
        )

        match.winner = winner_entry
        match.entry1_score = entry1_score  # type: ignore[assignment]  # nullable IntField, typed int by Tortoise
        match.entry2_score = entry2_score  # type: ignore[assignment]  # nullable IntField, typed int by Tortoise
        match.forfeit = forfeit
        match.state = BracketMatchState.COMPLETE
        await save_fields(match, 'winner', 'entry1_score', 'entry2_score', 'forfeit', 'state')

        details = {
            'bracket_id': bracket.id,
            'match_id': match.id,
            'winner_entry_id': winner_entry_id,
            'loser_entry_id': loser_id,
            'entry1_score': entry1_score,
            'entry2_score': entry2_score,
            'forfeit': forfeit,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_MATCH_COMPLETED, details,
            EventType.BRACKET_MATCH_COMPLETED,
        )

        await self._advance_after_result(bracket, match, winner_entry, loser_entry)
        await self._maybe_complete_stage(bracket, actor)
        await self.cancel_remaining_games(
            actor, match.id, 'result recorded for the match',
        )
        return require_found(await self.repository.get_match(match_id), "Match")

    async def _advance_after_result(
        self,
        bracket: Bracket,
        match: BracketMatch,
        winner_entry: BracketEntry,
        loser_entry: BracketEntry,
    ) -> None:
        """Push a completed match's winner/loser through its progression pointers."""
        is_elim = bracket.format in self._ELIM_FORMATS

        reset = await self._gf1_reset(match)
        if reset is not None:
            winner_from_wb = winner_entry.id == match.entry1_id
            if winner_from_wb:
                await self._clear_match_slots(reset)
                await self._set_entry_status(
                    loser_entry, BracketEntryStatus.ELIMINATED
                )
            else:
                await self._set_entry_status(winner_entry, BracketEntryStatus.ACTIVE)
                await self._set_entry_status(loser_entry, BracketEntryStatus.ACTIVE)
                self._place(reset, match.winner_to_slot, winner_entry)
                self._place(reset, match.loser_to_slot, loser_entry)
                was_open = reset.state == BracketMatchState.OPEN
                reset.state = BracketMatchState.OPEN
                await save_fields(reset, 'entry1', 'entry2', 'state')
                if not was_open:
                    await self.notify_matchup_ready(reset)
            return

        if is_elim:
            await self._set_entry_status(winner_entry, BracketEntryStatus.ACTIVE)

        winner_target = await self._propagate_winner(match, winner_entry)

        loser_target: Optional[BracketMatch] = None
        if match.loser_to_id is not None:
            loser_target = await self.repository.get_match(match.loser_to_id)
            if loser_target is not None:
                self._place(loser_target, match.loser_to_slot, loser_entry)
                await save_fields(loser_target, self._slot_field(match.loser_to_slot))
                if is_elim:
                    await self._set_entry_status(
                        loser_entry, BracketEntryStatus.ACTIVE
                    )
        elif is_elim:
            await self._set_entry_status(loser_entry, BracketEntryStatus.ELIMINATED)

        settled: set = set()
        for target in (winner_target, loser_target):
            if target is None or target.id in settled:
                continue
            settled.add(target.id)
            fresh = await self.repository.get_match(target.id)
            if fresh is not None:
                await self._settle_match(fresh)

    async def _gf1_reset(self, match: BracketMatch) -> Optional[BracketMatch]:
        """Return the reset match iff ``match`` is a double-elim Grand Final 1.

        Structural, since there is no ``is_reset`` column: GF1 is the match whose
        ``winner_to`` and ``loser_to`` point at the *same* target, and that shared
        target is terminal (no onward pointers) — i.e. the reset. The degenerate
        P=2 winners final also points both pointers at the grand final, but that
        target still has onward pointers (into the reset), so it is not mistaken
        for GF1.
        """
        if match.winner_to_id is None or match.winner_to_id != match.loser_to_id:
            return None
        target = await self.repository.get_match(match.winner_to_id)
        if target is None:
            return None
        if target.winner_to_id is None and target.loser_to_id is None:
            return target
        return None

    @staticmethod
    def _place(match: BracketMatch, slot: Optional[int], entry: BracketEntry) -> None:
        if slot == 1:
            match.entry1 = entry
        else:
            match.entry2 = entry

    @staticmethod
    def _slot_field(slot: Optional[int]) -> str:
        return 'entry1' if slot == 1 else 'entry2'

    async def _clear_match_slots(self, match: BracketMatch) -> None:
        """Empty a match's entry slots and reset it to PENDING (idempotent)."""
        match.entry1 = None
        match.entry2 = None
        match.winner = None
        match.state = BracketMatchState.PENDING
        await save_fields(match, 'entry1', 'entry2', 'winner', 'state')

    @staticmethod
    async def _set_entry_status(
        entry: BracketEntry, status: BracketEntryStatus
    ) -> None:
        if entry.status != status:
            entry.status = status
            await save_fields(entry, 'status')

    async def _settle_match(self, match: BracketMatch, _depth: int = 0) -> None:
        """Set a match's state after one of its slots changed.

        Both slots filled and not already COMPLETE → OPEN. Exactly one slot
        filled whose empty counterpart has no still-pending feeder → walkover:
        complete it with the present entry and recurse into the target. Otherwise
        → PENDING.
        """
        if _depth > 1000 or match.state == BracketMatchState.COMPLETE:
            return

        was_open = match.state == BracketMatchState.OPEN
        has1 = match.entry1_id is not None
        has2 = match.entry2_id is not None

        if has1 and has2:
            match.state = BracketMatchState.OPEN
            await save_fields(match, 'state')
            # Keyed on the transition, not the state: a matchup whose slots fill apart settles twice but invites once.
            if not was_open:
                await self.notify_matchup_ready(match)
            return

        if has1 != has2:
            empty_slot = 2 if has1 else 1
            if await self._slot_can_fill(match.id, empty_slot):
                match.state = BracketMatchState.PENDING
                await save_fields(match, 'state')
                return
            present_id = match.entry1_id if has1 else match.entry2_id
            present = await self.repository.get_entry(present_id)
            if present is None:
                match.state = BracketMatchState.PENDING
                await save_fields(match, 'state')
                return
            target = await self._propagate_winner(match, present)
            if target is not None:
                fresh = await self.repository.get_match(target.id)
                if fresh is not None:
                    await self._settle_match(fresh, _depth + 1)
            return

        match.state = BracketMatchState.PENDING
        await save_fields(match, 'state')

    async def _slot_can_fill(
        self, match_id: int, slot: int, _seen: Optional[set] = None
    ) -> bool:
        """Whether an entry can still arrive in ``slot`` of ``match_id``.

        A slot is fed by not-yet-COMPLETE upstream matches (via their ``winner_to``
        or ``loser_to`` pointers). A winner-feeder can deliver an entry only if it
        will ever have at least one entrant; a loser-feeder only if it will ever be
        a real two-entrant contest. This recurses so that a dead phantom (an
        elimination match both of whose own feeders were byes) is correctly seen
        as unable to fill the slot — turning the downstream lone entrant into a
        walkover instead of an eternally-pending match.
        """
        if _seen is None:
            _seen = set()
        key = (match_id, slot)
        if key in _seen:
            return False
        _seen.add(key)

        for feeder in await self.repository.winner_feeders(match_id, slot):
            if await self._can_produce_winner(feeder, _seen):
                return True
        for feeder in await self.repository.loser_feeders(match_id, slot):
            if await self._can_produce_loser(feeder, _seen):
                return True
        return False

    async def _can_produce_winner(
        self, match: BracketMatch, _seen: set
    ) -> bool:
        if match.state == BracketMatchState.COMPLETE:
            return match.winner_id is not None
        if match.entry1_id is not None or match.entry2_id is not None:
            return True
        return await self._slot_can_fill(
            match.id, 1, _seen
        ) or await self._slot_can_fill(match.id, 2, _seen)

    async def _can_produce_loser(
        self, match: BracketMatch, _seen: set
    ) -> bool:
        if match.state == BracketMatchState.COMPLETE:
            return match.entry1_id is not None and match.entry2_id is not None
        slot1_live = match.entry1_id is not None or await self._slot_can_fill(
            match.id, 1, _seen
        )
        slot2_live = match.entry2_id is not None or await self._slot_can_fill(
            match.id, 2, _seen
        )
        return slot1_live and slot2_live

    async def override_result(
        self,
        actor: Optional[User],
        match_id: int,
        winner_entry_id: int,
        *,
        entry1_score: Optional[int] = None,
        entry2_score: Optional[int] = None,
        forfeit: bool = False,
    ) -> BracketMatch:
        """Staff correction of an already-COMPLETE match's winner (and scores).

        Allowed only while no match downstream of this one is already COMPLETE
        (raise otherwise — staff must undo the downstream results first). The new
        winner/loser are re-pushed into the still-uncompleted downstream slots,
        and the reported scores / forfeit flag are re-stated (the same
        score-vs-winner rule applies).
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        match = require_found(await self.repository.get_match(match_id), "Match")
        bracket = await self._require_bracket(match.bracket_id)
        if match.state != BracketMatchState.COMPLETE:
            raise ValueError("Can only override a COMPLETE match")
        if winner_entry_id not in (match.entry1_id, match.entry2_id):
            raise ValueError("Winner must be one of the match's two entries")
        self._validate_scores(
            match, winner_entry_id, entry1_score, entry2_score, forfeit
        )

        downstream_ids = {
            tid for tid in (match.winner_to_id, match.loser_to_id) if tid is not None
        }
        for tid in downstream_ids:
            target = await self.repository.get_match(tid)
            if target is not None and target.state == BracketMatchState.COMPLETE:
                raise ValueError(
                    "A downstream match is already complete; undo it before "
                    "overriding this result"
                )

        winner_entry = require_found(
            await self.repository.get_entry(winner_entry_id), "Entry"
        )
        loser_id = (
            match.entry1_id if winner_entry_id == match.entry2_id else match.entry2_id
        )
        loser_entry = require_found(
            await self.repository.get_entry(loser_id), "Entry"
        )

        match.winner = winner_entry
        match.entry1_score = entry1_score  # type: ignore[assignment]  # nullable IntField, typed int by Tortoise
        match.entry2_score = entry2_score  # type: ignore[assignment]  # nullable IntField, typed int by Tortoise
        match.forfeit = forfeit
        await save_fields(match, 'winner', 'entry1_score', 'entry2_score', 'forfeit')

        await self._advance_after_result(bracket, match, winner_entry, loser_entry)
        if bracket.state == BracketState.COMPLETE:
            # Already COMPLETE, so _maybe_complete_stage would early-return and leave ranks stale: re-finalize directly.
            await self._finalize_stage(bracket, actor, tie_breaks=None)
        else:
            await self._maybe_complete_stage(bracket, actor)

        details = {
            'bracket_id': bracket.id,
            'match_id': match.id,
            'winner_entry_id': winner_entry_id,
            'loser_entry_id': loser_id,
            'entry1_score': entry1_score,
            'entry2_score': entry2_score,
            'forfeit': forfeit,
            'override': True,
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_MATCH_COMPLETED, details,
            EventType.BRACKET_MATCH_COMPLETED,
        )
        await self.cancel_remaining_games(
            actor, match.id, 'result overridden by staff',
        )
        return require_found(await self.repository.get_match(match_id), "Match")

    async def get_open_matches(self, bracket_id: int) -> List[BracketMatch]:
        """All OPEN (playable) matches of a bracket."""
        return await self.repository.list_open_matches(bracket_id)
