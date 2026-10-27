"""App-token scopes on the REST API: every route is mapped, the mapping agrees
with each route's own gate, and the check refuses what a grant doesn't cover
while leaving personal access tokens exactly as they were."""

from typing import Callable, Iterator, List, Optional, Tuple

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from api.dependencies import (
    require_admin,
    require_api_actor,
    require_staff,
    require_staff_write,
    require_super_admin,
    require_super_admin_write,
    require_volunteer_admin,
    resolve_token,
    tenant_context_scope,
)
from api.scopes import PAT_ONLY, PUBLIC, READ_ONLY_POSTS, ROUTE_SCOPES, route_key
from application.api_scopes import (
    ALL_SCOPES,
    READ_SCOPES,
    ApiScope,
    format_scope,
    parse_scope,
)
from application.services import OAuthAppService
from models import ApiToken, OAuthClientType, Role
from tests.api_helpers import build_api_app, client_for, create_user_token


def _calls(dependant: Dependant) -> Iterator[Callable]:
    for dep in dependant.dependencies:
        if dep.call is not None:
            yield dep.call
        yield from _calls(dep)


def _routes() -> Iterator[Tuple[str, RouteContext, List[Callable]]]:
    for route in iter_route_contexts(build_api_app().routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        calls = list(_calls(route.dependant))
        for method in sorted(route.methods):
            yield route_key(method, route.original_route.path), route, calls


async def _app_token(*, scope: Optional[str], roles=None, username='app-user') -> str:
    """A token shaped like an app grant: same table as a PAT, origin ``app``,
    limited to ``scope``."""
    user, raw = await create_user_token(username=username, roles=roles)
    app, _ = await OAuthAppService().create_app(
        user, name=f'{username} app', client_type=OAuthClientType.CONFIDENTIAL,
        redirect_uris=['https://app.example.com/callback'], allowed_scopes=[s.value for s in ALL_SCOPES],
    )
    await ApiToken.filter(user=user).update(origin='app', scope=scope, oauth_app_id=app.id)
    return raw


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_every_route_has_a_scope_decision():
    live = {key for key, _, _ in _routes()}
    missing = sorted(live - set(ROUTE_SCOPES))
    stale = sorted(set(ROUTE_SCOPES) - live)
    assert not missing, (
        'Add these routes to api/scopes.py under the scope an app needs to call them '
        '(or PAT_ONLY):\n' + '\n'.join(missing)
    )
    assert not stale, 'These api/scopes.py entries match no route any more:\n' + '\n'.join(stale)


def test_public_entries_are_exactly_the_tokenless_routes():
    wrong = [
        key for key, _, calls in _routes()
        if (ROUTE_SCOPES[key] is PUBLIC) != (resolve_token not in calls)
    ]
    assert not wrong, 'PUBLIC must list exactly the routes that take no token:\n' + '\n'.join(wrong)


def test_platform_routes_are_never_open_to_apps():
    platform_gates = {require_super_admin, require_super_admin_write}
    leaked = [
        key for key, _, calls in _routes()
        if platform_gates & set(calls) and ROUTE_SCOPES[key] is not PAT_ONLY
    ]
    assert not leaked, 'Super-admin routes must be PAT_ONLY:\n' + '\n'.join(leaked)


def test_staff_gated_routes_need_a_staff_scope():
    staff_gates = {require_staff, require_staff_write, require_admin, require_volunteer_admin}
    allowed = {ApiScope.STAFF_READ, ApiScope.STAFF_WRITE, PAT_ONLY}
    wrong = [
        f'{key} -> {ROUTE_SCOPES[key]}' for key, _, calls in _routes()
        if staff_gates & set(calls) and ROUTE_SCOPES[key] not in allowed
    ]
    assert not wrong, 'A route gated on a staff role needs staff:read or staff:write:\n' + '\n'.join(wrong)


def test_reads_and_writes_land_on_matching_scopes():
    write_only = {ApiScope.PROFILE_WRITE, ApiScope.STAFF_WRITE}
    wrong = []
    for key, _, _ in _routes():
        scope = ROUTE_SCOPES[key]
        is_read = key.startswith('GET ') or key in READ_ONLY_POSTS
        if is_read and scope in write_only:
            wrong.append(f'{key} is a read mapped to {scope.value}')
        if not is_read and scope in READ_SCOPES:
            wrong.append(f'{key} is a write mapped to read-only {scope.value}')
    assert not wrong, '\n'.join(wrong)


def test_parse_and_format_round_trip_and_drop_unknown_names():
    assert parse_scope('play  schedule:read bogus') == {ApiScope.PLAY, ApiScope.SCHEDULE_READ}
    assert parse_scope(None) == frozenset()
    assert format_scope({ApiScope.STAFF_READ, ApiScope.PROFILE}) == 'profile staff:read'
    assert parse_scope(format_scope(ALL_SCOPES)) == ALL_SCOPES


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


async def test_app_token_reaches_routes_inside_its_scope(db):
    raw = await _app_token(scope='schedule:read')
    async with client_for(build_api_app(), raw) as client:
        resp = await client.get('/api/tournaments')
    assert resp.status_code == 200, resp.text


async def test_app_token_is_refused_outside_its_scope_with_a_challenge(db):
    raw = await _app_token(scope='schedule:read')
    async with client_for(build_api_app(), raw) as client:
        resp = await client.get('/api/users/me')
    assert resp.status_code == 403
    assert 'profile' in resp.json()['detail']
    assert resp.headers['www-authenticate'] == 'Bearer error="insufficient_scope", scope="profile"'


async def test_app_token_without_a_scope_can_call_nothing(db):
    raw = await _app_token(scope=None)
    async with client_for(build_api_app(), raw) as client:
        assert (await client.get('/api/tournaments')).status_code == 403
        assert (await client.get('/api/users/me')).status_code == 403


async def test_pat_only_routes_refuse_an_app_holding_every_scope(db):
    raw = await _app_token(scope=format_scope(ALL_SCOPES), roles=[Role.STAFF])
    async with client_for(build_api_app(), raw) as client:
        tokens = await client.get('/api/tokens')
        webhooks = await client.get('/api/webhooks')
        minted = await client.post('/api/tokens', json={'name': 'escape'})
    assert tokens.status_code == webhooks.status_code == minted.status_code == 403
    assert 'personal API token' in tokens.json()['detail']
    assert await ApiToken.filter(name='escape').count() == 0


async def test_a_staff_scope_gives_a_player_nothing(db):
    raw = await _app_token(scope='staff:read staff:write')
    async with client_for(build_api_app(), raw) as client:
        users = await client.get('/api/users')
        created = await client.post('/api/tournaments', json={'name': 'Nope'})
    assert users.status_code == 403
    assert created.status_code == 403
    assert 'Staff' in users.json()['detail']


async def test_staff_scope_lets_staff_through(db):
    raw = await _app_token(scope='staff:read', roles=[Role.STAFF])
    async with client_for(build_api_app(), raw) as client:
        assert (await client.get('/api/users')).status_code == 200
        assert (await client.get('/api/tournaments')).status_code == 403


async def test_feature_gated_router_still_checks_scope_first(db):
    raw = await _app_token(scope='schedule:read')
    async with client_for(build_api_app(), raw) as client:
        resp = await client.get('/api/volunteers/me/assignments')
    assert resp.status_code == 403
    assert 'volunteer' in resp.json()['detail']


async def test_pats_are_unchanged(db):
    _, raw = await create_user_token(username='pat-user', roles=[Role.STAFF])
    _, read_only = await create_user_token(username='ro-user', read_only=True)
    async with client_for(build_api_app(), raw) as client:
        assert (await client.get('/api/tokens')).status_code == 200
        assert (await client.get('/api/users')).status_code == 200
        assert (await client.get('/api/users/me')).status_code == 200
    async with client_for(build_api_app(), read_only) as client:
        assert (await client.get('/api/users/me')).status_code == 200
        refused = await client.patch('/api/users/me', json={})
    assert refused.status_code == 403
    assert 'read-only' in refused.json()['detail']


@pytest.fixture
def unmapped_app() -> FastAPI:
    """A throwaway app with one authenticated route the table has never heard of."""
    unmapped = APIRouter(prefix='/api', dependencies=[Depends(tenant_context_scope)])

    @unmapped.get('/not-in-the-table')
    async def _unmapped(actor=Depends(require_api_actor)):
        return {'ok': True}

    app = FastAPI()
    app.include_router(unmapped)
    return app


async def test_unmapped_route_refuses_apps_but_not_pats(db, unmapped_app):
    app_raw = await _app_token(scope=format_scope(ALL_SCOPES), username='app-x')
    _, pat_raw = await create_user_token(username='pat-x')
    async with client_for(unmapped_app, app_raw) as client:
        assert (await client.get('/api/not-in-the-table')).status_code == 403
    async with client_for(unmapped_app, pat_raw) as client:
        assert (await client.get('/api/not-in-the-table')).status_code == 200
