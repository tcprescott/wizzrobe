"""Staff book a bracket matchup before its players are known; sync fills them in."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services import bracket_sources
from application.services.match.match_service import MatchService
from models import ChallongeMatch, Match, MatchAcknowledgment, MatchPlayers, StartggSet, Tournament
from tests.services.test_challonge_service import make_service, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


def _bracket(semis_state='open', final=None):
    """Two semi-finals feeding a final (Challonge letters A, B, C)."""
    return {
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
        'participants': [
            {'participant_id': f'90{n}', 'name': name, 'challonge_user_id': f'100{n}', 'username': name}
            for n, name in ((1, 'Andy'), (2, 'Benny'), (3, 'Cleo'), (4, 'Dash'))
        ],
        'matches': [
            {'match_id': '8001', 'identifier': 'A', 'state': semis_state, 'round': 1,
             'player1_participant_id': '901', 'player2_participant_id': '902', 'winner_participant_id': None},
            {'match_id': '8002', 'identifier': 'B', 'state': semis_state, 'round': 1,
             'player1_participant_id': '903', 'player2_participant_id': '904', 'winner_participant_id': None},
            final or {'match_id': '8003', 'identifier': 'C', 'state': 'pending', 'round': 2,
                      'player1_participant_id': None, 'player2_participant_id': None,
                      'winner_participant_id': None,
                      'player1_prereq_match_id': '8001', 'player2_prereq_match_id': '8002'},
        ],
    }


async def _setup(monkeypatch):
    queue = MagicMock()
    monkeypatch.setattr('application.services.match.match_placeholders.discord_queue', queue)
    actor = await make_user(1, 'staff')
    players = {name: await make_user(10 + n, name.lower(), challonge_user_id=f'100{n}')
               for n, name in ((1, 'Andy'), (2, 'Benny'), (3, 'Cleo'), (4, 'Dash'))}
    tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
    full = _bracket()
    api = MagicMock()
    api.get_tournament_full = AsyncMock(return_value=full)
    service = make_service(api=api)
    await service.sync_bracket(tournament.id, actor)
    service.queue = queue
    return actor, players, tournament, full, service


async def test_book_ahead_then_sync_fills_players_in(db, monkeypatch):
    actor, players, tournament, full, service = await _setup(monkeypatch)
    final = await ChallongeMatch.get(challonge_match_id='8003')
    assert final.slot1_label == 'Winner of A'

    pending = await service.list_unbooked_pending()
    assert [r.id for r in pending] == [final.id]
    ids, labels = (await service.placeholder_sides_for(pending))[final.id]
    assert ids == [None, None]
    assert labels == ['andy or benny', 'cleo or dash']

    match = await service.book_ahead(final.id, '2026-10-24', '18:00', actor)
    assert match.bracket_placeholders == ['andy or benny', 'cleo or dash']
    assert not await MatchPlayers.filter(match=match).exists()
    await final.refresh_from_db()
    assert final.match_id == match.id

    service.queue.enqueue.reset_mock()
    full['matches'][0].update(state='complete', winner_participant_id='901')
    full['matches'][2].update(player1_participant_id='901')
    await service.sync_bracket(tournament.id, actor, force=True)
    await match.refresh_from_db()
    assert set(await MatchPlayers.filter(match=match).values_list('user_id', flat=True)) == {players['Andy'].id}
    assert match.bracket_placeholders == ['cleo or dash']
    assert await MatchAcknowledgment.filter(match=match, user=players['Andy'], acknowledged_at=None).exists()
    service.queue.enqueue.assert_called_once()

    full['matches'][1].update(state='complete', winner_participant_id='904')
    full['matches'][2].update(player2_participant_id='904', state='open')
    await service.sync_bracket(tournament.id, actor, force=True)
    await match.refresh_from_db()
    assert set(await MatchPlayers.filter(match=match).values_list('user_id', flat=True)) == {
        players['Andy'].id, players['Dash'].id,
    }
    assert match.bracket_placeholders is None
    assert not match.bracket_link_lost


async def test_reset_keeps_the_booking_and_flags_it(db, monkeypatch):
    actor, _players, tournament, full, service = await _setup(monkeypatch)
    final = await ChallongeMatch.get(challonge_match_id='8003')
    match = await service.book_ahead(final.id, '2026-10-24', '18:00', actor)

    full['matches'][2] = dict(full['matches'][2], match_id='9003')
    await service.sync_bracket(tournament.id, actor, force=True)
    await match.refresh_from_db()
    assert match.bracket_link_lost
    assert await Match.filter(id=match.id).exists()

    items = await bracket_sources.list_unbooked(actor, include_players_tbd=True)
    tbd = [i for i in items if i.players_tbd]
    assert [m.id for m in tbd[0].lost_bookings] == [match.id]

    new_final = await ChallongeMatch.get(challonge_match_id='9003')
    await service.relink_ahead(new_final.id, match.id, actor)
    await match.refresh_from_db()
    await new_final.refresh_from_db()
    assert new_final.match_id == match.id
    assert not match.bracket_link_lost


async def test_players_tbd_rows_stay_out_of_the_default_list(db, monkeypatch):
    actor, *_ = await _setup(monkeypatch)
    items = await bracket_sources.list_unbooked(actor)
    assert all(not i.players_tbd for i in items)
    items = await bracket_sources.list_unbooked(actor, include_players_tbd=True)
    tbd = [i for i in items if i.players_tbd]
    assert [i.player_names for i in tbd] == [['andy or benny', 'cleo or dash']]
    assert not tbd[0].ready


async def test_fill_never_swaps_a_player_out(db, monkeypatch):
    actor, players, tournament, full, service = await _setup(monkeypatch)
    final = await ChallongeMatch.get(challonge_match_id='8003')
    match = await service.book_ahead(final.id, '2026-10-24', '18:00', actor)
    full['matches'][2].update(player1_participant_id='901')
    await service.sync_bracket(tournament.id, actor, force=True)

    full['matches'][2].update(player1_participant_id='902', player2_participant_id='904', state='open')
    await service.sync_bracket(tournament.id, actor, force=True)
    await match.refresh_from_db()
    assert set(await MatchPlayers.filter(match=match).values_list('user_id', flat=True)) == {players['Andy'].id}
    assert match.bracket_link_lost


async def test_startgg_sides_name_the_feeder_set(db):
    from application.services.startgg_service import StartggService
    from models import StartggEntrant, StartggPhase, StartggPool, StartggSetState

    tournament = await Tournament.create(name='S')
    phase = await StartggPhase.create(tournament=tournament, phase_id='p1', name='Bracket', phase_order=1)
    pool = await StartggPool.create(tournament=tournament, phase=phase, phase_group_id='g1', identifier='1')
    andy = await StartggEntrant.create(tournament=tournament, entrant_id='e1', name='Andy')
    benny = await StartggEntrant.create(tournament=tournament, entrant_id='e2', name='Benny')
    await StartggSet.create(tournament=tournament, pool=pool, set_id='s1', identifier='A',
                            state=StartggSetState.OPEN, entrant1=andy, entrant2=benny)
    final = await StartggSet.create(tournament=tournament, pool=pool, set_id='s3', identifier='C',
                                    state=StartggSetState.PENDING, slot1_prereq='s1', slot2_label='Winner of B')
    final = await StartggSet.get(id=final.id).prefetch_related('tournament', 'entrant1__user', 'entrant2__user')
    ids, labels = (await StartggService().placeholder_sides_for([final]))[final.id]
    assert ids == [None, None]
    assert labels == ['Andy or Benny', 'Winner of B']


async def test_create_match_still_needs_a_player_outside_a_bracket(db):
    actor = await make_user(1, 'staff')
    tournament = await Tournament.create(name='T')
    with pytest.raises(ValueError):
        await MatchService().create_match(
            tournament_id=tournament.id, scheduled_date='2026-10-24', scheduled_time='18:00',
            player_ids=[], placeholders=['TBD'], actor=actor,
        )


def test_run_sheet_names_the_empty_sides():
    from theme.tables.match_run_sheet import decorate_run_sheet_row, run_sheet_status

    row = {'state': 'Scheduled', 'players': [{'name': 'Andy'}], 'placeholders': ['Cleo or Dash']}
    decorate_run_sheet_row(row)
    assert row['_who'] == 'Andy vs Cleo or Dash'
    assert row['_status'] == 'Players TBD'
    assert row['_seats'] == []
    assert run_sheet_status({**row, 'bracket_link_lost': True})[0] == 'Bracket link lost'
    assert row['_next'] is None


async def test_check_in_waits_for_the_bracket(db, monkeypatch):
    actor, _players, tournament, full, service = await _setup(monkeypatch)
    final = await ChallongeMatch.get(challonge_match_id='8003')
    match = await service.book_ahead(final.id, '2026-10-24', '18:00', actor)
    full['matches'][2].update(player1_participant_id='901')
    await service.sync_bracket(tournament.id, actor, force=True)
    await match.refresh_from_db()
    from application.services.match.match_schedule_service import MatchScheduleService

    with pytest.raises(ValueError, match="can't be checked in"):
        await MatchScheduleService().seat_match(match, actor)
