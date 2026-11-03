"""`list_unbooked_matches`: the "Not booked yet" view over MCP.

Staff need the count during the event (SGLMAN-1W was a few hundred rows and
nobody could say how many without opening the page), so the totals must cover
every matchup even when `limit` cuts the rows.
"""

from models import (
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    MembershipSource,
    Role,
    TenantMembership,
    Tournament,
)
from tests.factories import make_member
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'


async def _matchup(tournament, mid, user1, user2):
    p1 = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id=f'{mid}a', name=f'{mid} one', user=user1,
    )
    p2 = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id=f'{mid}b', name=f'{mid} two', user=user2,
    )
    await ChallongeMatch.create(
        tournament=tournament, challonge_match_id=mid, round=1,
        state=ChallongeMatchState.OPEN, participant1=p1, participant2=p2,
    )


async def _seed():
    andy = await make_member(11, 'andy', display_name='Andy')
    benny = await make_member(12, 'benny', display_name='Benny')
    alttp = await Tournament.create(name='ALttPR', challonge_tournament_id='T1', is_active=True)
    dk = await Tournament.create(name='DK64', challonge_tournament_id='T2', is_active=True)
    await _matchup(alttp, 'm1', andy, benny)
    await _matchup(alttp, 'm2', andy, None)
    await _matchup(dk, 'm3', andy, benny)
    return alttp


async def test_staff_get_totals_over_every_matchup(db):
    await _seed()
    _, raw = await create_oauth_token(username='mcp-staff', roles=[Role.STAFF])
    async with mcp_session() as client:
        is_error, payload = await call_tool(
            client, raw, 'list_unbooked_matches', tenant=TENANT, limit=1,
        )
    assert not is_error, payload
    assert (payload['total'], payload['ready'], payload['needs_account']) == (3, 2, 1)
    assert payload['by_tournament'] == {'ALttPR': 2, 'DK64': 1}
    assert len(payload['matchups']) == 1


async def test_filters_by_tournament_and_readiness(db):
    alttp = await _seed()
    _, raw = await create_oauth_token(username='mcp-staff', roles=[Role.STAFF])
    async with mcp_session() as client:
        is_error, payload = await call_tool(
            client, raw, 'list_unbooked_matches', tenant=TENANT,
            tournament_id=alttp.id, ready_only=True,
        )
    assert not is_error, payload
    assert payload['total'] == 2
    [row] = payload['matchups']
    assert row['source'] == 'challonge'
    assert row['ready'] and row['tournament'] == 'ALttPR'
    assert len(row['players']) == 2 and row['unlinked'] == []


async def test_a_player_sees_nothing(db):
    await _seed()
    player, raw = await create_oauth_token(username='mcp-player')
    await TenantMembership.create(user=player, tenant_id=1, source=MembershipSource.STAFF)
    async with mcp_session() as client:
        is_error, payload = await call_tool(client, raw, 'list_unbooked_matches', tenant=TENANT)
    assert not is_error, payload
    assert payload['total'] == 0 and payload['matchups'] == []
