"""Initial schema: extensions + design §24 tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

from lifeos.db.migrations.sqlfiles import read_sql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(read_sql("0001_schema.sql"))


def downgrade() -> None:
    # Drop every application table (checkpoint tables are owned by the LangGraph saver, untouched).
    op.execute(
        """
        DO $$
        DECLARE t text;
        BEGIN
            FOR t IN SELECT tablename FROM pg_tables
                     WHERE schemaname = 'public'
                       AND tablename NOT LIKE 'checkpoint%'
                       AND tablename <> 'alembic_version'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', t);
            END LOOP;
        END
        $$;
        """
    )
