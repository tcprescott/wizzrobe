"""Seal a short secret for storage, and open it again.

AES-GCM under a key derived (HKDF-SHA256) from ``STORAGE_SECRET``, so no new
setting is needed to deploy it. Each value is bound to a *purpose* and a
*context* (the tenant id, say) as associated data: a sealed value copied into
another community's row, or into another kind of secret, fails to open.

Rotating ``STORAGE_SECRET`` makes every sealed value unreadable; :func:`open_`
then returns ``None`` and the owning screen asks for the secret again.
"""

import base64
import os
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = 'v1'


def _key(purpose: str) -> bytes:
    secret = (os.environ.get('STORAGE_SECRET') or '').encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b'wizzrobe-secret-box',
                info=purpose.encode()).derive(secret)


def seal(value: str, *, purpose: str, context: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_key(purpose)).encrypt(nonce, value.encode(), context.encode())
    return f'{_VERSION}:{base64.urlsafe_b64encode(nonce + sealed).decode()}'


def open_(token: str, *, purpose: str, context: str) -> Optional[str]:
    """The sealed value, or ``None`` if it can't be opened here (wrong key, row or purpose)."""
    version, _, body = (token or '').partition(':')
    if version != _VERSION or not body:
        return None
    try:
        raw = base64.urlsafe_b64decode(body.encode())
        return AESGCM(_key(purpose)).decrypt(raw[:12], raw[12:], context.encode()).decode()
    except (InvalidTag, ValueError):
        return None
