"""Every "this isn't here" state answers HTTP 404 — and says the same thing.

The audit found eight distinct not-found states and only the unknown route
returned 404; a feature the community has off, among others, answered 200 with
an error card, which a link checker or a cache in front of the app reads as a
live page. These drive the real decorator and renderer inside a NiceGUI client
(no server) and read back the status the response would carry.
"""

import types
from typing import ClassVar

import pytest
from nicegui import Client, ui
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

import middleware.auth as auth_mw
from application.services.feature_flag_service import reset_flag_cache
from application.tenant_context import tenant_scope
from middleware.tenant import EVENT_SLUG_SCOPE_KEY
from models import FeatureFlag, Tenant, TenantFeatureFlag
from theme.error_page import NOT_FOUND_HEADLINE, NOT_FOUND_MESSAGE


def _request(path='/x', **scope):
    # Named as the middleware leaves an /e/<slug> URL, so the gates below are
    # what answers rather than the bare link's redirect to the default event.
    return Request({
        'type': 'http', 'method': 'GET', 'path': path, 'headers': [],
        'query_string': b'', 'root_path': '', EVENT_SLUG_SCOPE_KEY: 'bare', **scope,
    })


class _Storage:
    """``app.storage`` without a request: an anonymous, empty session."""

    user: ClassVar[dict] = {}
    browser: ClassVar[dict] = {}


@pytest.fixture
def no_session(monkeypatch):
    fake_app = types.SimpleNamespace(storage=_Storage())
    for module in ('middleware.auth', 'theme.base', 'theme.error_page', 'theme.notice'):
        monkeypatch.setattr(f'{module}.app', fake_app, raising=False)


@pytest.fixture
def raw_page(monkeypatch):
    """Register pages without NiceGUI's route wrapper, so the decorator's own
    wrapper can be awaited directly inside a test client."""
    monkeypatch.setattr(
        auth_mw, 'ui', types.SimpleNamespace(page=lambda path, **kw: (lambda func: func)),
    )


@pytest.fixture
async def flagless_tenant(db) -> Tenant:
    """A community with no feature-flag rows: every optional feature is off."""
    return await Tenant.create(name='Bare', slug='bare')


@pytest.mark.parametrize('decorator', [auth_mw.public_page, auth_mw.protected_page])
async def test_a_feature_the_community_has_off_is_a_404(
    decorator, raw_page, no_session, flagless_tenant,
):
    rendered = {}

    @decorator('/test-feature-off', feature=FeatureFlag.BRACKETS)
    async def page():
        rendered['body'] = True

    with tenant_scope(flagless_tenant.id):
        with Client(ui.page(''), request=_request('/test-feature-off')) as client:
            await page()
    assert client.status_code == 404
    assert 'body' not in rendered


async def test_a_gate_of_several_flags_is_a_404_only_when_all_are_off(
    raw_page, no_session, flagless_tenant,
):
    rendered = []

    @auth_mw.public_page('/test-any-of', feature=(FeatureFlag.VOLUNTEERS, FeatureFlag.EVENT_CHECK_IN))
    async def page():
        rendered.append(True)

    with tenant_scope(flagless_tenant.id):
        with Client(ui.page(''), request=_request('/test-any-of')) as client:
            await page()
    assert client.status_code == 404
    assert rendered == []

    await TenantFeatureFlag.create(
        tenant_id=flagless_tenant.id, flag=FeatureFlag.EVENT_CHECK_IN.value, available=True, enabled=True,
    )
    reset_flag_cache()
    with tenant_scope(flagless_tenant.id):
        with Client(ui.page(''), request=_request('/test-any-of')):
            await page()
    assert rendered == [True]


async def test_the_feature_off_page_says_what_an_unknown_route_says(
    raw_page, no_session, flagless_tenant, monkeypatch,
):
    seen = {}

    async def fake_not_found(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr('theme.error_page.render_not_found', fake_not_found)

    @auth_mw.public_page('/test-feature-off-copy', feature=FeatureFlag.BRACKETS)
    async def page():
        pass

    with tenant_scope(flagless_tenant.id):
        with Client(ui.page(''), request=_request()):
            await page()
    # No headline/message override: the unknown-route defaults, word for word.
    assert 'headline' not in seen and 'message' not in seen


class _FakeClient:
    """Stands in for the 404 handler's own ``Client``, whose response build
    needs a configured NiceGUI app."""

    def __init__(self, page, request=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def build_response(self, request, status):
        from starlette.responses import Response

        return Response(status_code=status)


class TestUnknownCommunity:
    async def test_the_handler_renders_community_not_found(self, monkeypatch, db):
        """``TenantMiddleware`` marks the scope and lets the request fall
        through unrouted; the 404 handler turns that into a page with a way to
        the picker, instead of the bare body Chrome downloaded."""
        from fastapi import FastAPI

        from middleware import error_handlers
        from middleware.tenant import UNKNOWN_COMMUNITY_SCOPE_KEY

        captured = {}

        class _FakeApp:
            def exception_handler(self, code):
                def deco(func):
                    captured[code] = func
                    return func
                return deco

            def on_page_exception(self, func):
                captured['page'] = func

        rendered = {}
        monkeypatch.setattr(error_handlers, 'Client', _FakeClient)
        monkeypatch.setattr(error_handlers, 'app', _FakeApp())
        monkeypatch.setattr(error_handlers, 'render_error_page', lambda **kw: rendered.update(kw))
        error_handlers.register_error_handlers(FastAPI())

        response = await captured[404](
            _request('/t/nope/help', **{UNKNOWN_COMMUNITY_SCOPE_KEY: True}),
            StarletteHTTPException(status_code=404),
        )
        assert response.status_code == 404
        assert rendered['headline'] == 'Community not found'
        assert rendered['actions'] == [('See all communities', 'groups', '/')]

    async def test_an_ordinary_miss_keeps_the_shared_copy(self, monkeypatch, db):
        from fastapi import FastAPI

        from middleware import error_handlers

        captured = {}

        class _FakeApp:
            def exception_handler(self, code):
                return lambda func: captured.setdefault(code, func)

            def on_page_exception(self, func):
                pass

        rendered = {}
        monkeypatch.setattr(error_handlers, 'Client', _FakeClient)
        monkeypatch.setattr(error_handlers, 'app', _FakeApp())
        monkeypatch.setattr(error_handlers, 'render_error_page', lambda **kw: rendered.update(kw))
        error_handlers.register_error_handlers(FastAPI())

        response = await captured[404](_request('/zzz'), StarletteHTTPException(status_code=404))
        assert response.status_code == 404
        assert (rendered['headline'], rendered['message']) == (NOT_FOUND_HEADLINE, NOT_FOUND_MESSAGE)
