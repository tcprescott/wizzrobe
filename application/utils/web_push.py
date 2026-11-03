"""Web Push protocol primitives — no I/O, no ORM.

Implements the two cryptographic halves of sending a Web Push message:

- **Message encryption** (RFC 8291): the payload is encrypted to the
  subscription's ``p256dh``/``auth`` client keys using ECDH on P-256 plus
  HKDF-SHA256, packaged in the ``aes128gcm`` content coding (RFC 8188).
- **VAPID** (RFC 8292): each request to a push service carries an ES256 JWT
  signed by the server's long-lived VAPID key, proving the sender's identity.

Kept dependency-light (``cryptography`` only) and synchronous — encryption is
pure CPU work; the HTTP delivery lives in ``WebPushService``.
"""

import base64
import json
import os
import struct
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_RECORD_SIZE = 4096
# aes128gcm overhead: 86-byte header + 16-byte AEAD tag + 1 padding delimiter.
MAX_PLAINTEXT_LENGTH = _RECORD_SIZE - 86 - 16 - 1

VAPID_TOKEN_LIFETIME_SECONDS = 12 * 60 * 60  # max allowed is 24h; stay well under


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def generate_vapid_keys() -> Tuple[str, str]:
    """Generate a fresh VAPID keypair.

    Returns ``(private_key, public_key)`` — the private key as a base64url raw
    32-byte P-256 scalar (the format ``VAPID_PRIVATE_KEY`` expects), the public
    key as a base64url uncompressed point (what browsers take as
    ``applicationServerKey``).
    """
    private = ec.generate_private_key(ec.SECP256R1())
    private_raw = private.private_numbers().private_value.to_bytes(32, 'big')
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64url_encode(private_raw), b64url_encode(public_raw)


def load_vapid_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    """Load a VAPID private key from its base64url raw 32-byte scalar form."""
    raw = b64url_decode(value.strip())
    if len(raw) != 32:
        raise ValueError('VAPID private key must be a base64url-encoded 32-byte value')
    return ec.derive_private_key(int.from_bytes(raw, 'big'), ec.SECP256R1())


def public_key_b64url(private_key: ec.EllipticCurvePrivateKey) -> str:
    """The applicationServerKey the browser needs, derived from the private key."""
    return b64url_encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


def vapid_authorization(
    endpoint: str,
    private_key: ec.EllipticCurvePrivateKey,
    subject: str,
    *,
    now: Optional[int] = None,
) -> str:
    """Build the ``Authorization: vapid t=<jwt>, k=<key>`` header value (RFC 8292)."""
    parsed = urlparse(endpoint)
    claims = {
        'aud': f'{parsed.scheme}://{parsed.netloc}',
        'exp': (now or int(time.time())) + VAPID_TOKEN_LIFETIME_SECONDS,
        'sub': subject,
    }
    header = {'typ': 'JWT', 'alg': 'ES256'}
    signing_input = (
        b64url_encode(json.dumps(header, separators=(',', ':')).encode())
        + '.'
        + b64url_encode(json.dumps(claims, separators=(',', ':')).encode())
    )
    # JWS wants the raw 64-byte r||s signature, not the DER form cryptography emits.
    der_signature = private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    jwt = signing_input + '.' + b64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    return f'vapid t={jwt}, k={public_key_b64url(private_key)}'


def encrypt_payload(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    _as_private_key: Optional[ec.EllipticCurvePrivateKey] = None,
    _salt: Optional[bytes] = None,
) -> bytes:
    """Encrypt ``plaintext`` for a subscription per RFC 8291 (aes128gcm).

    ``p256dh``/``auth`` are the base64url values from the browser's
    ``PushSubscription``. The two keyword arguments exist solely so tests can
    pin the ephemeral key and salt to the RFC 8291 vector.
    """
    if len(plaintext) > MAX_PLAINTEXT_LENGTH:
        raise ValueError(f'Push payload exceeds {MAX_PLAINTEXT_LENGTH} bytes')
    ua_public_bytes = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    if len(ua_public_bytes) != 65 or ua_public_bytes[0] != 0x04:
        raise ValueError('p256dh must be a 65-byte uncompressed P-256 point')
    if len(auth_secret) != 16:
        raise ValueError('auth must be a 16-byte secret')

    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)
    as_private = _as_private_key or ec.generate_private_key(ec.SECP256R1())
    as_public_bytes = as_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_public)

    key_info = b'WebPush: info\x00' + ua_public_bytes + as_public_bytes
    ikm = HKDF(hashes.SHA256(), length=32, salt=auth_secret, info=key_info).derive(ecdh_secret)

    salt = _salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), length=16, salt=salt, info=b'Content-Encoding: aes128gcm\x00').derive(ikm)
    nonce = HKDF(hashes.SHA256(), length=12, salt=salt, info=b'Content-Encoding: nonce\x00').derive(ikm)

    # Single record: plaintext + the 0x02 last-record padding delimiter.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b'\x02', None)
    header = (
        salt
        + struct.pack('!I', _RECORD_SIZE)
        + bytes([len(as_public_bytes)])
        + as_public_bytes
    )
    return header + ciphertext
