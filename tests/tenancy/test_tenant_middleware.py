"""Tenant middleware resolution + ASGI scope-rewrite tests.

Drives the real ``TenantMiddleware`` through a minimal Starlette app (with a real
tenant DB) to confirm ``/t/<slug>`` resolves, sets tenant context, rewrites the
scope so unprefixed routes match, keeps ``root_path`` for link building, 404s
unknown/inactive tenants, and leaves platform + transport paths untouched.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from application.event_context import get_current_event_id
from application.services import tenant_service
from application.tenant_context import get_current_tenant_id, reset_tenant_id, set_tenant_id
from middleware.tenant import _TENANT_TRANSPORT_RE, TenantMiddleware, TransportPrefixMiddleware
from models import Event, EventStatus, Tenant


@pytest.fixture(autouse=True)
def _no_ambient_tenant():
    """Override the suite-wide default tenant context so these tests observe the
    middleware's own resolution (platform/api paths must see *no* tenant)."""
    token = set_tenant_id(None)
    try:
        yield
    finally:
        reset_tenant_id(token)


@pytest.fixture(autouse=True)
def _host_env(monkeypatch):
    """Pin PLATFORM_HOST to the test host and isolate the cross-tenant caches.

    The suite drives ``Host: platform``; pinning ``PLATFORM_HOST='platform'``
    makes that the platform host (so path/platform behavior is unchanged) and
    lets the host-mode tests use distinct custom domains. The domain cache is
    process-global, so clear it around each test to avoid cross-test bleed.
    """
    monkeypatch.setenv('PLATFORM_HOST', 'platform')
    monkeypatch.delenv('TRUST_FORWARDED_HOST', raising=False)
    tenant_service._clear_cache()
    yield
    tenant_service._clear_cache()


async def _probe(request):
    raw = request.scope.get('raw_path')
    return JSONResponse({
        'tenant': get_current_tenant_id(),
        'event': get_current_event_id(),
        'path': request.scope['path'],
        'root_path': request.scope.get('root_path', ''),
        'raw_path': raw.decode('latin-1') if raw is not None else None,
    })


def _build_app() -> Starlette:
    app = Starlette(routes=[
        Route('/', _probe),
        Route('/admin', _probe),
        Route('/api/x', _probe),
    ])
    app.add_middleware(TenantMiddleware)
    return app


async def _get(app, path, host='platform', headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f'http://{host}') as client:
        return await client.get(path, headers=headers or {})


@pytest.fixture
async def two_tenants(db):
    a = await Tenant.get(id=1)
    a.slug = 'acme'  # rename default -> acme for readability
    await a.save()
    b = await Tenant.create(name='Beta', slug='beta')
    inactive = await Tenant.create(name='Gone', slug='gone', is_active=False)
    return a, b, inactive


async def test_path_mode_resolves_and_rewrites(two_tenants):
    a, _b, _ = two_tenants
    r = await _get(_build_app(), '/t/acme/admin')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] == a.id
    assert body['path'] == '/admin'          # prefix stripped for routing
    assert body['root_path'] == '/t/acme'    # prefix preserved for links


async def test_tenant_home_without_trailing_path(two_tenants):
    a, _b, _ = two_tenants
    r = await _get(_build_app(), '/t/acme')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] == a.id
    assert body['path'] == '/'


async def test_second_tenant_resolves_independently(two_tenants):
    _a, b, _ = two_tenants
    r = await _get(_build_app(), '/t/beta/admin')
    assert r.json()['tenant'] == b.id


async def test_unknown_slug_404(two_tenants):
    r = await _get(_build_app(), '/t/nope/admin')
    assert r.status_code == 404


async def test_inactive_tenant_404(two_tenants):
    r = await _get(_build_app(), '/t/gone/admin')
    assert r.status_code == 404


async def test_platform_surface_has_no_tenant(two_tenants):
    r = await _get(_build_app(), '/')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] is None
    assert body['root_path'] == ''


async def test_api_path_is_excluded(two_tenants):
    r = await _get(_build_app(), '/api/x')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] is None          # API derives tenant from its token
    assert body['path'] == '/api/x'        # not rewritten


async def test_raw_path_preserves_encoding(two_tenants):
    # `%61` decodes to 'a', so the decoded path '/admin' still routes, while
    # raw_path must keep the original percent-encoding (stripped of the prefix)
    # rather than being rebuilt from the decoded path.
    r = await _get(_build_app(), '/t/acme/%61dmin')
    assert r.status_code == 200
    body = r.json()
    assert body['path'] == '/admin'
    assert body['raw_path'] == '/%61dmin'


async def test_context_is_reset_after_request(two_tenants):
    await _get(_build_app(), '/t/acme/admin')
    # The contextvar the middleware set must not leak past the request.
    assert get_current_tenant_id() is None


@pytest.fixture
async def host_tenants(two_tenants):
    """The path-mode fixture, plus custom domains on the active + inactive tenants."""
    a, b, inactive = two_tenants
    a.domain = 'foo.gg'
    await a.save()
    inactive.domain = 'gone.gg'
    await inactive.save()
    tenant_service._clear_cache()
    return a, b, inactive


async def test_host_mode_resolves_without_prefix(host_tenants):
    a, _b, _ = host_tenants
    r = await _get(_build_app(), '/admin', host='foo.gg')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] == a.id
    assert body['path'] == '/admin'      # scope untouched
    assert body['root_path'] == ''       # the host owns the whole host; no prefix


async def test_host_mode_home(host_tenants):
    a, _b, _ = host_tenants
    r = await _get(_build_app(), '/', host='foo.gg')
    assert r.json()['tenant'] == a.id


async def test_host_is_authoritative_path_prefix_ignored(host_tenants):
    # /t/<other> on a custom domain stays a literal path -> unrouted -> 404, so
    # one domain serves exactly one tenant.
    r = await _get(_build_app(), '/t/beta/admin', host='foo.gg')
    assert r.status_code == 404


async def test_inactive_domain_falls_through_to_platform(host_tenants):
    # A deactivated tenant's domain is not host mode; it falls through leniently
    # (like an unknown host) rather than 404ing the whole host.
    r = await _get(_build_app(), '/admin', host='gone.gg')
    assert r.status_code == 200
    assert r.json()['tenant'] is None


async def test_path_mode_works_on_inactive_domain(host_tenants):
    # Deactivating one tenant must not block path-mode access to others on its host.
    a, _b, _ = host_tenants
    r = await _get(_build_app(), '/t/acme/admin', host='gone.gg')
    assert r.json()['tenant'] == a.id


async def test_unknown_host_falls_through_to_platform(host_tenants):
    r = await _get(_build_app(), '/', host='random.example')
    assert r.status_code == 200
    body = r.json()
    assert body['tenant'] is None
    assert body['root_path'] == ''


async def test_path_mode_still_wins_on_platform_host(host_tenants):
    a, _b, _ = host_tenants
    r = await _get(_build_app(), '/t/acme/admin', host='platform')
    assert r.json()['tenant'] == a.id


async def test_forwarded_host_ignored_by_default(host_tenants):
    # Without TRUST_FORWARDED_HOST the forged forwarded host is ignored; the real
    # Host (platform) wins, so /admin runs with no tenant.
    r = await _get(
        _build_app(), '/admin', host='platform',
        headers={'x-forwarded-host': 'foo.gg'},
    )
    assert r.status_code == 200
    assert r.json()['tenant'] is None


async def test_forwarded_host_last_value_when_trusted(host_tenants, monkeypatch):
    monkeypatch.setenv('TRUST_FORWARDED_HOST', 'true')
    a, _b, _ = host_tenants
    # Append-ordered header: the leftmost element is client-forgeable, the last is
    # set by the trusted proxy. The last value (foo.gg) must select the tenant.
    r = await _get(
        _build_app(), '/admin', host='platform',
        headers={'x-forwarded-host': 'evil.gg, foo.gg'},
    )
    assert r.json()['tenant'] == a.id


async def test_forwarded_host_multiple_header_lines_last_wins(host_tenants, monkeypatch):
    monkeypatch.setenv('TRUST_FORWARDED_HOST', 'true')
    a, _b, _ = host_tenants
    # A proxy that *appends* a new header line (rather than extending the comma
    # list) must still be honored: the last line (foo.gg) is the nearest proxy;
    # the first (evil.gg) is client-forgeable and must be ignored.
    r = await _get(
        _build_app(), '/admin', host='platform',
        headers=[('x-forwarded-host', 'evil.gg'), ('x-forwarded-host', 'foo.gg')],
    )
    assert r.json()['tenant'] == a.id


# NiceGUI addresses its static files and socket.io under the page's root_path
# (/t/<slug>), so the browser requests them prefixed. They must be un-prefixed
# back to the app's real routes, with root_path left empty — otherwise every
# tenant page renders blank (assets 404). Regression for that whole-feature bug.

def _build_transport_app() -> Starlette:
    app = Starlette(routes=[
        Route('/_nicegui/{rest:path}', _probe),
        Route('/static/{rest:path}', _probe),
        Route('/admin', _probe),
    ])
    app.add_middleware(TransportPrefixMiddleware)
    return app


@pytest.mark.parametrize('prefixed,expected', [
    ('/t/acme/_nicegui/3.12.1/static/nicegui.js', '/_nicegui/3.12.1/static/nicegui.js'),
    ('/t/acme/_nicegui_ws/socket.io', '/_nicegui_ws/socket.io'),
    ('/t/acme/static/icons/icon.png', '/static/icons/icon.png'),
    ('/t/acme/sw.js', '/sw.js'),
])
def test_transport_regex_strips_prefix(prefixed, expected):
    m = _TENANT_TRANSPORT_RE.match(prefixed)
    assert m is not None
    assert m.group('rest') == expected
    assert m.group('prefix') == '/t/acme'


@pytest.mark.parametrize('page_path', ['/t/acme/admin', '/t/acme/', '/t/acme/equipment/5', '/admin'])
def test_transport_regex_ignores_page_paths(page_path):
    # Page routes must fall through to TenantMiddleware, not be treated as assets.
    assert _TENANT_TRANSPORT_RE.match(page_path) is None


async def test_transport_asset_is_unprefixed_and_unscoped():
    # A prefixed asset request reaches the real /_nicegui route with root_path
    # cleared (a non-empty root_path is what makes NiceGUI's static route 404).
    r = await _get(_build_transport_app(), '/t/acme/_nicegui/3.12.1/static/nicegui.js')
    assert r.status_code == 200
    body = r.json()
    assert body['path'] == '/_nicegui/3.12.1/static/nicegui.js'
    assert body['root_path'] == ''
    assert body['tenant'] is None


async def test_transport_middleware_passes_page_paths_through():
    r = await _get(_build_transport_app(), '/admin')
    assert r.status_code == 200
    assert r.json()['path'] == '/admin'


async def test_unknown_slug_falls_through_marked_for_the_themed_404(two_tenants):
    """No tenant is bound and the scope is marked, so the app's 404 handler can
    render "Community not found" — rather than a bare body with no
    Content-Type, which Chrome downloaded."""
    from middleware.tenant import UNKNOWN_COMMUNITY_SCOPE_KEY

    async def marked(request):
        return JSONResponse({
            'tenant': get_current_tenant_id(),
            'marked': bool(request.scope.get(UNKNOWN_COMMUNITY_SCOPE_KEY)),
            'path': request.scope['path'],
        })

    app = Starlette(routes=[Route('/t/nope/help', marked), Route('/t/gone/help', marked)])
    app.add_middleware(TenantMiddleware)
    for path in ('/t/nope/help', '/t/gone/help'):
        body = (await _get(app, path)).json()
        assert body == {'tenant': None, 'marked': True, 'path': path}


async def test_path_mode_binds_the_default_event(two_tenants):
    a, _b, _ = two_tenants
    default = await Event.get(tenant_id=a.id, is_default=True)
    await Event.create(tenant_id=a.id, name='Next year', slug='next', status=EventStatus.DRAFT)
    r = await _get(_build_app(), '/t/acme/admin')
    assert r.json()['event'] == default.id


async def test_event_is_never_borrowed_from_another_community(two_tenants, monkeypatch):
    # With no event of its own beta binds none, rather than falling back to acme's.
    from application import event_context

    monkeypatch.setattr(event_context, '_fallback_event_id', lambda _tenant_id: None)
    _a, b, _ = two_tenants
    await Event.filter(tenant_id=b.id).delete()
    r = await _get(_build_app(), '/t/beta/admin')
    assert r.json()['event'] is None


async def test_host_mode_binds_the_default_event(host_tenants):
    a = await Tenant.get(domain='foo.gg')
    default = await Event.get(tenant_id=a.id, is_default=True)
    r = await _get(_build_app(), '/admin', host='foo.gg')
    assert r.json()['event'] == default.id


async def test_platform_surface_binds_no_event(two_tenants):
    r = await _get(_build_app(), '/admin')
    assert r.json()['event'] is None
