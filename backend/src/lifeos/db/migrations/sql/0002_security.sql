-- Roles, grants, row-level security and immutability triggers (design §24.1, tasks T1.3).

-- 1. Application role: NOLOGIN, assumed per transaction with SET LOCAL ROLE (no RLS bypass).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lifeos_app') THEN
        CREATE ROLE lifeos_app NOLOGIN NOBYPASSRLS;
    END IF;
END
$$;
GRANT lifeos_app TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO lifeos_app;

-- 2. RLS on every user-scoped table (tables with a user_id column), except operational tables.
--    Only RLS-protected tables are granted to lifeos_app; everything else is system-only.
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT c.table_name
        FROM information_schema.columns c
        JOIN information_schema.tables tb
          ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
        WHERE c.table_schema = 'public'
          AND c.column_name = 'user_id'
          AND tb.table_type = 'BASE TABLE'
          AND c.table_name NOT IN ('background_job_runs', 'developer_alerts', 'account_deletion_requests')
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY user_isolation ON %I USING (user_id = nullif(current_setting(''app.user_id'', true), '''')::uuid)',
            t
        );
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO lifeos_app', t);
    END LOOP;
END
$$;

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON users
    USING (id = nullif(current_setting('app.user_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO lifeos_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lifeos_app;

-- 3. Append-only history tables: UPDATE is never allowed; DELETE only inside the account purge
--    transaction, which sets app.allow_history_delete = 'on' (design §24.14).
CREATE OR REPLACE FUNCTION lifeos_forbid_history_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND coalesce(current_setting('app.allow_history_delete', true), '') = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'table % is append-only: % rejected', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['checkin_records', 'daily_action_schedule_history',
                             'commitment_events', 'objective_value_history']
    LOOP
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION lifeos_forbid_history_mutation()',
            t || '_append_only', t
        );
    END LOOP;
END
$$;
