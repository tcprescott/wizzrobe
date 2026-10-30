"""Bracket multi-stage chaining mixin: advance a completed stage into the next.

Split out of ``bracket_service.py`` as pure code motion (see that module's
docstring). ``MultiStageMixin`` is composed into :class:`BracketService`; its
methods reach siblings (including ``_require_tournament`` defined on the
composer), ``self.repository`` and ``self.audit_service`` through that composed
class.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from application.errors import require_found
from application.events import EventType
from application.services._bracket._composed import ComposedBracketService
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.bracket_config import AdvancementConfig
from application.services.bracket_engines.base import next_power_of_two
from models import (
    Bracket,
    BracketEntry,
    BracketEntryStatus,
    BracketFormat,
    BracketState,
    User,
)


@dataclass(frozen=True)
class AdvancementPlan:
    """The resolved advance: source, destination, rule, and the seeded field.

    ``rows`` are ``(entry, new seed, source group)`` for the entries that would
    be drawn — the same tuples :meth:`_seed_advancers` hands the writer, so the
    confirmation shows the seeding that will actually be applied rather than the
    rank order it came from (snake and preserve differ materially, and only this
    tells them apart). Empty when ``already_seeded``.
    """

    source: Bracket
    destination: Bracket
    advancement: AdvancementConfig
    rows: List[Tuple[BracketEntry, int, Optional[int]]]
    already_seeded: bool


class MultiStageMixin(ComposedBracketService):
    async def advance_stage(
        self, actor: Optional[User], tournament_id: int, from_stage_order: int
    ) -> Bracket:
        """Seed the stage after ``from_stage_order`` from that stage's final ranks.

        Staff-triggered — nothing chains silently. The source stage must be
        COMPLETE (its ``final_rank`` written); the next stage must exist, be
        DRAFT, and carry an ``advancement`` rule. The rule's ``count`` entrants
        (per source ``group_number`` when ``per_group``, else overall) are drawn
        by best ``final_rank``, skipping DROPPED entries, and enrolled into the
        next stage as fresh :class:`BracketEntry` rows pointing at the SAME
        :class:`BracketEntrant` — identity carries across stages. Seeds are laid
        out by the rule's seeding policy. The next stage is left DRAFT and seeded
        for staff to review and then :meth:`start_bracket`.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can manage brackets",
        )
        await self._require_tournament(tournament_id)

        source, next_stage, advancement, already_seeded = await self._advancement_context(
            tournament_id, from_stage_order
        )
        if already_seeded:
            raise ValueError("The next stage has already been seeded")

        advancers = await self._compute_advancers(source, advancement)
        next_is_grouped = next_stage.format == BracketFormat.ROUND_ROBIN

        for entry, seed, source_group in self._seed_advancers(advancers, advancement):
            await self.repository.create_entry(
                bracket_id=next_stage.id,
                entrant_id=entry.entrant_id,
                seed=seed,
                group_number=source_group if next_is_grouped else None,
                status=BracketEntryStatus.ACTIVE,
            )

        details = {
            'tournament_id': tournament_id,
            'from_stage_order': from_stage_order,
            'from_bracket_id': source.id,
            'to_bracket_id': next_stage.id,
            'advanced': len(advancers),
        }
        await self.audit_service.write_and_publish(
            actor, AuditActions.BRACKET_STAGE_ADVANCED, details,
            EventType.BRACKET_STAGE_ADVANCED,
        )
        return next_stage

    async def get_advancing_preview(
        self, tournament_id: int, from_stage_order: int
    ) -> List[BracketEntry]:
        """Source-stage entries that would advance, in advancement order.

        Read helper for the UI/tests — same selection :meth:`advance_stage`
        performs, without writing anything, and raising every guard it raises.
        A caller that also needs the resulting seeds, the destination stage or
        whether the draw has already been run wants :meth:`advancement_plan`.
        """
        source, _next_stage, advancement, _seeded = await self._advancement_context(
            tournament_id, from_stage_order
        )
        return await self._compute_advancers(source, advancement)

    async def advancement_plan(
        self, tournament_id: int, from_stage_order: int
    ) -> AdvancementPlan:
        """The whole projected advance: who, into what, at which seed.

        Everything :meth:`advance_stage` is about to do, resolved once so the
        confirmation can state it. ``already_seeded`` is reported rather than
        raised: a chain that has already been advanced is a fact worth showing,
        not an error to discover after clicking through a confident list.
        """
        source, destination, advancement, already_seeded = (
            await self._advancement_context(tournament_id, from_stage_order)
        )
        rows: List[Tuple[BracketEntry, int, Optional[int]]] = []
        if not already_seeded:
            advancers = await self._compute_advancers(source, advancement)
            rows = self._seed_advancers(advancers, advancement)
        return AdvancementPlan(
            source=source,
            destination=destination,
            advancement=advancement,
            rows=rows,
            already_seeded=already_seeded,
        )

    async def _advancement_context(
        self, tournament_id: int, from_stage_order: int
    ) -> Tuple[Bracket, Bracket, AdvancementConfig, bool]:
        """Resolve and validate (source, next stage, rule, already-seeded).

        Guard order is deliberate: the *destination* is checked before the
        source's state, because "there is no next stage" is both the likelier
        mistake and the one the operator can act on — the previous order answered
        a question about a stage they had not thought about yet.
        """
        source = await self.repository.get_stage(tournament_id, from_stage_order)
        source = require_found(source, "Stage")

        next_stage = await self.repository.get_stage(
            tournament_id, from_stage_order + 1
        )
        if next_stage is None:
            raise ValueError(
                "There is no next stage to advance into — create the stage that "
                "follows this one first"
            )
        if next_stage.state != BracketState.DRAFT:
            raise ValueError("The next stage must be DRAFT to seed it")

        raw = (next_stage.config or {}).get('advancement')
        if not raw:
            raise ValueError(
                f"“{next_stage.name}” has no advancement rule — edit that stage "
                "and set how many entrants it draws in from this one"
            )
        if source.state != BracketState.COMPLETE:
            raise ValueError("The predecessor stage must complete first")

        advancement = AdvancementConfig.model_validate(raw)
        already_seeded = bool(await self.repository.list_entries(next_stage.id))
        return source, next_stage, advancement, already_seeded

    async def _compute_advancers(
        self, source: Bracket, advancement: AdvancementConfig
    ) -> List[BracketEntry]:
        """Ranked, DROPPED-filtered advancers from ``source`` in seeding order.

        ``per_group`` returns the top ``count`` of each source group ordered
        tier-major (every group's winner, then every group's runner-up, …) so the
        seeding policy can spread them; otherwise the ``count`` best overall.
        """
        entries = [
            e
            for e in await self.repository.list_entries(source.id)
            if e.status != BracketEntryStatus.DROPPED and e.final_rank is not None
        ]

        if not advancement.per_group:
            ordered = sorted(entries, key=lambda e: e.final_rank)
            if len(ordered) < advancement.count:
                raise ValueError(
                    "Not enough ranked entrants to advance the requested count"
                )
            return ordered[: advancement.count]

        by_group: Dict[Optional[int], List[BracketEntry]] = defaultdict(list)
        for e in entries:
            by_group[e.group_number].append(e)

        groups = sorted(by_group, key=lambda g: (g is None, g))
        tiers: List[List[BracketEntry]] = []
        for group in groups:
            ranked = sorted(by_group[group], key=lambda e: e.final_rank)
            if len(ranked) < advancement.count:
                raise ValueError(
                    "Not enough ranked entrants in a group to advance the "
                    "requested count"
                )
            for tier, entry in enumerate(ranked[: advancement.count]):
                if tier >= len(tiers):
                    tiers.append([])
                tiers[tier].append(entry)

        return [entry for tier in tiers for entry in tier]

    def _seed_advancers(
        self, advancers: List[BracketEntry], advancement: AdvancementConfig
    ) -> List[Tuple[BracketEntry, int, Optional[int]]]:
        """Assign 1..K seeds to ``advancers`` per the seeding policy.

        ``advancers`` arrive tier-major (see :meth:`_compute_advancers`): for a
        per-group rule, the first ``G`` are the group winners, the next ``G`` the
        runners-up, and so on. ``preserve`` keeps that order as seeds 1..K.
        ``snake`` re-seeds them so no round-1 playoff match pairs two advancers
        from the same source group (see :meth:`_snake_order`).
        """
        if not advancement.per_group or advancement.seeding == 'preserve':
            order = advancers
        else:
            group_count = self._infer_group_count(advancers, advancement)
            order = self._snake_order(advancers, group_count, advancement.count)

        seeded: List[Tuple[BracketEntry, int, Optional[int]]] = []
        for index, entry in enumerate(order, start=1):
            seeded.append((entry, index, entry.group_number))
        return seeded

    @staticmethod
    def _infer_group_count(
        advancers: List[BracketEntry], advancement: AdvancementConfig
    ) -> int:
        tiers = max(1, advancement.count)
        return max(1, len(advancers) // tiers)

    @staticmethod
    def _snake_order(
        advancers: List[BracketEntry], group_count: int, tiers: int
    ) -> List[BracketEntry]:
        """Seed per-group advancers so no round-1 match is a same-group pairing.

        Guarantees that, for a single-elimination next stage, no opening-round
        playoff match pairs two advancers from the same source group whenever the
        group count ``G >= 2``. The single-elim engine's round-1 pairs are the
        seed reflections ``{s, size+1-s}`` (``size`` = next power of two ``>= K``);
        restricted to the real field those are the contested-block seeds
        ``(byes+1 .. K)`` paired by reversal, with the strongest ``byes`` seeds
        unopposed. Each contested pair is assigned two *distinct* source groups
        (balanced, so the field never starves), byes absorb the remainder, and a
        group's strongest seed takes its lowest tier — so group winners land in
        the top seed band by group rank. Degenerate ``G < 2`` falls back to the
        tier-major (preserve) order.
        """
        g = group_count
        t = tiers
        k = len(advancers)
        if g < 2 or t < 1 or k != g * t:
            return list(advancers)

        size = next_power_of_two(k)
        byes = size - k
        contested = k - byes

        group_of_seed: Dict[int, int] = {}
        remaining = [t] * g
        for c in range(contested // 2):
            s_strong = byes + 1 + c
            s_weak = k - c
            ranked = sorted(range(g), key=lambda gi: (-remaining[gi], gi))
            first, second = ranked[0], ranked[1]
            # Alternate orientation by pair index so the strongest seeds spread
            # across distinct groups (group winners land in the top band).
            if c % 2 == 0:
                group_of_seed[s_strong], group_of_seed[s_weak] = first, second
            else:
                group_of_seed[s_strong], group_of_seed[s_weak] = second, first
            remaining[group_of_seed[s_strong]] -= 1
            remaining[group_of_seed[s_weak]] -= 1
        for s in range(1, byes + 1):
            gi = max(range(g), key=lambda gi: (remaining[gi], -gi))
            group_of_seed[s] = gi
            remaining[gi] -= 1

        seeds_by_group: Dict[int, List[int]] = defaultdict(list)
        for s in range(1, k + 1):
            seeds_by_group[group_of_seed[s]].append(s)
        order: List[Optional[BracketEntry]] = [None] * k
        for gi in range(g):
            for tier, s in enumerate(sorted(seeds_by_group[gi])):
                order[s - 1] = advancers[tier * g + gi]
        return [entry for entry in order if entry is not None]
