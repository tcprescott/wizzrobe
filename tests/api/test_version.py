"""Tests for the unauthenticated /api/version endpoint."""

import pytest

from application.utils.build_info import build_info
from tests.api_helpers import build_api_app, client_for


@pytest.fixture(autouse=True)
def _fresh_build_info():
    build_info.cache_clear()
    yield
    build_info.cache_clear()


async def test_version_without_auth(db, monkeypatch):
    monkeypatch.setenv('GIT_SHA', 'abcdef1234567890')
    monkeypatch.setenv('GIT_COMMIT_DATE', '2026-10-18T01:00:00+02:00')
    monkeypatch.delenv('APP_VERSION', raising=False)
    async with client_for(build_api_app()) as client:
        resp = await client.get('/api/version')
    assert resp.status_code == 200
    body = resp.json()
    # The commit was the 17th in UTC, whatever the committer's offset.
    assert body['version'] == '2026.10.17'
    assert body['commit'] == 'abcdef1234567890'
    assert body['commit_short'] == 'abcdef1'


async def test_tag_build_overrides_the_date(db, monkeypatch):
    monkeypatch.setenv('APP_VERSION', 'v1.2.0')
    monkeypatch.setenv('GIT_COMMIT_DATE', '2026-10-17T00:00:00Z')
    async with client_for(build_api_app()) as client:
        resp = await client.get('/api/version')
    assert resp.json()['version'] == '1.2.0'


async def test_local_checkout_reports_dev(db, monkeypatch):
    for name in ('APP_VERSION', 'GIT_SHA', 'GIT_COMMIT_DATE'):
        monkeypatch.delenv(name, raising=False)
    async with client_for(build_api_app()) as client:
        resp = await client.get('/api/version')
    body = resp.json()
    assert body['version'] == 'dev'
    assert body['commit'] is None
    assert body['source_url'] == 'https://github.com/tcprescott/wizzrobe'
