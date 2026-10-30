"""B7 tests — result recording, advancement, and stage completion.

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). The core
correctness gate is a play-through harness: start a bracket, then loop —
fetch OPEN matches, report a deterministic winner for each — advancing until an
elimination bracket auto-completes or a Swiss/round-robin stage runs out of
playable matches and is finalized with ``complete_stage``. The harness is run
with three deterministic winner pickers across a spread of formats and entrant
counts.
"""

import math

import pytest

from application.services.bracket_service import BracketService
from application.tenant_context import tenant_scope
from models import (
    BracketEntryStatus,
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


async def _started_bracket(service, fmt, n, config=None):
    """Create, seed 1..n, and start a bracket. Returns (actor, bracket)."""
    actor = await _staff()
    t = await Tournament.create(name='Cup')
    bracket = await service.create_bracket(actor, t.id, 'Main', fmt, config=config)
    for i in range(1, n + 1):
        entrant = await service.add_entrant(actor, t.id, f'P{i}')
        await service.enroll(actor, bracket.id, entrant.id, seed=i)
    await service.start_bracket(actor, bracket.id)
    return actor, bracket


def lower_seed_wins(match, seed_of):
    return (
        match.entry1_id
        if seed_of[match.entry1_id] <= seed_of[match.entry2_id]
        else match.entry2_id
    )


def higher_seed_wins(match, seed_of):
    return (
        match.entry1_id
        if seed_of[match.entry1_id] >= seed_of[match.entry2_id]
        else match.entry2_id
    )


def pseudo_random(match, seed_of):
    return (
        match.entry1_id
        if (match.round + match.position + match.id) % 2 == 0
        else match.entry2_id
    )


PICKERS = [lower_seed_wins, higher_seed_wins, pseudo_random]


async def _play_through(service, actor, bracket, picker):
    """Report a winner for every OPEN match until the bracket resolves.

    Elimination brackets auto-complete; Swiss/round-robin run out of playable
    matches while still ACTIVE and are finalized with ``complete_stage``.
    """
    entries = await service.list_entries(bracket.id)
    seed_of = {e.id: e.seed for e in entries}

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
            await service.report_result(actor, m.id, picker(fresh, seed_of))

    current = await service.get_bracket(bracket.id)
    if current.state != BracketState.COMPLETE:
        await service.complete_stage(actor, bracket.id)
    return await service.get_bracket(bracket.id)


def _assert_permutation(ranks, n):
    assert sorted(ranks) == list(range(1, n + 1)), ranks


class TestSingleElim:
    @pytest.mark.parametrize('n', [2, 3, 4, 5, 8, 16])
    @pytest.mark.parametrize('picker', PICKERS)
    async def test_play_through(self, service, n, picker):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, n)
        final = await _play_through(service, actor, bracket, picker)

        assert final.state == BracketState.COMPLETE
        entries = await service.list_entries(bracket.id)
        ranks = [e.final_rank for e in entries]
        assert all(r is not None for r in ranks)
        _assert_permutation(ranks, n)
        assert sum(1 for e in entries if e.final_rank == 1) == 1

        assert await service.get_open_matches(bracket.id) == []

    @pytest.mark.parametrize('n', [2, 3, 4, 5, 8, 16])
    async def test_lower_seed_champion(self, service, n):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, n)
        await _play_through(service, actor, bracket, lower_seed_wins)
        entries = await service.list_entries(bracket.id)
        champion = next(e for e in entries if e.final_rank == 1)
        assert champion.seed == 1

    @pytest.mark.parametrize('n', [3, 5])
    async def test_byes_autoadvance_and_complete(self, service, n):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, n)
        final = await _play_through(service, actor, bracket, lower_seed_wins)
        assert final.state == BracketState.COMPLETE
        entries = await service.list_entries(bracket.id)
        champion = next(e for e in entries if e.final_rank == 1)
        assert champion.seed == 1


class TestDoubleElim:
    @pytest.mark.parametrize('n', [2, 4, 5, 8])
    @pytest.mark.parametrize('picker', PICKERS)
    async def test_play_through(self, service, n, picker):
        actor, bracket = await _started_bracket(service, BracketFormat.DOUBLE_ELIM, n)
        final = await _play_through(service, actor, bracket, picker)

        assert final.state == BracketState.COMPLETE
        entries = await service.list_entries(bracket.id)
        ranks = [e.final_rank for e in entries]
        _assert_permutation(ranks, n)

        champion = next(e for e in entries if e.final_rank == 1)
        for e in entries:
            if e.id == champion.id:
                assert e.status == BracketEntryStatus.ACTIVE
            else:
                assert e.status == BracketEntryStatus.ELIMINATED

        matches = await service.list_matches(bracket.id)
        terminals = [
            m for m in matches if m.winner_to_id is None and m.loser_to_id is None
        ]
        reset = terminals[0]
        gf1 = next(
            m for m in matches
            if m.winner_to_id == reset.id and m.loser_to_id == reset.id
        )
        lb_won_gf1 = (
            gf1.state == BracketMatchState.COMPLETE
            and gf1.winner_id == gf1.entry2_id
        )
        reset_played = reset.state == BracketMatchState.COMPLETE
        assert reset_played == lb_won_gf1

    @pytest.mark.parametrize('n', [2, 4, 5, 8])
    async def test_lower_seed_champion_no_reset(self, service, n):
        actor, bracket = await _started_bracket(service, BracketFormat.DOUBLE_ELIM, n)
        await _play_through(service, actor, bracket, lower_seed_wins)
        entries = await service.list_entries(bracket.id)
        champion = next(e for e in entries if e.final_rank == 1)
        assert champion.seed == 1

        matches = await service.list_matches(bracket.id)
        terminals = [
            m for m in matches if m.winner_to_id is None and m.loser_to_id is None
        ]
        assert terminals[0].state != BracketMatchState.COMPLETE

    async def test_grand_final_reset_disabled_lb_wins_is_champion(self, service):
        actor, bracket = await _started_bracket(
            service, BracketFormat.DOUBLE_ELIM, 4,
            config={'grand_final_reset': False},
        )
        matches = await service.list_matches(bracket.id)
        gf_to_reset = [
            m for m in matches
            if m.winner_to_id is not None and m.winner_to_id == m.loser_to_id
        ]
        assert gf_to_reset == []
        terminals = [
            m for m in matches if m.winner_to_id is None and m.loser_to_id is None
        ]
        assert len(terminals) == 1
        gf_id = terminals[0].id

        entries = await service.list_entries(bracket.id)
        seed_of = {e.id: e.seed for e in entries}
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
                if fresh.id == gf_id:
                    await service.report_result(actor, fresh.id, fresh.entry2_id)
                else:
                    await service.report_result(
                        actor, fresh.id, lower_seed_wins(fresh, seed_of)
                    )

        final = await service.get_bracket(bracket.id)
        assert final.state == BracketState.COMPLETE
        gf = await service.repository.get_match(gf_id)
        assert gf.winner_id == gf.entry2_id
        champion = next(
            e for e in await service.list_entries(bracket.id) if e.final_rank == 1
        )
        assert champion.id == gf.entry2_id


class TestRoundRobin:
    @pytest.mark.parametrize('n', [4, 6])
    @pytest.mark.parametrize('group_count', [1, 2])
    @pytest.mark.parametrize('picker', PICKERS)
    async def test_play_through(self, service, n, group_count, picker):
        actor, bracket = await _started_bracket(
            service, BracketFormat.ROUND_ROBIN, n, config={'group_count': group_count},
        )
        final = await _play_through(service, actor, bracket, picker)
        assert final.state == BracketState.COMPLETE

        entries = await service.list_entries(bracket.id)
        assert all(e.final_rank is not None for e in entries)
        assert all(e.group_number is not None for e in entries)

        by_group = {}
        for e in entries:
            by_group.setdefault(e.group_number, []).append(e)
        assert len(by_group) == group_count
        for group_entries in by_group.values():
            ranks = [e.final_rank for e in group_entries]
            size = len(group_entries)
            assert min(ranks) == 1
            assert max(ranks) <= size
            assert all(1 <= r <= size for r in ranks)

    async def test_lower_seed_tops_single_group(self, service):
        actor, bracket = await _started_bracket(
            service, BracketFormat.ROUND_ROBIN, 4, config={'group_count': 1},
        )
        await _play_through(service, actor, bracket, lower_seed_wins)
        entries = await service.list_entries(bracket.id)
        top = next(e for e in entries if e.final_rank == 1)
        assert top.seed == 1


class TestSwiss:
    @pytest.mark.parametrize('n', [4, 5, 8])
    @pytest.mark.parametrize('picker', PICKERS)
    async def test_play_through(self, service, n, picker):
        actor, bracket = await _started_bracket(service, BracketFormat.SWISS, n)
        final = await _play_through(service, actor, bracket, picker)
        assert final.state == BracketState.COMPLETE

        matches = await service.list_matches(bracket.id)
        target = max(1, math.ceil(math.log2(n)))
        assert max(m.round for m in matches) == target

        pairs = [
            frozenset({m.entry1_id, m.entry2_id})
            for m in matches
            if m.entry1_id is not None and m.entry2_id is not None
        ]
        assert len(pairs) == len(set(pairs)), "a pairing repeated across rounds"

        entries = await service.list_entries(bracket.id)
        assert all(e.final_rank is not None for e in entries)
        assert min(e.final_rank for e in entries) == 1

    async def test_target_rounds_counts_active_entries_only(self, service):
        # Dropped entries don't count: 4 active entrants target 2 rounds, not 3.
        actor = await _staff()
        t = await Tournament.create(name='Cup')
        bracket = await service.create_bracket(actor, t.id, 'Main', BracketFormat.SWISS)
        entries = []
        for i in range(1, 9):
            entrant = await service.add_entrant(actor, t.id, f'P{i}')
            entries.append(await service.enroll(actor, bracket.id, entrant.id, seed=i))
        for entry in entries[4:]:
            entry.status = BracketEntryStatus.DROPPED
            await entry.save()

        await service.start_bracket(actor, bracket.id)
        await _play_through(service, actor, bracket, lower_seed_wins)

        matches = await service.list_matches(bracket.id)
        assert max(m.round for m in matches) == math.ceil(math.log2(4))

    async def test_complete_stage_writes_standings(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SWISS, 4)
        # Swiss isn't auto-completed, so finalize explicitly.
        entries = await service.list_entries(bracket.id)
        seed_of = {e.id: e.seed for e in entries}
        while True:
            open_matches = await service.get_open_matches(bracket.id)
            if not open_matches:
                break
            for m in open_matches:
                fresh = await service.repository.get_match(m.id)
                if fresh and fresh.state == BracketMatchState.OPEN:
                    await service.report_result(actor, m.id, lower_seed_wins(fresh, seed_of))

        assert (await service.get_bracket(bracket.id)).state == BracketState.ACTIVE
        result = await service.complete_stage(actor, bracket.id)
        assert result.state == BracketState.COMPLETE
        entries = await service.list_entries(bracket.id)
        assert all(e.final_rank is not None for e in entries)


class TestOverride:
    async def _match_at(self, service, bracket_id, rnd, pos):
        matches = await service.list_matches(bracket_id)
        return next(m for m in matches if m.round == rnd and m.position == pos)

    async def _entry_for_seed(self, service, bracket_id, seed):
        entries = await service.list_entries(bracket_id)
        return next(e for e in entries if e.seed == seed)

    async def test_override_before_downstream_completes(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m1 = await self._match_at(service, bracket.id, 1, 1)
        seed1 = await self._entry_for_seed(service, bracket.id, 1)
        await service.report_result(actor, m1.id, seed1.id)

        loser_seed = 4 if seed1.seed == 1 else 1
        other = await self._entry_for_seed(service, bracket.id, loser_seed)
        overridden = await service.override_result(actor, m1.id, other.id)
        assert overridden.winner_id == other.id

        final = await self._match_at(service, bracket.id, 2, 1)
        assert final.entry1_id == other.id

    async def test_override_rejected_after_downstream_complete(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        entries = await service.list_entries(bracket.id)
        seed_of = {e.id: e.seed for e in entries}

        m1 = await self._match_at(service, bracket.id, 1, 1)
        m2 = await self._match_at(service, bracket.id, 1, 2)
        await service.report_result(actor, m1.id, lower_seed_wins(
            await service.repository.get_match(m1.id), seed_of))
        await service.report_result(actor, m2.id, lower_seed_wins(
            await service.repository.get_match(m2.id), seed_of))
        final = await self._match_at(service, bracket.id, 2, 1)
        await service.report_result(actor, final.id, lower_seed_wins(
            await service.repository.get_match(final.id), seed_of))

        with pytest.raises(ValueError, match='downstream'):
            m1_winner = (await service.repository.get_match(m1.id)).winner_id
            await service.override_result(actor, m1.id, m1_winner)

    async def test_override_requires_complete_match(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m1 = await self._match_at(service, bracket.id, 1, 1)
        entry = await self._entry_for_seed(service, bracket.id, 1)
        with pytest.raises(ValueError, match='COMPLETE'):
            await service.override_result(actor, m1.id, entry.id)

    async def test_override_final_reranks_completed_bracket(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 2)
        final = next(
            m for m in await service.list_matches(bracket.id) if m.round == 1
        )
        e1 = await service.repository.get_entry(final.entry1_id)
        e2 = await service.repository.get_entry(final.entry2_id)

        await service.report_result(actor, final.id, e1.id)
        assert (await service.get_bracket(bracket.id)).state == BracketState.COMPLETE
        champ = next(
            e for e in await service.list_entries(bracket.id) if e.id == e1.id
        )
        assert champ.final_rank == 1

        await service.override_result(actor, final.id, e2.id)
        assert (await service.get_bracket(bracket.id)).state == BracketState.COMPLETE

        entries = {e.id: e for e in await service.list_entries(bracket.id)}
        assert entries[e2.id].final_rank == 1
        assert entries[e1.id].final_rank != 1
        assert entries[e1.id].final_rank == 2


class TestReportGuards:
    async def test_rejects_non_open_match(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        matches = await service.list_matches(bracket.id)
        final = next(m for m in matches if m.round == 2)
        entries = await service.list_entries(bracket.id)
        with pytest.raises(ValueError, match='OPEN'):
            await service.report_result(actor, final.id, entries[0].id)

    async def test_rejects_foreign_winner(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m1 = next(
            m for m in await service.list_matches(bracket.id)
            if m.state == BracketMatchState.OPEN
        )
        entries = await service.list_entries(bracket.id)
        foreign = next(
            e for e in entries if e.id not in (m1.entry1_id, m1.entry2_id)
        )
        with pytest.raises(ValueError, match='one of the'):
            await service.report_result(actor, m1.id, foreign.id)


class TestScoresAndForfeit:
    async def _open_match(self, service, bracket):
        return next(
            m for m in await service.list_matches(bracket.id)
            if m.state == BracketMatchState.OPEN
        )

    async def test_scores_persist_alongside_winner(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        result = await service.report_result(
            actor, m.id, m.entry1_id, entry1_score=2, entry2_score=1,
        )
        assert result.entry1_score == 2
        assert result.entry2_score == 1
        assert result.forfeit is False
        assert result.winner_id == m.entry1_id

    async def test_win_only_report_leaves_scores_null(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        result = await service.report_result(actor, m.id, m.entry1_id)
        assert result.entry1_score is None
        assert result.entry2_score is None
        assert result.forfeit is False

    async def test_winner_must_have_higher_score(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        with pytest.raises(ValueError, match='strictly-higher'):
            await service.report_result(
                actor, m.id, m.entry1_id, entry1_score=1, entry2_score=2,
            )

    async def test_equal_scores_rejected(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        with pytest.raises(ValueError, match='strictly-higher'):
            await service.report_result(
                actor, m.id, m.entry1_id, entry1_score=1, entry2_score=1,
            )

    async def test_forfeit_allows_lower_winner_score(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        result = await service.report_result(
            actor, m.id, m.entry1_id, entry1_score=0, entry2_score=0, forfeit=True,
        )
        assert result.forfeit is True
        assert result.winner_id == m.entry1_id

    async def test_forfeit_without_scores(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        result = await service.report_result(
            actor, m.id, m.entry1_id, forfeit=True,
        )
        assert result.forfeit is True
        assert result.entry1_score is None

    async def test_partial_scores_rejected(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        with pytest.raises(ValueError, match='both scores or neither'):
            await service.report_result(actor, m.id, m.entry1_id, entry1_score=2)

    async def test_negative_scores_rejected(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        with pytest.raises(ValueError, match='negative'):
            await service.report_result(
                actor, m.id, m.entry1_id, entry1_score=1, entry2_score=-1,
            )

    async def test_override_restates_scores(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m = await self._open_match(service, bracket)
        await service.report_result(
            actor, m.id, m.entry1_id, entry1_score=2, entry2_score=0,
        )
        overridden = await service.override_result(
            actor, m.id, m.entry2_id, entry1_score=1, entry2_score=2,
        )
        assert overridden.winner_id == m.entry2_id
        assert overridden.entry1_score == 1
        assert overridden.entry2_score == 2


class TestAuthAndTenant:
    async def test_non_staff_cannot_report(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        m1 = next(
            m for m in await service.list_matches(bracket.id)
            if m.state == BracketMatchState.OPEN
        )
        nobody = await _plain_user()
        with pytest.raises(PermissionError):
            await service.report_result(nobody, m1.id, m1.entry1_id)

    async def test_non_staff_cannot_complete_stage(self, service):
        actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        nobody = await _plain_user()
        with pytest.raises(PermissionError):
            await service.complete_stage(nobody, bracket.id)

    async def test_report_scoped_to_tenant(self, service, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            actor, bracket = await _started_bracket(service, BracketFormat.SINGLE_ELIM, 4)
            m1 = next(
                m for m in await service.list_matches(bracket.id)
                if m.state == BracketMatchState.OPEN
            )
            winner_id = m1.entry1_id
            match_id = m1.id

        with tenant_scope(tenant_b.id):
            b_actor = await _staff(discord_id=4321, username='b-staff')
            with pytest.raises(ValueError):
                await service.report_result(b_actor, match_id, winner_id)
