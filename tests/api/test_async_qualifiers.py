"""REST API tests for the async-qualifier endpoints (api/routers/async_qualifiers.py).

Covers the admin management surface (gated by ``can_admin_qualifier``), the player
run lifecycle (start → submit on your own run), review (self-review blocked), the
open/public reads, the while-open leaderboard lockdown, and cross-tenant isolation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.tenant_context import tenant_scope
from models import AsyncQualifier, AsyncQualifierRun, Role, Tenant, User
from tests.api_helpers import build_api_app, client_for, create_community_member, create_user_token, enable_all_features

UTC = timezone.utc


def past_iso():
    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


def future_iso():
    return (datetime.now(UTC) + timedelta(days=1)).isoformat()



async def _backdate(run_id: int, seconds: int):
    """Make a run look ``seconds`` old so a claim of that length is possible.

    ``submit_run`` compares the claim against the server-stamped ``started_at``
    and refuses a time longer than the run has existed.
    """
    await AsyncQualifierRun.filter(id=run_id).update(
        started_at=datetime.now(UTC) - timedelta(seconds=seconds),
    )


async def _admin_token(username='qadmin', read_only=False):
    return await create_user_token(username=username, roles=[Role.QUALIFIER_ADMIN], read_only=read_only)


async def _open_qualifier(client, name='Open Cup'):
    resp = await client.post('/api/async-qualifiers', json={
        'name': name,
        'opens_at': past_iso(),
        'closes_at': future_iso(),
    })
    assert resp.status_code == 201, resp.text
    return resp.json()['id']


async def _pool_with_permalink(client, qualifier_id, pool_name='Pool A'):
    pool_resp = await client.post(f'/api/async-qualifiers/{qualifier_id}/pools', json={'name': pool_name})
    assert pool_resp.status_code == 201, pool_resp.text
    pool_id = pool_resp.json()['id']
    pl_resp = await client.post(
        f'/api/async-qualifiers/pools/{pool_id}/permalinks',
        json={'url': 'https://example.com/seed-1'},
    )
    assert pl_resp.status_code == 201, pl_resp.text
    return pool_id


class TestBaseline:
    async def test_list_qualifiers_admin_ok(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            await _open_qualifier(c, name='Alpha')
            resp = await c.get('/api/async-qualifiers')
            assert resp.status_code == 200
            assert 'Alpha' in {q['name'] for q in resp.json()}

    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/async-qualifiers')
            assert resp.status_code == 401

    async def test_read_only_token_forbidden_on_write(self, db, app):
        _, raw = await _admin_token(read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/async-qualifiers', json={'name': 'Nope'})
            assert resp.status_code == 403

    async def test_role_less_token_forbidden_on_gated_read(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/async-qualifiers')
            assert resp.status_code == 403

    async def test_role_less_token_forbidden_on_create(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/async-qualifiers', json={'name': 'X'})
            assert resp.status_code == 403


class TestAdminManagement:
    async def test_qualifier_admin_can_create_and_get(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            create = await c.post('/api/async-qualifiers', json={
                'name': 'Season 1', 'runs_per_pool': 2, 'allowed_reattempts': 1,
            })
            assert create.status_code == 201
            qid = create.json()['id']
            assert create.json()['runs_per_pool'] == 2

            got = await c.get(f'/api/async-qualifiers/{qid}')
            assert got.status_code == 200
            assert got.json()['name'] == 'Season 1'

    async def test_update_qualifier(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c, name='Rename Me')
            resp = await c.patch(f'/api/async-qualifiers/{qid}', json={'name': 'Renamed'})
            assert resp.status_code == 200
            assert resp.json()['name'] == 'Renamed'

    async def test_update_qualifier_not_found(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            resp = await c.patch('/api/async-qualifiers/999999', json={'name': 'X'})
            assert resp.status_code == 404

    async def test_delete_qualifier(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c, name='Temp')
            resp = await c.delete(f'/api/async-qualifiers/{qid}')
            assert resp.status_code == 204
            assert await AsyncQualifier.get_or_none(id=qid) is None

    async def test_add_and_remove_admin(self, db, app):
        _, raw = await _admin_token()
        target = await create_community_member(discord_id=778899, username='reviewer')
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c, name='With Admins')
            add = await c.post(f'/api/async-qualifiers/{qid}/admins', json={'user_id': target.id})
            assert add.status_code == 201
            listed = await c.get(f'/api/async-qualifiers/{qid}/admins')
            assert target.id in {u['id'] for u in listed.json()}
            remove = await c.delete(f'/api/async-qualifiers/{qid}/admins/{target.id}')
            assert remove.status_code == 204

    async def test_add_admin_from_outside_the_community_404(self, db, app):
        _, raw = await _admin_token()
        outsider = await User.create(discord_id=778900, username='elsewhere')
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            resp = await c.post(f'/api/async-qualifiers/{qid}/admins', json={'user_id': outsider.id})
            assert resp.status_code == 404
            listed = await c.get(f'/api/async-qualifiers/{qid}/admins')
            assert outsider.id not in {u['id'] for u in listed.json()}

    async def test_add_admin_unknown_user_404(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            resp = await c.post(f'/api/async-qualifiers/{qid}/admins', json={'user_id': 999999})
            assert resp.status_code == 404

    async def test_pool_and_permalink_crud(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            pool = await c.post(f'/api/async-qualifiers/{qid}/pools', json={'name': 'Main'})
            assert pool.status_code == 201
            pool_id = pool.json()['id']

            pools = await c.get(f'/api/async-qualifiers/{qid}/pools')
            assert [p['id'] for p in pools.json()] == [pool_id]

            patched = await c.patch(f'/api/async-qualifiers/pools/{pool_id}', json={'name': 'Renamed Pool'})
            assert patched.status_code == 200
            assert patched.json()['name'] == 'Renamed Pool'

            pl = await c.post(
                f'/api/async-qualifiers/pools/{pool_id}/permalinks',
                json={'url': 'https://example.com/s1', 'live_race': False},
            )
            assert pl.status_code == 201
            permalink_id = pl.json()['id']
            assert pl.json()['url'] == 'https://example.com/s1'

            bulk = await c.post(
                f'/api/async-qualifiers/pools/{pool_id}/permalinks/bulk',
                json={'urls': ['https://example.com/a', '', 'not-a-url',
                               'https://example.com/b']},
            )
            assert bulk.status_code == 201
            body = bulk.json()
            assert [p['url'] for p in body['created']] == [
                'https://example.com/a', 'https://example.com/b',
            ]
            # A bad line is reported with its position rather than silently dropped:
            # line 3 of the submitted list, blank line included in the count.
            assert [(r['line'], r['value']) for r in body['rejected']] == [(3, 'not-a-url')]

            upd = await c.patch(
                f'/api/async-qualifiers/permalinks/{permalink_id}', json={'notes': 'seeded'},
            )
            assert upd.status_code == 200
            assert upd.json()['notes'] == 'seeded'

            deleted = await c.delete(f'/api/async-qualifiers/permalinks/{permalink_id}')
            assert deleted.status_code == 204

            deleted_pool = await c.delete(f'/api/async-qualifiers/pools/{pool_id}')
            assert deleted_pool.status_code == 204


class TestPublicReads:
    async def test_open_route_precedes_id_and_lists_active(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            await _open_qualifier(c, name='Live One')
            resp = await c.get('/api/async-qualifiers/open')
            assert resp.status_code == 200
            assert 'Live One' in {q['name'] for q in resp.json()}

    async def test_public_shell_ungated(self, db, app):
        _, admin_raw = await _admin_token()
        async with client_for(app, admin_raw) as c:
            qid = await _open_qualifier(c, name='Public Shell')
        _, plain_raw = await create_user_token(username='player')
        async with client_for(app, plain_raw) as c:
            resp = await c.get(f'/api/async-qualifiers/{qid}/public')
            assert resp.status_code == 200
            assert resp.json()['name'] == 'Public Shell'

    async def test_ungated_reads_omit_internal_config(self, db, app):
        # ``config`` holds internal draw-fairness / par-scoring knobs and messaging
        # templates. The admin-gated GET exposes it; the two ungated player-facing
        # surfaces (/{id}/public and /open) must NOT leak it.
        _, admin_raw = await _admin_token()
        async with client_for(app, admin_raw) as c:
            qid = await _open_qualifier(c, name='Config Guard')
            admin_get = await c.get(f'/api/async-qualifiers/{qid}')
            assert admin_get.status_code == 200
            assert 'config' in admin_get.json()
        _, plain_raw = await create_user_token(username='config-peeker')
        async with client_for(app, plain_raw) as c:
            pub = await c.get(f'/api/async-qualifiers/{qid}/public')
            assert pub.status_code == 200
            assert pub.json()['name'] == 'Config Guard'
            assert 'config' not in pub.json()

            open_list = await c.get('/api/async-qualifiers/open')
            assert open_list.status_code == 200
            rows = [q for q in open_list.json() if q['id'] == qid]
            assert rows and 'config' not in rows[0]


class TestRunLifecycle:
    async def test_start_then_submit_own_run(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            pool_id = await _pool_with_permalink(c, qid)

            start = await c.post(f'/api/async-qualifiers/{qid}/runs', json={'pool_id': pool_id})
            assert start.status_code == 201, start.text
            run_id = start.json()['id']
            assert start.json()['status'] == 'in_progress'

            active = await c.get(f'/api/async-qualifiers/{qid}/me/active-run')
            assert active.status_code == 200
            assert active.json()['id'] == run_id

            await _backdate(run_id, 1200)
            submit = await c.post(
                f'/api/async-qualifiers/runs/{run_id}/submit', json={'elapsed_seconds': 1200},
            )
            assert submit.status_code == 200, submit.text
            assert submit.json()['status'] == 'finished'
            assert submit.json()['review_status'] == 'pending'

            my_runs = await c.get(f'/api/async-qualifiers/{qid}/me/runs')
            assert run_id in {r['id'] for r in my_runs.json()}

    async def test_submit_refuses_a_time_longer_than_the_run(self, db, app):
        """The server timed the run; a claim above that wall clock is impossible."""
        _, raw = await _admin_token(username='qadmin_drift')
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            pool_id = await _pool_with_permalink(c, qid)
            start = await c.post(f'/api/async-qualifiers/{qid}/runs', json={'pool_id': pool_id})
            run_id = start.json()['id']

            await _backdate(run_id, 900)
            resp = await c.post(
                f'/api/async-qualifiers/runs/{run_id}/submit', json={'elapsed_seconds': 4462},
            )
            assert resp.status_code == 400, resp.text
            assert 'longer than the run itself' in resp.json()['detail']

            active = await c.get(f'/api/async-qualifiers/{qid}/me/active-run')
            assert active.json()['id'] == run_id

            ok = await c.post(
                f'/api/async-qualifiers/runs/{run_id}/submit', json={'elapsed_seconds': 880},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()['measured_seconds'] >= 900

    async def test_self_review_blocked(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            pool_id = await _pool_with_permalink(c, qid)
            start = await c.post(f'/api/async-qualifiers/{qid}/runs', json={'pool_id': pool_id})
            run_id = start.json()['id']
            await _backdate(run_id, 900)
            await c.post(f'/api/async-qualifiers/runs/{run_id}/submit', json={'elapsed_seconds': 900})

            review = await c.post(
                f'/api/async-qualifiers/runs/{run_id}/review', json={'approved': True},
            )
            assert review.status_code == 400
            assert 'own run' in review.json()['detail'].lower()

    async def test_reject_without_a_reason_is_a_400(self, db, app):
        _, raw = await _admin_token(username='qadmin_rej')
        _, runner_raw = await create_user_token(username='rejected_runner')
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            pool_id = await _pool_with_permalink(c, qid)
        async with client_for(app, runner_raw) as rc:
            start = await rc.post(f'/api/async-qualifiers/{qid}/runs', json={'pool_id': pool_id})
            run_id = start.json()['id']
            await _backdate(run_id, 900)
            await rc.post(f'/api/async-qualifiers/runs/{run_id}/submit',
                          json={'elapsed_seconds': 900})
        async with client_for(app, raw) as c:
            blank = await c.post(f'/api/async-qualifiers/runs/{run_id}/review',
                                 json={'approved': False})
            assert blank.status_code == 400, blank.text
            assert 'needs a reason' in blank.json()['detail']

            ok = await c.post(f'/api/async-qualifiers/runs/{run_id}/review',
                              json={'approved': False, 'note': 'VoD ends early.'})
            assert ok.status_code == 200, ok.text
            assert ok.json()['review_status'] == 'rejected'

            notes = await c.get(f'/api/async-qualifiers/runs/{run_id}/notes')
            assert [n['note'] for n in notes.json()] == ['VoD ends early.']

    async def test_grant_reattempt_and_the_runs_list(self, db, app):
        """A forfeit never reaches the review queue, so the runs list is how a
        reviewer finds one — and the grant ignores the runner's allowance."""
        _, raw = await _admin_token(username='qadmin_grant')
        _, runner_raw = await create_user_token(username='forfeit_runner')
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            pool_id = await _pool_with_permalink(c, qid)
        async with client_for(app, runner_raw) as rc:
            start = await rc.post(f'/api/async-qualifiers/{qid}/runs', json={'pool_id': pool_id})
            run_id = start.json()['id']
            await rc.post(f'/api/async-qualifiers/runs/{run_id}/forfeit')

            # The runner has no allowance of their own (default 0).
            own = await rc.post(f'/api/async-qualifiers/runs/{run_id}/reattempt',
                                json={'reason': 'mis-click'})
            assert own.status_code == 400
            assert 'No reattempts remaining' in own.json()['detail']

            forbidden = await rc.get(f'/api/async-qualifiers/{qid}/runs')
            assert forbidden.status_code == 403

        async with client_for(app, raw) as c:
            queue = await c.get(f'/api/async-qualifiers/{qid}/review-queue')
            assert queue.json() == []
            runs = await c.get(f'/api/async-qualifiers/{qid}/runs')
            page = runs.json()
            assert [r['id'] for r in page['items']] == [run_id]
            assert (page['total'], page['offset']) == (1, 0)

            # A page past the end is empty and still reports the total, so a client
            # paging through knows it has reached the end rather than guessing.
            beyond = await c.get(f'/api/async-qualifiers/{qid}/runs?limit=1&offset=5')
            assert beyond.json()['items'] == []
            assert beyond.json()['total'] == 1

            blank = await c.post(f'/api/async-qualifiers/runs/{run_id}/grant-reattempt',
                                 json={'reason': ' '})
            assert blank.status_code == 400

            granted = await c.post(f'/api/async-qualifiers/runs/{run_id}/grant-reattempt',
                                   json={'reason': 'mis-click, confirmed on stream'})
            assert granted.status_code == 200, granted.text
            assert granted.json()['reattempted'] is True

        async with client_for(app, runner_raw) as rc:
            pools = await rc.get(f'/api/async-qualifiers/{qid}/pools/available')
            assert [p['id'] for p in pools.json()] == [pool_id]

    async def test_review_queue_forbidden_for_non_admin(self, db, app):
        _, admin_raw = await _admin_token()
        async with client_for(app, admin_raw) as c:
            qid = await _open_qualifier(c)
        _, plain_raw = await create_user_token(username='plain')
        async with client_for(app, plain_raw) as c:
            resp = await c.get(f'/api/async-qualifiers/{qid}/review-queue')
            assert resp.status_code == 403

    async def test_review_queue_ok_for_admin(self, db, app):
        _, raw = await _admin_token()
        async with client_for(app, raw) as c:
            qid = await _open_qualifier(c)
            resp = await c.get(f'/api/async-qualifiers/{qid}/review-queue')
            assert resp.status_code == 200
            assert resp.json() == []


class TestLeaderboard:
    async def test_hidden_while_open_for_non_admin(self, db, app):
        _, admin_raw = await _admin_token()
        async with client_for(app, admin_raw) as c:
            qid = await _open_qualifier(c, name='Locked')
            admin_board = await c.get(f'/api/async-qualifiers/{qid}/leaderboard')
            assert admin_board.status_code == 200
        _, plain_raw = await create_user_token(username='peeker')
        async with client_for(app, plain_raw) as c:
            resp = await c.get(f'/api/async-qualifiers/{qid}/leaderboard')
            assert resp.status_code == 403


# --- cross-tenant isolation ----------------------------------------------

class TestTenantIsolation:
    @pytest.fixture
    async def two_tenants(self, db):
        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')
        with tenant_scope(a.id):
            qa = await AsyncQualifier.create(name='A Cup', is_active=True)
        with tenant_scope(b.id):
            await enable_all_features(b.id)
            _, token_b = await create_user_token(username='b-admin', roles=[Role.QUALIFIER_ADMIN])
        return {'app': build_api_app(), 'token_b': token_b, 'qa': qa}

    async def test_other_tenant_qualifier_is_404(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get(f"/api/async-qualifiers/{ctx['qa'].id}")
            assert resp.status_code == 404

    async def test_list_omits_other_tenant_row(self, two_tenants):
        ctx = two_tenants
        async with client_for(ctx['app'], ctx['token_b']) as c:
            resp = await c.get('/api/async-qualifiers')
            assert resp.status_code == 200
            assert ctx['qa'].id not in {q['id'] for q in resp.json()}
