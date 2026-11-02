"""REST tests for the app's Admin → Brackets screen (api/routers/admin_brackets.py)."""

from models import Role
from tests.api._bracket_api import draft, enroll, staff_token, tournament
from tests.api_helpers import client_for, create_user_token


class TestBoard:
    async def test_lists_tournaments_and_stage_next_steps(self, db, app):
        _, staff = await staff_token()
        t = await tournament('Zelda 1')
        await tournament('Empty Cup')
        async with client_for(app, staff) as c:
            empty = await draft(c, t, name='Top 8')
            full = await draft(c, t, name='Swiss', format='swiss', stage_order=1)
            await enroll(c, full, t, ('Ari', 'Bex', 'Cato'))

            r = await c.get('/api/admin/brackets/board')
        assert r.status_code == 200
        body = r.json()
        names = [x['name'] for x in body['tournaments']]
        assert names == ['Empty Cup', 'Zelda 1']
        stages = {s['id']: s for s in body['tournaments'][1]['stages']}
        assert stages[empty]['next_step']['key'] == 'enroll'
        assert stages[empty]['stage_label'] == 'Stage 1'
        assert stages[full]['next_step']['key'] == 'start'
        assert stages[full]['next_step']['done'] == 3
        assert {f['value'] for f in body['formats']} >= {'single_elim', 'swiss'}

    async def test_staff_only(self, db, app):
        _, player = await create_user_token(username='player', roles=[])
        async with client_for(app, player) as c:
            r = await c.get('/api/admin/brackets/board')
        assert r.status_code == 403


class TestStage:
    async def test_draft_shows_seeds_and_roster(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            entry_ids = await enroll(c, bracket_id, t, ('Ari', 'Bex'))
            await c.post('/api/brackets/entrants', json={'tournament_id': t.id, 'display_name': 'Cato'})
            await c.patch(f'/api/brackets/{bracket_id}/seeds', json={'seeds': {str(entry_ids[1]): 1}})

            r = await c.get(f'/api/admin/brackets/{bracket_id}')
        assert r.status_code == 200
        body = r.json()
        assert [e['name'] for e in body['entries']] == ['Bex', 'Ari']
        assert body['entries'][0]['seed'] == 1
        roster = {x['name']: x['enrolled'] for x in body['roster']}
        assert roster == {'Ari': True, 'Bex': True, 'Cato': False}
        assert body['row']['next_step']['detail'].endswith("1 on the roster aren't in yet")
        assert body['matches'] == []

    async def test_live_stage_lists_open_matches_first(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Ari', 'Bex', 'Cato', 'Dee'))
            assert (await c.post(f'/api/brackets/{bracket_id}/start')).status_code == 200
            r = await c.get(f'/api/admin/brackets/{bracket_id}')
        body = r.json()
        assert body['row']['next_step']['key'] == 'report'
        assert body['matches']
        assert all(m['state'] == 'open' for m in body['matches'])
        assert body['matches'][0]['label'].startswith('Semifinals')
        assert {body['matches'][0]['side1']['name'], body['matches'][0]['side2']['name']} <= {'Ari', 'Bex', 'Cato', 'Dee'}

    async def test_unknown_stage_404s(self, db, app):
        _, staff = await staff_token()
        async with client_for(app, staff) as c:
            r = await c.get('/api/admin/brackets/999999')
        assert r.status_code == 404

    async def test_player_refused(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
        _, player = await create_user_token(username='player2', roles=[Role.VOLUNTEER])
        async with client_for(app, player) as c:
            r = await c.get(f'/api/admin/brackets/{bracket_id}')
        assert r.status_code == 403
