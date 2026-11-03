"""GET /api/users/{id}/details: Staff only, and only for members of this community."""

from models import Role
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member, make_user


async def test_non_staff_are_refused(db, app):
    _, raw = await create_user_token(username='plain', roles=[Role.PROCTOR])
    member = await make_member(discord_id=7101, username='p')
    async with client_for(app, raw) as c:
        assert (await c.get(f'/api/users/{member.id}/details')).status_code == 403


async def test_staff_read_a_member(db, app):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    member = await make_member(discord_id=123456789012345678, username='aerinon', email='a@example.com')
    async with client_for(app, raw) as c:
        body = (await c.get(f'/api/users/{member.id}/details')).json()
    assert body['username'] == 'aerinon'
    assert body['discord_id'] == 123456789012345678
    assert body['preset'] == 'everything'
    assert body['notifications'] and {'community', 'theirs', 'effective'} <= set(body['notifications'][0])
    assert 'a@example.com' not in str(body)


async def test_an_outsider_is_not_found(db, app):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    outsider = await make_user(discord_id=7102, username='outsider')
    async with client_for(app, raw) as c:
        assert (await c.get(f'/api/users/{outsider.id}/details')).status_code == 404
