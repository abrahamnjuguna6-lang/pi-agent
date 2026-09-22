"""Envelope encryption for application-level secrets and PII (design §21.7, §33.5).

Each value is encrypted with a fresh random 256-bit data key (DEK) using AES-256-GCM; the DEK is
wrapped by a versioned key-encryption key (KEK) from a `KeyProvider`. The blob records the KEK
version, so old values stay decryptable after rotation.

Blob layout (bytes):  b"v1" | kek_version_len (1) | kek_version | wrapped_dek (60) | nonce (12) | ciphertext
  wrapped_dek = nonce (12) || AES-GCM(KEK, DEK) (32 + 16 tag)
"""

from __future__ import annotations

import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT = b"v1"
_NONCE = 12
_WRAPPED_DEK = _NONCE + 32 + 16


class DecryptionError(Exception):
    """Ciphertext is malformed, tampered with, or was encrypted with an unknown key."""


def derive_key(secret: bytes, purpose: str, length: int = 32) -> bytes:
    """HKDF-SHA256 subkey for one purpose; distinct purposes never share key material."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"lifeos/v1", info=purpose.encode()).derive(
        secret
    )


class KeyProvider(Protocol):
    @property
    def active_version(self) -> str: ...

    def kek(self, version: str) -> bytes:
        """Return the 32-byte key-encryption key for `version` (KeyError if unknown)."""
        ...


class LocalKeyProvider:
    """KEKs derived from configured secrets (sourced from the secret manager in deployed envs).

    A cloud-KMS provider implementing the same protocol replaces this in T18.3.
    """

    def __init__(self, secrets: dict[str, bytes], active_version: str) -> None:
        if active_version not in secrets:
            raise ValueError(f"active key version {active_version!r} not configured")
        self._keks = {v: derive_key(s, "kek") for v, s in secrets.items()}
        self._active = active_version

    @property
    def active_version(self) -> str:
        return self._active

    def kek(self, version: str) -> bytes:
        return self._keks[version]


class EnvelopeCipher:
    def __init__(self, keys: KeyProvider) -> None:
        self._keys = keys

    def encrypt(self, plaintext: bytes, associated_data: bytes | None = None) -> bytes:
        version = self._keys.active_version.encode()
        if len(version) > 255:
            raise ValueError("key version too long")
        dek = AESGCM.generate_key(bit_length=256)
        wrap_nonce = os.urandom(_NONCE)
        wrapped = wrap_nonce + AESGCM(self._keys.kek(self._keys.active_version)).encrypt(
            wrap_nonce, dek, version
        )
        nonce = os.urandom(_NONCE)
        body = AESGCM(dek).encrypt(nonce, plaintext, associated_data)
        return FORMAT + bytes([len(version)]) + version + wrapped + nonce + body

    def decrypt(self, blob: bytes, associated_data: bytes | None = None) -> bytes:
        try:
            if blob[:2] != FORMAT:
                raise DecryptionError("unknown ciphertext format")
            vlen = blob[2]
            version = blob[3 : 3 + vlen]
            offset = 3 + vlen
            wrapped = blob[offset : offset + _WRAPPED_DEK]
            nonce = blob[offset + _WRAPPED_DEK : offset + _WRAPPED_DEK + _NONCE]
            body = blob[offset + _WRAPPED_DEK + _NONCE :]
            kek = self._keys.kek(version.decode())
            dek = AESGCM(kek).decrypt(wrapped[:_NONCE], wrapped[_NONCE:], version)
            return AESGCM(dek).decrypt(nonce, body, associated_data)
        except (InvalidTag, KeyError, IndexError, UnicodeDecodeError) as exc:
            raise DecryptionError("ciphertext could not be decrypted") from exc

    def encrypt_str(self, value: str, associated_data: bytes | None = None) -> bytes:
        return self.encrypt(value.encode("utf-8"), associated_data)

    def decrypt_str(self, blob: bytes, associated_data: bytes | None = None) -> str:
        return self.decrypt(blob, associated_data).decode("utf-8")

    @staticmethod
    def key_version_of(blob: bytes) -> str:
        return blob[3 : 3 + blob[2]].decode()
