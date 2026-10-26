"""One tournament's whole bracket, flattened the same way for native, start.gg and Challonge."""

import copy

import pytest

from application.errors import FeatureDisabledError, NotFoundError
from application.services import BracketOverviewService
from application.services.challonge_service import ChallongeService
from application.services.feature_flag_service import reset_flag_cache
from application.services.startgg_connection_service import StartggConnectionService
from application.services.startgg_service import StartggService
from application.utils.mocks.mock_startgg import mock_event_full
from models import (
    BracketMatchGame,
    BracketState,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    FeatureFlag,
    Match,
    MatchPlayers,
    Role,
    StartggSet,
    TenantFeatureFlag,
    Tournament,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_member, utc
from tests.services._bracket_overview_setup import native_elim as _native_elim

SLUG = 'tournament/test/event/singles'


async def _staff():
    user = await make_member(1, 'staff')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


async def _player():
    return await make_member(50, 'player')


async def _flag_off(flag: FeatureFlag) -> None:
    await TenantFeatureFlag.filter(tenant_id=DEFAULT_TEST_TENANT_ID, flag=flag.value).update(enabled=False)
    reset_flag_cache()


def _find(view, set_id):
    return next(s for p in view.phases for pool in p.pools for s in pool.sets if s.id == set_id)


# ---------------------------------------------------------------------------
# start.gg
# ---------------------------------------------------------------------------


@pytest.fixture
def startgg(monkeypatch):
    class Fake:
        async def get_event_full(self, slug):
            return copy.deepcopy(mock_event_full(slug))

    async def _client(self):
        return Fake()

    async def has_access(self):
        return True

    monkeypatch.setattr(StartggService, '_client', _client)
    monkeypatch.setattr(StartggConnectionService, 'has_access', has_access)


async def _startgg_tournament(staff):
    tournament = await Tournament.create(name='OoTR', is_active=True)
    await StartggService().link_event(tournament.id, SLUG, staff)
    return tournament


async def test_startgg_bracket_has_phases_pools_feeders_and_standings(db, startgg):
    staff = await _staff()
    tournament = await _startgg_tournament(staff)

    view = await BracketOverviewService().get_tournament_bracket(staff, tournament.id)

    assert view.source == 'startgg'
    assert [p.name for p in view.phases] == ['Group Prelim', 'Final Bracket']
    prelim, final = view.phases
    assert [pool.name for pool in prelim.pools] == ['1', '2']
    assert prelim.pools[0].sets_total == 6 and prelim.pools[0].sets_complete == 6
    assert len(prelim.pools[0].standings) == 4

    sets = {s.identifier: s for s in final.pools[0].sets}
    semi_a, semi_b, grand = sets['A'], sets['B'], sets['C']
    assert grand.state == 'pending'
    assert [s.placeholder for s in grand.slots] == ['Winner of A', 'Winner of B']
    assert [(s.from_set_id, s.from_outcome) for s in grand.slots] == [(semi_a.id, 'winner'), (semi_b.id, 'winner')]
    assert (semi_a.winner_to.set_id, semi_a.winner_to.slot) == (grand.id, 1)
    assert (semi_b.winner_to.set_id, semi_b.winner_to.slot) == (grand.id, 2)
    assert semi_a.loser_to is None
    assert len(view.entrants) == 8


async def test_startgg_booking_shows_on_its_set(db, startgg):
    staff = await _staff()
    tournament = await _startgg_tournament(staff)
    row = await StartggSet.get(tournament=tournament, set_id='8101')
    match = await Match.create(tournament=tournament, scheduled_at=utc(2026, 10, 23, 18))
    row.match = match
    await row.save(update_fields=['match_id'])

    view = await BracketOverviewService().get_tournament_bracket(staff, tournament.id)

    booked = _find(view, row.id)
    assert [(b.match_id, b.status) for b in booked.bookings] == [(match.id, 'scheduled')]
    assert booked.best_of == 1


async def test_accounts_are_for_staff_and_tournament_admins_only(db, startgg):
    staff = await _staff()
    tournament = await _startgg_tournament(staff)
    entrant = await tournament.startgg_entrants.all().first()
    entrant.user = staff
    await entrant.save(update_fields=['user_id'])

    as_staff = await BracketOverviewService().get_tournament_bracket(staff, tournament.id)
    as_player = await BracketOverviewService().get_tournament_bracket(await _player(), tournament.id)

    assert staff.id in {e.user_id for e in as_staff.entrants}
    assert {e.user_id for e in as_player.entrants} == {None}
    assert all(
        slot.user_id is None
        for p in as_player.phases for pool in p.pools for s in pool.sets for slot in s.slots
    )


async def test_filters_trim_sets_but_not_pool_counts(db, startgg):
    staff = await _staff()
    tournament = await _startgg_tournament(staff)
    service = BracketOverviewService()

    final_only = await service.get_tournament_bracket(staff, tournament.id, phase='final bracket')
    assert [p.name for p in final_only.phases] == ['Final Bracket']
    assert final_only.filtered

    pending = await service.get_tournament_bracket(staff, tournament.id, state='pending')
    assert [s.identifier for p in pending.phases for pool in p.pools for s in pool.sets] == ['C']
    assert pending.phases[0].pools[0].sets_total == 3

    structure = await service.get_tournament_bracket(staff, tournament.id, include_sets=False)
    assert all(not pool.sets for p in structure.phases for pool in p.pools)
    assert structure.phases[0].pools[0].standings

    with pytest.raises(ValueError):
        await service.get_tournament_bracket(staff, tournament.id, state='done')


async def test_startgg_feature_off_is_not_found(db, startgg):
    staff = await _staff()
    tournament = await _startgg_tournament(staff)
    await _flag_off(FeatureFlag.STARTGG)

    with pytest.raises(FeatureDisabledError):
        await BracketOverviewService().get_tournament_bracket(staff, tournament.id)


async def test_an_inactive_tournament_is_hidden_from_players(db, startgg):
    staff = await _staff()
    tournament = await _startgg_tournament(staff)
    await Tournament.filter(id=tournament.id).update(is_active=False)

    with pytest.raises(NotFoundError):
        await BracketOverviewService().get_tournament_bracket(await _player(), tournament.id)
    assert (await BracketOverviewService().get_tournament_bracket(staff, tournament.id)).phases


# ---------------------------------------------------------------------------
# Native
# ---------------------------------------------------------------------------


async def test_native_bracket_feeders_names_and_standings(db):
    staff = await _staff()
    tournament, bracket, (semi1, semi2, final), _ = await _native_elim()

    view = await BracketOverviewService().get_tournament_bracket(staff, tournament.id)

    assert view.source == 'native'
    assert [p.id for p in view.phases] == [bracket.id]
    pool = view.phases[0].pools[0]
    assert pool.sets_total == 3 and pool.sets_complete == 1
    final_view = _find(view, final.id)
    assert final_view.round_label == 'Final'
    assert final_view.slots[0].name == 'Alice'
    assert final_view.slots[1].name is None
    assert final_view.slots[1].placeholder == 'Winner of Semifinals #2'
    assert (final_view.slots[1].from_set_id, final_view.slots[1].from_outcome) == (semi2.id, 'winner')
    assert (_find(view, semi1.id).winner_to.set_id, _find(view, semi1.id).winner_to.slot) == (final.id, 1)
    alice = next(e for e in view.entrants if e.name == 'Alice')
    assert _find(view, semi1.id).winner_entrant_id == alice.id
    top = pool.standings[0]
    assert (top.name, top.wins, top.losses) == ('Alice', 1, 0)


async def test_native_booked_games_are_listed(db):
    staff = await _staff()
    tournament, _, (_, semi2, _), _ = await _native_elim()
    match = await Match.create(tournament=tournament, scheduled_at=utc(2026, 10, 23, 18))
    await BracketMatchGame.create(bracket_match=semi2, game_number=1, match=match)

    view = await BracketOverviewService().get_tournament_bracket(staff, tournament.id)

    [booking] = _find(view, semi2.id).bookings
    assert (booking.match_id, booking.game, booking.status) == (match.id, 'Game 1 of 1', 'scheduled')


async def test_native_unpublished_bracket_is_not_found_for_players(db):
    tournament, *_ = await _native_elim(state=BracketState.DRAFT)

    with pytest.raises(NotFoundError):
        await BracketOverviewService().get_tournament_bracket(await _player(), tournament.id)
    view = await BracketOverviewService().get_tournament_bracket(await _staff(), tournament.id)
    assert view.phases


# ---------------------------------------------------------------------------
# Challonge
# ---------------------------------------------------------------------------


async def test_challonge_reads_the_mirror_without_calling_challonge(db, monkeypatch):
    async def no_calls(*_args, **_kwargs):
        raise AssertionError('Challonge was called')

    monkeypatch.setattr(ChallongeService, '_client', no_calls, raising=False)
    monkeypatch.setattr(ChallongeService, 'get_valid_access_token', no_calls)

    staff = await _staff()
    one = await make_member(11, 'one')
    tournament = await Tournament.create(
        name='Challonge Cup', is_active=True, challonge_tournament_id='555',
        challonge_tournament_url='https://challonge.com/cup', challonge_tournament_type='single elimination',
    )
    p = [
        await ChallongeParticipant.create(tournament=tournament, challonge_participant_id=str(i), name=name, user=user)
        for i, (name, user) in enumerate((('One', one), ('Two', None), ('Three', None)), start=1)
    ]
    semi = await ChallongeMatch.create(
        tournament=tournament, challonge_match_id='9001', identifier='A', round=1,
        state=ChallongeMatchState.COMPLETE, participant1=p[0], participant2=p[1], winner_participant=p[0],
    )
    final = await ChallongeMatch.create(
        tournament=tournament, challonge_match_id='9002', identifier='B', round=2,
        state=ChallongeMatchState.PENDING, participant2=p[2],
        slot1_prereq='9001', slot1_label='Winner of A',
    )
    booking = await Match.create(tournament=tournament, scheduled_at=utc(2026, 10, 22, 18), confirmed_at=utc(2026, 10, 22, 19))
    await MatchPlayers.create(match=booking, user=one, finish_rank=1)
    semi.match = booking
    await semi.save(update_fields=['match_id'])

    view = await BracketOverviewService().get_tournament_bracket(staff, tournament.id)

    assert view.source == 'challonge'
    assert view.url == 'https://challonge.com/cup'
    [phase] = view.phases
    assert phase.name == 'Final stage'
    final_view = _find(view, final.id)
    assert (final_view.slots[0].from_set_id, final_view.slots[0].placeholder) == (semi.id, 'Winner of A')
    semi_view = _find(view, semi.id)
    assert (semi_view.winner_to.set_id, semi_view.winner_to.slot) == (final.id, 1)
    assert semi_view.slots[0].score == 1
    assert [b.status for b in semi_view.bookings] == ['complete']
    assert phase.pools[0].standings[0].name == 'One'


async def test_challonge_feature_off_is_not_found(db):
    tournament = await Tournament.create(name='Challonge Cup', is_active=True, challonge_tournament_id='555')
    await _flag_off(FeatureFlag.CHALLONGE)

    with pytest.raises(FeatureDisabledError):
        await BracketOverviewService().get_tournament_bracket(await _staff(), tournament.id)
