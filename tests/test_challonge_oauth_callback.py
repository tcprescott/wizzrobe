"""The shared Challonge OAuth callback completes inside the originating tenant.

``/challonge/oauth/callback`` is served on the bare platform host, so the request
carries no tenant. Every ``ChallongeService`` entry point is
``@requires_feature(CHALLONGE)`` and the connection row is tenant-scoped, so the
callback must re-enter the community the flow started in — otherwise the real
(non-mock) flow always ends in "feature is not enabled for this community".
"""

from typing import ClassVar

import pytest
from nicegui import app

import pages.challonge_oauth as ch
from application.tenant_context import get_current_tenant_id, tenant_scope


class _FakeStorage(dict):
    pass


class _FakeClient:
    is_deleted = False

    async def connected(self) -> None:
        return None


@pytest.fixture
def storage(monkeypatch):
    store = _FakeStorage()
    monkeypatch.setattr(type(app.storage), 'user', property(lambda self: store))
    return store


@pytest.fixture
def callback(monkeypatch, storage):
    pages = {}

    def fake_page(path, *args, **kwargs):
        def register(fn):
            pages[path] = fn
            return fn
        return register

    monkeypatch.setattr(ch.ui, 'page', fake_page)
    monkeypatch.setattr(ch, 'register_link_handoff_provider', lambda provider: None)

    async def no_handoff(url):
        return False

    async def a_user(storage):
        return object()

    monkeypatch.setattr(ch, 'handle_link_handoff_callback', no_handoff)
    monkeypatch.setattr(ch, 'get_user_from_session', a_user)
    ch.create()

    async def run(url: str) -> None:
        async def js(_):
            return url
        monkeypatch.setattr(ch.ui, 'run_javascript', js)
        with tenant_scope(None):
            await pages['/challonge/oauth/callback'](_FakeClient())
            assert get_current_tenant_id() is None

    return run


@pytest.fixture
def seen(monkeypatch):
    calls = []

    async def finish_service(user, code, return_path, grant):
        calls.append(('service', code, get_current_tenant_id()))

    async def finish_player(user, code, return_path):
        calls.append(('player', code, get_current_tenant_id()))

    monkeypatch.setattr(ch, '_finish_service_connect', finish_service)
    monkeypatch.setattr(ch, '_finish_player_link', finish_player)
    return calls


async def test_service_connect_finishes_in_the_initiating_tenant(callback, storage, seen):
    storage.update({
        'challonge_service_state': 'st',
        'challonge_service_return': '/t/demo/admin/settings/integrations',
        'challonge_service_tenant': 7,
    })
    await callback('https://wizzrobe.test/challonge/oauth/callback?code=abc&state=st')
    assert seen == [('service', 'abc', 7)]
    assert 'challonge_service_tenant' not in storage


async def test_player_link_finishes_in_the_initiating_tenant(callback, storage, seen):
    storage.update({
        'challonge_player_state': 'ps',
        'challonge_player_return': '/t/demo/home/profile',
        'challonge_player_tenant': 3,
    })
    await callback('https://wizzrobe.test/challonge/oauth/callback?code=xyz&state=ps')
    assert seen == [('player', 'xyz', 3)]
    assert 'challonge_player_tenant' not in storage


async def test_a_failed_service_redirect_still_reports_in_its_tenant(callback, storage, seen):
    storage.update({'challonge_service_state': 'st', 'challonge_service_tenant': 7})
    await callback('https://wizzrobe.test/challonge/oauth/callback?error=access_denied')
    assert seen == [('service', None, 7)]


async def test_the_callback_carries_the_reconnect_queue(callback, storage, monkeypatch):
    grants = []

    async def finish_service(user, code, return_path, grant):
        grants.append(grant)

    monkeypatch.setattr(ch, '_finish_service_connect', finish_service)
    storage.update({
        'challonge_service_state': 'st', 'challonge_service_tenant': 7,
        'challonge_service_community': 'speedgaming',
        'challonge_service_queue': ['alttprleague'],
        'challonge_service_granted': ['first'],
    })
    await callback('https://wizzrobe.test/challonge/oauth/callback?code=abc&state=st')
    assert grants == [ch._ServiceGrant('speedgaming', ['alttprleague'], ['first'], 7)]
    assert 'challonge_service_queue' not in storage


class _FakeService:
    saved: ClassVar[list] = []

    async def exchange_service_code(self, code):
        return {'access_token': code}

    async def save_service_connection(self, payload, user, community=None):
        self.saved.append(community)

    @staticmethod
    def service_authorize_url(state, community=None):
        return f'https://challonge.test/authorize?community_id={community}&state={state}'


@pytest.fixture
def walk(monkeypatch, storage):
    navigated, notices = [], []
    _FakeService.saved = []
    monkeypatch.setattr(ch, 'ChallongeService', _FakeService)
    monkeypatch.setattr(ch.ui.navigate, 'to', lambda target: navigated.append(target))
    monkeypatch.setattr(ch, 'stash_notice', lambda text, color=None: notices.append(text))
    return navigated, notices


async def test_a_grant_with_more_queued_asks_challonge_for_the_next(walk, storage):
    navigated, notices = walk
    await ch._finish_service_connect(
        object(), 'code', '/t/demo/admin/settings/integrations',
        ch._ServiceGrant('speedgaming', ['alttprleague', 'zsr'], [], 7),
    )
    assert _FakeService.saved == ['speedgaming']
    assert navigated[0].startswith('https://challonge.test/authorize?community_id=alttprleague')
    assert storage['challonge_service_queue'] == ['zsr']
    assert storage['challonge_service_community'] == 'alttprleague'
    assert storage['challonge_service_granted'] == ['speedgaming']
    assert storage['challonge_service_tenant'] == 7
    assert notices == []


async def test_the_last_grant_returns_to_admin_naming_every_community(walk):
    navigated, notices = walk
    await ch._finish_service_connect(
        object(), 'code', '/t/demo/admin/settings/integrations',
        ch._ServiceGrant('zsr', [], ['speedgaming', 'alttprleague'], 7),
    )
    assert navigated == ['/t/demo/admin/settings/integrations']
    assert notices == ['Challonge connected for speedgaming, alttprleague and zsr.']


async def test_cancelling_midway_says_which_community_was_left_out(walk):
    navigated, notices = walk
    await ch._finish_service_connect(
        object(), None, '/t/demo/admin/settings/integrations',
        ch._ServiceGrant('zsr', [], ['speedgaming'], 7),
    )
    assert _FakeService.saved == []
    assert navigated == ['/t/demo/admin/settings/integrations']
    assert notices == ['Challonge connected for speedgaming. Challonge access for zsr was cancelled.']
