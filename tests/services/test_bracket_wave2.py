"""Unit tests for the bracket surface added by the UX review's waves 2 and 3.

Covers the pieces the admin surface now depends on: the read-only draw preview,
un-enrolling and retiring entries, cancelling a stage, the format-aware config
validation, the stage-wide ``default_best_of``, and the advancement plan the
advance confirmation is built from.
"""

import pytest

from application.services.bracket_config import validate_bracket_config
from application.services.bracket_service import BracketService
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


async def _staff() -> User:
    user = await User.create(discord_id=4321, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


@pytest.fixture
def service() -> BracketService:
    return BracketService()


async def _field(service, fmt=BracketFormat.SINGLE_ELIM, n=4, config=None, stage_order=0):
    """A DRAFT stage with ``n`` enrolled entrants seeded 1..n."""
    actor = await _staff()
    tournament = await Tournament.create(name='Cup')
    bracket = await service.create_bracket(
        actor, tournament.id, 'Main', fmt, stage_order=stage_order, config=config,
    )
    entries = []
    for i in range(1, n + 1):
        entrant = await service.add_entrant(actor, tournament.id, f'P{i}')
        entries.append(await service.enroll(actor, bracket.id, entrant.id, seed=i))
    return actor, tournament, bracket, entries


class TestDrawPreview:
    async def test_preview_matches_what_start_persists(self, service):
        """The rehearsal has to be the performance, or it is worse than nothing."""
        actor, _t, bracket, _entries = await _field(service, n=8)
        preview = await service.preview_draw(bracket.id)
        projected = sorted(
            (m.round, m.position, m.entry1_id, m.entry2_id) for m in preview.matches
        )

        await service.start_bracket(actor, bracket.id)
        persisted = sorted(
            (m.round, m.position, m.entry1_id, m.entry2_id)
            for m in await service.list_matches(bracket.id)
        )
        assert projected == persisted

    async def test_preview_writes_nothing(self, service):
        _actor, _t, bracket, _entries = await _field(service, n=4)
        await service.preview_draw(bracket.id)
        assert await service.list_matches(bracket.id) == []
        assert (await service.get_bracket(bracket.id)).state == BracketState.DRAFT

    async def test_preview_resolves_byes_like_start(self, service):
        actor, _t, bracket, _entries = await _field(service, n=5)
        preview = await service.preview_draw(bracket.id)
        preview_byes = sum(
            1 for m in preview.matches if m.state == BracketMatchState.COMPLETE
        )
        await service.start_bracket(actor, bracket.id)
        started_byes = sum(
            1 for m in await service.list_matches(bracket.id)
            if m.state == BracketMatchState.COMPLETE
        )
        assert preview_byes == started_byes > 0

    async def test_preview_reports_a_broken_seeding_instead_of_raising(self, service):
        actor, _t, bracket, entries = await _field(service, n=4)
        # Written raw: the service would refuse this duplicate.
        entry = entries[0]
        entry.seed = 2
        await entry.save()

        preview = await service.preview_draw(bracket.id)
        assert preview.error is not None
        assert 'contiguous' in preview.error
        assert preview.matches == []
        with pytest.raises(ValueError, match='contiguous'):
            await service.start_bracket(actor, bracket.id)

    async def test_preview_of_an_empty_stage_says_what_is_missing(self, service):
        _actor, _t, bracket, _entries = await _field(service, n=0)
        preview = await service.preview_draw(bracket.id)
        assert preview.error is None
        assert any('at least 2' in line for line in preview.summary)

    async def test_swiss_preview_pairs_round_one(self, service):
        _actor, _t, bracket, _entries = await _field(
            service, fmt=BracketFormat.SWISS, n=6, config={'swiss_rounds': 3},
        )
        preview = await service.preview_draw(bracket.id)
        assert len(preview.matches) == 3
        assert any('3 rounds' in line for line in preview.summary)


class TestUnenrollAndRetire:
    async def test_unenroll_removes_a_draft_entry(self, service):
        actor, _t, bracket, entries = await _field(service, n=4)
        await service.unenroll(actor, entries[0].id)
        assert len(await service.list_entries(bracket.id)) == 3

    async def test_unenroll_rejected_once_started(self, service):
        actor, _t, bracket, entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        with pytest.raises(ValueError, match='DRAFT'):
            await service.unenroll(actor, entries[0].id)

    async def test_retire_marks_dropped_and_is_idempotent_by_refusing(self, service):
        actor, _t, bracket, entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        retired = await service.retire_entry(actor, entries[0].id)
        assert retired.status == BracketEntryStatus.DROPPED
        with pytest.raises(ValueError, match='already retired'):
            await service.retire_entry(actor, entries[0].id)

    async def test_retire_does_not_disturb_played_results(self, service):
        actor, _t, bracket, entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        first = next(
            m for m in await service.list_matches(bracket.id)
            if m.state == BracketMatchState.OPEN
        )
        await service.report_result(actor, first.id, first.entry1_id)
        await service.retire_entry(actor, entries[0].id)
        replayed = await service.repository.get_match(first.id)
        assert replayed.winner_id == first.entry1_id
        assert replayed.state == BracketMatchState.COMPLETE

    async def test_role_less_actor_refused(self, service):
        _actor, _t, _bracket, entries = await _field(service, n=4)
        plain = await User.create(discord_id=777, username='plain')
        with pytest.raises(PermissionError):
            await service.unenroll(plain, entries[0].id)
        with pytest.raises(PermissionError):
            await service.retire_entry(plain, entries[0].id)


class TestCancelStage:
    async def test_cancel_an_active_stage(self, service):
        actor, _t, bracket, _entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        cancelled = await service.cancel_stage(actor, bracket.id)
        assert cancelled.state == BracketState.CANCELLED

    async def test_cancelled_stage_writes_no_ranking(self, service):
        actor, _t, bracket, _entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        await service.cancel_stage(actor, bracket.id)
        assert all(
            e.final_rank is None for e in await service.list_entries(bracket.id)
        )

    async def test_cannot_cancel_a_completed_stage(self, service):
        actor, _t, bracket, _entries = await _field(service, n=2)
        await service.start_bracket(actor, bracket.id)
        final = (await service.list_matches(bracket.id))[0]
        await service.report_result(actor, final.id, final.entry1_id)
        assert (await service.get_bracket(bracket.id)).state == BracketState.COMPLETE
        with pytest.raises(ValueError, match='completed stage'):
            await service.cancel_stage(actor, bracket.id)

    async def test_a_cancelled_stage_can_be_deleted(self, service):
        """The slot has to come back — otherwise the abandoned stage blocks the
        stage_order a replacement needs."""
        actor, _t, bracket, _entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        await service.cancel_stage(actor, bracket.id)
        await service.delete_bracket(actor, bracket.id)
        assert await service.get_bracket(bracket.id) is None

    async def test_an_active_stage_still_cannot_be_deleted(self, service):
        actor, _t, bracket, _entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        with pytest.raises(ValueError, match='DRAFT or CANCELLED'):
            await service.delete_bracket(actor, bracket.id)


class TestConfigValidation:
    async def test_swiss_rounds_rejected_off_swiss(self, service):
        actor = await _staff()
        tournament = await Tournament.create(name='Cup')
        with pytest.raises(ValueError, match='swiss_rounds'):
            await service.create_bracket(
                actor, tournament.id, 'Main', BracketFormat.SINGLE_ELIM,
                config={'swiss_rounds': 5},
            )

    async def test_group_count_rejected_off_round_robin(self, service):
        actor = await _staff()
        tournament = await Tournament.create(name='Cup')
        with pytest.raises(ValueError, match='group_count'):
            await service.create_bracket(
                actor, tournament.id, 'Main', BracketFormat.SWISS,
                config={'group_count': 2},
            )

    async def test_advancement_rejected_on_the_first_stage(self, service):
        actor = await _staff()
        tournament = await Tournament.create(name='Cup')
        with pytest.raises(ValueError, match='drawn INTO'):
            await service.create_bracket(
                actor, tournament.id, 'Main', BracketFormat.SINGLE_ELIM,
                config={'advancement': {'count': 4}},
            )

    async def test_a_stages_own_stored_blob_round_trips(self, service):
        """The normalizer injects every non-None default, so an edit re-submits
        keys nobody typed — those must not read as intent."""
        actor, _t, bracket, _entries = await _field(
            service, fmt=BracketFormat.SWISS, n=4, config={'swiss_rounds': 3},
        )
        stored = dict(bracket.config or {})
        assert 'grand_final_reset' in stored
        updated = await service.update_bracket(actor, bracket.id, config=stored)
        assert updated.config['swiss_rounds'] == 3

    async def test_an_explicit_non_default_still_gets_caught(self):
        with pytest.raises(ValueError, match='grand_final_reset'):
            validate_bracket_config(
                {'grand_final_reset': False}, fmt=BracketFormat.SINGLE_ELIM,
            )

    async def test_default_best_of_must_be_odd(self):
        with pytest.raises(ValueError, match='odd'):
            validate_bracket_config({'default_best_of': 2})


class TestDefaultBestOf:
    async def test_resolution_order_is_match_then_round_then_stage(self, service):
        actor, _t, bracket, _entries = await _field(
            service, n=4, config={'default_best_of': 3, 'rounds': {'2': {'best_of': 5}}},
        )
        await service.start_bracket(actor, bracket.id)
        matches = await service.list_matches(bracket.id)
        round1 = next(m for m in matches if m.round == 1)
        round2 = next(m for m in matches if m.round == 2)
        bracket = await service.get_bracket(bracket.id)

        assert service.resolve_best_of(bracket, round1) == 3
        assert service.resolve_best_of(bracket, round2) == 5
        await service.set_best_of(actor, round1.id, 7)
        round1 = await service.repository.get_match(round1.id)
        assert service.resolve_best_of(bracket, round1) == 7

    async def test_falls_back_to_one_when_nothing_is_set(self, service):
        actor, _t, bracket, _entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        bracket = await service.get_bracket(bracket.id)
        match = (await service.list_matches(bracket.id))[0]
        assert service.resolve_best_of(bracket, match) == 1


class TestAdvancementPlan:
    async def _two_stage(self, service):
        actor = await _staff()
        tournament = await Tournament.create(name='Cup')
        groups = await service.create_bracket(
            actor, tournament.id, 'Groups', BracketFormat.ROUND_ROBIN, stage_order=0,
        )
        playoff = await service.create_bracket(
            actor, tournament.id, 'Playoff', BracketFormat.SINGLE_ELIM, stage_order=1,
            config={'advancement': {'count': 2}},
        )
        for i in range(1, 5):
            entrant = await service.add_entrant(actor, tournament.id, f'P{i}')
            await service.enroll(actor, groups.id, entrant.id, seed=i)
        await service.start_bracket(actor, groups.id)
        for match in await service.list_matches(groups.id):
            if match.state == BracketMatchState.OPEN:
                await service.report_result(actor, match.id, match.entry1_id)
        await service.complete_stage(actor, groups.id)
        return actor, tournament, groups, playoff

    async def test_plan_carries_the_resulting_seeds(self, service):
        _actor, tournament, _groups, playoff = await self._two_stage(service)
        plan = await service.advancement_plan(tournament.id, 0)
        assert plan.destination.id == playoff.id
        assert [seed for _entry, seed, _group in plan.rows] == [1, 2]
        assert all(entry.final_rank is not None for entry, _s, _g in plan.rows)

    async def test_plan_reports_an_already_seeded_chain_instead_of_raising(self, service):
        actor, tournament, _groups, _playoff = await self._two_stage(service)
        await service.advance_stage(actor, tournament.id, 0)
        plan = await service.advancement_plan(tournament.id, 0)
        assert plan.already_seeded is True
        assert plan.rows == []
        with pytest.raises(ValueError, match='already been seeded'):
            await service.advance_stage(actor, tournament.id, 0)

    async def test_missing_next_stage_is_diagnosed_before_the_source_state(self, service):
        """The likelier mistake, and the one the operator can act on."""
        actor, tournament, bracket, _entries = await _field(service, n=4)
        await service.start_bracket(actor, bracket.id)
        with pytest.raises(ValueError, match='no next stage'):
            await service.advancement_plan(tournament.id, 0)

    async def test_missing_rule_names_the_stage_that_needs_it(self, service):
        actor = await _staff()
        tournament = await Tournament.create(name='Cup')
        await service.create_bracket(
            actor, tournament.id, 'Groups', BracketFormat.SINGLE_ELIM, stage_order=0,
        )
        await service.create_bracket(
            actor, tournament.id, 'Playoff', BracketFormat.SINGLE_ELIM, stage_order=1,
        )
        with pytest.raises(ValueError, match='Playoff'):
            await service.advancement_plan(tournament.id, 0)
