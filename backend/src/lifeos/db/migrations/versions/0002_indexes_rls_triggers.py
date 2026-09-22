"""Design §25 indexes, RLS policies, grants and append-only triggers.

Revision ID: 0002_indexes_rls_triggers
Revises: 0001_initial
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

from lifeos.db.migrations.sqlfiles import read_sql

revision: str = "0002_indexes_rls_triggers"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HISTORY_TABLES = (
    "checkin_records",
    "daily_action_schedule_history",
    "commitment_events",
    "objective_value_history",
)


def upgrade() -> None:
    op.execute(read_sql("0002_indexes.sql"))
    op.execute(read_sql("0002_security.sql"))


def downgrade() -> None:
    for table in HISTORY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS lifeos_forbid_history_mutation()")
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT tablename FROM pg_policies
                     WHERE schemaname = 'public' AND policyname = 'user_isolation'
            LOOP
                EXECUTE format('DROP POLICY user_isolation ON %I', r.tablename);
                EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', r.tablename);
            END LOOP;
        END
        $$;
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM lifeos_app;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM lifeos_app;
        REVOKE USAGE ON SCHEMA public FROM lifeos_app;
        """
    )
    # Index removal: every §25 index is named idx_*.
    op.execute(
        """
        DO $$
        DECLARE i text;
        BEGIN
            FOR i IN SELECT indexname FROM pg_indexes
                     WHERE schemaname = 'public' AND indexname LIKE 'idx\\_%'
            LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', i);
            END LOOP;
        END
        $$;
        """
    )
