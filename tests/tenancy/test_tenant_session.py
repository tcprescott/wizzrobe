"""Tenant-namespaced session state (``application/utils/tenant_session.py``).

Path-mode tenants share one session cookie, so tenant-scoped UI state
(filters, etc.) must not leak across communities. These tests drive the helper
with a fake ``nicegui.app`` whose ``storage.user`` is a plain dict and switch
the ambient tenant with ``tenant_scope``.
"""

import pytest

from application.tenant_context import tenant_scope
from application.utils import tenant_session


class _Storage:
    def __init__(self):
        self.user = {}


class _FakeApp:
    def __init__(self):
        self.storage = _Storage()


@pytest.fixture
def fake_app(monkeypatch):
    app = _FakeApp()
    # The helper imports `app` at call time, so patching the module attribute is enough.
    monkeypatch.setattr('nicegui.app', app, raising=False)
    return app


def test_set_and_get_within_same_tenant(fake_app):
    with tenant_scope(1):
        tenant_session.tenant_session_set('tournament_filter', 42)
        assert tenant_session.tenant_session_get('tournament_filter') == 42


def test_isolated_across_tenants(fake_app):
    with tenant_scope(1):
        tenant_session.tenant_session_set('tournament_filter', 42)
    with tenant_scope(2):
        assert tenant_session.tenant_session_get('tournament_filter', None) is None
        tenant_session.tenant_session_set('tournament_filter', 99)
    with tenant_scope(1):
        assert tenant_session.tenant_session_get('tournament_filter') == 42
    with tenant_scope(2):
        assert tenant_session.tenant_session_get('tournament_filter') == 99


def test_namespaced_under_by_tenant(fake_app):
    with tenant_scope(7):
        tenant_session.tenant_session_set('state_filter', ['Started'])
    assert fake_app.storage.user['by_tenant']['7']['state_filter'] == ['Started']
    assert 'state_filter' not in fake_app.storage.user


def test_falls_back_to_flat_without_tenant(fake_app):
    with tenant_scope(None):
        tenant_session.tenant_session_set('state_filter', ['Finished'])
        assert tenant_session.tenant_session_get('state_filter') == ['Finished']
    assert fake_app.storage.user['state_filter'] == ['Finished']
    assert 'by_tenant' not in fake_app.storage.user


def test_get_default_when_missing(fake_app):
    with tenant_scope(1):
        assert tenant_session.tenant_session_get('never_set', 'fallback') == 'fallback'
