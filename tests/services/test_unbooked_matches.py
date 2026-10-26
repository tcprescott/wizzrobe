"""Match Schedule's "Not booked yet" view: who sees which unbooked bracket matchups,
and staff booking a linked bracket's matchup for its players."""

from datetime import datetime, timedelta, timezone

import pytest

from application.services import bracket_sources
from models import (
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    MatchPlayers,
    Role,
    Tournament,
    UserRole,
)
from tests.factories import make_member


async def _challonge_match(tournament, name1, user1, name2, user2, mid, **extra):
    p1 = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id=f'{mid}a', name=name1, user=user1,
    )
    p2 = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id=f'{mid}b', name=name2, user=user2,
    )
    return await ChallongeMatch.create(
        tournament=tournament, challonge_match_id=mid, round=1,
        state=extra.pop('state', ChallongeMatchState.OPEN),
        participant1=p1, participant2=p2, **extra,
    )


@pytest.fixture
async def world(db):
    staff = await make_member(1, 'staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
    admin = await make_member(2, 'admin')
    player = await make_member(3, 'player')
    andy = await make_member(11, 'andy', display_name='Andy')
    benny = await make_member(12, 'benny', display_name='Benny')
    alttp = await Tournament.create(name='ALttPR', challonge_tournament_id='T1')
    dk = await Tournament.create(name='DK64', challonge_tournament_id='T2')
    await alttp.admins.add(admin)
    ready = await _challonge_match(alttp, 'Andy', andy, 'Benny', benny, 'm1')
    blocked = await _challonge_match(alttp, 'Cleo', andy, 'Dash', None, 'm2')
    other = await _challonge_match(dk, 'Ezra', andy, 'Fynn', benny, 'm3')
    booked = await _challonge_match(
        alttp, 'Gil', andy, 'Hana', benny, 'm4', match=await Match.create(tournament=alttp),
    )
    await _challonge_match(alttp, 'Ivy', andy, 'Jules', benny, 'm5', state=ChallongeMatchState.PENDING)
    return {
        'staff': staff, 'admin': admin, 'player': player, 'andy': andy, 'benny': benny,
        'alttp': alttp, 'ready': ready, 'blocked': blocked, 'other': other, 'booked': booked,
    }


async def test_staff_see_every_open_unbooked_matchup(world):
    rows = await bracket_sources.list_unbooked(world['staff'])
    assert sorted(r.key for r in rows if r.source == 'challonge') == sorted(
        [world['ready'].id, world['blocked'].id, world['other'].id]
    )


async def test_a_matchup_with_an_unlinked_player_says_who(world):
    rows = {r.key: r for r in await bracket_sources.list_unbooked(world['staff'])}
    assert rows[world['ready'].id].ready
    blocked = rows[world['blocked'].id]
    assert not blocked.ready
    assert blocked.unlinked == ['Dash']
    assert blocked.matchup == 'Cleo vs Dash'


async def test_tournament_admins_see_only_their_tournaments(world):
    rows = await bracket_sources.list_unbooked(world['admin'])
    assert {r.tournament.id for r in rows} == {world['alttp'].id}


async def test_players_see_nothing(world):
    assert await bracket_sources.list_unbooked(world['player']) == []


async def test_finished_tournaments_drop_out(world):
    await Tournament.filter(id=world['alttp'].id).update(is_active=False)
    rows = await bracket_sources.list_unbooked(world['staff'])
    assert {r.tournament.name for r in rows if r.source == 'challonge'} == {'DK64'}


async def test_staff_book_a_challonge_matchup_for_its_players(world):
    when = datetime.now(timezone.utc) + timedelta(days=1)
    match = await bracket_sources.schedule(
        'challonge', world['ready'].id, when.strftime('%Y-%m-%d'), '14:00', world['staff'],
    )
    players = {mp.user_id for mp in await MatchPlayers.filter(match_id=match.id)}
    assert players == {world['andy'].id, world['benny'].id}
    assert (await ChallongeMatch.get(id=world['ready'].id)).match_id == match.id
    keys = {r.key for r in await bracket_sources.list_unbooked(world['staff'])}
    assert world['ready'].id not in keys


async def test_a_bystander_still_cannot_book_someone_elses_matchup(world):
    when = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(PermissionError):
        await bracket_sources.schedule(
            'challonge', world['ready'].id, when.strftime('%Y-%m-%d'), '14:00', world['player'],
        )


async def _hand_made(tournament, *players, **extra):
    match = await Match.create(tournament=tournament, **extra)
    for user in players:
        await MatchPlayers.create(match=match, user=user)
    return match


async def test_a_hand_made_match_with_the_same_players_is_suggested(world):
    match = await _hand_made(world['alttp'], world['andy'], world['benny'])
    await _hand_made(world['alttp'], world['andy'], world['staff'])
    await _hand_made(world['alttp'], world['andy'], world['benny'], confirmed_at=datetime.now(timezone.utc))
    rows = {r.key: r for r in await bracket_sources.list_unbooked(world['staff'])}
    assert [m.id for m in rows[world['ready'].id].same_player_matches] == [match.id]


async def test_linking_a_hand_made_match_books_the_matchup(world):
    match = await _hand_made(world['alttp'], world['andy'], world['benny'])
    await bracket_sources.link_existing('challonge', world['ready'].id, match.id, world['admin'])
    assert (await ChallongeMatch.get(id=world['ready'].id)).match_id == match.id
    keys = {r.key for r in await bracket_sources.list_unbooked(world['staff'])}
    assert world['ready'].id not in keys


async def test_a_match_with_other_players_cannot_be_linked(world):
    match = await _hand_made(world['alttp'], world['andy'], world['staff'])
    with pytest.raises(ValueError, match="players"):
        await bracket_sources.link_existing('challonge', world['ready'].id, match.id, world['staff'])


async def test_a_match_already_linked_elsewhere_cannot_be_linked_again(world):
    with pytest.raises(ValueError, match="can't be linked"):
        await bracket_sources.link_existing(
            'challonge', world['ready'].id, world['booked'].match_id, world['staff'],
        )


async def test_players_cannot_link_matches(world):
    match = await _hand_made(world['alttp'], world['andy'], world['benny'])
    with pytest.raises(PermissionError):
        await bracket_sources.link_existing('challonge', world['ready'].id, match.id, world['andy'])
