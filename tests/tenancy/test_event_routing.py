"""``/e/<slug>`` routing: the middleware binds the event a URL names, pages
redirect bare links, and every link a DM carries names its event.

The middleware half drives the real ``TenantMiddleware`` through a probe app,
as ``test_tenant_middleware`` does for ``/t/<slug>``. The redirect helpers are
called directly with a request shaped like the one the middleware leaves.
"""

from datetime import date

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from application.event_context import event_scope, get_current_event_id
from application.services import event_service, notification_links, tenant_service
from application.tenant_context import get_current_tenant_id, reset_tenant_id, set_tenant_id, tenant_scope
from middleware.event_routing import default_event_redirect, event_href, row_event_redirect
from middleware.tenant import (
    _TENANT_TRANSPORT_RE,
    COMMUNITY_ROOT_SCOPE_KEY,
    EVENT_SLUG_SCOPE_KEY,
    TenantMiddleware,
)
from models import Event, EventStatus, Tenant


@pytest.fixture(autouse=True)
def _no_ambient_tenant():
    token = set_tenant_id(None)
    try:
        yield
    finally:
        reset_tenant_id(token)


@pytest.fixture(autouse=True)
def _host_env(monkeypatch):
    monkeypatch.setenv('PLATFORM_HOST', 'platform')
    monkeypatch.delenv('TRUST_FORWARDED_HOST', raising=False)
    tenant_service._clear_cache()
    event_service._clear_cache()
    yield
    tenant_service._clear_cache()
    event_service._clear_cache()


async def _probe(request):
    return JSONResponse({
        'tenant': get_current_tenant_id(),
        'event': get_current_event_id(),
        'path': request.scope['path'],
        'root_path': request.scope.get('root_path', ''),
        'slug': request.scope.get(EVENT_SLUG_SCOPE_KEY),
        'community_root': request.scope.get(COMMUNITY_ROOT_SCOPE_KEY),
    })


def _app() -> Starlette:
    app = Starlette(routes=[Route('/', _probe), Route('/admin', _probe)])
    app.add_middleware(TenantMiddleware)
    return app


async def _get(path: str):
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url='http://platform') as client:
        return await client.get(path)


@pytest.fixture
async def acme(db):
    tenant = await Tenant.get(id=1)
    tenant.slug = 'acme'
    await tenant.save()
    default = await Event.get(tenant_id=tenant.id, is_default=True)
    second = await Event.create(
        tenant_id=tenant.id, name='Winter', slug='winter', status=EventStatus.LIVE,
        starts_on=date(2027, 1, 10), ends_on=date(2027, 1, 12),
    )
    return tenant, default, second


async def test_a_named_event_is_bound_and_stripped_into_root_path(acme):
    tenant, _default, second = acme
    r = await _get('/t/acme/e/winter/admin')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] == tenant.id
    assert body['event'] == second.id
    assert body['path'] == '/admin'
    assert body['root_path'] == '/t/acme/e/winter'
    assert body['community_root'] == '/t/acme'
    assert body['slug'] == 'winter'


async def test_a_bare_path_binds_the_default_event_and_names_none(acme):
    _tenant, default, _second = acme
    body = (await _get('/t/acme/admin')).json()
    assert body['event'] == default.id
    assert body['root_path'] == '/t/acme'
    assert body['slug'] is None


async def test_an_unknown_event_is_a_404(acme):
    assert (await _get('/t/acme/e/nope/admin')).status_code == 404


async def test_another_communitys_event_slug_is_unknown_here(acme):
    other = await Tenant.create(name='Beta', slug='beta')
    await Event.create(tenant_id=other.id, name='Beta Cup', slug='beta-cup', status=EventStatus.LIVE)
    assert (await _get('/t/acme/e/beta-cup/admin')).status_code == 404


def test_transport_paths_under_an_event_are_shared():
    match = _TENANT_TRANSPORT_RE.match('/t/acme/e/winter/_nicegui/3.0/static/x.js')
    assert match is not None
    assert match.group('prefix') == '/t/acme/e/winter'
    assert match.group('rest') == '/_nicegui/3.0/static/x.js'


def _request(path: str, *, slug=None, query: str = '') -> Request:
    root = '/t/acme' + (f'/e/{slug}' if slug else '')
    return Request({
        'type': 'http', 'method': 'GET', 'path': path, 'headers': [],
        'query_string': query.encode(), 'root_path': root,
        EVENT_SLUG_SCOPE_KEY: slug, COMMUNITY_ROOT_SCOPE_KEY: '/t/acme',
    })


async def test_a_bare_event_page_redirects_to_the_default_keeping_the_query(acme):
    tenant, default, _second = acme
    with tenant_scope(tenant.id):
        redirect = await default_event_redirect(_request('/admin/schedule', query='day=2'))
    assert redirect is not None and redirect.status_code == 302
    assert redirect.headers['location'] == f'/t/acme/e/{default.slug}/admin/schedule?day=2'


async def test_a_named_event_page_is_left_alone(acme):
    tenant, _default, _second = acme
    with tenant_scope(tenant.id):
        assert await default_event_redirect(_request('/admin', slug='winter')) is None


async def test_a_row_page_opens_in_its_rows_event(acme):
    tenant, default, second = acme
    with tenant_scope(tenant.id):
        moved = await row_event_redirect(_request('/brackets/4', slug=default.slug), second.id)
        stays = await row_event_redirect(_request('/brackets/4', slug='winter'), second.id)
    assert moved is not None
    assert moved.headers['location'] == '/t/acme/e/winter/brackets/4'
    assert stays is None


def test_event_href_builds_from_the_community_root():
    assert event_href(_request('/admin', slug='old'), 'winter', '/admin') == '/t/acme/e/winter/admin'


async def test_a_dm_link_names_the_event_it_was_sent_from(acme):
    tenant, _default, second = acme
    with tenant_scope(tenant.id), event_scope(second.id):
        link = await notification_links.player_match(7)
    assert link is not None
    assert '/t/acme/e/winter/' in link.url
