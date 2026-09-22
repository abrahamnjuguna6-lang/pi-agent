"""Registration, verification, login/lockout, refresh rotation, logout, password reset (design §21).

All auth operations run in `system_session` (no user identity exists yet, or the operation
spans the user's sessions); every query filters explicitly by user or token hash.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.config import Settings
from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.auth.sessions import AuthContext, SessionCache
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError
from lifeos.notifications.email import EmailKind, EmailMessage, EmailSender
from lifeos.security.crypto import EnvelopeCipher
from lifeos.security.hashing import KeyedHasher, PasswordHasher, generate_token, normalize_email
from lifeos.security.jwt import JwtService
from lifeos.security.ratelimit import SlidingWindow

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MAX_LENGTH = 128


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret


class AuthService:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        clock: Clock,
        passwords: PasswordHasher,
        hasher: KeyedHasher,
        cipher: EnvelopeCipher,
        jwt: JwtService,
        email: EmailSender,
        session_cache: SessionCache,
    ) -> None:
        self._s = settings
        self._maker = sessionmaker
        self._clock = clock
        self._passwords = passwords
        self._hasher = hasher
        self._cipher = cipher
        self._jwt = jwt
        self._email = email
        self._cache = session_cache
        window = timedelta(minutes=settings.login_lockout_window_minutes)
        self._account_failures = SlidingWindow(redis, clock, "auth:fail:acct", window)
        self._ip_failures = SlidingWindow(redis, clock, "auth:fail:ip", window)

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        return self._clock.now()

    def _validate_email(self, email: str) -> str:
        normalized = normalize_email(email)
        if not _EMAIL_RE.match(normalized) or len(normalized) > 254:
            raise DomainError("VALIDATION_ERROR", "Enter a valid email address", {"field": "email"})
        return normalized

    def _validate_password(self, password: str) -> None:
        if len(password) < self._s.password_min_length or len(password) > PASSWORD_MAX_LENGTH:
            raise DomainError(
                "WEAK_PASSWORD",
                f"Password must be {self._s.password_min_length}–{PASSWORD_MAX_LENGTH} characters",
                {"min_length": self._s.password_min_length},
            )

    async def _find_user_by_email(self, s: AsyncSession, email: str) -> m.User | None:
        lookup = self._hasher.email_lookup_hash(email)
        return (
            await s.execute(select(m.User).where(m.User.email_lookup_hash == lookup))
        ).scalar_one_or_none()

    def _email_of(self, user: m.User) -> str:
        return self._cipher.decrypt_str(user.email_ciphertext, associated_data=b"users.email")

    async def _send(self, to: str, kind: EmailKind, subject: str, body: str, link: str | None = None) -> None:
        await self._email.send(EmailMessage(to=to, kind=kind, subject=subject, body=body, link=link))

    def _link(self, path: str, token: str) -> str:
        return f"{self._s.public_app_url.rstrip('/')}/{path}?token={token}"

    async def _issue_verification(self, s: AsyncSession, user: m.User, email: str) -> None:
        token = generate_token()
        s.add(
            m.EmailVerificationToken(
                user_id=user.id,
                token_hash=self._hasher.digest("one_time_token", token),
                expires_at=self._now() + timedelta(hours=self._s.email_verification_ttl_hours),
            )
        )
        await s.flush()
        await self._send(
            email,
            "verify_email",
            "Verify your email",
            "Confirm your email address to start using Personal Life OS.",
            self._link("verify-email", token),
        )

    # ------------------------------------------------------------------ registration & verification

    async def register(self, email: str, password: str, full_name: str | None = None) -> uuid.UUID:
        normalized = self._validate_email(email)
        self._validate_password(password)
        try:
            async with system_session(sessionmaker=self._maker) as s:
                if await self._find_user_by_email(s, normalized) is not None:
                    raise DomainError("EMAIL_TAKEN", "An account with this email already exists")
                user = m.User(
                    id=uuid.uuid4(),
                    email_ciphertext=self._cipher.encrypt_str(normalized, associated_data=b"users.email"),
                    email_lookup_hash=self._hasher.email_lookup_hash(normalized),
                    email_verified=False,
                    password_hash=self._passwords.hash(password),
                    full_name_ciphertext=(
                        self._cipher.encrypt_str(full_name, associated_data=b"users.full_name")
                        if full_name
                        else None
                    ),
                )
                s.add(user)
                await s.flush()
                await self._issue_verification(s, user, normalized)
                return user.id
        except IntegrityError as exc:  # concurrent registration raced past the existence check
            raise DomainError("EMAIL_TAKEN", "An account with this email already exists") from exc

    async def verify_email(self, token: str) -> None:
        now = self._now()
        async with system_session(sessionmaker=self._maker) as s:
            row = (
                await s.execute(
                    select(m.EmailVerificationToken)
                    .where(
                        m.EmailVerificationToken.token_hash == self._hasher.digest("one_time_token", token)
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.used_at is not None or row.expires_at <= now:
                raise DomainError("INVALID_TOKEN", "This verification link is invalid or has expired")
            row.used_at = now
            await s.execute(
                update(m.User).where(m.User.id == row.user_id).values(email_verified=True, updated_at=now)
            )

    async def resend_verification(self, email: str) -> None:
        """Always succeeds from the caller's view (no account enumeration)."""
        async with system_session(sessionmaker=self._maker) as s:
            user = await self._find_user_by_email(s, email)
            if user is not None and not user.email_verified and user.status == "active":
                await self._issue_verification(s, user, self._email_of(user))

    # ------------------------------------------------------------------ login

    async def login(self, email: str, password: str, ip: str) -> TokenPair:
        now = self._now()
        lookup_subject = self._hasher.email_lookup_hash(email).hex()
        if await self._ip_failures.count(ip) >= self._s.login_ip_threshold:
            raise DomainError("RATE_LIMITED", "Too many attempts. Try again later.")

        async with system_session(sessionmaker=self._maker) as s:
            user = await self._find_user_by_email(s, email)
            if user is None or user.status == "deletion_pending":
                self._passwords.dummy_verify(password)
                await self._ip_failures.hit(ip)
                raise DomainError("INVALID_CREDENTIALS", "Email or password is incorrect")

            if user.locked_until is not None and user.locked_until > now:
                raise DomainError(
                    "ACCOUNT_LOCKED",
                    "Too many failed attempts. Your account is temporarily locked.",
                    {"locked_until": user.locked_until.isoformat()},
                )

            if self._passwords.verify(user.password_hash, password):
                if not user.email_verified:
                    raise DomainError("EMAIL_NOT_VERIFIED", "Verify your email address before signing in")
                await self._account_failures.reset(lookup_subject)
                if self._passwords.needs_rehash(user.password_hash):
                    user.password_hash = self._passwords.hash(password)
                user.locked_until = None
                return await self._create_session(s, user.id)

            # Wrong password: record the failure and COMMIT any lock before raising (a raise inside
            # the transaction would roll the lock back).
            await self._ip_failures.hit(ip)
            locked_email: str | None = None
            if await self._account_failures.hit(lookup_subject) >= self._s.login_lockout_threshold:
                user.locked_until = now + timedelta(minutes=self._s.login_lockout_duration_minutes)
                await self._account_failures.reset(lookup_subject)
                locked_email = self._email_of(user)

        if locked_email is not None:
            await self._send(
                locked_email,
                "account_locked",
                "Your account was temporarily locked",
                "We locked your account for 15 minutes after repeated failed sign-in attempts. "
                "If this wasn't you, reset your password.",
            )
        raise DomainError("INVALID_CREDENTIALS", "Email or password is incorrect")

    async def _create_session(self, s: AsyncSession, user_id: uuid.UUID) -> TokenPair:
        now = self._now()
        refresh = generate_token()
        refresh_expires = now + timedelta(hours=self._s.refresh_token_ttl_hours)
        session = m.AuthSession(
            id=uuid.uuid4(),
            user_id=user_id,
            refresh_token_hash=self._hasher.digest("refresh_token", refresh),
            token_family_id=uuid.uuid4(),
            session_version=1,
            issued_at=now,
            refresh_expires_at=refresh_expires,
            last_accessed_at=now,
        )
        s.add(session)
        await s.flush()
        await self._cache.put(session.id, 1, active=True)
        return TokenPair(
            access_token=self._jwt.issue(user_id, session.id, 1),
            expires_in=self._jwt.ttl_seconds,
            refresh_token=refresh,
            refresh_expires_at=refresh_expires,
        )

    # ------------------------------------------------------------------ refresh rotation

    async def refresh(self, refresh_token: str) -> TokenPair:
        now = self._now()
        token_hash = self._hasher.digest("refresh_token", refresh_token)
        async with system_session(sessionmaker=self._maker) as s:
            session = (
                await s.execute(
                    select(m.AuthSession)
                    .where(m.AuthSession.refresh_token_hash == token_hash)
                    .with_for_update()
                )
            ).scalar_one_or_none()

            if session is None:
                retired = (
                    await s.execute(
                        select(m.AuthRefreshTokenHistory.auth_session_id).where(
                            m.AuthRefreshTokenHistory.token_hash == token_hash
                        )
                    )
                ).scalar_one_or_none()
                if retired is not None:
                    # Replay of a rotated token: assume theft, kill the whole family (design §21.3).
                    # Committed by leaving the transaction normally; the error is raised afterwards.
                    await self._invalidate_sessions(s, [retired], now)
                pair = None
            else:
                pair = await self._rotate(s, session, token_hash, now)
        if pair is None:
            raise DomainError("INVALID_TOKEN", "Session expired. Sign in again.")
        return pair

    async def _rotate(
        self, s: AsyncSession, session: m.AuthSession, token_hash: bytes, now: datetime
    ) -> TokenPair:
        user_status = (
            await s.execute(select(m.User.status).where(m.User.id == session.user_id))
        ).scalar_one()
        if session.invalidated_at is not None or session.refresh_expires_at <= now or user_status != "active":
            raise DomainError("INVALID_TOKEN", "Session expired. Sign in again.")

        s.add(
            m.AuthRefreshTokenHistory(
                token_hash=token_hash,
                auth_session_id=session.id,
                retired_at=now,
                expires_at=session.refresh_expires_at,
            )
        )
        new_refresh = generate_token()
        session.refresh_token_hash = self._hasher.digest("refresh_token", new_refresh)
        session.refresh_expires_at = now + timedelta(hours=self._s.refresh_token_ttl_hours)
        session.last_accessed_at = now
        await s.flush()
        return TokenPair(
            access_token=self._jwt.issue(session.user_id, session.id, session.session_version),
            expires_in=self._jwt.ttl_seconds,
            refresh_token=new_refresh,
            refresh_expires_at=session.refresh_expires_at,
        )

    # ------------------------------------------------------------------ logout & invalidation

    async def _invalidate_sessions(
        self, s: AsyncSession, session_ids: list[uuid.UUID], now: datetime
    ) -> None:
        rows = (
            await s.execute(
                update(m.AuthSession)
                .where(m.AuthSession.id.in_(session_ids), m.AuthSession.invalidated_at.is_(None))
                .values(session_version=m.AuthSession.session_version + 1, invalidated_at=now)
                .returning(m.AuthSession.id, m.AuthSession.session_version)
            )
        ).all()
        for sid, version in rows:
            await self._cache.revoke(sid, int(version))

    async def logout(self, ctx: AuthContext) -> None:
        async with system_session(sessionmaker=self._maker) as s:
            await self._invalidate_sessions(s, [ctx.session_id], self._now())

    async def invalidate_all_sessions(self, s: AsyncSession, user_id: uuid.UUID) -> None:
        ids = list(
            (
                await s.execute(
                    select(m.AuthSession.id).where(
                        m.AuthSession.user_id == user_id, m.AuthSession.invalidated_at.is_(None)
                    )
                )
            ).scalars()
        )
        if ids:
            await self._invalidate_sessions(s, ids, self._now())

    # ------------------------------------------------------------------ password reset

    async def request_password_reset(self, email: str) -> None:
        """Always succeeds from the caller's view (design §21.4: identical response)."""
        async with system_session(sessionmaker=self._maker) as s:
            user = await self._find_user_by_email(s, email)
            if user is None or user.status != "active":
                return
            token = generate_token()
            s.add(
                m.PasswordResetToken(
                    user_id=user.id,
                    token_hash=self._hasher.digest("one_time_token", token),
                    expires_at=self._now() + timedelta(minutes=self._s.password_reset_ttl_minutes),
                )
            )
            await s.flush()
            await self._send(
                self._email_of(user),
                "password_reset",
                "Reset your password",
                "Use this link within 1 hour to choose a new password.",
                self._link("reset-password", token),
            )

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        self._validate_password(new_password)
        now = self._now()
        async with system_session(sessionmaker=self._maker) as s:
            row = (
                await s.execute(
                    select(m.PasswordResetToken)
                    .where(m.PasswordResetToken.token_hash == self._hasher.digest("one_time_token", token))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.used_at is not None or row.expires_at <= now:
                raise DomainError("INVALID_TOKEN", "This reset link is invalid or has expired")
            row.used_at = now
            user = (await s.execute(select(m.User).where(m.User.id == row.user_id))).scalar_one()
            user.password_hash = self._passwords.hash(new_password)
            user.locked_until = None
            user.updated_at = now
            await self.invalidate_all_sessions(s, user.id)
            await self._send(
                self._email_of(user),
                "password_changed",
                "Your password was changed",
                "Your password was changed and all devices were signed out.",
            )
