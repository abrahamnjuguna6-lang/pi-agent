"""T2.1: envelope encryption, key rotation, keyed hashes, Argon2id, email normalization."""

import pytest

from lifeos.security.crypto import DecryptionError, EnvelopeCipher, LocalKeyProvider, derive_key
from lifeos.security.hashing import KeyedHasher, PasswordHasher, generate_token, normalize_email

FAST_ARGON = {"memory_kib": 19456, "time_cost": 2}


def cipher(active: str = "k1", **secrets: bytes) -> EnvelopeCipher:
    keys = secrets or {"k1": b"secret-one"}
    return EnvelopeCipher(LocalKeyProvider(keys, active))


# --------------------------------------------------------------------------- envelope encryption


def test_roundtrip_and_randomized_ciphertext() -> None:
    c = cipher()
    a, b = c.encrypt_str("alice@example.com"), c.encrypt_str("alice@example.com")
    assert a != b  # randomized: ciphertext is not usable for lookups (design §21.7)
    assert c.decrypt_str(a) == c.decrypt_str(b) == "alice@example.com"


def test_tampering_detected() -> None:
    c = cipher()
    blob = bytearray(c.encrypt_str("secret"))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        c.decrypt(bytes(blob))


def test_associated_data_is_bound() -> None:
    c = cipher()
    blob = c.encrypt(b"payload", associated_data=b"user-1")
    assert c.decrypt(blob, associated_data=b"user-1") == b"payload"
    with pytest.raises(DecryptionError):
        c.decrypt(blob, associated_data=b"user-2")


def test_key_rotation_old_blobs_still_decrypt() -> None:
    old = cipher("k1", k1=b"first")
    blob = old.encrypt_str("pii")
    rotated = cipher("k2", k1=b"first", k2=b"second")
    assert EnvelopeCipher.key_version_of(blob) == "k1"
    assert rotated.decrypt_str(blob) == "pii"
    assert EnvelopeCipher.key_version_of(rotated.encrypt_str("pii")) == "k2"


def test_unknown_key_version_fails_closed() -> None:
    blob = cipher("k1", k1=b"first").encrypt_str("pii")
    with pytest.raises(DecryptionError):
        cipher("k9", k9=b"other").decrypt(blob)


def test_wrong_secret_same_version_fails() -> None:
    blob = cipher("k1", k1=b"first").encrypt_str("pii")
    with pytest.raises(DecryptionError):
        cipher("k1", k1=b"different").decrypt(blob)


def test_garbage_input_fails_closed() -> None:
    with pytest.raises(DecryptionError):
        cipher().decrypt(b"not-a-blob")


def test_active_version_must_exist() -> None:
    with pytest.raises(ValueError, match="not configured"):
        LocalKeyProvider({"k1": b"x"}, "k2")


def test_derive_key_separates_purposes() -> None:
    assert derive_key(b"root", "a") != derive_key(b"root", "b")
    assert len(derive_key(b"root", "a")) == 32


# --------------------------------------------------------------------------- keyed hashes


def test_email_lookup_hash_deterministic_and_normalized() -> None:
    h = KeyedHasher(b"root")
    assert h.email_lookup_hash("  Alice@Example.COM ") == h.email_lookup_hash("alice@example.com")
    assert h.email_lookup_hash("alice@example.com") != h.email_lookup_hash("bob@example.com")


def test_hmac_purposes_are_independent() -> None:
    h = KeyedHasher(b"root")
    assert h.digest("refresh_token", "abc") != h.digest("one_time_token", "abc")


def test_different_root_keys_differ() -> None:
    assert KeyedHasher(b"a").email_lookup_hash("x@y.z") != KeyedHasher(b"b").email_lookup_hash("x@y.z")


def test_normalize_email_unicode() -> None:
    assert normalize_email("ＡＬＩＣＥ@example.com") == "alice@example.com"  # fullwidth → ASCII via NFKC


def test_generate_token_entropy() -> None:
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)  # 32 bytes url-safe


# --------------------------------------------------------------------------- passwords


def test_argon2id_verify_and_reject() -> None:
    hasher = PasswordHasher(**FAST_ARGON)
    stored = hasher.hash("correct horse battery")
    assert stored.startswith("$argon2id$")
    assert hasher.verify(stored, "correct horse battery")
    assert not hasher.verify(stored, "wrong")
    assert not hasher.verify("not-a-hash", "anything")


def test_rehash_detected_when_parameters_change() -> None:
    weak = PasswordHasher(memory_kib=19456, time_cost=2).hash("pw-1234567")
    assert PasswordHasher(memory_kib=19456, time_cost=3).needs_rehash(weak)
    assert not PasswordHasher(memory_kib=19456, time_cost=2).needs_rehash(weak)


def test_dummy_verify_runs_without_error() -> None:
    PasswordHasher(**FAST_ARGON).dummy_verify("whatever")
