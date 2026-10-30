"""Unit tests for BracketService (B6) — authoring, roster, enrollment, start.

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). The
generate-then-persist ``start_bracket`` is checked against the pure engine's
``generate(N, {})`` graph so the persistence layer stays faithful to the engine.
"""

import pytest

from application.errors import NotFoundError
from application.services.bracket_engines import get_bracket_engine
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
from tests.factories import join_community

pytestmark = pytest.mark.usefixtures("db")


async def _staff() -> User:
    user = await User.create(discord_id=1234, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _plain_user() -> User:
    return await User.create(discord_id=9999, username='nobody')


async def _tournament(name: str = 'Cup') -> Tournament:
    return await Tournament.create(name=name)


@pytest.fixture
def service() -> BracketService:
    return BracketService()


class TestCreateBracket:
    async def test_happy_path(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(
            actor, t.id, 'Main', BracketFormat.SINGLE_ELIM,
        )
        assert bracket.id is not None
        assert bracket.state == BracketState.DRAFT
        assert bracket.format == BracketFormat.SINGLE_ELIM
        assert bracket.stage_order == 0

    async def test_format_as_string(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(actor, t.id, 'Main', 'double_elim')
        assert bracket.format == BracketFormat.DOUBLE_ELIM

    async def test_config_validation_rejects_unknown_key(self, service):
        actor = await _staff()
        t = await _tournament()
        with pytest.raises(ValueError):
            await service.create_bracket(
                actor, t.id, 'Main', BracketFormat.SWISS, config={'bogus_key': 1},
            )

    async def test_set_round_metadata_any_state(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(
            actor, t.id, 'Main', BracketFormat.SINGLE_ELIM,
        )
        updated = await service.set_round_metadata(
            actor, bracket.id,
            {'1': {'best_of': 3, 'scheduled_at': '2026-08-01T18:00:00+00:00'}},
        )
        assert updated.config['rounds']['1']['best_of'] == 3

    async def test_set_round_metadata_validates(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(
            actor, t.id, 'Main', BracketFormat.SINGLE_ELIM,
        )
        with pytest.raises(ValueError, match='odd'):
            await service.set_round_metadata(actor, bracket.id, {'1': {'best_of': 2}})

    async def test_set_round_metadata_staff_only(self, service):
        staff = await _staff()
        nobody = await _plain_user()
        t = await _tournament()
        bracket = await service.create_bracket(
            staff, t.id, 'Main', BracketFormat.SINGLE_ELIM,
        )
        with pytest.raises(PermissionError):
            await service.set_round_metadata(nobody, bracket.id, {'1': {'best_of': 3}})

    async def test_duplicate_stage_order_rejected(self, service):
        actor = await _staff()
        t = await _tournament()
        await service.create_bracket(actor, t.id, 'Stage 1', BracketFormat.ROUND_ROBIN, stage_order=0)
        with pytest.raises(ValueError, match='stage_order'):
            await service.create_bracket(actor, t.id, 'Stage 1b', BracketFormat.SINGLE_ELIM, stage_order=0)

    async def test_non_staff_rejected(self, service):
        actor = await _plain_user()
        t = await _tournament()
        with pytest.raises(PermissionError):
            await service.create_bracket(actor, t.id, 'Main', BracketFormat.SINGLE_ELIM)

    async def test_missing_tournament_rejected(self, service):
        actor = await _staff()
        with pytest.raises(ValueError):
            await service.create_bracket(actor, 999999, 'Main', BracketFormat.SINGLE_ELIM)

    async def test_empty_name_rejected(self, service):
        actor = await _staff()
        t = await _tournament()
        with pytest.raises(ValueError, match='name is required'):
            await service.create_bracket(actor, t.id, '   ', BracketFormat.SINGLE_ELIM)


class TestRoster:
    async def test_add_entrant_placeholder(self, service):
        actor = await _staff()
        t = await _tournament()
        entrant = await service.add_entrant(actor, t.id, 'Placeholder')
        assert entrant.display_name == 'Placeholder'
        assert entrant.user_id is None

    async def test_add_entrant_linked(self, service):
        actor = await _staff()
        t = await _tournament()
        linked = await _plain_user()
        await join_community(linked)
        entrant = await service.add_entrant(actor, t.id, 'Nobody', user_id=linked.id)
        assert entrant.user_id == linked.id

    async def test_add_entrant_unknown_user_is_not_found(self, service):
        """An unknown id must 404, not reach the FK as a raw IntegrityError —
        that surfaced as a 500 over the API and doubled as a user-id oracle."""
        actor = await _staff()
        t = await _tournament()
        with pytest.raises(NotFoundError):
            await service.add_entrant(actor, t.id, 'Ghost', user_id=999_999)

    async def test_drop_entrant(self, service):
        actor = await _staff()
        t = await _tournament()
        entrant = await service.add_entrant(actor, t.id, 'Dropme')
        dropped = await service.drop_entrant(actor, entrant.id)
        assert dropped.status.value == 'dropped'

    async def test_enroll(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(actor, t.id, 'Main', BracketFormat.SINGLE_ELIM)
        entrant = await service.add_entrant(actor, t.id, 'E1')
        entry = await service.enroll(actor, bracket.id, entrant.id, seed=1)
        assert entry.seed == 1
        assert entry.bracket_id == bracket.id
        assert entry.entrant_id == entrant.id

    async def test_enroll_duplicate_rejected(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(actor, t.id, 'Main', BracketFormat.SINGLE_ELIM)
        entrant = await service.add_entrant(actor, t.id, 'E1')
        await service.enroll(actor, bracket.id, entrant.id)
        with pytest.raises(ValueError, match='already enrolled'):
            await service.enroll(actor, bracket.id, entrant.id)

    async def test_enroll_wrong_tournament_rejected(self, service):
        actor = await _staff()
        t1 = await _tournament('T1')
        t2 = await _tournament('T2')
        bracket = await service.create_bracket(actor, t1.id, 'Main', BracketFormat.SINGLE_ELIM)
        entrant = await service.add_entrant(actor, t2.id, 'Foreign')
        with pytest.raises(ValueError, match='different tournament'):
            await service.enroll(actor, bracket.id, entrant.id)


async def _seeded_bracket(service, fmt, n, config=None, stage_order=0):
    """Create a bracket with ``n`` enrolled entrants seeded 1..n. Returns (actor, bracket)."""
    actor = await _staff()
    t = await _tournament()
    bracket = await service.create_bracket(actor, t.id, 'Main', fmt, stage_order=stage_order, config=config)
    for i in range(1, n + 1):
        entrant = await service.add_entrant(actor, t.id, f'P{i}')
        await service.enroll(actor, bracket.id, entrant.id, seed=i)
    return actor, bracket


class TestStartSingleElim:
    @pytest.mark.parametrize('n', [2, 3, 4, 5, 8])
    async def test_matches_mirror_engine_graph(self, service, n):
        actor, bracket = await _seeded_bracket(service, BracketFormat.SINGLE_ELIM, n)
        await service.start_bracket(actor, bracket.id)

        graph = get_bracket_engine('single_elim')().generate(n, {})
        matches = await service.list_matches(bracket.id)

        assert len(matches) == len(graph)
        assert {m.round for m in matches} == {gm.round for gm in graph}

        by_coord = {(m.round, m.position): m for m in matches}
        for gm in graph:
            m = by_coord[(gm.round, gm.position)]
            if gm.is_bye:
                assert m.state == BracketMatchState.COMPLETE
                real_seed = gm.entry1_seed if gm.entry1_seed is not None else gm.entry2_seed
                winner_entry = await service.repository.get_entry(m.winner_id)
                assert winner_entry.seed == real_seed

        for gm in graph:
            if gm.round == 1 and not gm.is_bye and gm.entry1_seed and gm.entry2_seed:
                m = by_coord[(1, gm.position)]
                assert m.state == BracketMatchState.OPEN

        for gm in graph:
            if gm.winner_to is None:
                continue
            m = by_coord[(gm.round, gm.position)]
            target = by_coord[(gm.winner_to.round, gm.winner_to.position)]
            assert m.winner_to_id == target.id
            assert m.winner_to_slot == gm.winner_to.slot

        assert (await service.get_bracket(bracket.id)).state == BracketState.ACTIVE


class TestStartDoubleElim:
    @pytest.mark.parametrize('n', [4, 5])
    async def test_losers_grand_final_reset_rows(self, service, n):
        actor, bracket = await _seeded_bracket(service, BracketFormat.DOUBLE_ELIM, n)
        await service.start_bracket(actor, bracket.id)

        graph = get_bracket_engine('double_elim')().generate(n, {})
        matches = await service.list_matches(bracket.id)
        assert len(matches) == len(graph)

        rounds = {m.round for m in matches}
        assert any(r < 0 for r in rounds)
        gf_reset = sorted(r for r in rounds if r > 0)[-2:]
        assert len(gf_reset) == 2


class TestStartRoundRobin:
    async def test_group_of_four_all_open(self, service):
        actor, bracket = await _seeded_bracket(
            service, BracketFormat.ROUND_ROBIN, 4, config={'group_count': 1},
        )
        await service.start_bracket(actor, bracket.id)

        matches = await service.list_matches(bracket.id)
        assert len(matches) == 6
        assert all(m.state == BracketMatchState.OPEN for m in matches)


class TestStartSwiss:
    async def test_round_one_pairings(self, service):
        actor, bracket = await _seeded_bracket(service, BracketFormat.SWISS, 5)
        await service.start_bracket(actor, bracket.id)

        matches = await service.list_matches(bracket.id)
        assert all(m.round == 1 for m in matches)
        byes = [m for m in matches if m.state == BracketMatchState.COMPLETE]
        opens = [m for m in matches if m.state == BracketMatchState.OPEN]
        assert len(byes) == 1
        assert len(opens) == 2
        assert byes[0].winner_id is not None
        assert byes[0].entry2_id is None


class TestStartGuards:
    async def test_rejects_non_draft(self, service):
        actor, bracket = await _seeded_bracket(service, BracketFormat.SINGLE_ELIM, 4)
        await service.start_bracket(actor, bracket.id)
        with pytest.raises(ValueError, match='DRAFT'):
            await service.start_bracket(actor, bracket.id)

    async def test_rejects_too_few_entries(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(actor, t.id, 'Main', BracketFormat.SINGLE_ELIM)
        entrant = await service.add_entrant(actor, t.id, 'Solo')
        await service.enroll(actor, bracket.id, entrant.id)
        with pytest.raises(ValueError, match='at least 2'):
            await service.start_bracket(actor, bracket.id)

    async def test_missing_seeds_auto_assigned(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(actor, t.id, 'Main', BracketFormat.SINGLE_ELIM)
        for i in range(4):
            entrant = await service.add_entrant(actor, t.id, f'P{i}')
            await service.enroll(actor, bracket.id, entrant.id)
        await service.start_bracket(actor, bracket.id)
        seeds = sorted(e.seed for e in await service.list_entries(bracket.id))
        assert seeds == [1, 2, 3, 4]


class TestSeedValidation:
    async def _drafted(self, service):
        actor = await _staff()
        t = await _tournament()
        bracket = await service.create_bracket(
            actor, t.id, 'Main', BracketFormat.SINGLE_ELIM,
        )
        return actor, t, bracket

    async def test_set_seeds_rejects_duplicate(self, service):
        actor, t, bracket = await self._drafted(service)
        ids = []
        for i in range(3):
            entrant = await service.add_entrant(actor, t.id, f'P{i}')
            entry = await service.enroll(actor, bracket.id, entrant.id)
            ids.append(entry.id)
        with pytest.raises(ValueError, match='more than one'):
            await service.set_seeds(
                actor, bracket.id, {ids[0]: 1, ids[1]: 1, ids[2]: 2},
            )

    async def test_set_seeds_rejects_below_one(self, service):
        actor, t, bracket = await self._drafted(service)
        entrant = await service.add_entrant(actor, t.id, 'P')
        entry = await service.enroll(actor, bracket.id, entrant.id)
        with pytest.raises(ValueError, match='1 or greater'):
            await service.set_seeds(actor, bracket.id, {entry.id: 0})

    async def test_set_seeds_accepts_valid_full_seeding(self, service):
        actor, t, bracket = await self._drafted(service)
        ids = []
        for i in range(4):
            entrant = await service.add_entrant(actor, t.id, f'P{i}')
            entry = await service.enroll(actor, bracket.id, entrant.id)
            ids.append(entry.id)
        await service.set_seeds(
            actor, bracket.id, {ids[0]: 4, ids[1]: 3, ids[2]: 2, ids[3]: 1},
        )
        await service.start_bracket(actor, bracket.id)
        seeds = sorted(e.seed for e in await service.list_entries(bracket.id))
        assert seeds == [1, 2, 3, 4]
        assert (await service.get_bracket(bracket.id)).state == BracketState.ACTIVE

    async def test_start_rejects_duplicate_manual_seeds(self, service):
        actor, t, bracket = await self._drafted(service)
        for i, seed in enumerate([1, 2, 2, 4]):
            entrant = await service.add_entrant(actor, t.id, f'P{i}')
            await service.enroll(actor, bracket.id, entrant.id, seed=seed)
        with pytest.raises(ValueError, match='contiguous'):
            await service.start_bracket(actor, bracket.id)

    async def test_start_rejects_out_of_range_manual_seed(self, service):
        actor, t, bracket = await self._drafted(service)
        for i, seed in enumerate([1, 2, 3, 9]):
            entrant = await service.add_entrant(actor, t.id, f'P{i}')
            await service.enroll(actor, bracket.id, entrant.id, seed=seed)
        with pytest.raises(ValueError, match='contiguous'):
            await service.start_bracket(actor, bracket.id)


class TestListAllBrackets:
    async def test_orders_by_tournament_then_stage_with_tournament_loaded(self, service):
        actor = await _staff()
        alpha = await _tournament('Alpha')
        beta = await _tournament('Beta')
        await service.create_bracket(actor, beta.id, 'Main', BracketFormat.SWISS)
        await service.create_bracket(actor, alpha.id, 'Groups', BracketFormat.ROUND_ROBIN)
        await service.create_bracket(
            actor, alpha.id, 'Playoff', BracketFormat.SINGLE_ELIM, stage_order=1,
        )

        rows = await service.list_all_brackets()

        assert [b.name for b in rows] == ['Groups', 'Playoff', 'Main']
        assert [b.tournament.name for b in rows] == ['Alpha', 'Alpha', 'Beta']

    async def test_inactive_tournaments_sort_last(self, service):
        actor = await _staff()
        finished = await Tournament.create(name='Alpha', is_active=False)
        running = await Tournament.create(name='Zulu', is_active=True)
        await service.create_bracket(actor, finished.id, 'Old', BracketFormat.SWISS)
        await service.create_bracket(actor, running.id, 'New', BracketFormat.SWISS)

        assert [b.name for b in await service.list_all_brackets()] == ['New', 'Old']

    async def test_empty_when_no_brackets(self, service):
        assert await service.list_all_brackets() == []


async def test_bracket_not_visible_across_tenants(service, two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        actor = await _staff()
        t = await _tournament('A Cup')
        bracket = await service.create_bracket(actor, t.id, 'Main', BracketFormat.SINGLE_ELIM)

    with tenant_scope(tenant_b.id):
        assert await service.get_bracket(bracket.id) is None
        assert await service.list_brackets(t.id) == []
        assert await service.list_all_brackets() == []
