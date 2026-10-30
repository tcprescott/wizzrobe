"""Tests for the Web Push protocol primitives (application/utils/web_push.py).

The encryption path is pinned to the RFC 8291 Appendix A test vector — the
implementation must reproduce the spec's exact bytes when the ephemeral key
and salt are fixed. VAPID output is verified by checking the ES256 signature
with the public key and inspecting the JWT claims.
"""

import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from application.utils.web_push import (
    MAX_PLAINTEXT_LENGTH,
    b64url_decode,
    b64url_encode,
    encrypt_payload,
    generate_vapid_keys,
    load_vapid_private_key,
    public_key_b64url,
    vapid_authorization,
)

# RFC 8291 Appendix A vector
RFC_PLAINTEXT = b'When I grow up, I want to be a watermelon'
RFC_AS_PRIVATE = 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw'
RFC_UA_PRIVATE = 'q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94'
RFC_UA_PUBLIC = 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4'
RFC_AUTH = 'BTBZMqHH6r4Tts7J_aSIgg'
RFC_SALT = 'DGv6ra1nlYgDCS1FRnbzlw'
RFC_MESSAGE = (
    'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml'
    'mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT'
    'pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN'
)


def decrypt_payload(message: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """Receiver side of RFC 8291, for round-trip verification."""
    salt = message[:16]
    record_size = struct.unpack('!I', message[16:20])[0]
    assert record_size == 4096
    key_length = message[20]
    as_public_bytes = message[21:21 + key_length]
    ciphertext = message[21 + key_length:]

    ua_public_bytes = ua_private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    as_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public_bytes)
    ecdh_secret = ua_private.exchange(ec.ECDH(), as_public)
    key_info = b'WebPush: info\x00' + ua_public_bytes + as_public_bytes
    ikm = HKDF(hashes.SHA256(), length=32, salt=auth, info=key_info).derive(ecdh_secret)
    cek = HKDF(hashes.SHA256(), length=16, salt=salt, info=b'Content-Encoding: aes128gcm\x00').derive(ikm)
    nonce = HKDF(hashes.SHA256(), length=12, salt=salt, info=b'Content-Encoding: nonce\x00').derive(ikm)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert padded.endswith(b'\x02')
    return padded[:-1]


class TestEncryption:
    def test_rfc8291_appendix_a_vector(self):
        message = encrypt_payload(
            RFC_PLAINTEXT,
            RFC_UA_PUBLIC,
            RFC_AUTH,
            _as_private_key=load_vapid_private_key(RFC_AS_PRIVATE),
            _salt=b64url_decode(RFC_SALT),
        )
        assert message == b64url_decode(RFC_MESSAGE)

    def test_roundtrip_with_fresh_keys(self):
        ua_private = ec.generate_private_key(ec.SECP256R1())
        p256dh = b64url_encode(
            ua_private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        )
        auth = b64url_encode(b'0123456789abcdef')
        plaintext = json.dumps({'web_push': 8030, 'notification': {'title': 'hi'}}).encode()

        message = encrypt_payload(plaintext, p256dh, auth)

        assert decrypt_payload(message, ua_private, b'0123456789abcdef') == plaintext

    def test_each_message_uses_fresh_salt_and_key(self):
        first = encrypt_payload(b'x', RFC_UA_PUBLIC, RFC_AUTH)
        second = encrypt_payload(b'x', RFC_UA_PUBLIC, RFC_AUTH)
        assert first != second

    def test_rejects_malformed_client_keys(self):
        with pytest.raises(ValueError, match='p256dh'):
            encrypt_payload(b'x', b64url_encode(b'\x04' + b'a' * 10), RFC_AUTH)
        with pytest.raises(ValueError, match='auth'):
            encrypt_payload(b'x', RFC_UA_PUBLIC, b64url_encode(b'short'))

    def test_rejects_oversized_payload(self):
        with pytest.raises(ValueError, match='exceeds'):
            encrypt_payload(b'x' * (MAX_PLAINTEXT_LENGTH + 1), RFC_UA_PUBLIC, RFC_AUTH)


class TestVapid:
    def test_generate_and_load_roundtrip(self):
        private_b64, public_b64 = generate_vapid_keys()
        loaded = load_vapid_private_key(private_b64)
        assert public_key_b64url(loaded) == public_b64
        assert len(b64url_decode(public_b64)) == 65

    def test_load_rejects_wrong_length(self):
        with pytest.raises(ValueError, match='32-byte'):
            load_vapid_private_key(b64url_encode(b'too short'))

    def test_authorization_header_signs_valid_es256_jwt(self):
        private_b64, _ = generate_vapid_keys()
        private_key = load_vapid_private_key(private_b64)
        header_value = vapid_authorization(
            'https://push.example.net/send/abc123', private_key, 'mailto:ops@example.com',
            now=1_700_000_000,
        )

        assert header_value.startswith('vapid t=')
        token_part, key_part = header_value[len('vapid '):].split(', ')
        jwt = token_part[len('t='):]
        assert key_part[len('k='):] == public_key_b64url(private_key)

        header_b64, claims_b64, signature_b64 = jwt.split('.')
        assert json.loads(b64url_decode(header_b64)) == {'typ': 'JWT', 'alg': 'ES256'}
        claims = json.loads(b64url_decode(claims_b64))
        assert claims['aud'] == 'https://push.example.net'
        assert claims['sub'] == 'mailto:ops@example.com'
        assert claims['exp'] == 1_700_000_000 + 12 * 60 * 60

        raw_signature = b64url_decode(signature_b64)
        assert len(raw_signature) == 64
        der = encode_dss_signature(
            int.from_bytes(raw_signature[:32], 'big'), int.from_bytes(raw_signature[32:], 'big')
        )
        private_key.public_key().verify(
            der, f'{header_b64}.{claims_b64}'.encode(), ec.ECDSA(hashes.SHA256())
        )
