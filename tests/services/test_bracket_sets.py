"""Best-of sets on a Challonge or start.gg matchup: one match per game, one result to the bracket."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services import bracket_sources
from application.services.bracket_set_service import BracketSetService, game_winner
from application.services.match.match_service import MatchService
from application.services.round_schedule_service import RoundScheduleService
from application.services.tournament_config import validate_tournament_config
from models import ChallongeMatch, Match, MatchPlayers, Tournament
from tests.services.test_challonge_service import make_service
from tests.services.test_placeholder_bookings import _setup

pytestmark = pytest.mark.usefixtures("bypass_auth")

THREE = [('2026-10-25', '19:00'), ('2026-10-25', '20:20'), ('2026-10-25', '21:40')]


def _api():
    api = MagicMock()
    api.get_match = AsyncMock(return_value={'state': 'open'})
    api.update_match = AsyncMock()
    api.get_tournament_full = AsyncMock(return_value={
        'tournament': {'id': 'T1', 'name': 'T', 'url': None, 'state': 'underway'},
        'participants': [], 'matches': [],
    })
    return api


async def _win(match: Match, user_id: int) -> None:
    for player in await MatchPlayers.filter(match_id=match.id):
        await MatchPlayers.filter(id=player.id).update(finish_rank=1 if player.user_id == user_id else 2)


async def _book_semi(monkeypatch, stub_discord_queue):
    actor, players, tournament, full, service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    games = await bracket_sources.schedule_set('challonge', semi.id, THREE, actor)
    return actor, players, semi, games


async def test_a_set_books_one_match_per_game(db, monkeypatch, stub_discord_queue):
    _actor, players, semi, games = await _book_semi(monkeypatch, stub_discord_queue)

    assert len(games) == 3
    await semi.refresh_from_db()
    assert semi.match_id == games[0].id
    for number, game in enumerate(games, start=1):
        game = await Match.get(id=game.id)
        assert (game.set_game, game.set_best_of) == (number, 3)
        assert game.set_first_id == (None if number == 1 else games[0].id)  # type: ignore[attr-defined]
        assert set(await MatchPlayers.filter(match=game).values_list('user_id', flat=True)) == {
            players['Andy'].id, players['Benny'].id,
        }
        assert BracketSetService.label(game) == f'Game {number} of 3'
    # Later games don't show up as hand-booked matches waiting for a bracket link.
    candidates = await MatchService().bracket_link_candidates([semi.tournament_id])  # type: ignore[attr-defined]
    assert not {m.id for m in candidates} & {g.id for g in games}


async def test_challonge_hears_the_set_only_once_it_is_won(db, monkeypatch, stub_discord_queue):
    actor, players, _semi, games = await _book_semi(monkeypatch, stub_discord_queue)
    api = _api()
    service = make_service(api=api)
    andy = players['Andy'].id

    await _win(games[0], andy)
    assert await service.push_result_if_linked(games[0], actor) is False
    api.update_match.assert_not_awaited()
    assert await BracketSetService().call_off_unneeded(games[0]) == []

    await _win(games[1], andy)
    assert await service.push_result_if_linked(games[1], actor) is True
    _, kwargs = api.update_match.call_args
    assert kwargs['winner_participant_id'] == '901'
    assert kwargs['loser_participant_id'] == '902'
    assert (kwargs['winner_score'], kwargs['loser_score']) == ('2', '0')

    assert await BracketSetService().call_off_unneeded(games[1]) == [games[2].id]
    assert not await Match.filter(id=games[2].id).exists()


async def test_a_deciding_game_three_reports_two_one(db, monkeypatch, stub_discord_queue):
    actor, players, _semi, games = await _book_semi(monkeypatch, stub_discord_queue)
    api = _api()
    service = make_service(api=api)
    await _win(games[0], players['Andy'].id)
    await _win(games[1], players['Benny'].id)
    assert await service.push_result_if_linked(games[1], actor) is False

    await _win(games[2], players['Benny'].id)
    assert await service.push_result_if_linked(games[2], actor) is True
    _, kwargs = api.update_match.call_args
    assert kwargs['winner_participant_id'] == '902'
    assert (kwargs['winner_score'], kwargs['loser_score']) == ('2', '1')


async def test_out_of_order_times_book_nothing(db, monkeypatch, stub_discord_queue):
    actor, _players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    with pytest.raises(ValueError, match='Game 2 has to start after game 1'):
        await bracket_sources.schedule_set(
            'challonge', semi.id, [('2026-10-25', '19:00'), ('2026-10-25', '18:00'), ('2026-10-25', '21:00')],
            actor,
        )
    with pytest.raises(ValueError, match='odd number'):
        await bracket_sources.schedule_set('challonge', semi.id, THREE[:2], actor)
    await semi.refresh_from_db()
    assert semi.match_id is None
    assert not await Match.all().exists()


async def test_calling_off_game_one_takes_the_set_with_it(db, monkeypatch, stub_discord_queue):
    actor, _players, semi, games = await _book_semi(monkeypatch, stub_discord_queue)
    await MatchService().cancel_match(games[0].id, actor=actor)
    assert not await Match.filter(id__in=[g.id for g in games]).exists()
    await semi.refresh_from_db()
    assert semi.match_id is None


async def test_a_set_booked_ahead_fills_every_game(db, monkeypatch, stub_discord_queue):
    actor, players, tournament, full, service = await _setup(monkeypatch)
    final = await ChallongeMatch.get(challonge_match_id='8003')
    games = await bracket_sources.schedule_set('challonge', final.id, THREE, actor, ahead=True)
    for game in games:
        assert (await Match.get(id=game.id)).bracket_placeholders == ['andy or benny', 'cleo or dash']

    full['matches'][0].update(state='complete', winner_participant_id='901')
    full['matches'][2].update(player1_participant_id='901')
    await service.sync_bracket(tournament.id, actor, force=True)
    for game in games:
        game = await Match.get(id=game.id)
        assert game.bracket_placeholders == ['cleo or dash']
        assert set(await MatchPlayers.filter(match=game).values_list('user_id', flat=True)) == {players['Andy'].id}


async def test_the_round_schedule_sets_each_stage_best_of(db, monkeypatch):
    actor, _players, tournament, _full, _service = await _setup(monkeypatch)
    rounds = RoundScheduleService()
    stages = await rounds.list_stages(tournament.id, actor)
    final_key = next(s.key for s in stages if s.label == 'Final')
    await rounds.save_schedule(tournament.id, {final_key: {'best_of': 3}}, actor)

    final = await ChallongeMatch.get(challonge_match_id='8003')
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    assert await bracket_sources.best_of_for('challonge', final.id) == 3
    assert await bracket_sources.best_of_for('challonge', semi.id) == 1
    stages = await rounds.list_stages(tournament.id, actor)
    assert next(s for s in stages if s.key == final_key).best_of == 3
    # A best-of alone is no booking window.
    assert next(s for s in stages if s.key == final_key).window is None
    assert (await Tournament.get(id=tournament.id)).config['round_schedule'] == {final_key: {'best_of': 3}}


def test_best_of_must_be_odd():
    with pytest.raises(ValueError):
        validate_tournament_config({'round_schedule': {'e0': {'best_of': 2}}})
    assert validate_tournament_config({'round_schedule': {'e0': {'best_of': 5}}}) == {
        'round_schedule': {'e0': {'best_of': 5}},
    }


async def test_a_booked_game_becomes_game_one_of_a_set(db, monkeypatch, stub_discord_queue):
    actor, players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    [single] = await bracket_sources.schedule_set('challonge', semi.id, THREE[:1], actor)
    await Match.filter(id=single.id).update(comment='keep me')

    games = await BracketSetService().extend_booking(single.id, THREE[1:], actor)

    assert games[0].id == single.id and len(games) == 3
    first = await Match.get(id=single.id)
    assert (first.set_game, first.set_best_of, first.comment) == (1, 3, 'keep me')
    await semi.refresh_from_db()
    assert semi.match_id == single.id
    for number, game in enumerate(games[1:], start=2):
        game = await Match.get(id=game.id)
        assert (game.set_game, game.set_best_of, game.set_first_id) == (number, 3, single.id)  # type: ignore[attr-defined]
        assert set(await MatchPlayers.filter(match=game).values_list('user_id', flat=True)) == {
            players['Andy'].id, players['Benny'].id,
        }

    with pytest.raises(ValueError, match='already part of a set'):
        await BracketSetService().extend_booking(single.id, THREE[1:], actor)


async def test_only_an_unplayed_bracket_booking_becomes_a_set(db, monkeypatch, stub_discord_queue):
    actor, _players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    [single] = await bracket_sources.schedule_set('challonge', semi.id, THREE[:1], actor)
    await ChallongeMatch.filter(id=semi.id).update(match_id=None)
    with pytest.raises(ValueError, match=r'Challonge or start\.gg'):
        await BracketSetService().extend_booking(single.id, THREE[1:], actor)
    await ChallongeMatch.filter(id=semi.id).update(match_id=single.id)

    with pytest.raises(ValueError, match='Game 2 has to start after game 1'):
        await BracketSetService().extend_booking(single.id, [('2026-10-25', '18:00'), ('2026-10-25', '21:00')], actor)
    from datetime import datetime, timezone
    await Match.filter(id=single.id).update(started_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match='already started'):
        await BracketSetService().extend_booking(single.id, THREE[1:], actor)
    assert await Match.filter(set_first_id=single.id).count() == 0


async def _hand_booked(actor, players, tournament_id, clock):
    from tests.factories import join_community

    await join_community(players['Andy'], players['Benny'])
    return await MatchService().create_match(
        tournament_id=tournament_id, scheduled_date='2026-10-25', scheduled_time=clock,
        player_ids=[players['Andy'].id, players['Benny'].id], actor=actor,
    )


async def test_a_hand_booked_game_joins_a_played_game_one(db, monkeypatch, stub_discord_queue):
    """SG Live 2026: game 1 played, games 2 and 3 booked by hand, game 1's winner sent as the set's."""
    actor, players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    [single] = await bracket_sources.schedule_set('challonge', semi.id, THREE[:1], actor)
    await _win(single, players['Andy'].id)
    pair = [players['Andy'].id, players['Benny'].id]
    sets = BracketSetService()

    found = await sets.open_set_for(single.tournament_id, pair, actor=actor)  # type: ignore[attr-defined]
    assert found is not None and found.first.id == single.id
    assert (found.next_game, found.best_of_after, found.site) == (2, 3, 'Challonge')
    assert [game_winner(g) for g in found.games] == [players['Andy'].id]

    game2 = await _hand_booked(actor, players, single.tournament_id, '20:20')  # type: ignore[attr-defined]
    await sets.join_set(game2.id, single.id, actor)
    game3 = await _hand_booked(actor, players, single.tournament_id, '21:40')  # type: ignore[attr-defined]
    found = await sets.open_set_for(single.tournament_id, pair, actor=actor)  # type: ignore[attr-defined]
    assert (found.first.id, found.next_game, found.best_of_after) == (single.id, 3, 3)
    await sets.join_set(game3.id, single.id, actor)

    for number, game in enumerate([single, game2, game3], start=1):
        game = await Match.get(id=game.id)
        assert (game.set_game, game.set_best_of) == (number, 3)

    api = _api()
    challonge = make_service(api=api)
    assert await challonge.push_result_if_linked(await Match.get(id=single.id), actor) is False
    await _win(game2, players['Benny'].id)
    await _win(game3, players['Benny'].id)
    assert await challonge.push_result_if_linked(await Match.get(id=game3.id), actor) is True
    _, kwargs = api.update_match.call_args
    assert kwargs['winner_participant_id'] == '902'
    assert (kwargs['winner_score'], kwargs['loser_score']) == ('2', '1')
    assert await sets.open_set_for(single.tournament_id, pair, actor=actor) is None  # type: ignore[attr-defined]


async def test_a_fourth_game_grows_the_set_to_best_of_five(db, monkeypatch, stub_discord_queue):
    actor, players, semi, games = await _book_semi(monkeypatch, stub_discord_queue)
    extra = await _hand_booked(actor, players, semi.tournament_id, '23:00')  # type: ignore[attr-defined]
    await BracketSetService().join_set(extra.id, games[0].id, actor)
    rows = await Match.filter(id__in=[g.id for g in games] + [extra.id]).order_by('set_game')
    assert [(m.set_game, m.set_best_of) for m in rows] == [(1, 5), (2, 5), (3, 5), (4, 5)]


async def test_nothing_to_join_once_the_bracket_has_the_result(db, monkeypatch, stub_discord_queue):
    from datetime import datetime, timezone

    actor, players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    [single] = await bracket_sources.schedule_set('challonge', semi.id, THREE[:1], actor)
    tid = single.tournament_id  # type: ignore[attr-defined]
    sets = BracketSetService()
    assert await sets.open_set_for(tid, [players['Andy'].id], actor=actor) is None

    await Match.filter(id=single.id).update(confirmed_at=datetime.now(timezone.utc))
    assert await sets.open_set_for(tid, [players['Andy'].id, players['Benny'].id], actor=actor) is None
    extra = await _hand_booked(actor, players, tid, '20:20')
    with pytest.raises(ValueError, match='already gone to the bracket site'):
        await sets.join_set(extra.id, single.id, actor)


async def test_a_plain_match_is_not_a_set_to_join(db, monkeypatch, stub_discord_queue):
    actor, players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    await _hand_booked(actor, players, semi.tournament_id, '19:00')  # type: ignore[attr-defined]
    found = await BracketSetService().open_set_for(
        semi.tournament_id, [players['Andy'].id, players['Benny'].id], actor=actor,  # type: ignore[attr-defined]
    )
    assert found is None


async def test_a_result_already_on_challonge_is_not_a_set_to_join(db, monkeypatch, stub_discord_queue):
    from models import ChallongeMatchState

    actor, players, _tournament, _full, _service = await _setup(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    [single] = await bracket_sources.schedule_set('challonge', semi.id, THREE[:1], actor)
    await ChallongeMatch.filter(id=semi.id).update(state=ChallongeMatchState.COMPLETE)
    found = await BracketSetService().open_set_for(
        single.tournament_id, [players['Andy'].id, players['Benny'].id], actor=actor,  # type: ignore[attr-defined]
    )
    assert found is None
