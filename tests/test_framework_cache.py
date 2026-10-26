"""A failed framework-script answer must never be kept by the browser.

NiceGUI marks every ``/_nicegui/<version>/`` response immutable for a year. A
404 or 5xx caught during a restart was then reused by every reload, and an
iPhone's page stayed blank (SGLMAN-1R, SGLMAN-1S).
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.framework_cache import FrameworkCacheMiddleware, is_framework_path
from theme.chrome import install_framework_load_guard

_IMMUTABLE = 'public, max-age=31536000, immutable, stale-while-revalidate=31536000'


def _answer(status: int):
    async def endpoint(request):
        if 300 <= status < 400 and status != 304:
            return RedirectResponse('/login', status_code=status, headers={'Cache-Control': _IMMUTABLE})
        return PlainTextResponse('x', status_code=status, headers={'Cache-Control': _IMMUTABLE})
    return endpoint


def _client(status: int) -> TestClient:
    paths = ('/_nicegui/3.18.0/static/vue.esm-browser.prod.js',
             '/t/sgl26/_nicegui/3.18.0/static/vue.esm-browser.prod.js',
             '/home/player')
    app = Starlette(routes=[Route(p, _answer(status)) for p in paths])
    app.add_middleware(FrameworkCacheMiddleware)
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize('status', [302, 404, 500, 502, 503])
@pytest.mark.parametrize('path', [
    '/_nicegui/3.18.0/static/vue.esm-browser.prod.js',
    '/t/sgl26/_nicegui/3.18.0/static/vue.esm-browser.prod.js',
])
def test_a_failed_framework_answer_is_not_stored(path, status):
    resp = _client(status).get(path)
    assert resp.status_code == status
    assert resp.headers.get_list('cache-control') == ['no-store']


@pytest.mark.parametrize('status', [200, 304])
def test_a_good_framework_answer_keeps_its_year_long_cache(status):
    resp = _client(status).get('/_nicegui/3.18.0/static/vue.esm-browser.prod.js')
    assert resp.headers['cache-control'] == _IMMUTABLE


def test_other_paths_are_left_alone():
    resp = _client(404).get('/home/player')
    assert resp.headers['cache-control'] == _IMMUTABLE


def test_the_websocket_transport_is_not_a_framework_path():
    assert not is_framework_path('/_nicegui_ws/socket.io/')


def _guard_script(monkeypatch) -> str:
    out: list[str] = []
    monkeypatch.setattr('theme.chrome.ui.add_body_html', out.append)
    install_framework_load_guard()
    return out[0]


def test_the_guard_refreshes_the_cached_framework_scripts_before_its_reload(monkeypatch):
    script = _guard_script(monkeypatch)
    refetch = script.index("fetch(u,{cache:'reload'})")
    assert "script[type=importmap]" in script
    assert script.index('location.reload()') < refetch < script.index('.then(go,go)')
    assert script.index('.then(go,go)') < script.index("Sentry.captureMessage(")


def test_a_browser_without_import_maps_is_told_to_update_not_reloaded(monkeypatch):
    """Safari before 16.4 can't resolve NiceGUI's ``import ... from "vue"`` and
    can't parse its Tailwind build (SGLMAN-21/23). A reload never fixes that, so
    the guard says so and stops before reloading or reporting."""
    script = _guard_script(monkeypatch)
    check = script.index("HTMLScriptElement.supports('importmap')")
    stop = script.index('return;}', check)
    assert 'too old for this site' in script[check:stop]
    assert "getElementById('esm-fallback')" in script[check:stop]
    assert stop < script.index("sessionStorage.setItem(k")
    assert stop < script.index('location.reload()')
    assert stop < script.index('Sentry.captureMessage(')


def test_sentry_sends_nothing_from_a_browser_without_import_maps():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / 'static/js/sentry-browser.js').read_text()
    assert "HTMLScriptElement.supports('importmap')" in js
    assert 'if (!live.enabled || unsupportedBrowser) return null;' in js
