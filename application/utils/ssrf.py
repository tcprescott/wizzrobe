"""Shared SSRF guard for server-side outbound requests.

Before the app dereferences a URL that a user supplied — staff-managed webhook
targets, browser-supplied web-push endpoints — the destination host is resolved
and rejected when it points at a non-public address. This keeps an attacker from
aiming an outbound request at internal services, loopback, or the cloud metadata
endpoint (169.254.169.254).
"""

import asyncio
import ipaddress
import socket
from typing import Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit


async def ensure_public_host(hostname: str, *, subject: str = 'URL') -> List[str]:
    """Raise ``ValueError`` unless every address ``hostname`` resolves to is public.

    Rejects private, loopback, link-local, reserved, multicast, and unspecified
    ranges — covering RFC1918, ``127.0.0.0/8``, ``169.254.0.0/16`` (incl. the
    ``169.254.169.254`` metadata endpoint), ``::1``, ``fc00::/7``, ``100.64.0.0/10``, and anything else not globally routable.

    ``subject`` is woven into the error message (e.g. "Webhook URL", "Push
    subscription endpoint") so callers surface a domain-appropriate message.
    """
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve {subject} host '{hostname}'") from exc
    addresses: List[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            # Not redundant: catches what the flags miss, notably 100.64.0.0/10 (carrier-grade NAT).
            or not ip.is_global
        ):
            raise ValueError(f"{subject} must point to a public address")
        addresses.append(str(ip))
    return addresses


async def pin_public_url(
    url: str, *, subject: str = 'URL',
) -> Tuple[str, Dict[str, str], Dict[str, str]]:
    """``url`` rewritten to connect to an address :func:`ensure_public_host` approved.

    Checking a hostname and then handing it to httpx resolves it twice, and a
    rebinding DNS answer can differ between the two. Connecting to the checked
    IP, with the original name as ``Host`` and as the TLS server name (which is
    also what the certificate is verified against), closes that gap.

    Returns ``(url, headers, extensions)`` to pass to the request.
    """
    parts = urlsplit(url)
    hostname = parts.hostname or ''
    ip = ipaddress.ip_address((await ensure_public_host(hostname, subject=subject))[0])
    literal = f'[{ip}]' if ip.version == 6 else str(ip)
    netloc = f'{literal}:{parts.port}' if parts.port else literal
    host_header = f'{hostname}:{parts.port}' if parts.port else hostname
    pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return pinned, {'Host': host_header}, {'sni_hostname': hostname}
