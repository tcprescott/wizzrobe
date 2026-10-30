"""Unit tests for hostname normalization + effective-request-host resolution.

These are the shared contract between write-time (``Tenant.domain`` on
``/platform``) and resolution-time (``TenantMiddleware``): a domain routes iff
both ends normalize to the same string.
"""

import pytest

from application.utils.hostname import effective_request_host, normalize_hostname


@pytest.mark.parametrize('raw,expected', [
    ('foo.gg', 'foo.gg'),
    ('  FOO.GG  ', 'foo.gg'),           # trim + lowercase
    ('https://foo.gg', 'foo.gg'),       # strip scheme
    ('https://foo.gg/path?q=1#f', 'foo.gg'),  # strip path/query/fragment
    ('foo.gg.', 'foo.gg'),              # strip fully-qualified trailing dot
    ('foo.gg:443', 'foo.gg'),           # drop default https port
    ('foo.gg:80', 'foo.gg'),            # drop default http port
    ('foo.gg:8000', 'foo.gg:8000'),     # keep a non-default port
    ('second.localhost:8000', 'second.localhost:8000'),  # dev host keeps its port
    ('user@foo.gg', 'foo.gg'),          # strip userinfo
    ('localhost', 'localhost'),         # single label is valid
    ('sub.foo.gg', 'sub.foo.gg'),
])
def test_normalize_hostname_valid(raw, expected):
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize('raw', [
    None, '', '   ', 'has space.gg', 'foo_bar.gg', 'foo..gg', '-foo.gg', 'foo-.gg',
    'https://',
])
def test_normalize_hostname_invalid(raw):
    assert normalize_hostname(raw) is None


def test_effective_host_uses_host_header_by_default(monkeypatch):
    monkeypatch.delenv('TRUST_FORWARDED_HOST', raising=False)
    headers = {'host': 'foo.gg', 'x-forwarded-host': 'evil.gg'}
    assert effective_request_host(headers) == 'foo.gg'


def test_effective_host_honors_forwarded_when_trusted(monkeypatch):
    monkeypatch.setenv('TRUST_FORWARDED_HOST', 'true')
    headers = {'host': 'platform', 'x-forwarded-host': 'foo.gg'}
    assert effective_request_host(headers) == 'foo.gg'


def test_effective_host_takes_last_forwarded_value(monkeypatch):
    monkeypatch.setenv('TRUST_FORWARDED_HOST', 'true')
    # Append-ordered: the leftmost is client-supplied/forgeable, the rightmost is
    # set by the nearest trusted proxy. The last value must win.
    headers = {'host': 'platform', 'x-forwarded-host': 'evil.gg, foo.gg'}
    assert effective_request_host(headers) == 'foo.gg'


def test_effective_host_last_of_repeated_forwarded_headers(monkeypatch):
    monkeypatch.setenv('TRUST_FORWARDED_HOST', 'true')
    from starlette.datastructures import Headers
    # Two separate X-Forwarded-Host lines (proxy appended a new header rather than
    # extending the comma list): the last line is the nearest trusted proxy.
    headers = Headers(raw=[
        (b'host', b'platform'),
        (b'x-forwarded-host', b'evil.gg'),
        (b'x-forwarded-host', b'foo.gg'),
    ])
    assert effective_request_host(headers) == 'foo.gg'


def test_effective_host_none_when_no_host(monkeypatch):
    monkeypatch.delenv('TRUST_FORWARDED_HOST', raising=False)
    assert effective_request_host({}) is None
