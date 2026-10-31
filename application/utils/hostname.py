"""Hostname normalization + effective-request-host resolution.

Host-based tenant routing compares a request's ``Host`` against each tenant's
stored ``domain``. For that comparison to be reliable, both ends must be reduced
to one canonical form: the **same** normalization is applied at write-time (when
a super-admin sets ``Tenant.domain`` on ``/platform``) and at resolution-time
(when ``TenantMiddleware`` reads the incoming host). :func:`normalize_hostname`
is that single contract; storing ``https://Foo.GG/`` and matching a browser's
``Host: foo.gg`` only works because both collapse to ``foo.gg`` here.

Kept dependency-light (only :func:`env_flag`) so it is import-safe from the
middleware, services, and presentation layers alike.
"""

import re
from typing import Mapping, Optional

from application.utils.environment import env_flag

_LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
_HOSTNAME_RE = re.compile(rf'^{_LABEL}(?:\.{_LABEL})*$')

_DEFAULT_PORTS = frozenset({'80', '443'})


def scheme_for_host(host: str) -> str:
    """``'http'`` for a local dev host, else ``'https'``.

    A ``*.localhost`` / ``127.0.0.1`` host is the local dev case where TLS isn't
    available; every real domain is served over https. Shared by the OAuth
    redirect builder and the deep-link builder so both agree on the scheme.
    """
    bare = host.split(':', 1)[0]
    if bare in ('localhost', '127.0.0.1') or bare.endswith('.localhost'):
        return 'http'
    return 'https'


def normalize_hostname(value: Optional[str]) -> Optional[str]:
    """Reduce a host/domain to canonical comparison form, or ``None`` if invalid.

    Lowercases; strips surrounding whitespace, a leading scheme, any userinfo,
    and any path/query/fragment (so ``https://foo.gg/x`` → ``foo.gg``); strips a
    fully-qualified trailing dot; drops a default ``:80``/``:443`` while
    preserving any other port (``foo.localhost:8000`` stays, for dev); IDNA /
    punycode-encodes non-ascii labels; and validates the result against a
    hostname grammar. Returns ``None`` for empty or malformed input rather than a
    best-effort guess, so a bad value fails loudly at write-time instead of
    silently never routing.
    """
    if not value:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if '://' in v:
        v = v.split('://', 1)[1]
    v = v.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
    if '@' in v:
        v = v.rsplit('@', 1)[1]
    if not v:
        return None
    host, sep, port = v.rpartition(':')
    if not (sep and port.isdigit()):
        host, port = v, ''
    host = host.rstrip('.')
    if not host:
        return None
    try:
        host = host.encode('idna').decode('ascii')
    except (UnicodeError, ValueError):
        # The strict idna codec rejects some ascii hosts; leave them to the regex rather than drop them.
        pass
    if not _HOSTNAME_RE.match(host):
        return None
    if port and port not in _DEFAULT_PORTS:
        return f'{host}:{port}'
    return host


def _forwarded_host(headers: Mapping[str, str]) -> Optional[str]:
    """The nearest-proxy ``X-Forwarded-Host`` value, or ``None``.

    ``X-Forwarded-Host`` is append-ordered, so the value set by the nearest
    (trusted) proxy is the **last** one — both across comma-separated values
    *and* across repeated header lines. A proxy may append either way, so read
    all header instances (``getlist`` when the mapping supports it, e.g.
    Starlette ``Headers``) and take the last comma-value of the last line. Using
    only ``.get()`` (first instance) would read a client-forgeable leftmost value.
    """
    getlist = getattr(headers, 'getlist', None)
    values = list(getlist('x-forwarded-host')) if callable(getlist) else []
    if not values:
        single = headers.get('x-forwarded-host')
        values = [single] if single else []
    if not values:
        return None
    last = values[-1].split(',')[-1].strip()
    return last or None


def effective_request_host(headers: Mapping[str, str]) -> Optional[str]:
    """The normalized host this request is addressed to, or ``None``.

    Uses the real ``Host`` header by default. ``X-Forwarded-Host`` is honored
    only when ``TRUST_FORWARDED_HOST`` is set — off by default because a client
    can forge it — and then only its last (nearest-proxy) value (see
    :func:`_forwarded_host`).
    """
    raw: Optional[str] = None
    if env_flag('TRUST_FORWARDED_HOST'):
        raw = _forwarded_host(headers)
    if not raw:
        raw = headers.get('host')
    return normalize_hostname(raw)
