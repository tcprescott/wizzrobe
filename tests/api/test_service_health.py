"""REST API tests for the service-health endpoints (api/routers/service_health.py).

The HTTP dependency is the authorization gate: tenant STAFF may read their own
subset; the full board and the on-demand refresh are SUPER_ADMIN-only. A
read-only token may not trigger a refresh.
"""

import pytest

from application.services import service_health_service as shs
from application.services.service_health_service import reset_cache
from models import Role
from tests.api_helpers import client_for, create_user_token


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


class TestTenantSubset:
    async def test_staff_gets_subset_list(self, db, app):
        """A tenant STAFF token returns 200 with a (possibly empty) list."""
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/service-health')
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/service-health')
            assert resp.status_code == 401

    async def test_role_less_token_forbidden(self, db, app):
        """A token with no STAFF role is rejected from the tenant subset."""
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/service-health')
            assert resp.status_code == 403


class TestBoard:
    async def test_board_super_admin_ok(self, db, app):
        _, raw = await create_user_token(username='sa', roles=[Role.SUPER_ADMIN])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/service-health/board')
            assert resp.status_code == 200
            body = resp.json()
            assert len(body) > 0
            keys = {row['key'] for row in body}
            assert 'postgres' in keys
            assert all('checked_at' in row and 'status' in row for row in body)

    async def test_board_staff_forbidden(self, db, app):
        """A tenant STAFF (non-super) may not view the full board."""
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/service-health/board')
            assert resp.status_code == 403

    async def test_board_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/service-health/board')
            assert resp.status_code == 401


class TestRefresh:
    @pytest.fixture(autouse=True)
    def _no_network_probes(self, monkeypatch):
        """Replace the probe registry so a refresh touches no third-party host.

        Mirrors tests/services/test_service_health.py: only the real ``postgres``
        probe (a local test-DB round-trip) is kept, so the endpoint's assertions
        stay meaningful while the five network probes never run.
        """
        monkeypatch.setattr(shs, '_PROBES', [
            shs._Probe('postgres', 'PostgreSQL', 'core', shs._probe_postgres),
        ])

    async def test_refresh_super_admin_write_ok(self, db, app):
        """A super-admin write token refreshes and must not raise (alert=False)."""
        _, raw = await create_user_token(username='sa', roles=[Role.SUPER_ADMIN])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/service-health/refresh')
            assert resp.status_code == 200
            body = resp.json()
            assert len(body) > 0
            assert {row['key'] for row in body} >= {'postgres'}

    async def test_refresh_read_only_super_admin_forbidden(self, db, app):
        """A read-only token — even a super-admin's — cannot trigger a write."""
        _, raw = await create_user_token(
            username='sa-ro', roles=[Role.SUPER_ADMIN], read_only=True,
        )
        async with client_for(app, raw) as c:
            resp = await c.post('/api/service-health/refresh')
            assert resp.status_code == 403

    async def test_refresh_non_super_admin_forbidden(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/service-health/refresh')
            assert resp.status_code == 403
