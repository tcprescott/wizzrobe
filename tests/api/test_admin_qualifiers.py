"""REST tests for the app's Admin → Qualifiers screen (api/routers/admin_qualifiers.py)."""

from datetime import datetime, timedelta, timezone

from models import AsyncQualifierRun, Role
from tests.api_helpers import client_for, create_user_token

UTC = timezone.utc


async def _qualifier(c, name='Open Cup'):
    r = await c.post('/api/async-qualifiers', json={
        'name': name,
        'opens_at': (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        'closes_at': (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    })
    assert r.status_code == 201, r.text
    return r.json()['id']


async def _pool(c, qid, name='Pool A'):
    r = await c.post(f'/api/async-qualifiers/{qid}/pools', json={'name': name})
    assert r.status_code == 201, r.text
    pool_id = r.json()['id']
    r = await c.post(f'/api/async-qualifiers/pools/{pool_id}/permalinks/bulk',
                     json={'urls': ['https://example.com/seed-1', 'https://example.com/seed-2']})
    assert r.status_code in (200, 201), r.text
    return pool_id


async def _finished_run(app, raw, qid, pool_id, seconds=1200):
    async with client_for(app, raw) as c:
        r = await c.post(f'/api/async-qualifiers/{qid}/runs', json={'pool_id': pool_id})
        assert r.status_code == 201, r.text
        run_id = r.json()['id']
        await AsyncQualifierRun.filter(id=run_id).update(started_at=datetime.now(UTC) - timedelta(seconds=seconds))
        r = await c.post(f'/api/async-qualifiers/runs/{run_id}/submit', json={'elapsed_seconds': seconds})
        assert r.status_code == 200, r.text
    return run_id


class TestQualifierScreen:
    async def test_board_counts_runs_waiting(self, db, app):
        _, admin = await create_user_token(username='qadmin', roles=[Role.QUALIFIER_ADMIN])
        _, runner = await create_user_token(username='runner', roles=[])
        async with client_for(app, admin) as c:
            qid = await _qualifier(c)
            await _qualifier(c, name='Quiet Cup')
            pool_id = await _pool(c, qid)
        await _finished_run(app, runner, qid, pool_id)

        async with client_for(app, admin) as c:
            r = await c.get('/api/admin/qualifiers/board')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['can_create'] is True
        assert [q['name'] for q in body['qualifiers']] == ['Open Cup', 'Quiet Cup']
        first = body['qualifiers'][0]
        assert (first['state'], first['to_review'], first['runs']) == ('open', 1, 1)

    async def test_detail_leads_with_the_review_queue(self, db, app):
        _, admin = await create_user_token(username='qadmin', roles=[Role.QUALIFIER_ADMIN])
        _, runner = await create_user_token(username='runner', roles=[])
        async with client_for(app, admin) as c:
            qid = await _qualifier(c)
            pool_id = await _pool(c, qid)
        run_id = await _finished_run(app, runner, qid, pool_id)

        async with client_for(app, admin) as c:
            r = await c.get(f'/api/admin/qualifiers/{qid}')
            pools = await c.get(f'/api/admin/qualifiers/{qid}/pools')
            races = await c.get(f'/api/admin/qualifiers/{qid}/live-races')
        assert r.status_code == 200, r.text
        body = r.json()
        card = body['queue'][0]
        assert (card['run_id'], card['runner'], card['pool'], card['claimed_seconds']) == (run_id, 'runner', 'Pool A', 1200)
        assert card['held_by'] is None and card['held_by_me'] is False
        assert body['setup'] == {'pools': 1, 'seeds': 2, 'live_races': 0, 'live_races_open': 0, 'reviewers': 0}
        assert body['settings']['runs_per_pool'] >= 1
        assert [p['name'] for p in pools.json()] == ['Pool A']
        assert len(pools.json()[0]['permalinks']) == 2
        assert pools.json()[0]['roll_refusal']
        assert races.json() == []

    async def test_reviewers_and_members_to_add(self, db, app):
        _, admin = await create_user_token(username='qadmin', roles=[Role.QUALIFIER_ADMIN])
        helper, _ = await create_user_token(username='helper', roles=[])
        async with client_for(app, admin) as c:
            qid = await _qualifier(c)
            assert (await c.post(f'/api/async-qualifiers/{qid}/admins', json={'user_id': helper.id})).status_code == 201
            r = await c.get(f'/api/admin/qualifiers/{qid}/reviewers')
        assert r.status_code == 200, r.text
        assert [p['username'] for p in r.json()['reviewers']] == ['helper']
        assert 'helper' in {p['username'] for p in r.json()['members']}

    async def test_claim_shows_who_holds_it(self, db, app):
        admin_user, admin = await create_user_token(username='qadmin', roles=[Role.QUALIFIER_ADMIN])
        _, runner = await create_user_token(username='runner', roles=[])
        async with client_for(app, admin) as c:
            qid = await _qualifier(c)
            pool_id = await _pool(c, qid)
        run_id = await _finished_run(app, runner, qid, pool_id)
        async with client_for(app, admin) as c:
            assert (await c.post(f'/api/async-qualifiers/runs/{run_id}/claim')).status_code == 200
            card = (await c.get(f'/api/admin/qualifiers/{qid}')).json()['queue'][0]
        assert card['held_by'] == 'qadmin'
        assert card['held_by_me'] is True

    async def test_players_are_refused(self, db, app):
        _, admin = await create_user_token(username='qadmin', roles=[Role.QUALIFIER_ADMIN])
        _, player = await create_user_token(username='player', roles=[])
        async with client_for(app, admin) as c:
            qid = await _qualifier(c)
        async with client_for(app, player) as c:
            board = await c.get('/api/admin/qualifiers/board')
            detail = await c.get(f'/api/admin/qualifiers/{qid}')
        assert board.status_code == 403
        assert detail.status_code == 403
