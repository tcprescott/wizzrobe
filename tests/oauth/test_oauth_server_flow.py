"""The third-party app authorization server, end to end.

authorize → consent → token → API → refresh → revoke against the real routes,
with the consent screen replaced by a direct service call (the page is W3).
The assertions that matter most are about reuse and narrowing: a code that
works twice, a refresh token that survives rotation, a grant that does more
than the person approved, or an app token accepted somewhere it wasn't minted
for each turn one leak into standing access.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from application.services import McpAuthService, OAuthAppService, OAuthGrantService
from application.services.api_token_service import ApiTokenService
from application.services.feature_flag_service import reset_flag_cache
from models import ApiToken, ApiTokenOrigin, FeatureFlag, OAuthClientType, Role, TenantFeatureFlag, User
from tests.api_helpers import create_community_member, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.oauth.conftest import (
    REDIRECT,
    authorize_code,
    basic,
    client,
    exchange,
    grant_tokens,
    make_app,
    pkce,
    query,
    start,
)

pytestmark = pytest.mark.usefixtures('db')

TENANT = DEFAULT_TEST_TENANT_ID


async def _owner_and_member():
    owner = await create_community_member(username='dev')
    member = await create_community_member(username='player')
    return owner, member


async def _flag_off(tenant_id: int = TENANT) -> None:
    await TenantFeatureFlag.filter(tenant_id=tenant_id, flag=FeatureFlag.THIRD_PARTY_APPS.value).update(
        enabled=False,
    )
    # The flag cache is a contextvar the direct service calls above already filled.
    reset_flag_cache()


async def test_metadata_is_served_at_the_path_insertion_location():
    async with client() as http:
        resp = await http.get('/.well-known/oauth-authorization-server/oauth')
    assert resp.status_code == 200
    doc = resp.json()
    assert doc['issuer'].endswith('/oauth')
    assert doc['token_endpoint'] == doc['issuer'] + '/token'
    assert doc['code_challenge_methods_supported'] == ['S256']
    assert 'client_credentials' not in doc['grant_types_supported']
    assert 'staff:write' in doc['scopes_supported']


async def test_unknown_client_and_unregistered_redirect_never_redirect():
    owner, _ = await _owner_and_member()
    app, _ = await make_app(owner)
    _, challenge = pkce()
    async with client() as http:
        unknown = await http.get('/oauth/authorize', params={
            'client_id': 'wza_nope', 'redirect_uri': REDIRECT, 'response_type': 'code',
            'code_challenge': challenge, 'code_challenge_method': 'S256',
        })
        evil = await http.get('/oauth/authorize', params={
            'client_id': app.client_id, 'redirect_uri': 'https://evil.example/cb', 'response_type': 'code',
            'code_challenge': challenge, 'code_challenge_method': 'S256',
        })
    assert unknown.status_code == 400 and 'location' not in unknown.headers
    assert evil.status_code == 400 and 'location' not in evil.headers
    assert 'evil.example' not in evil.text


@pytest.mark.parametrize('params, error', [
    ({'code_challenge_method': None}, 'invalid_request'),
    ({'code_challenge_method': 'plain'}, 'invalid_request'),
    ({'response_type': 'token'}, 'unsupported_response_type'),
    ({'scope': 'staff:write'}, 'invalid_scope'),
    ({'scope': 'admin'}, 'invalid_scope'),
])
async def test_bad_requests_from_a_verified_client_go_back_to_it(params, error):
    owner, _ = await _owner_and_member()
    app, _ = await make_app(owner)
    _, challenge = pkce()
    base = {
        'client_id': app.client_id, 'redirect_uri': REDIRECT, 'response_type': 'code',
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': 'abc',
    }
    base.update(params)
    async with client() as http:
        resp = await http.get('/oauth/authorize', params={k: v for k, v in base.items() if v is not None})
    assert resp.status_code == 302
    assert resp.headers['location'].startswith(REDIRECT)
    back = query(resp.headers['location'])
    assert back['error'] == error and back['state'] == 'abc' and back['iss'].endswith('/oauth')


async def test_a_disabled_app_cannot_start_a_sign_in():
    owner, _ = await _owner_and_member()
    app, _ = await make_app(owner)
    admin = await User.create(discord_id=999, username='root')
    from models import UserRole
    await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
    await OAuthAppService().disable_app(admin, app.id, 'spam')
    _, challenge = pkce()
    async with client() as http:
        resp = await http.get('/oauth/authorize', params={
            'client_id': app.client_id, 'redirect_uri': REDIRECT, 'response_type': 'code',
            'code_challenge': challenge, 'code_challenge_method': 'S256',
        })
    assert resp.status_code == 400


async def test_consent_drops_staff_scopes_for_a_player_and_keeps_them_for_staff():
    owner, member = await _owner_and_member()
    staff, _ = await create_user_token(username='boss', roles=[Role.STAFF])
    app, _ = await make_app(owner)
    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    service = OAuthGrantService()
    player_view = await service.consent_view(txn, member)
    staff_view = await service.consent_view(txn, staff)
    assert player_view.redirect_host == 'app.example.com'
    assert player_view.verified is False
    assert [c.tenant_id for c in player_view.communities] == [TENANT]
    assert {s.value for s in player_view.communities[0].scopes} == {'profile', 'schedule:read', 'play'}
    assert 'staff:read' in {s.value for s in staff_view.communities[0].scopes}
    with pytest.raises(ValueError):
        await service.approve(txn, member, TENANT, ['staff:read'])


async def test_consent_hides_communities_with_the_flag_off():
    owner, member = await _owner_and_member()
    app, _ = await make_app(owner)
    await _flag_off()
    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    view = await OAuthGrantService().consent_view(txn, member)
    assert view.communities == []
    with pytest.raises(ValueError):
        await OAuthGrantService().approve(txn, member, TENANT, ['profile'])


async def test_deny_sends_access_denied_and_spends_the_request():
    owner, _ = await _owner_and_member()
    app, _ = await make_app(owner)
    _, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge)
    back = query(OAuthGrantService().deny(txn))
    assert back['error'] == 'access_denied' and back['state'] == 'st4te'
    with pytest.raises(ValueError):
        OAuthGrantService().get_pending(txn)


async def test_confidential_app_full_flow_and_grant_is_narrowed():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile', 'schedule:read'])
    assert tokens['access_token'].startswith('wizzrobe_app_')
    assert tokens['refresh_token'].startswith('wizzrobe_appref_')
    assert tokens['scope'] == 'profile schedule:read'
    assert tokens['expires_in'] == 3600

    grant = await ApiToken.get(token_hash=hashlib.sha256(tokens['access_token'].encode()).hexdigest())
    assert grant.origin == ApiTokenOrigin.APP.value
    assert grant.tenant_id == TENANT
    assert grant.read_only is True

    async with client(tokens['access_token']) as api:
        me = await api.get('/api/users/me')
        write = await api.post('/api/tokens', json={'name': 'nope'})
    assert me.status_code == 200 and me.json()['id'] == member.id
    assert write.status_code == 403


async def test_client_secret_post_also_works():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile'], challenge)
        resp = await http.post('/oauth/token', data={
            'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier,
            'redirect_uri': REDIRECT, 'client_id': app.client_id, 'client_secret': secret,
        })
    assert resp.status_code == 200, resp.text
    assert resp.headers['cache-control'] == 'no-store'


async def test_public_app_uses_pkce_alone_and_may_not_send_a_secret():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner, client_type=OAuthClientType.PUBLIC,
                                 redirect_uris=['http://127.0.0.1:5000/cb'], scopes=['profile', 'crew'])
    assert secret is None
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile', 'crew'], challenge,
                                    redirect_uri='http://127.0.0.1:5000/cb')
        with_secret = await http.post('/oauth/token', data={
            'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier,
            'redirect_uri': 'http://127.0.0.1:5000/cb', 'client_id': app.client_id, 'client_secret': 'x',
        })
        ok = await exchange(http, app, None, code, verifier, redirect_uri='http://127.0.0.1:5000/cb')
    assert with_secret.status_code == 401 and with_secret.json()['error'] == 'invalid_client'
    assert ok.status_code == 200
    grant = await ApiToken.get(token_hash=hashlib.sha256(ok.json()['access_token'].encode()).hexdigest())
    assert grant.read_only is False


async def test_confidential_app_must_authenticate():
    owner, member = await _owner_and_member()
    app, _ = await make_app(owner)
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile'], challenge)
        missing = await exchange(http, app, None, code, verifier)
        wrong = await exchange(http, app, 'wzs_wrong', code, verifier)
    assert missing.status_code == 401 and wrong.status_code == 401
    assert 'Basic' in wrong.headers['www-authenticate']


async def test_wrong_verifier_and_wrong_redirect_are_refused():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    verifier, challenge = pkce()
    other_verifier, _ = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile'], challenge)
        bad_pkce = await exchange(http, app, secret, code, other_verifier)
        bad_redirect = await exchange(http, app, secret, code, verifier, redirect_uri='https://app.example.com/other')
        ok = await exchange(http, app, secret, code, verifier)
    assert bad_pkce.json()['error'] == 'invalid_grant'
    assert bad_redirect.json()['error'] == 'invalid_grant'
    assert ok.status_code == 200


async def test_a_replayed_code_fails_and_revokes_what_it_issued():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile'], challenge)
        first = await exchange(http, app, secret, code, verifier)
        second = await exchange(http, app, secret, code, verifier)
    assert first.status_code == 200
    assert second.json()['error'] == 'invalid_grant'
    async with client(first.json()['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401


async def test_another_apps_code_is_useless():
    owner, member = await _owner_and_member()
    app, _ = await make_app(owner)
    other, other_secret = await make_app(owner)
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile'], challenge)
        resp = await exchange(http, other, other_secret, code, verifier)
    assert resp.json()['error'] == 'invalid_grant'


async def test_approving_again_replaces_the_old_grant():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        first = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
        await grant_tokens(http, app, secret, member, TENANT, ['profile', 'play'])
    live = await ApiToken.filter(oauth_app_id=app.id, user_id=member.id, revoked_at=None).count()
    assert live == 1
    async with client(first['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401


async def _refresh(http, app, secret, refresh_token, scope=None):
    data = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
    if scope is not None:
        data['scope'] = scope
    return await http.post('/oauth/token', data=data, headers=basic(app.client_id, secret))


async def test_refresh_rotates_both_halves():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile', 'play'])
        resp = await _refresh(http, app, secret, tokens['refresh_token'])
    fresh = resp.json()
    assert resp.status_code == 200
    assert fresh['refresh_token'] != tokens['refresh_token']
    async with client(tokens['access_token']) as old:
        assert (await old.get('/api/users/me')).status_code == 401
    async with client(fresh['access_token']) as new:
        assert (await new.get('/api/users/me')).status_code == 200


async def test_reusing_a_spent_refresh_token_revokes_the_grant():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
        fresh = (await _refresh(http, app, secret, tokens['refresh_token'])).json()
        replay = await _refresh(http, app, secret, tokens['refresh_token'])
        after = await _refresh(http, app, secret, fresh['refresh_token'])
    assert replay.json()['error'] == 'invalid_grant'
    assert after.json()['error'] == 'invalid_grant'
    async with client(fresh['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401
    from models import AuditLog
    assert await AuditLog.filter(action='oauth_grant.refresh_reused').exists()


async def test_refresh_may_narrow_but_never_widen():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile', 'play'])
        wider = await _refresh(http, app, secret, tokens['refresh_token'], scope='profile play schedule:read')
        narrower = await _refresh(http, app, secret, tokens['refresh_token'], scope='profile')
    assert wider.json()['error'] == 'invalid_scope'
    assert narrower.status_code == 200 and narrower.json()['scope'] == 'profile'
    grant = await ApiToken.get(token_hash=hashlib.sha256(narrower.json()['access_token'].encode()).hexdigest())
    assert grant.scope == 'profile' and grant.read_only is True


async def test_refresh_stops_when_the_member_leaves():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
        from models import TenantMembership
        await TenantMembership.filter(user_id=member.id, tenant_id=TENANT).delete()
        resp = await _refresh(http, app, secret, tokens['refresh_token'])
    assert resp.json()['error'] == 'invalid_grant'


async def test_revoke_endpoint_ends_the_grant_with_either_half():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
        resp = await http.post('/oauth/revoke', data={'token': tokens['refresh_token']},
                               headers=basic(app.client_id, secret))
        unknown = await http.post('/oauth/revoke', data={'token': 'nonsense'},
                                  headers=basic(app.client_id, secret))
    assert resp.status_code == 200 and unknown.status_code == 200
    async with client(tokens['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401


async def test_flag_off_stops_existing_grants_without_revoking_them():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
        await _flag_off()
        refresh = await _refresh(http, app, secret, tokens['refresh_token'])
    async with client(tokens['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 404
    assert refresh.json()['error'] == 'invalid_grant'
    assert await ApiToken.filter(oauth_app_id=app.id, revoked_at=None).exists()


async def test_disabled_app_stops_its_grants_and_re_enabling_restores_them():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    admin = await User.create(discord_id=4242, username='root')
    from models import UserRole
    await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
    await OAuthAppService().disable_app(admin, app.id, 'abuse report')
    async with client(tokens['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401
    await OAuthAppService().enable_app(admin, app.id)
    async with client(tokens['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 200


async def test_an_app_token_is_refused_by_the_mcp_server():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
    assert await McpAuthService().load_access_token(tokens['access_token']) is None
    from tests.mcp.conftest import MCP_HEADERS, mcp_session
    async with mcp_session() as mcp:
        resp = await mcp.post('/mcp', headers={**MCP_HEADERS, 'Authorization': f'Bearer {tokens["access_token"]}'},
                              json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert resp.status_code == 401


async def test_an_expired_code_is_refused():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    verifier, challenge = pkce()
    async with client() as http:
        code = await authorize_code(http, app, member, TENANT, ['profile'], challenge)
        from models import OAuthAppCode
        await OAuthAppCode.filter(code_hash=hashlib.sha256(code.encode()).hexdigest()).update(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        resp = await exchange(http, app, secret, code, verifier)
    assert resp.json()['error'] == 'invalid_grant'


async def test_profile_revoke_covers_app_grants():
    owner, member = await _owner_and_member()
    app, secret = await make_app(owner)
    async with client() as http:
        tokens = await grant_tokens(http, app, secret, member, TENANT, ['profile'])
    listed = await ApiTokenService().list_tokens(member)
    assert [t.origin for t in listed] == [ApiTokenOrigin.APP.value]
    await ApiTokenService().revoke_token(member, listed[0].id)
    async with client(tokens['access_token']) as api:
        assert (await api.get('/api/users/me')).status_code == 401
