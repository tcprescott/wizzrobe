"""Registering and managing third-party apps.

Everything an owner types is player input under the threat model: plain text,
tight caps, and redirect addresses that can't run script or point a
confidential app at a custom scheme.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import NotFoundError
from application.services import OAuthAppService
from application.services.oauth_app_service import MAX_APPS_PER_OWNER, secret_matches
from models import AuditLog, OAuthApp, OAuthClientType, Role, User, UserRole
from tests.api_helpers import create_community_member
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.oauth.conftest import client, grant_tokens, make_app

pytestmark = pytest.mark.usefixtures('db')


async def _create(owner, **overrides):
    fields = dict(
        name='My App', client_type=OAuthClientType.CONFIDENTIAL,
        redirect_uris=['https://app.example.com/cb'], allowed_scopes=['profile'],
    )
    fields.update(overrides)
    return await OAuthAppService().create_app(owner, **fields)


async def test_confidential_app_gets_a_secret_shown_once():
    owner = await create_community_member(username='dev')
    app, secret = await _create(owner)
    assert app.client_id.startswith('wza_')
    assert secret.startswith('wzs_')
    assert app.client_secret_hash and secret not in app.client_secret_hash
    assert secret_matches(app, secret)
    assert await AuditLog.filter(action='oauth_app.created').exists()


async def test_public_app_has_no_secret_and_may_use_an_app_scheme():
    owner = await create_community_member(username='dev')
    app, secret = await _create(
        owner, client_type='public', redirect_uris=['com.example.app:/cb', 'http://localhost:1234/cb'],
    )
    assert secret is None and app.client_secret_hash is None


@pytest.mark.parametrize('uris', [
    ['javascript:alert(1)'],
    ['https://app.example.com/cb#frag'],
    ['http://app.example.com/cb'],
    ['com.example.app:/cb'],
    ['https://user:pw@app.example.com/cb'],
    [],
    [f'https://app.example.com/{i}' for i in range(11)],
])
async def test_bad_redirect_addresses_are_refused_for_a_confidential_app(uris):
    owner = await create_community_member(username='dev')
    with pytest.raises(ValueError):
        await _create(owner, redirect_uris=uris)


async def test_text_fields_are_plain_and_capped():
    owner = await create_community_member(username='dev')
    app, _ = await _create(owner, name='  Bot‮\n  Name ', description='line one\nline two')
    assert app.name == 'Bot Name'
    assert app.description == 'line one line two'
    with pytest.raises(ValueError):
        await _create(owner, name='x' * 61)
    with pytest.raises(ValueError):
        await _create(owner, name='   ')
    with pytest.raises(ValueError):
        await _create(owner, homepage_url='http://insecure.example')
    with pytest.raises(ValueError):
        await _create(owner, allowed_scopes=['profile', 'everything'])
    with pytest.raises(ValueError):
        await _create(owner, allowed_scopes=[])


async def test_each_person_may_register_ten_apps():
    owner = await create_community_member(username='dev')
    for i in range(MAX_APPS_PER_OWNER):
        await _create(owner, name=f'App {i}')
    with pytest.raises(ValueError):
        await _create(owner, name='One too many')


async def test_someone_elses_app_reads_as_missing():
    owner = await create_community_member(username='dev')
    stranger = await create_community_member(username='stranger')
    app, _ = await _create(owner)
    service = OAuthAppService()
    for call in (
        service.get_my_app(stranger, app.id),
        service.update_app(stranger, app.id, name='Mine now'),
        service.rotate_secret(stranger, app.id),
        service.delete_app(stranger, app.id),
        service.connected_user_count(stranger, app.id),
    ):
        with pytest.raises(NotFoundError):
            await call


async def test_rotation_keeps_the_old_secret_for_a_day():
    owner = await create_community_member(username='dev')
    app, old = await _create(owner)
    new = await OAuthAppService().rotate_secret(owner, app.id)
    app = await OAuthApp.get(id=app.id)
    assert secret_matches(app, new)
    assert secret_matches(app, old)
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    assert not secret_matches(app, old, now=later)
    assert secret_matches(app, new, now=later)


async def test_public_app_has_nothing_to_rotate():
    owner = await create_community_member(username='dev')
    app, _ = await _create(owner, client_type='public', redirect_uris=['http://127.0.0.1/cb'])
    with pytest.raises(ValueError):
        await OAuthAppService().rotate_secret(owner, app.id)


async def test_update_revalidates_and_redirects_follow_the_client_type():
    owner = await create_community_member(username='dev')
    app, _ = await _create(owner)
    updated = await OAuthAppService().update_app(owner, app.id, name='Renamed', homepage_url='')
    assert updated.name == 'Renamed' and updated.homepage_url is None
    with pytest.raises(ValueError):
        await OAuthAppService().update_app(owner, app.id, redirect_uris=['com.example.app:/cb'])


async def test_delete_ends_every_grant_and_owner_sees_only_a_count():
    owner = await create_community_member(username='dev')
    member = await create_community_member(username='player')
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, DEFAULT_TEST_TENANT_ID, ['profile'])
    assert await OAuthAppService().connected_user_count(owner, app.id) == 1
    assert await OAuthAppService().delete_app(owner, app.id) == 1
    assert not await OAuthApp.filter(id=app.id).exists()
    async with client(tokens['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401


async def test_only_a_platform_admin_disables_an_app():
    owner = await create_community_member(username='dev')
    staff = await create_community_member(username='staff')
    await UserRole.create(user=staff, role=Role.STAFF)
    app, _ = await _create(owner)
    with pytest.raises(PermissionError):
        await OAuthAppService().disable_app(staff, app.id, 'no')
    with pytest.raises(PermissionError):
        await OAuthAppService().disable_app(owner, app.id, 'no')
    admin = await User.create(discord_id=31337, username='root')
    await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
    disabled = await OAuthAppService().disable_app(admin, app.id, 'spam')
    assert disabled.disabled_at is not None and disabled.disabled_reason == 'spam'
