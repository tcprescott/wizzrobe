"""B8 tests — multi-stage chaining (advancement between bracket stages).

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). Each test
builds a real two-stage tournament and drives it end to end through the service:
create stage 0, roster + enroll, start, play to completion, then ``advance_stage``
to seed stage 1 from stage 0's ``final_rank``, then start and play stage 1.

The two headline shapes are group → single-elim (top-N per group, snake seeding
with same-source-group round-1 avoidance) and Swiss → top cut (top-N overall).
"""

import pytest

from application.services.bracket_engines.base import standard_seeding
from application.services.bracket_service import BracketService
from application.tenant_context import tenant_scope
from models import (
    BracketFormat,
    BracketMatchState,
    BracketState,
    Role,
    Tournament,
    User,
    UserRole,
)

pytestmark = pytest.mark.usefixtures("db")


async def _staff(discord_id: int = 1234, username: str = 'staff') -> User:
    user = await User.create(discord_id=discord_id, username=username)
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _plain_user() -> User:
    return await User.create(discord_id=9999, username='nobody')


@pytest.fixture
def service() -> BracketService:
    return BracketService()


def lower_seed_wins(match, seed_of):
    return (
        match.entry1_id
        if seed_of[match.entry1_id] <= seed_of[match.entry2_id]
        else match.entry2_id
    )


async def _play_to_completion(service, actor, bracket, seed_of, finalize=True):
    """Report the lower seed as winner for every OPEN match until exhausted.

    Elimination stages auto-complete; round-robin / Swiss run out of playable
    matches while still ACTIVE and are finalized with ``complete_stage`` when
    ``finalize`` (the Swiss top-cut test finalizes itself with explicit ranks).
    """
    for _ in range(10000):
        current = await service.get_bracket(bracket.id)
        if current.state == BracketState.COMPLETE:
            break
        open_matches = await service.get_open_matches(bracket.id)
        if not open_matches:
            break
        for m in open_matches:
            fresh = await service.repository.get_match(m.id)
            if fresh is None or fresh.state != BracketMatchState.OPEN:
                continue
            await service.report_result(actor, m.id, lower_seed_wins(fresh, seed_of))

    if finalize and (await service.get_bracket(bracket.id)).state != BracketState.COMPLETE:
        await service.complete_stage(actor, bracket.id)


async def _build_stage(service, actor, tournament_id, fmt, n, *, stage_order, config=None):
    """Create a stage and (for stage 0) enroll a fresh 1..n seeded roster."""
    bracket = await service.create_bracket(
        actor, tournament_id, f'Stage {stage_order}', fmt,
        stage_order=stage_order, config=config,
    )
    if stage_order == 0:
        for i in range(1, n + 1):
            entrant = await service.add_entrant(actor, tournament_id, f'P{i}')
            await service.enroll(actor, bracket.id, entrant.id, seed=i)
    return bracket


async def _seed_of(service, bracket_id):
    entries = await service.list_entries(bracket_id)
    return {e.id: e.seed for e in entries}


class TestGroupToSingleElim:
    async def _run(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        # Stage 0: two round-robin groups of four (seeds 1,4,5,8 | 2,3,6,7).
        stage0 = await _build_stage(
            service, actor, t.id, BracketFormat.ROUND_ROBIN, 8,
            stage_order=0, config={'group_count': 2},
        )
        await service.start_bracket(actor, stage0.id)
        await _play_to_completion(service, actor, stage0, await _seed_of(service, stage0.id))

        # Stage 1: single elimination, top 2 per group advance, snake seeded.
        stage1 = await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0,
            stage_order=1,
            config={'advancement': {'count': 2, 'per_group': True, 'seeding': 'snake'}},
        )
        return actor, t, stage0, stage1

    async def test_top_two_per_group_advance(self, service):
        actor, t, stage0, stage1 = await self._run(service)

        s0_entries = await service.list_entries(stage0.id)
        by_group = {}
        for e in s0_entries:
            by_group.setdefault(e.group_number, []).append(e)
        expected_entrants = set()
        for group_entries in by_group.values():
            top2 = sorted(group_entries, key=lambda e: e.final_rank)[:2]
            assert [e.final_rank for e in top2] == [1, 2]
            expected_entrants.update(e.entrant_id for e in top2)

        await service.advance_stage(actor, t.id, 0)
        s1_entries = await service.list_entries(stage1.id)

        assert len(s1_entries) == 4
        assert {e.entrant_id for e in s1_entries} == expected_entrants

    async def test_snake_avoids_same_group_round_one(self, service):
        actor, t, stage0, stage1 = await self._run(service)

        s0_entries = await service.list_entries(stage0.id)
        source_group = {e.entrant_id: e.group_number for e in s0_entries}

        await service.advance_stage(actor, t.id, 0)
        await service.start_bracket(actor, stage1.id)

        s1_entries = await service.list_entries(stage1.id)
        entrant_of = {e.id: e.entrant_id for e in s1_entries}

        round1 = [
            m for m in await service.list_matches(stage1.id)
            if m.round == 1 and m.entry1_id is not None and m.entry2_id is not None
        ]
        assert round1, "expected contested round-1 matches"
        for m in round1:
            g1 = source_group[entrant_of[m.entry1_id]]
            g2 = source_group[entrant_of[m.entry2_id]]
            assert g1 != g2, "two same-source-group entrants met in round 1"

    async def test_group_winners_take_the_top_seeds(self, service):
        actor, t, stage0, stage1 = await self._run(service)

        s0_entries = await service.list_entries(stage0.id)
        winners = {
            e.entrant_id for e in s0_entries if e.final_rank == 1
        }

        await service.advance_stage(actor, t.id, 0)
        s1_entries = await service.list_entries(stage1.id)
        seed_of_entrant = {e.entrant_id: e.seed for e in s1_entries}

        # Group winners occupy the two lowest seeds; standard seeding then places
        # them on opposite halves of the bracket.
        winner_seeds = sorted(seed_of_entrant[eid] for eid in winners)
        assert winner_seeds == [1, 2]

    async def test_preview_matches_advancement(self, service):
        actor, t, stage0, stage1 = await self._run(service)
        preview = await service.get_advancing_preview(t.id, 0)
        preview_entrants = [e.entrant_id for e in preview]

        await service.advance_stage(actor, t.id, 0)
        s1_entrants = {e.entrant_id for e in await service.list_entries(stage1.id)}
        assert set(preview_entrants) == s1_entrants
        assert len(preview) == 4

    async def test_cross_stage_identity(self, service):
        actor, t, stage0, stage1 = await self._run(service)
        await service.advance_stage(actor, t.id, 0)

        s0_by_entrant = {e.entrant_id: e for e in await service.list_entries(stage0.id)}
        for s1 in await service.list_entries(stage1.id):
            assert s1.entrant_id in s0_by_entrant
            assert s1.id != s0_by_entrant[s1.entrant_id].id

    async def test_stage1_plays_to_a_champion(self, service):
        actor, t, stage0, stage1 = await self._run(service)
        await service.advance_stage(actor, t.id, 0)
        await service.start_bracket(actor, stage1.id)
        await _play_to_completion(service, actor, stage1, await _seed_of(service, stage1.id))

        final = await service.get_bracket(stage1.id)
        assert final.state == BracketState.COMPLETE
        entries = await service.list_entries(stage1.id)
        assert sum(1 for e in entries if e.final_rank == 1) == 1


class TestSnakeSameGroupAvoidance:
    @pytest.mark.parametrize('group_count', [2, 3, 4, 5])
    @pytest.mark.parametrize('count', [2, 3])
    async def test_round_one_never_pairs_same_source_group(
        self, service, group_count, count
    ):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        per_group = count + 1
        n = group_count * per_group
        stage0 = await _build_stage(
            service, actor, t.id, BracketFormat.ROUND_ROBIN, n,
            stage_order=0, config={'group_count': group_count},
        )
        await service.start_bracket(actor, stage0.id)
        await _play_to_completion(
            service, actor, stage0, await _seed_of(service, stage0.id)
        )

        stage1 = await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {
                'count': count, 'per_group': True, 'seeding': 'snake',
            }},
        )

        s0_entries = await service.list_entries(stage0.id)
        source_group = {e.entrant_id: e.group_number for e in s0_entries}

        await service.advance_stage(actor, t.id, 0)
        await service.start_bracket(actor, stage1.id)

        s1_entries = await service.list_entries(stage1.id)
        assert len(s1_entries) == group_count * count
        entrant_of = {e.id: e.entrant_id for e in s1_entries}

        round1 = [
            m for m in await service.list_matches(stage1.id)
            if m.round == 1 and m.entry1_id is not None and m.entry2_id is not None
        ]
        assert round1, "expected contested round-1 matches"
        for m in round1:
            g1 = source_group[entrant_of[m.entry1_id]]
            g2 = source_group[entrant_of[m.entry2_id]]
            assert g1 != g2, (
                f"same-source-group round-1 pairing "
                f"(group_count={group_count}, count={count}): {g1} vs {g2}"
            )


class TestSwissToTopCut:
    async def _completed_swiss(self, service, actor, t, n, ranks):
        """Run an n-player Swiss stage 0 and finalize with explicit ranks 1..n."""
        stage0 = await _build_stage(
            service, actor, t.id, BracketFormat.SWISS, n, stage_order=0,
        )
        await service.start_bracket(actor, stage0.id)
        seed_of = await _seed_of(service, stage0.id)
        await _play_to_completion(service, actor, stage0, seed_of, finalize=False)

        # Finalize with an explicit, tie-free ranking so "top N" is unambiguous:
        # entrant with stage-0 seed k gets final_rank = ranks[k].
        entries = await service.list_entries(stage0.id)
        tie_breaks = {e.id: ranks[e.seed] for e in entries}
        if (await service.get_bracket(stage0.id)).state != BracketState.COMPLETE:
            await service.complete_stage(actor, stage0.id, tie_breaks=tie_breaks)
        return stage0

    @pytest.mark.parametrize('cut', [4, 8])
    async def test_top_n_overall_advance_in_order(self, service, cut):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        n = 8
        ranks = {k: k for k in range(1, n + 1)}
        stage0 = await self._completed_swiss(service, actor, t, n, ranks)

        stage1 = await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {'count': cut, 'per_group': False}},
        )
        await service.advance_stage(actor, t.id, 0)

        s0_by_entrant = {e.entrant_id: e for e in await service.list_entries(stage0.id)}
        s1_entries = await service.list_entries(stage1.id)

        assert len(s1_entries) == cut
        advancing_ranks = sorted(
            s0_by_entrant[e.entrant_id].final_rank for e in s1_entries
        )
        assert advancing_ranks == list(range(1, cut + 1))

        for e in s1_entries:
            assert e.seed == s0_by_entrant[e.entrant_id].final_rank

    async def test_top_cut_bracket_completes(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        ranks = {k: k for k in range(1, 9)}
        await self._completed_swiss(service, actor, t, 8, ranks)
        stage1 = await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {'count': 4, 'per_group': False}},
        )
        await service.advance_stage(actor, t.id, 0)
        await service.start_bracket(actor, stage1.id)
        await _play_to_completion(service, actor, stage1, await _seed_of(service, stage1.id))

        # Standard seeding pairs the cut as 1v4 and 2v3 in round 1.
        assert standard_seeding(4) == [1, 4, 2, 3]
        final = await service.get_bracket(stage1.id)
        assert final.state == BracketState.COMPLETE


class TestAdvanceGuards:
    async def _round_robin_pair(self, service, actor, t, *, complete):
        stage0 = await _build_stage(
            service, actor, t.id, BracketFormat.ROUND_ROBIN, 8, stage_order=0,
            config={'group_count': 2},
        )
        await service.start_bracket(actor, stage0.id)
        if complete:
            await _play_to_completion(service, actor, stage0, await _seed_of(service, stage0.id))
        return stage0

    async def test_rejects_incomplete_source(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._round_robin_pair(service, actor, t, complete=False)
        await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {'count': 2, 'per_group': True}},
        )
        with pytest.raises(ValueError, match='predecessor'):
            await service.advance_stage(actor, t.id, 0)

    async def test_rejects_missing_next_stage(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._round_robin_pair(service, actor, t, complete=True)
        with pytest.raises(ValueError, match='no next stage'):
            await service.advance_stage(actor, t.id, 0)

    async def test_rejects_next_stage_without_advancement(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._round_robin_pair(service, actor, t, complete=True)
        await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
        )
        with pytest.raises(ValueError, match='advancement'):
            await service.advance_stage(actor, t.id, 0)

    async def test_rejects_too_few_ranked(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._round_robin_pair(service, actor, t, complete=True)
        # Each group has 4; asking for 5 per group cannot be satisfied.
        await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {'count': 5, 'per_group': True}},
        )
        with pytest.raises(ValueError, match='Not enough'):
            await service.advance_stage(actor, t.id, 0)

    async def test_rejects_double_advance(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._round_robin_pair(service, actor, t, complete=True)
        await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {'count': 2, 'per_group': True}},
        )
        await service.advance_stage(actor, t.id, 0)
        with pytest.raises(ValueError, match='already been seeded'):
            await service.advance_stage(actor, t.id, 0)

    async def test_stage1_cannot_start_before_source_complete(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._round_robin_pair(service, actor, t, complete=False)
        stage1 = await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 8, stage_order=1,
        )
        # Even with its own entries, a later stage may not start until stage 0 is
        # complete.
        with pytest.raises(ValueError, match='previous stage'):
            await service.start_bracket(actor, stage1.id)


class TestAuthAndTenant:
    async def _completed_group_stage(self, service, actor, t):
        stage0 = await _build_stage(
            service, actor, t.id, BracketFormat.ROUND_ROBIN, 8, stage_order=0,
            config={'group_count': 2},
        )
        await service.start_bracket(actor, stage0.id)
        await _play_to_completion(service, actor, stage0, await _seed_of(service, stage0.id))
        await _build_stage(
            service, actor, t.id, BracketFormat.SINGLE_ELIM, 0, stage_order=1,
            config={'advancement': {'count': 2, 'per_group': True}},
        )
        return stage0

    async def test_non_staff_cannot_advance(self, service):
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        await self._completed_group_stage(service, actor, t)
        nobody = await _plain_user()
        with pytest.raises(PermissionError):
            await service.advance_stage(nobody, t.id, 0)

    async def test_advance_scoped_to_tenant(self, service, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            actor = await _staff()
            t = await Tournament.create(name='Cup')
            await self._completed_group_stage(service, actor, t)
            tournament_id = t.id

        with tenant_scope(tenant_b.id):
            b_actor = await _staff(discord_id=4321, username='b-staff')
            with pytest.raises(ValueError):
                await service.advance_stage(b_actor, tournament_id, 0)
