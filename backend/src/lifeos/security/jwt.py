"""Access-token issuing and verification (design §21.1).

EdDSA (Ed25519) with a `kid` header for rotation. Expiry is checked against the injected Clock
(not the library's wall clock) so behaviour is deterministic under test.
Claims: sub=user_id, sid=auth_session_id, jti, iat, exp, ver=session_version.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from lifeos.domain.clock import Clock

log = logging.getLogger(__name__)
ALGORITHM = "EdDSA"
ISSUER = "lifeos"


class TokenError(Exception):
    """Token is malformed, has a bad signature/kid/issuer, or is expired."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    session_version: int
    jti: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()


def load_signing_key(kid: str, private_pem: str, *, allow_ephemeral: bool) -> SigningKey:
    """Load an Ed25519 PEM private key; in development/test, fall back to an ephemeral key."""
    try:
        key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    except ValueError:
        if not allow_ephemeral:
            raise
        log.warning("JWT_PRIVATE_KEY is not a PEM key; using an ephemeral Ed25519 key (dev/test only)")
        return SigningKey(kid, Ed25519PrivateKey.generate())
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("JWT_PRIVATE_KEY must be an Ed25519 private key")
    return SigningKey(kid, key)


class JwtService:
    def __init__(
        self,
        signing_key: SigningKey,
        clock: Clock,
        ttl: timedelta,
        verification_keys: dict[str, Ed25519PublicKey] | None = None,
    ) -> None:
        self._signing = signing_key
        self._clock = clock
        self._ttl = ttl
        # Retired public keys stay verifiable until their tokens expire (rotation by kid).
        self._verify_keys = {signing_key.kid: signing_key.public_key, **(verification_keys or {})}

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, user_id: uuid.UUID, session_id: uuid.UUID, session_version: int) -> str:
        now = int(self._clock.now().timestamp())
        claims = {
            "iss": ISSUER,
            "sub": str(user_id),
            "sid": str(session_id),
            "ver": session_version,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + self.ttl_seconds,
        }
        return jwt.encode(
            claims, self._signing.private_key, algorithm=ALGORITHM, headers={"kid": self._signing.kid}
        )

    def verify(self, token: str) -> AccessClaims:
        try:
            header = jwt.get_unverified_header(token)
            key = self._verify_keys.get(str(header.get("kid")))
            if key is None:
                raise TokenError("unknown signing key")
            payload = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"verify_exp": False, "require": ["exp", "iat", "sub", "sid", "ver", "jti"]},
            )
            claims = AccessClaims(
                user_id=uuid.UUID(payload["sub"]),
                session_id=uuid.UUID(payload["sid"]),
                session_version=int(payload["ver"]),
                jti=str(payload["jti"]),
                issued_at=int(payload["iat"]),
                expires_at=int(payload["exp"]),
            )
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise TokenError("invalid token") from exc
        if self._clock.now().timestamp() >= claims.expires_at:
            raise TokenError("token expired")
        return claims
