"""Password hashing, keyed lookup hashes, and one-time token generation (design §21)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from typing import Literal

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from lifeos.security.crypto import derive_key

HmacPurpose = Literal["email_lookup", "refresh_token", "one_time_token", "push_token", "context_ref"]


def normalize_email(email: str) -> str:
    """Canonical form used for the lookup hash: NFKC, trimmed, lower-cased."""
    return unicodedata.normalize("NFKC", email).strip().lower()


class PasswordHasher:
    """Argon2id with rehash-on-parameter-change (design §21.6)."""

    def __init__(self, memory_kib: int = 65536, time_cost: int = 3, parallelism: int = 1) -> None:
        self._argon = _Argon2(memory_cost=memory_kib, time_cost=time_cost, parallelism=parallelism)

    def hash(self, password: str) -> str:
        return self._argon.hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self._argon.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        return self._argon.check_needs_rehash(stored_hash)

    def dummy_verify(self, password: str) -> None:
        """Spend comparable time when the account does not exist (prevents user enumeration by timing)."""
        self.verify(_DUMMY_HASH, password)


_DUMMY_HASH = _Argon2(memory_cost=19456, time_cost=2, parallelism=1).hash("lifeos-dummy-password")


class KeyedHasher:
    """HMAC-SHA-256 with an independent derived key per purpose; the root key never hashes directly."""

    def __init__(self, root_key: bytes) -> None:
        self._root = root_key
        self._keys: dict[str, bytes] = {}

    def _key(self, purpose: HmacPurpose) -> bytes:
        if purpose not in self._keys:
            self._keys[purpose] = derive_key(self._root, f"hmac:{purpose}")
        return self._keys[purpose]

    def digest(self, purpose: HmacPurpose, value: str) -> bytes:
        return hmac.new(self._key(purpose), value.encode("utf-8"), hashlib.sha256).digest()

    def email_lookup_hash(self, email: str) -> bytes:
        return self.digest("email_lookup", normalize_email(email))


def generate_token(nbytes: int = 32) -> str:
    """High-entropy URL-safe opaque token (refresh, verification, reset, tickets)."""
    return secrets.token_urlsafe(nbytes)
