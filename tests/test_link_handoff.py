"""Cross-host secondary-provider link handoff (racetime / Twitch / Challonge).

The custom-domain counterpart of the Discord-login handoff: the provider OAuth
runs on the platform host, then the verified **public** provider identity is
handed back to the custom domain (where the user's session and tenant live)
through the same single-use, host-bound, browser-bound token. These tests cover
the generic payload token, the handoff-URL builders, and the provider registry;
the security invariants of the token itself live in ``test_oauth_handoff.py``.
"""

import hashlib
import importlib

import pytest

from application.services import oauth_handoff_service as h
from application.utils.tenant_urls import safe_next


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv('STORAGE_SECRET', 'x' * 40)


async def test_mint_data_roundtrips_arbitrary_payload(db):
    token = await h.mint_data(
        data={'key': 'racetime', 'user_id': 'rt-1', 'name': 'Speedy'},
        target_host='foo.gg', next_path='/home/profile', bind_commit='c' * 64,
    )
    payload = await h.claim(token, 'foo.gg')
    assert payload is not None
    assert payload['data'] == {'key': 'racetime', 'user_id': 'rt-1', 'name': 'Speedy'}
    assert payload['next'] == '/home/profile'
    assert payload['bind_commit'] == 'c' * 64


async def test_mint_data_is_single_use(db):
    token = await h.mint_data(data={'key': 'twitch'}, target_host='foo.gg', next_path='/')
    assert await h.claim(token, 'foo.gg') is not None
    assert await h.claim(token, 'foo.gg') is None


async def test_mint_data_is_host_bound(db):
    token = await h.mint_data(data={'key': 'twitch'}, target_host='foo.gg', next_path='/')
    assert await h.claim(token, 'bar.gg') is None
    assert await h.claim(token, 'foo.gg') is None


async def test_mint_data_rejects_unnormalizable_host(db):
    assert await h.mint_data(data={'key': 'x'}, target_host='not a host', next_path='/') is None


async def test_login_mint_keeps_flat_identity_shape(db):
    token = await h.mint(discord_id=1, username='u', avatar=None, target_host='foo.gg', next_path='/')
    payload = await h.claim(token, 'foo.gg')
    assert payload['discord_id'] == 1
    assert 'data' not in payload


@pytest.fixture
def link(monkeypatch):
    monkeypatch.setenv('STORAGE_SECRET', 'x' * 40)
    return importlib.import_module('pages._oauth_link')


def test_link_handoff_start_url(link, monkeypatch):
    monkeypatch.setenv('PLATFORM_HOST', 'main.gg')
    url = link._link_handoff_start_url('racetime', 'foo.gg', '/home/profile', 'abc')
    assert url == 'https://main.gg/oauth/link/start?p=racetime&host=foo.gg&next=%2Fhome%2Fprofile&b=abc'


def test_link_claim_url(link):
    assert link._link_claim_url('foo.gg', 'tok en') == 'https://foo.gg/oauth/link/claim?token=tok%20en'


def test_link_handoff_urls_use_http_for_localhost_dev(link, monkeypatch):
    monkeypatch.setenv('PLATFORM_HOST', 'localhost:8000')
    assert link._link_handoff_start_url('twitch', 's.localhost:8000', '/', 'b').startswith('http://localhost:8000/')
    assert link._link_claim_url('s.localhost:8000', 't').startswith('http://s.localhost:8000/')


def test_bind_commit_is_deterministic_sha256(link):
    assert link._bind_commit('secret') == hashlib.sha256(b'secret').hexdigest()
    assert link._bind_commit('other') != link._bind_commit('secret')


def test_identity_flows_carry_provider_key():
    import pages.racetime_oauth as rt
    import pages.twitch_oauth as tw
    assert rt._FLOW.provider_key == 'racetime'
    assert tw._FLOW.provider_key == 'twitch'


def test_register_link_handoff_provider(link):
    provider = link.LinkHandoffProvider(
        key='demo', label='Demo', profile_return='/home/profile',
        authorize_url=lambda s: f'https://x/y?state={s}',
        exchange=None, record=None,
        is_mock=lambda: False, callback_route='/demo/oauth/callback',
    )
    link.register_link_handoff_provider(provider)
    assert link._HANDOFF_PROVIDERS['demo'] is provider


def test_link_handoff_provider_requires_its_mock_exit(link):
    import dataclasses
    required = {
        f.name for f in dataclasses.fields(link.LinkHandoffProvider)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    assert {'is_mock', 'callback_route'} <= required


def test_mock_callback_url_targets_the_providers_own_callback(link, monkeypatch):
    monkeypatch.setenv('PLATFORM_HOST', 'main.gg')
    provider = link.LinkHandoffProvider(
        key='demo', label='Demo', profile_return='/home/profile',
        authorize_url=lambda s: 'https://provider.example/authorize',
        exchange=None, record=None,
        is_mock=lambda: True, callback_route='/demo/oauth/callback',
    )
    assert link._mock_callback_url(provider, 'st8') == (
        'https://main.gg/demo/oauth/callback?code=mock&state=st8'
    )


def test_link_start_never_self_redirects_in_production(monkeypatch):
    from application.utils.mocks.mock_challonge import is_mock_challonge
    from application.utils.mocks.mock_racetime import is_mock_racetime
    from application.utils.mocks.mock_twitch import is_mock_twitch
    monkeypatch.setenv('ENVIRONMENT', 'production')
    for is_mock in (is_mock_challonge, is_mock_racetime, is_mock_twitch):
        monkeypatch.setenv(f'MOCK_{is_mock.__name__.removeprefix("is_mock_").upper()}', 'true')
        with pytest.raises(RuntimeError):
            is_mock()


async def test_maybe_start_link_handoff_is_none_in_path_mode(db, link, monkeypatch):
    monkeypatch.setenv('HOST_OAUTH_MODE', 'handoff')
    monkeypatch.setattr(link, 'is_host_mode', lambda: False)
    assert await link.maybe_start_link_handoff('racetime', '/home/profile') is None


async def test_maybe_start_link_handoff_is_none_with_handoff_off(db, link, monkeypatch):
    monkeypatch.delenv('HOST_OAUTH_MODE', raising=False)
    monkeypatch.setattr(link, 'is_host_mode', lambda: True)
    assert await link.maybe_start_link_handoff('racetime', '/home/profile') is None


def test_link_page_prefers_handoff_over_mock(link):
    # Source-level on purpose: the ordering is the fix (handoff before the is_mock() short-circuit).
    import inspect

    import pages.challonge_oauth as ch
    for module, mock_call in ((link, 'flow.is_mock()'), (ch, 'is_mock_challonge()')):
        src = inspect.getsource(module)
        handoff = src.index('maybe_start_link_handoff(')
        # The link page's mock branch is the last occurrence; challonge's /connect page has an earlier one.
        assert src.rindex(mock_call) > handoff, module.__name__


def test_bind_matches_accepts_the_committing_browser(link):
    secret = 'a-browser-secret'
    assert link._bind_matches(link._bind_commit(secret), secret) is True


def test_bind_matches_rejects_a_different_browser(link):
    assert link._bind_matches(link._bind_commit('secret-A'), 'secret-B') is False


@pytest.mark.parametrize('expected_commit,browser_secret', [
    (None, 'anything'),
    ('', 'anything'),
    ('deadbeef' * 8, None),
    ('deadbeef' * 8, 123),
])
def test_bind_matches_fails_closed(link, expected_commit, browser_secret):
    assert link._bind_matches(expected_commit, browser_secret) is False


def test_handoff_failure_hands_back_through_the_claim_route(link):
    url = link._link_handback_url('foo.gg', 'racetime', 'denied', '/home/profile')
    assert url.startswith('https://foo.gg/oauth/link/claim?')
    assert 'r=denied' in url and 'p=racetime' in url and 'next=%2Fhome%2Fprofile' in url


def test_handback_reasons_map_to_the_existing_wording(link):
    provider = link.LinkHandoffProvider(
        key='demo', label='Demo', profile_return='/home/profile',
        authorize_url=lambda s: '', exchange=None, record=None,
        is_mock=lambda: False, callback_route='/demo/oauth/callback',
    )
    link.register_link_handoff_provider(provider)
    assert link._handback_message('denied', 'demo') == 'Demo linking was cancelled or failed.'
    assert link._handback_message('failed', 'demo') == 'Could not link Demo. Please try again.'


def test_unknown_reason_code_falls_back_to_the_generic_message(link):
    provider = link.LinkHandoffProvider(
        key='demo', label='Demo', profile_return='/home/profile',
        authorize_url=lambda s: '', exchange=None, record=None,
        is_mock=lambda: False, callback_route='/demo/oauth/callback',
    )
    link.register_link_handoff_provider(provider)
    for reason in ('', 'nonsense', '<script>', None):
        assert link._handback_message(reason, 'demo') == 'Could not link Demo. Please try again.'
    assert 'Demo' not in link._handback_message('denied', 'no-such-provider')


def test_default_link_return_is_derived_not_hardcoded(link, monkeypatch):
    # Not three literal '/home/profile's: the next provider with a different return would silently break.
    monkeypatch.setattr(link, '_HANDOFF_PROVIDERS', {}, raising=False)
    assert link._default_link_return() == '/'
    one = link.LinkHandoffProvider(
        key='a', label='A', profile_return='/somewhere/else',
        authorize_url=lambda s: '', exchange=None, record=None,
        is_mock=lambda: False, callback_route='/a/cb',
    )
    monkeypatch.setattr(link, '_HANDOFF_PROVIDERS', {'a': one}, raising=False)
    assert link._default_link_return() == '/somewhere/else'


def test_claim_route_is_disabled_in_path_mode(link, monkeypatch):
    import inspect
    src = inspect.getsource(link.register_link_handoff_pages)
    claim = src.index("@ui.page('/oauth/link/claim')")
    guard = src.index('if not host_oauth_handoff_enabled():', claim)
    connected = src.index('await browser_url(client)', claim)
    assert guard < connected, 'the mode guard must run before the page does any work'
    assert "RedirectResponse('/')" in src[guard:connected]


def test_claim_failure_paths_are_all_safe_next_guarded(link):
    # safe_next is the open-redirect guard here; hoisting a default drops it.
    import inspect
    src = inspect.getsource(link.register_link_handoff_pages)
    claim = src[src.index("@ui.page('/oauth/link/claim')"):]
    for line in claim.splitlines():
        stripped = line.strip()
        if not stripped.startswith('ui.navigate.to('):
            continue
        # The one legitimate literal: /login is not a return path the token could have supplied.
        if stripped == "ui.navigate.to('/login')":
            continue
        assert 'safe_next(' in stripped or 'handed_back' in stripped or 'next_path' in stripped, line
    assert "handed_back = safe_next(" in claim
    assert "next_path = safe_next(" in claim


@pytest.mark.parametrize('raw,expected', [
    ('/home/profile', '/home/profile'),
    ('/home/profile?tab=1', '/home/profile?tab=1'),
    ('//evil.com', '/'),
    ('/\\evil.com', '/'),
    ('/a\r\nSet-Cookie: x', '/'),
    ('/a b', '/'),
    ('https://evil.com', '/'),
    ('/login', '/'),
    ('', '/'),
    (None, '/'),
])
def test_safe_next_guards_cross_host_return(raw, expected):
    assert safe_next(raw) == expected


async def test_handoff_exchange_runs_in_the_target_tenant(db, link, monkeypatch):
    from nicegui import app

    from application.tenant_context import get_current_tenant_id, tenant_scope

    store = {
        'oauth_link_provider': 'demo', 'oauth_link_state': 'st8',
        'oauth_link_host': 'foo.gg', 'oauth_link_next': '/home/profile',
        'oauth_link_bind_commit': 'c' * 64, 'oauth_link_tenant': 7,
    }
    monkeypatch.setattr(type(app.storage), 'user', property(lambda self: store))
    seen = []

    async def exchange(code):
        seen.append((code, get_current_tenant_id()))
        return {'user_id': '42', 'name': 'someone'}

    link.register_link_handoff_provider(link.LinkHandoffProvider(
        key='demo', label='Demo', profile_return='/home/profile',
        authorize_url=lambda s: '', exchange=exchange, record=None,
        is_mock=lambda: False, callback_route='/demo/oauth/callback',
    ))
    went = []
    monkeypatch.setattr(link.ui.navigate, 'to', went.append)

    with tenant_scope(None):
        handled = await link.handle_link_handoff_callback(
            'https://main.gg/demo/oauth/callback?code=abc&state=st8',
        )
        assert get_current_tenant_id() is None

    assert handled is True
    assert seen == [('abc', 7)]
    assert went and went[0].startswith('https://foo.gg/oauth/link/claim?token=')
    assert 'oauth_link_tenant' not in store


def test_link_start_pins_the_target_tenant(link):
    import inspect
    src = inspect.getsource(link.register_link_handoff_pages)
    start = src[src.index("@ui.page('/oauth/link/start')"):src.index("@ui.page('/oauth/link/claim')")]
    assert "app.storage.user['oauth_link_tenant'] = tenant.id" in start
