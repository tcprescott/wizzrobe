"""Unit tests for the in-process REST API rate limiter."""

import pytest
from fastapi import HTTPException

from api.rate_limit import _hits, rate_limit


class _FakeRequest:
    def __init__(self, headers=None, host='10.0.0.1'):
        self.headers = headers or {}
        self.client = type('Client', (), {'host': host})()


async def test_requests_under_limit_pass(monkeypatch):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '3')
    _hits.clear()
    req = _FakeRequest(host='10.0.0.10')
    for _ in range(3):
        await rate_limit(req)


async def test_request_over_limit_429(monkeypatch):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '3')
    _hits.clear()
    req = _FakeRequest(host='10.0.0.11')
    for _ in range(3):
        await rate_limit(req)
    with pytest.raises(HTTPException) as exc:
        await rate_limit(req)
    assert exc.value.status_code == 429
    assert 'Retry-After' in exc.value.headers


def _bearer(suffix: str) -> dict:
    return {'authorization': f'Bearer wizzrobe_pat_{suffix}'}


async def test_distinct_wellformed_tokens_isolated_across_ips(monkeypatch):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '2')
    _hits.clear()
    a = _FakeRequest(headers=_bearer('a' * 32), host='10.0.0.20')
    b = _FakeRequest(headers=_bearer('b' * 32), host='10.0.0.21')
    for _ in range(2):
        await rate_limit(a)
    await rate_limit(b)
    with pytest.raises(HTTPException):
        await rate_limit(a)


async def test_wellformed_token_flood_from_one_ip_is_capped(monkeypatch):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '3')
    _hits.clear()
    for i in range(3):
        await rate_limit(_FakeRequest(headers=_bearer(f'{i:032d}'), host='9.9.9.9'))
    with pytest.raises(HTTPException):
        await rate_limit(_FakeRequest(headers=_bearer('f' * 32), host='9.9.9.9'))


async def test_key_dict_bounded_under_token_flood(monkeypatch):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '5')
    _hits.clear()
    for i in range(500):
        try:
            await rate_limit(_FakeRequest(headers=_bearer(f'{i:032d}'), host='7.7.7.7'))
        except HTTPException:
            pass
    # 1 IP key + at most `limit` per-token keys created before the ceiling trips.
    assert len(_hits) <= 1 + 5


async def test_garbage_tokens_fall_back_to_ip(monkeypatch):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MIN', '2')
    _hits.clear()
    for i in range(2):
        await rate_limit(_FakeRequest(headers={'authorization': f'Bearer junk{i}'}, host='9.9.9.9'))
    with pytest.raises(HTTPException):
        await rate_limit(_FakeRequest(headers={'authorization': 'Bearer another-garbage'}, host='9.9.9.9'))
