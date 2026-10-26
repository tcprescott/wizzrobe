"""``GET /api/tournaments/{id}/bracket``: the whole bracket, same payload as the MCP tool."""

from models import BracketState, Role
from tests.api_helpers import client_for, create_user_token
from tests.services._bracket_overview_setup import native_elim


async def test_a_member_reads_the_whole_bracket(db, app):
    tournament, _, (semi1, semi2, final), _ = await native_elim()
    _, raw = await create_user_token(username='plain')

    async with client_for(app, raw) as c:
        resp = await c.get(f'/api/tournaments/{tournament.id}/bracket')

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['source'] == 'native'
    sets = {s['id']: s for s in body['phases'][0]['pools'][0]['sets']}
    assert sets[final.id]['slots'][1]['from_set_id'] == semi2.id
    assert sets[semi1.id]['winner_to'] == {'set_id': final.id, 'slot': 1}
    assert {e['user_id'] for e in body['entrants']} == {None}


async def test_filters_and_a_bad_state(db, app):
    tournament, *_ = await native_elim()
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

    async with client_for(app, raw) as c:
        opened = await c.get(f'/api/tournaments/{tournament.id}/bracket', params={'state': 'open'})
        bad = await c.get(f'/api/tournaments/{tournament.id}/bracket', params={'state': 'done'})

    assert [s['identifier'] for s in opened.json()['phases'][0]['pools'][0]['sets']] == ['1.2']
    assert opened.json()['filtered'] is True
    assert bad.status_code == 400


async def test_an_unpublished_bracket_is_404_to_members(db, app):
    tournament, *_ = await native_elim(state=BracketState.DRAFT)
    _, raw = await create_user_token(username='plain')

    async with client_for(app, raw) as c:
        resp = await c.get(f'/api/tournaments/{tournament.id}/bracket')

    assert resp.status_code == 404
