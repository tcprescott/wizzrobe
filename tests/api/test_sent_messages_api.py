"""GET /api/sent-messages: Staff only, and a DM's text never leaves its community."""

from application.tenant_context import tenant_scope
from models import Role, SentMessage, SentMessageStatus
from tests.api_helpers import client_for, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID


async def test_non_staff_are_refused(db, app):
    _, raw = await create_user_token(username='plain', roles=[Role.PROCTOR])
    async with client_for(app, raw) as c:
        assert (await c.get('/api/sent-messages')).status_code == 403


async def test_staff_read_their_community_with_filters(db, app):
    staff, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    await SentMessage.create(
        tenant_id=DEFAULT_TEST_TENANT_ID, recipient=staff, discord_id='123456789012345678',
        title='Stage call', body='Stage call: Stage 2 in 10 minutes', status=SentMessageStatus.DELIVERED,
    )
    await SentMessage.create(
        tenant_id=DEFAULT_TEST_TENANT_ID, discord_id='2', title='Shift reminder', body='Your shift',
        status=SentMessageStatus.FAILED, error='timed out',
    )
    async with client_for(app, raw) as c:
        body = (await c.get('/api/sent-messages')).json()
        assert body['total'] == 2
        assert body['counts'] == {'sent': 2, 'silent': 0, 'off_or_blocked': 0, 'failed': 1}
        stage = next(i for i in body['items'] if i['title'] == 'Stage call')
        assert stage['discord_id'] == 123456789012345678
        assert stage['recipient_name'] == staff.preferred_name

        problems = (await c.get('/api/sent-messages', params={'problems_only': True})).json()
        assert [i['error'] for i in problems['items']] == ['timed out']
        assert (await c.get('/api/sent-messages', params={'search': 'stage 2'})).json()['total'] == 1


async def test_other_communities_messages_stay_hidden(db, app, two_tenants):
    _, b = two_tenants
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    with tenant_scope(b.id):
        await SentMessage.create(tenant_id=b.id, discord_id='9', title='B only', body='private to B')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/sent-messages')).json()['total'] == 0
