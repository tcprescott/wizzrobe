"""REST API tests for the feedback endpoints (api/routers/feedback.py).

Submitting and reading your own submissions are open to any member; the queue
is staff. Marking reviewed is re-gated by ``FeedbackService`` at
``can_view_admin``, which is wider than STAFF — asserted here so the two do not
quietly converge.
"""

from application.tenant_context import tenant_scope
from models import (
    FeatureFlag,
    Feedback,
    FeedbackStatus,
    Role,
    TenantFeatureFlag,
    Tournament,
)
from tests.api_helpers import client_for, create_user_token


async def _member_token(**kwargs):
    """A token for someone who belongs to the ambient community — the feedback
    queue takes submissions from members only."""
    return await create_user_token(**kwargs)


class TestSubmit:
    async def test_any_member_can_submit_and_read_it_back(self, db, app):
        user, raw = await _member_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/feedback',
                json={'message': 'the schedule page is slow', 'category': 'bug',
                      'page_url': '/home/schedule'},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body['message'] == 'the schedule page is slow'
            assert body['category'] == 'bug'
            assert body['status'] == FeedbackStatus.NEW.value
            assert body['submitted_by_id'] == user.id
            assert body['submitted_by_name'] == user.preferred_name

            mine = (await c.get('/api/feedback/mine')).json()
            assert [row['id'] for row in mine] == [body['id']]

    async def test_a_non_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='stranger', member=False)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/feedback', json={'message': 'let me in'})
            assert resp.status_code == 403
            assert 'member' in resp.json()['detail']

    async def test_an_empty_message_is_400(self, db, app):
        _, raw = await _member_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/feedback', json={'message': '   '})
            assert resp.status_code == 400

    async def test_an_unknown_category_falls_back_to_other(self, db, app):
        _, raw = await _member_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.post(
                '/api/feedback', json={'message': 'hi', 'category': 'nonsense'},
            )).status_code == 422

    async def test_read_only_token_cannot_submit(self, db, app):
        _, raw = await _member_token(username='plain', read_only=True)
        async with client_for(app, raw) as c:
            assert (await c.post('/api/feedback', json={'message': 'hi'})).status_code == 403

    async def test_mine_shows_only_your_own(self, db, app):
        _, mine = await _member_token(username='a')
        _, theirs = await _member_token(username='b')
        async with client_for(app, mine) as c:
            await c.post('/api/feedback', json={'message': 'from a'})
        async with client_for(app, theirs) as c:
            assert (await c.get('/api/feedback/mine')).json() == []

    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            assert (await c.post('/api/feedback', json={'message': 'hi'})).status_code == 401


class TestQueue:
    async def test_staff_reads_the_queue_newest_first(self, db, app):
        submitter, sub_token = await _member_token(username='plain')
        _, staff_token = await _member_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, sub_token) as c:
            await c.post('/api/feedback', json={'message': 'first'})
            await c.post('/api/feedback', json={'message': 'second'})
        async with client_for(app, staff_token) as c:
            rows = (await c.get('/api/feedback')).json()
            assert [row['message'] for row in rows] == ['second', 'first']
            assert rows[0]['submitted_by_name'] == submitter.preferred_name

    async def test_a_plain_member_cannot_read_the_queue(self, db, app):
        _, raw = await _member_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/feedback')).status_code == 403


class TestReview:
    async def test_staff_marks_reviewed_and_reopens(self, db, app):
        _, sub_token = await _member_token(username='plain')
        _, staff_token = await _member_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, sub_token) as c:
            row_id = (await c.post('/api/feedback', json={'message': 'hi'})).json()['id']
        async with client_for(app, staff_token) as c:
            resp = await c.post(f'/api/feedback/{row_id}/reviewed', json={'reviewed': True})
            assert resp.status_code == 200
            assert resp.json()['status'] == FeedbackStatus.REVIEWED.value
            assert resp.json()['submitted_by_name'] is not None

            reopened = await c.post(f'/api/feedback/{row_id}/reviewed', json={'reviewed': False})
            assert reopened.json()['status'] == FeedbackStatus.NEW.value

    async def test_a_tournament_admin_cannot_review(self, db, app):
        """Staff only, like the queue: the response carries the whole
        submission, so a wider gate here read the queue one id at a time."""
        _, sub_token = await _member_token(username='plain')
        ta, ta_token = await _member_token(username='ta')
        tournament = await Tournament.create(name='Cup')
        await tournament.admins.add(ta)
        async with client_for(app, sub_token) as c:
            row_id = (await c.post('/api/feedback', json={'message': 'hi'})).json()['id']
        async with client_for(app, ta_token) as c:
            resp = await c.post(f'/api/feedback/{row_id}/reviewed', json={'reviewed': True})
            assert resp.status_code == 403
            assert (await c.get('/api/feedback')).status_code == 403

    async def test_a_plain_member_cannot_review(self, db, app):
        _, raw = await _member_token(username='plain')
        async with client_for(app, raw) as c:
            row_id = (await c.post('/api/feedback', json={'message': 'hi'})).json()['id']
            resp = await c.post(f'/api/feedback/{row_id}/reviewed', json={'reviewed': True})
            assert resp.status_code == 403

    async def test_unknown_id_is_404(self, db, app):
        _, raw = await _member_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/feedback/9999/reviewed', json={'reviewed': True})
            assert resp.status_code == 404


class TestFeatureGate:
    async def test_router_404s_when_the_flag_is_off(self, db, app):
        await TenantFeatureFlag.filter(
            tenant_id=1, flag=FeatureFlag.FEEDBACK.value,
        ).update(available=False, enabled=False)
        _, raw = await _member_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/feedback')).status_code == 404
            assert (await c.post('/api/feedback', json={'message': 'hi'})).status_code == 404


class TestTenantIsolation:
    async def test_the_queue_never_crosses_communities(self, app, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            _, a_user_token = await _member_token(username='a-plain')
        with tenant_scope(tenant_b.id):
            _, b_staff_token = await _member_token(username='b-staff', roles=[Role.STAFF])

        async with client_for(app, a_user_token) as c:
            row_id = (await c.post('/api/feedback', json={'message': 'a-only'})).json()['id']
        async with client_for(app, b_staff_token) as c:
            assert (await c.get('/api/feedback')).json() == []
            resp = await c.post(f'/api/feedback/{row_id}/reviewed', json={'reviewed': True})
            assert resp.status_code == 404
        assert (await Feedback.get(id=row_id)).status == FeedbackStatus.NEW
