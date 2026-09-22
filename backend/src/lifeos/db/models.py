"""ORM mappings for the design §24 schema.

Generated from the migrated database with sqlacodegen, then curated (T1.2):
  * columns, primary keys, foreign keys and unique constraints are mapped here;
  * CHECK constraints, indexes, RLS policies and triggers live ONLY in the SQL migrations
    (`lifeos/db/migrations/sql`), which are extracted from design.md §24–§25;
  * relationships are added per Domain Service when needed.
`tests/integration/test_schema.py::test_models_match_database` guards against drift.
"""

import datetime
import decimal
import uuid
from typing import Any

from pgvector.sqlalchemy.vector import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base


class AccountDeletionRequest(Base):
    __tablename__ = "account_deletion_requests"
    __table_args__ = (PrimaryKeyConstraint("id", name="account_deletion_requests_pkey"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    confirmed_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    purge_due_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    purged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="users_pkey"),
        UniqueConstraint("email_lookup_hash", name="users_email_lookup_hash_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    email_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_lookup_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UTC'::text"))
    accountability_style: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Balanced'::text")
    )
    notification_prefs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    integrity_score_threshold: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("70"))
    briefing_time: Mapped[datetime.time] = mapped_column(
        Time, nullable=False, server_default=text("'05:00:00'::time without time zone")
    )
    reflection_time: Mapped[datetime.time] = mapped_column(
        Time, nullable=False, server_default=text("'21:00:00'::time without time zone")
    )
    ceo_meeting_weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    ceo_meeting_time: Mapped[datetime.time] = mapped_column(
        Time, nullable=False, server_default=text("'19:00:00'::time without time zone")
    )
    life_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    full_name_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    locked_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    wake_time: Mapped[datetime.time | None] = mapped_column(Time)
    sleep_time: Mapped[datetime.time | None] = mapped_column(Time)
    working_hours_start: Mapped[datetime.time | None] = mapped_column(Time)
    working_hours_end: Mapped[datetime.time | None] = mapped_column(Time)
    values_text: Mapped[str | None] = mapped_column(Text)
    principles_text: Mapped[str | None] = mapped_column(Text)
    vision_statement: Mapped[str | None] = mapped_column(Text)


class AiInsight(Base):
    __tablename__ = "ai_insights"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="ai_insights_user_id_fkey"),
        PrimaryKeyConstraint("id", name="ai_insights_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class AnalyticsResult(Base):
    __tablename__ = "analytics_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="analytics_results_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="analytics_results_pkey"),
        UniqueConstraint(
            "user_id", "kind", "computed_for", name="analytics_results_user_id_kind_computed_for_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    computed_for: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="auth_sessions_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="auth_sessions_pkey"),
        UniqueConstraint("refresh_token_hash", name="auth_sessions_refresh_token_hash_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    issued_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    refresh_expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    last_accessed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    device_label: Mapped[str | None] = mapped_column(Text)
    invalidated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class BackgroundJobRun(Base):
    __tablename__ = "background_job_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="background_job_runs_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="background_job_runs_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    logical_run_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[str | None] = mapped_column(Text)


class Commitment(Base):
    __tablename__ = "commitments"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="commitments_user_id_fkey"),
        PrimaryKeyConstraint("id", name="commitments_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    current_due_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    completion_condition: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Open'::text"))
    deferral_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    explanation_window_ends_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    goal_category: Mapped[str | None] = mapped_column(Text)
    kept_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    broken_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="conversation_sessions_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="conversation_sessions_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'chat'::text"))
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_activity_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    flow_ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class DailyBriefing(Base):
    __tablename__ = "daily_briefings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="daily_briefings_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="daily_briefings_pkey"),
        UniqueConstraint("user_id", "local_date", name="daily_briefings_user_id_local_date_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    local_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    narrative_status: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class DataExport(Base):
    __tablename__ = "data_exports"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="data_exports_user_id_fkey"),
        PrimaryKeyConstraint("id", name="data_exports_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    object_key: Mapped[str | None] = mapped_column(Text)
    ready_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class DeveloperAlert(Base):
    __tablename__ = "developer_alerts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="developer_alerts_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="developer_alerts_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class DomainEvent(Base):
    __tablename__ = "domain_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="domain_events_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="domain_events_pkey"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    available_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    dead_lettered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="email_verification_tokens_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="email_verification_tokens_pkey"),
        UniqueConstraint("token_hash", name="email_verification_tokens_token_hash_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="goals_user_id_fkey"),
        PrimaryKeyConstraint("id", name="goals_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    progress: Mapped[decimal.Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("0"))
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("3"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[datetime.date | None] = mapped_column(Date)
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="idempotency_records_user_id_fkey"
        ),
        PrimaryKeyConstraint("user_id", "idempotency_key", name="idempotency_records_pkey"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class IntegrityScoreSnapshot(Base):
    __tablename__ = "integrity_score_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="integrity_score_snapshots_user_id_fkey"
        ),
        PrimaryKeyConstraint("user_id", "local_date", name="integrity_score_snapshots_pkey"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    local_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    kept: Mapped[int] = mapped_column(Integer, nullable=False)
    broken: Mapped[int] = mapped_column(Integer, nullable=False)
    overdue_deferred: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    score: Mapped[decimal.Decimal | None] = mapped_column(Numeric(4, 1))


class MemoryStoreEntry(Base):
    __tablename__ = "memory_store_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["superseded_by"],
            ["memory_store_entries.id"],
            ondelete="SET NULL",
            name="memory_store_entries_superseded_by_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="memory_store_entries_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="memory_store_entries_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[decimal.Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    is_inference: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    embedding_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'::text")
    )
    generation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'final'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    source_ref_type: Mapped[str | None] = mapped_column(Text)
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_featured_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    embedding: Mapped[Any | None] = mapped_column(VECTOR(1536))
    embedding_model: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="notifications_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="notifications_pkey"),
        UniqueConstraint("user_id", "dedupe_key", name="notifications_user_id_dedupe_key_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    deep_link: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    delivery_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Scheduled'::text")
    )
    scheduled_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    retry_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    final_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    level: Mapped[int | None] = mapped_column(SmallInteger)
    not_before: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    stale_after: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    opened_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    acted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class OnboardingRun(Base):
    __tablename__ = "onboarding_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="onboarding_runs_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="onboarding_runs_pkey"),
        UniqueConstraint("user_id", "run_no", name="onboarding_runs_user_id_run_no_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="password_reset_tokens_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="password_reset_tokens_pkey"),
        UniqueConstraint("token_hash", name="password_reset_tokens_token_hash_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class ProactiveFlag(Base):
    __tablename__ = "proactive_flags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="proactive_flags_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="proactive_flags_pkey"),
        UniqueConstraint("user_id", "dedupe_key", name="proactive_flags_user_id_dedupe_key_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    flag_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_agent: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    ref_type: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    surfaced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class PushDevice(Base):
    __tablename__ = "push_devices"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="push_devices_user_id_fkey"),
        PrimaryKeyConstraint("id", name="push_devices_pkey"),
        UniqueConstraint("token_hash", name="push_devices_token_hash_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class RealtimeEvent(Base):
    __tablename__ = "realtime_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="realtime_events_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="realtime_events_pkey"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Reflection(Base):
    __tablename__ = "reflections"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="reflections_user_id_fkey"),
        PrimaryKeyConstraint("id", name="reflections_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    goal_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    transcript: Mapped[str | None] = mapped_column(Text)
    mood: Mapped[int | None] = mapped_column(SmallInteger)
    energy: Mapped[int | None] = mapped_column(SmallInteger)
    stress: Mapped[int | None] = mapped_column(SmallInteger)
    escalation_episode_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    search_tsv: Mapped[Any | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple'::regconfig, ((COALESCE(content, ''::text) || ' '::text) || COALESCE((answers)::text, ''::text)))",
            persisted=True,
        ),
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class RoutineTemplate(Base):
    __tablename__ = "routine_templates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="routine_templates_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="routine_templates_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    active_days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger()), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class StaffRole(Base):
    __tablename__ = "staff_roles"
    __table_args__ = (
        ForeignKeyConstraint(["granted_by"], ["users.id"], name="staff_roles_granted_by_fkey"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="staff_roles_user_id_fkey"),
        PrimaryKeyConstraint("user_id", name="staff_roles_pkey"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class AccountabilityEscalationState(Base):
    __tablename__ = "accountability_escalation_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reflection_id"],
            ["reflections.id"],
            ondelete="SET NULL",
            name="accountability_escalation_states_reflection_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="accountability_escalation_states_user_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="accountability_escalation_states_pkey"),
        UniqueConstraint(
            "user_id",
            "source_type",
            "source_id",
            name="accountability_escalation_sta_user_id_source_type_source_id_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    escalation_episode_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, server_default=text("gen_random_uuid()")
    )
    episode_started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    reflection_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_evaluated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    reflection_completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    reflection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recovery_window_anchor_date: Mapped[datetime.date | None] = mapped_column(Date)
    last_reduced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.id"],
            ondelete="CASCADE",
            name="agent_traces_session_id_fkey",
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="agent_traces_user_id_fkey"),
        PrimaryKeyConstraint("id", name="agent_traces_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    graph: Mapped[str] = mapped_column(Text, nullable=False)
    ambiguity_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    classifier_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    memory_queries: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    proposed_actions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    has_tool_error: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    grounding_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'::text"))
    model_calls: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    mode: Mapped[str | None] = mapped_column(Text)
    selected_agent: Mapped[str | None] = mapped_column(Text)
    routing_confidence: Mapped[decimal.Decimal | None] = mapped_column(Numeric(3, 2))
    outcome: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)


class AuthRefreshTokenHistory(Base):
    __tablename__ = "auth_refresh_token_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["auth_session_id"],
            ["auth_sessions.id"],
            ondelete="CASCADE",
            name="auth_refresh_token_history_auth_session_id_fkey",
        ),
        PrimaryKeyConstraint("token_hash", name="auth_refresh_token_history_pkey"),
    )

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    auth_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    retired_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)


class CommitmentEvent(Base):
    __tablename__ = "commitment_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.id"],
            ondelete="CASCADE",
            name="commitment_events_commitment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="commitment_events_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="commitment_events_pkey"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    commitment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    previous_status: Mapped[str | None] = mapped_column(Text)
    new_status: Mapped[str | None] = mapped_column(Text)
    previous_due: Mapped[datetime.date | None] = mapped_column(Date)
    new_due: Mapped[datetime.date | None] = mapped_column(Date)
    explanation: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class CommitmentLink(Base):
    __tablename__ = "commitment_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.id"],
            ondelete="CASCADE",
            name="commitment_links_commitment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="commitment_links_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="commitment_links_pkey"),
        UniqueConstraint(
            "commitment_id",
            "entity_type",
            "entity_id",
            name="commitment_links_commitment_id_entity_type_entity_id_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    commitment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    removed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    removed_reason: Mapped[str | None] = mapped_column(Text)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.id"],
            ondelete="CASCADE",
            name="conversation_messages_session_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="conversation_messages_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="conversation_messages_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    agent: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    search_tsv: Mapped[Any | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, content)", persisted=True)
    )


class MemoryProposal(Base):
    __tablename__ = "memory_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["memory_entry_id"],
            ["memory_store_entries.id"],
            ondelete="SET NULL",
            name="memory_proposals_memory_entry_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="memory_proposals_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="memory_proposals_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    proposed_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    theme_embedding: Mapped[Any] = mapped_column(VECTOR(1536), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    memory_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            ondelete="CASCADE",
            name="notification_attempts_notification_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="notification_attempts_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="notification_attempts_pkey"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notification_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    error_code: Mapped[str | None] = mapped_column(Text)


class Objective(Base):
    __tablename__ = "objectives"
    __table_args__ = (
        ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE", name="objectives_goal_id_fkey"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="objectives_user_id_fkey"),
        PrimaryKeyConstraint("id", name="objectives_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    goal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    metric_direction: Mapped[str] = mapped_column(Text, nullable=False)
    current_value: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    target_value: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False, server_default=text("1"))
    target_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    progress: Mapped[decimal.Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    baseline_value: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.id"],
            ondelete="CASCADE",
            name="pending_confirmations_session_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="pending_confirmations_user_id_fkey"
        ),
        PrimaryKeyConstraint("action_id", name="pending_confirmations_pkey"),
    )

    action_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class RoutineInstance(Base):
    __tablename__ = "routine_instances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["routine_template_id"],
            ["routine_templates.id"],
            ondelete="CASCADE",
            name="routine_instances_routine_template_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="routine_instances_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="routine_instances_pkey"),
        UniqueConstraint(
            "user_id",
            "routine_template_id",
            "date",
            name="routine_instances_user_id_routine_template_id_date_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    routine_template_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class WeeklyCeoSession(Base):
    __tablename__ = "weekly_ceo_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reflection_id"],
            ["reflections.id"],
            ondelete="SET NULL",
            name="weekly_ceo_sessions_reflection_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="weekly_ceo_sessions_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="weekly_ceo_sessions_pkey"),
        UniqueConstraint(
            "user_id", "week_start_date", name="weekly_ceo_sessions_user_id_week_start_date_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    week_start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    scheduled_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    grace_ends_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    pre_session_briefing: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    opened_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    summary: Mapped[str | None] = mapped_column(Text)
    reflection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    focus_plan_status: Mapped[str | None] = mapped_column(Text)


class AgentTracePayload(Base):
    __tablename__ = "agent_trace_payloads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trace_id"], ["agent_traces.id"], ondelete="CASCADE", name="agent_trace_payloads_trace_id_fkey"
        ),
        PrimaryKeyConstraint("trace_id", name="agent_trace_payloads_pkey"),
    )

    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    capture_mode: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class DailyAction(Base):
    __tablename__ = "daily_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["routine_instance_id"],
            ["routine_instances.id"],
            ondelete="SET NULL",
            name="daily_actions_routine_instance_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="daily_actions_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="daily_actions_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    occurrence_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    scheduled_start: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    scheduled_end: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Planned'::text"))
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    routine_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class ObjectiveValueHistory(Base):
    __tablename__ = "objective_value_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["objective_id"],
            ["objectives.id"],
            ondelete="CASCADE",
            name="objective_value_history_objective_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="objective_value_history_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="objective_value_history_pkey"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    objective_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    current_value: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    progress: Mapped[decimal.Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["objective_id"], ["objectives.id"], ondelete="CASCADE", name="projects_objective_id_fkey"
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="projects_user_id_fkey"),
        PrimaryKeyConstraint("id", name="projects_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    objective_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class WeeklyFocusPlan(Base):
    __tablename__ = "weekly_focus_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ceo_session_id"],
            ["weekly_ceo_sessions.id"],
            ondelete="CASCADE",
            name="weekly_focus_plans_ceo_session_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="weekly_focus_plans_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="weekly_focus_plans_pkey"),
        UniqueConstraint("ceo_session_id", name="weekly_focus_plans_ceo_session_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ceo_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    week_start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    accepted_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class CheckinRecord(Base):
    __tablename__ = "checkin_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["daily_action_id"],
            ["daily_actions.id"],
            ondelete="CASCADE",
            name="checkin_records_daily_action_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="checkin_records_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="checkin_records_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    daily_action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    new_status: Mapped[str] = mapped_column(Text, nullable=False)
    transition_source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    previous_status: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class DailyActionScheduleHistory(Base):
    __tablename__ = "daily_action_schedule_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["daily_action_id"],
            ["daily_actions.id"],
            ondelete="CASCADE",
            name="daily_action_schedule_history_daily_action_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="daily_action_schedule_history_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="daily_action_schedule_history_pkey"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    daily_action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    previous_start: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    previous_end: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    new_start: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    new_end: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    reason: Mapped[str | None] = mapped_column(Text)


class Habit(Base):
    __tablename__ = "habits"
    __table_args__ = (
        ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="SET NULL", name="habits_goal_id_fkey"),
        ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="SET NULL", name="habits_project_id_fkey"
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="habits_user_id_fkey"),
        PrimaryKeyConstraint("id", name="habits_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    recurrence_type: Mapped[str] = mapped_column(Text, nullable=False)
    frequency_target: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recurrence_days: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger()))
    preferred_start: Mapped[datetime.time | None] = mapped_column(Time)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[datetime.date | None] = mapped_column(Date)
    end_date: Mapped[datetime.date | None] = mapped_column(Date)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class ScheduleSuggestion(Base):
    __tablename__ = "schedule_suggestions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trigger_action_id"],
            ["daily_actions.id"],
            ondelete="CASCADE",
            name="schedule_suggestions_trigger_action_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="schedule_suggestions_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="schedule_suggestions_pkey"),
        UniqueConstraint("trigger_action_id", name="schedule_suggestions_trigger_action_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    trigger_action_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    local_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    proposal: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'proposed'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="SET NULL", name="tasks_goal_id_fkey"),
        ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE", name="tasks_project_id_fkey"
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="tasks_user_id_fkey"),
        PrimaryKeyConstraint("id", name="tasks_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    due_date: Mapped[datetime.date | None] = mapped_column(Date)
    scheduled_date: Mapped[datetime.date | None] = mapped_column(Date)
    scheduled_time: Mapped[datetime.time | None] = mapped_column(Time)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class HabitOccurrenceOverride(Base):
    __tablename__ = "habit_occurrence_overrides"
    __table_args__ = (
        ForeignKeyConstraint(
            ["habit_id"], ["habits.id"], ondelete="CASCADE", name="habit_occurrence_overrides_habit_id_fkey"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="habit_occurrence_overrides_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="habit_occurrence_overrides_pkey"),
        UniqueConstraint(
            "habit_id", "occurrence_date", name="habit_occurrence_overrides_habit_id_occurrence_date_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    habit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    occurrence_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    override_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    new_start: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    new_end: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    reason: Mapped[str | None] = mapped_column(Text)


class HabitOccurrenceRecord(Base):
    __tablename__ = "habit_occurrence_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["daily_action_id"],
            ["daily_actions.id"],
            ondelete="SET NULL",
            name="habit_occurrence_records_daily_action_id_fkey",
        ),
        ForeignKeyConstraint(
            ["habit_id"], ["habits.id"], ondelete="CASCADE", name="habit_occurrence_records_habit_id_fkey"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="habit_occurrence_records_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="habit_occurrence_records_pkey"),
        UniqueConstraint(
            "habit_id", "occurrence_date", name="habit_occurrence_records_habit_id_occurrence_date_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    habit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    occurrence_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    daily_action_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    completion_percent: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(Text)


class HabitPausePeriod(Base):
    __tablename__ = "habit_pause_periods"
    __table_args__ = (
        ForeignKeyConstraint(
            ["habit_id"], ["habits.id"], ondelete="CASCADE", name="habit_pause_periods_habit_id_fkey"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="habit_pause_periods_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="habit_pause_periods_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    habit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    starts_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    ends_on: Mapped[datetime.date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)


class RoutineEntry(Base):
    __tablename__ = "routine_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id"], ["goals.id"], ondelete="SET NULL", name="routine_entries_goal_id_fkey"
        ),
        ForeignKeyConstraint(
            ["habit_id"], ["habits.id"], ondelete="SET NULL", name="routine_entries_habit_id_fkey"
        ),
        ForeignKeyConstraint(
            ["routine_template_id"],
            ["routine_templates.id"],
            ondelete="CASCADE",
            name="routine_entries_routine_template_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="routine_entries_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="routine_entries_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    routine_template_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    habit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class RoutineException(Base):
    __tablename__ = "routine_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["daily_action_id"],
            ["daily_actions.id"],
            ondelete="SET NULL",
            name="routine_exceptions_daily_action_id_fkey",
        ),
        ForeignKeyConstraint(
            ["routine_entry_id"],
            ["routine_entries.id"],
            ondelete="SET NULL",
            name="routine_exceptions_routine_entry_id_fkey",
        ),
        ForeignKeyConstraint(
            ["routine_template_id"],
            ["routine_templates.id"],
            ondelete="CASCADE",
            name="routine_exceptions_routine_template_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="routine_exceptions_user_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="routine_exceptions_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    routine_template_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    exception_type: Mapped[str] = mapped_column(Text, nullable=False)
    exception_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    routine_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    daily_action_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
