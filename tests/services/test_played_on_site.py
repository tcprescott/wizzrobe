"""A booked match whose result was reported on Challonge or start.gg directly."""

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services import bracket_sources
from application.services.match.match_display_service import MatchDisplayService
from models import (
    AuditLog,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    MatchPlayers,
    Role,
    StartggEntrant,
    StartggPhase,
    StartggPool,
    StartggSet,
    StartggSetState,
    TenantMembership,
    Tournament,
    User,
)
from theme.tables.match_run_sheet import admin_group, next_step, proctor_group, run_sheet_status


@pytest.fixture
async def staff(db):
    boss = await User.create(discord_id=7700, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


async def _booked(state=ChallongeMatchState.COMPLETE, winner_is='alice'):
    alice = await User.create(discord_id=7701, username='alice')
    bob = await User.create(discord_id=7702, username='bob')
    tournament = await Tournament.create(name='ALttPR', challonge_tournament_id='T1')
    match = await Match.create(tournament=tournament)
    await MatchPlayers.create(match=match, user=alice)
    await MatchPlayers.create(match=match, user=bob)
    p1 = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id='100', name='Alice C', user=alice,
    )
    p2 = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id='200', name='Bob C', user=bob,
    )
    winners = {'alice': p1, 'nobody': None}
    if winner_is == 'stranger':
        winners['stranger'] = await ChallongeParticipant.create(
            tournament=tournament, challonge_participant_id='300', name='Carol C',
        )
    await ChallongeMatch.create(
        tournament=tournament, challonge_match_id='8001', state=state,
        participant1=p1, participant2=p2, match=match,
        winner_participant=winners[winner_is] if state == ChallongeMatchState.COMPLETE else None,
    )
    return match, alice, bob


async def test_board_marks_a_booked_match_already_played_on_challonge(staff):
    match, alice, _ = await _booked()
    row = await MatchDisplayService().get_match_for_display(match.id)
    assert row['played_on_site'] == {
        'site': 'Challonge', 'winner': 'Alice C', 'winner_user_id': alice.id, 'no_winner': False,
    }


async def test_an_open_bracket_match_is_not_marked(staff):
    match, _, _ = await _booked(state=ChallongeMatchState.OPEN)
    row = await MatchDisplayService().get_match_for_display(match.id)
    assert row['played_on_site'] is None


async def test_record_copies_the_winner_and_confirms_without_floor_steps(staff):
    match, alice, _ = await _booked()
    await bracket_sources.record_site_result(match.id, staff)
    match = await Match.get(id=match.id).prefetch_related('players')
    assert match.confirmed_at is not None and match.finished_at is not None
    ranks = {p.user_id: p.finish_rank for p in match.players}
    assert ranks[alice.id] == 1
    confirm = await AuditLog.filter(action='match.confirmed').first()
    assert 'Challonge' in confirm.details
    assert not await AuditLog.filter(action__in=['match.seated', 'match.started', 'match.finished']).exists()
    row = await MatchDisplayService().get_match_for_display(match.id)
    assert row['played_on_site'] is None


async def test_record_refuses_a_winner_who_is_not_in_the_match(staff):
    match, _, _ = await _booked(winner_is='stranger')
    with pytest.raises(ValueError, match='Record the result by hand'):
        await bracket_sources.record_site_result(match.id, staff)


async def test_a_challonge_match_closed_with_no_winner_says_so(staff):
    match, _, _ = await _booked(winner_is='nobody')
    row = await MatchDisplayService().get_match_for_display(match.id)
    assert row['played_on_site']['no_winner'] is True
    with pytest.raises(ValueError, match='no winner'):
        await bracket_sources.record_site_result(match.id, staff)


async def test_a_startgg_set_closed_with_no_winner_is_not_someone_winning(staff):
    # SGL26 OoTR pool 6: start.gg closed alsoa vs jaxstronomy 0-0 with no
    # winner. The board said "Someone won on start.gg" and Record result failed.
    alice = await User.create(discord_id=7711, username='alice')
    bob = await User.create(discord_id=7712, username='bob')
    tournament = await Tournament.create(name='OoTR', startgg_event_slug='tournament/x/event/y')
    match = await Match.create(tournament=tournament)
    await MatchPlayers.create(match=match, user=alice)
    await MatchPlayers.create(match=match, user=bob)
    phase = await StartggPhase.create(tournament=tournament, phase_id='1', name='Pools')
    pool = await StartggPool.create(tournament=tournament, phase=phase, phase_group_id='2', identifier='6')
    e1 = await StartggEntrant.create(tournament=tournament, entrant_id='3', name='Alsoa', user=alice)
    e2 = await StartggEntrant.create(tournament=tournament, entrant_id='4', name='jaxstronomy', user=bob)
    await StartggSet.create(
        tournament=tournament, pool=pool, set_id='5', state=StartggSetState.COMPLETE,
        entrant1=e1, entrant2=e2, score1=0, score2=0, match=match,
    )

    row = await MatchDisplayService().get_match_for_display(match.id)
    assert row['played_on_site'] == {'site': 'start.gg', 'winner': '', 'winner_user_id': None, 'no_winner': True}
    assert run_sheet_status(row) == ('No winner on start.gg', 'pending')
    with pytest.raises(ValueError, match='Call it off instead'):
        await bracket_sources.record_site_result(match.id, staff)
    match = await Match.get(id=match.id).prefetch_related('players')
    assert all(p.finish_rank is None for p in match.players)


def test_run_sheet_tags_the_row_and_drops_the_floor_step():
    row = {'state': 'Scheduled', 'players': [{'name': 'A'}, {'name': 'B'}],
           'played_on_site': {'site': 'start.gg', 'winner': 'A', 'winner_user_id': 1}}
    assert run_sheet_status(row) == ('Played on start.gg', 'pending')
    assert admin_group(row)[1] == 'Needs a decision'
    assert proctor_group(row)[1] == 'Done'
    assert next_step(row) is None
