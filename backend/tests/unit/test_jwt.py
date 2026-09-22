"""T2.2: access-token issue/verify — expiry, kid, tampering, rotation."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.utils import base64url_encode

from lifeos.domain.clock import FrozenClock
from lifeos.security.jwt import JwtService, SigningKey, TokenError, load_signing_key

T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
USER, SID = uuid.uuid4(), uuid.uuid4()


def service(clock: FrozenClock, kid: str = "k1", key: Ed25519PrivateKey | None = None) -> JwtService:
    return JwtService(SigningKey(kid, key or Ed25519PrivateKey.generate()), clock, timedelta(hours=1))


def test_issue_and_verify_roundtrip() -> None:
    clock = FrozenClock(T0)
    svc = service(clock)
    claims = svc.verify(svc.issue(USER, SID, 3))
    assert (claims.user_id, claims.session_id, claims.session_version) == (USER, SID, 3)
    assert claims.expires_at - claims.issued_at == 3600


def test_expired_token_rejected_by_injected_clock() -> None:
    clock = FrozenClock(T0)
    svc = service(clock)
    token = svc.issue(USER, SID, 1)
    clock.set(T0 + timedelta(minutes=59, seconds=59))
    svc.verify(token)
    clock.set(T0 + timedelta(hours=1))
    with pytest.raises(TokenError, match="expired"):
        svc.verify(token)


def test_unknown_kid_rejected() -> None:
    clock = FrozenClock(T0)
    token = service(clock, kid="k1").issue(USER, SID, 1)
    with pytest.raises(TokenError):
        service(clock, kid="k2").verify(token)


def test_wrong_key_same_kid_rejected() -> None:
    clock = FrozenClock(T0)
    token = service(clock).issue(USER, SID, 1)
    with pytest.raises(TokenError):
        service(clock).verify(token)  # fresh random key under the same kid


def test_tampered_payload_rejected() -> None:
    clock = FrozenClock(T0)
    svc = service(clock)
    header, _payload, sig = svc.issue(USER, SID, 1).split(".")
    forged_claims = base64url_encode(
        b'{"iss":"lifeos","sub":"%s","sid":"%s","ver":99,"jti":"x","iat":1,"exp":9999999999}'
        % (str(USER).encode(), str(SID).encode())
    ).decode()
    with pytest.raises(TokenError):
        svc.verify(f"{header}.{forged_claims}.{sig}")


def test_alg_none_and_hs256_rejected() -> None:
    clock = FrozenClock(T0)
    svc = service(clock)
    claims = {
        "iss": "lifeos",
        "sub": str(USER),
        "sid": str(SID),
        "ver": 1,
        "jti": "j",
        "iat": 1,
        "exp": 2**40,
    }
    none_token = pyjwt.encode(claims, key=None, algorithm="none", headers={"kid": "k1"})  # pyright: ignore[reportArgumentType]
    hs_token = pyjwt.encode(
        claims, "secret-used-as-hmac-key-012345678", algorithm="HS256", headers={"kid": "k1"}
    )
    for token in (none_token, hs_token, "garbage"):
        with pytest.raises(TokenError):
            svc.verify(token)


def test_rotation_keeps_retired_key_verifiable() -> None:
    clock = FrozenClock(T0)
    old_key = Ed25519PrivateKey.generate()
    old = service(clock, "k1", old_key)
    token = old.issue(USER, SID, 1)
    new = JwtService(
        SigningKey("k2", Ed25519PrivateKey.generate()),
        clock,
        timedelta(hours=1),
        verification_keys={"k1": old_key.public_key()},
    )
    assert new.verify(token).user_id == USER
    assert pyjwt.get_unverified_header(new.issue(USER, SID, 1))["kid"] == "k2"


def test_load_signing_key_pem_and_ephemeral_fallback() -> None:
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    assert load_signing_key("k1", pem.decode(), allow_ephemeral=False).kid == "k1"
    assert isinstance(
        load_signing_key("k1", "not-a-pem", allow_ephemeral=True).private_key, Ed25519PrivateKey
    )
    with pytest.raises(ValueError, match=r"Could not deserialize|Unable to load"):
        load_signing_key("k1", "not-a-pem", allow_ephemeral=False)
