-- GENERATED from design.md §24 by backend/scripts/extract_schema.py (T1.2). Edit design.md, then re-extract.

CREATE TABLE users (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email_ciphertext           bytea NOT NULL,
    email_lookup_hash          bytea NOT NULL UNIQUE,          -- HMAC-SHA-256(normalized email)
    email_verified             boolean NOT NULL DEFAULT false,
    password_hash              text NOT NULL,                  -- Argon2id
    full_name_ciphertext       bytea,
    status                     text NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active','locked','deletion_pending')),
    locked_until               timestamptz,
    timezone                   text NOT NULL DEFAULT 'UTC',    -- IANA identifier, validated by zoneinfo
    wake_time                  time,
    sleep_time                 time,
    working_hours_start        time,
    working_hours_end          time,
    accountability_style       text NOT NULL DEFAULT 'Balanced'
                               CHECK (accountability_style IN ('Gentle','Balanced','Direct','Strict')),
    notification_prefs         jsonb NOT NULL DEFAULT '{}',    -- schema in Section 24.11
    integrity_score_threshold  int NOT NULL DEFAULT 70 CHECK (integrity_score_threshold BETWEEN 0 AND 100),
    briefing_time              time NOT NULL DEFAULT '05:00',
    reflection_time            time NOT NULL DEFAULT '21:00',
    ceo_meeting_weekday        smallint NOT NULL DEFAULT 0 CHECK (ceo_meeting_weekday = 0), -- 0=Sunday; V1 fixed (Section 40)
    ceo_meeting_time           time NOT NULL DEFAULT '19:00',
    life_categories            text[] NOT NULL DEFAULT '{}',
    values_text                text,
    principles_text            text,
    vision_statement           text,
    onboarding_completed       boolean NOT NULL DEFAULT false,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staff_roles (                      -- developer access for traces / delivery log (R22.4, R23.3)
    user_id     uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('developer','trace_payload_reader')),
    granted_by  uuid REFERENCES users(id),
    granted_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash  bytea NOT NULL UNIQUE,     -- HMAC of the current refresh token
    token_family_id     uuid NOT NULL,
    session_version     int NOT NULL DEFAULT 1,
    device_label        text,
    issued_at           timestamptz NOT NULL DEFAULT now(),
    refresh_expires_at  timestamptz NOT NULL,      -- issued/rotated + 24 h
    invalidated_at      timestamptz,
    last_accessed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_refresh_token_history (        -- retired refresh tokens for replay detection
    token_hash       bytea PRIMARY KEY,
    auth_session_id  uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    retired_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL
);

CREATE TABLE email_verification_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  bytea NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,             -- + 24 h
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE password_reset_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  bytea NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,             -- + 1 h
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE onboarding_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_no        int NOT NULL,
    status        text NOT NULL CHECK (status IN ('in_progress','completed','abandoned')),
    started_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz,
    UNIQUE (user_id, run_no)
);

CREATE TABLE goals (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        text NOT NULL,
    category     text NOT NULL CHECK (category IN ('Life','Career','Personal','Spiritual','Fitness','Family')),
    description  text,
    target_date  date,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','completed')),
    progress     numeric(5,2) NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),  -- computed (16.1)
    priority     smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    archived_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz
);

CREATE TABLE objectives (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id          uuid NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            text NOT NULL,
    metric_direction text NOT NULL CHECK (metric_direction IN ('higher_is_better','lower_is_better')),
    current_value    numeric NOT NULL,
    target_value     numeric NOT NULL,
    baseline_value   numeric,
    unit             text NOT NULL,
    weight           numeric NOT NULL DEFAULT 1 CHECK (weight > 0),
    target_date      date NOT NULL,                                   -- required by R1.2
    status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    progress         numeric(5,2) NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    CONSTRAINT objectives_no_zero_target      CHECK (metric_direction <> 'higher_is_better' OR target_value <> 0),
    CONSTRAINT objectives_lower_requires_base CHECK (metric_direction <> 'lower_is_better' OR baseline_value IS NOT NULL),
    CONSTRAINT objectives_no_equal_range      CHECK (metric_direction <> 'lower_is_better' OR baseline_value <> target_value)
);

CREATE TABLE objective_value_history (             -- immutable
    id            bigserial PRIMARY KEY,
    objective_id  uuid NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_value numeric NOT NULL,
    progress      numeric(5,2) NOT NULL,
    changed_by    text NOT NULL CHECK (changed_by IN ('User','Agent','System')),
    changed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    objective_id uuid NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        text NOT NULL,
    description  text,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz
);

CREATE TABLE tasks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id      uuid REFERENCES projects(id) ON DELETE CASCADE,
    goal_id         uuid REFERENCES goals(id) ON DELETE SET NULL,
    title           text NOT NULL,
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','completed','cancelled')),
    due_date        date,
    scheduled_date  date,
    scheduled_time  time,
    duration_minutes int CHECK (duration_minutes > 0),
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);

CREATE TABLE habits (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    goal_id          uuid REFERENCES goals(id) ON DELETE SET NULL,
    project_id       uuid REFERENCES projects(id) ON DELETE SET NULL,
    title            text NOT NULL,
    recurrence_type  text NOT NULL CHECK (recurrence_type IN ('daily','weekly','custom')),
    recurrence_days  smallint[],                   -- 0=Sun..6=Sat; required for weekly/custom
    preferred_start  time,                          -- local time for generated Daily Actions
    duration_minutes int CHECK (duration_minutes > 0),
    frequency_target int NOT NULL DEFAULT 1 CHECK (frequency_target > 0),
    start_date       date,
    end_date         date,
    status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    CHECK (recurrence_type = 'daily' OR cardinality(recurrence_days) > 0),
    CHECK (goal_id IS NOT NULL OR project_id IS NOT NULL)          -- R1.10: beneath a Goal or Project
);

CREATE TABLE habit_pause_periods (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id   uuid NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    starts_on  date NOT NULL,
    ends_on    date,                                -- NULL = still paused
    reason     text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE TABLE habit_occurrence_overrides (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id        uuid NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    occurrence_date date NOT NULL,
    override_type   text NOT NULL CHECK (override_type IN ('rescheduled','removed')),
    new_start       timestamptz,
    new_end         timestamptz,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (habit_id, occurrence_date)
);

CREATE TABLE habit_occurrence_records (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id           uuid NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    occurrence_date    date NOT NULL,
    daily_action_id    uuid,                        -- FK added after daily_actions
    result             text NOT NULL CHECK (result IN ('completed','skipped','partial')),
    completion_percent smallint,
    note               text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (habit_id, occurrence_date),
    CHECK ((result = 'partial') = coalesce(completion_percent BETWEEN 1 AND 99, false)),   -- NULL-safe
    CHECK (result <> 'skipped' OR coalesce(length(trim(note)), 0) > 0)
);

CREATE TABLE routine_templates (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       text NOT NULL,
    active_days smallint[] NOT NULL CHECK (cardinality(active_days) > 0),   -- 0=Sun..6=Sat
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

CREATE TABLE routine_entries (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),      -- stable Routine Entry ID
    routine_template_id uuid NOT NULL REFERENCES routine_templates(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               text NOT NULL,
    start_time          time NOT NULL,
    end_time            time NOT NULL,              -- end < start means the block crosses midnight
    sort_order          int NOT NULL DEFAULT 0,
    goal_id             uuid REFERENCES goals(id) ON DELETE SET NULL,
    habit_id            uuid REFERENCES habits(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CHECK (start_time <> end_time)
);

CREATE TABLE routine_instances (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_template_id uuid NOT NULL REFERENCES routine_templates(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                date NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, routine_template_id, date)
);

CREATE TABLE daily_actions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               text NOT NULL,
    date                date NOT NULL,                  -- scheduled local date (changes on cross-day reschedule)
    occurrence_date     date NOT NULL,                  -- originating occurrence local date (immutable)
    scheduled_start     timestamptz NOT NULL,
    scheduled_end       timestamptz NOT NULL,
    status              text NOT NULL DEFAULT 'Planned'
                        CHECK (status IN ('Planned','Started','Completed','Skipped')),
    lifecycle_state     text NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active','cancelled')),
    cancelled_at        timestamptz,
    source_type         text NOT NULL CHECK (source_type IN ('ROUTINE_ENTRY','HABIT','TASK','MANUAL')),
    source_id           uuid,
    routine_instance_id uuid REFERENCES routine_instances(id) ON DELETE SET NULL,
    completed_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (scheduled_end > scheduled_start),
    CHECK ((source_type = 'MANUAL') = (source_id IS NULL)),
    CHECK ((lifecycle_state = 'cancelled') = (cancelled_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_daily_actions_occurrence
    ON daily_actions (user_id, source_type, source_id, occurrence_date)
    WHERE source_type IN ('ROUTINE_ENTRY','HABIT','TASK');

ALTER TABLE habit_occurrence_records
    ADD FOREIGN KEY (daily_action_id) REFERENCES daily_actions(id) ON DELETE SET NULL;

CREATE TABLE checkin_records (                      -- immutable
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    daily_action_id   uuid NOT NULL REFERENCES daily_actions(id) ON DELETE CASCADE,
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    previous_status   text CHECK (previous_status IN ('Planned','Started','Completed','Skipped')),  -- NULL = none
    new_status        text NOT NULL CHECK (new_status IN ('Planned','Started','Completed','Skipped')),
    transition_source text NOT NULL CHECK (transition_source IN ('User','Agent','System')),
    note              text,                             -- skip reason (required) or completion note
    trace_id          uuid,                             -- agent trace when transition_source = 'Agent'
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (new_status <> 'Skipped' OR coalesce(length(trim(note)), 0) > 0)                                  -- R3.6
);

CREATE TABLE routine_exceptions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_template_id uuid NOT NULL REFERENCES routine_templates(id) ON DELETE CASCADE,
    routine_entry_id    uuid REFERENCES routine_entries(id) ON DELETE SET NULL,   -- NULL for 'added'
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                date NOT NULL,
    exception_type      text NOT NULL CHECK (exception_type IN ('modified','removed','added')),
    daily_action_id     uuid REFERENCES daily_actions(id) ON DELETE SET NULL,
    exception_payload   jsonb NOT NULL DEFAULT '{}',   -- changed fields / one-day entry definition
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE daily_action_schedule_history (         -- immutable
    id              bigserial PRIMARY KEY,
    daily_action_id uuid NOT NULL REFERENCES daily_actions(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    change_type     text NOT NULL CHECK (change_type IN ('rescheduled','cancelled','timezone_change','habit_paused')),
    previous_start  timestamptz NOT NULL,
    previous_end    timestamptz NOT NULL,
    new_start       timestamptz,                         -- NULL for cancellation
    new_end         timestamptz,
    changed_by      text NOT NULL CHECK (changed_by IN ('User','Agent','System')),
    reason          text,
    changed_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schedule_suggestions (                  -- R2.9 proposals (Section 19.9)
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trigger_action_id  uuid NOT NULL REFERENCES daily_actions(id) ON DELETE CASCADE,
    local_date         date NOT NULL,
    proposal           jsonb NOT NULL,                   -- [{daily_action_id, new_start, new_end, move_to_tomorrow}]
    status             text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','rejected','expired')),
    decided_at         timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trigger_action_id)
);

CREATE TABLE accountability_escalation_states (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type                 text NOT NULL CHECK (source_type IN ('HABIT','ROUTINE_ENTRY')),
    source_id                   uuid NOT NULL,
    level                       smallint NOT NULL CHECK (level BETWEEN 1 AND 5),
    escalation_episode_id       uuid NOT NULL DEFAULT gen_random_uuid(),
    episode_started_at          timestamptz NOT NULL DEFAULT now(),
    reflection_required         boolean NOT NULL DEFAULT false,
    reflection_completed_at     timestamptz,
    reflection_id               uuid,                    -- FK to reflections (added below)
    recovery_window_anchor_date date,
    last_reduced_at             timestamptz,
    last_evaluated_at           timestamptz NOT NULL DEFAULT now(),
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, source_type, source_id)
);

CREATE TABLE integrity_score_snapshots (
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date        date NOT NULL,
    score             numeric(4,1),                     -- NULL when denominator = 0
    kept              int NOT NULL,
    broken            int NOT NULL,
    overdue_deferred  int NOT NULL,
    computed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, local_date)
);

CREATE TABLE commitments (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title                      text NOT NULL,
    source                     text NOT NULL CHECK (source IN ('User-stated','AI-recommended')),
    due_date                   date NOT NULL,             -- original due date
    current_due_date           date NOT NULL,             -- = due_date, or latest deferred due date
    completion_condition       text NOT NULL CHECK (completion_condition IN ('single','all','any','explicit')),
    status                     text NOT NULL DEFAULT 'Open'
                               CHECK (status IN ('Open','Kept','Broken','Deferred','Cancelled')),
    deferral_count             smallint NOT NULL DEFAULT 0,
    explanation_window_ends_at timestamptz,
    goal_category              text,                      -- derived at creation (11.6)
    kept_at                    timestamptz,
    broken_at                  timestamptz,
    cancelled_at               timestamptz,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE commitment_links (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    commitment_id  uuid NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_type    text NOT NULL CHECK (entity_type IN ('Goal','Objective','Project','Task','DailyAction')),
    entity_id      uuid NOT NULL,
    removed_at     timestamptz,                         -- set when a linked Daily Action is deleted/cancelled
    removed_reason text,
    UNIQUE (commitment_id, entity_type, entity_id)
);

CREATE TABLE commitment_events (                     -- immutable audit
    id               bigserial PRIMARY KEY,
    commitment_id    uuid NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type       text NOT NULL CHECK (event_type IN
                     ('created','kept','broken','deferred','redeferred','cancelled',
                      'explanation_window_opened','explanation_submitted','link_removed','condition_changed')),
    previous_status  text,
    new_status       text,
    previous_due     date,
    new_due          date,
    explanation      text,
    actor            text NOT NULL CHECK (actor IN ('User','Agent','System')),
    trace_id         uuid,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memory_store_entries (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content          text NOT NULL,
    type             text NOT NULL CHECK (type IN ('Fact','Preference','Value','Principle','Lesson',
                                                   'Achievement','Failure','Reflection','Commitment','Pattern')),
    source           text NOT NULL CHECK (source IN ('User-stated','AI-inferred','System-derived')),
    categories       text[] NOT NULL DEFAULT '{}',
    importance       smallint NOT NULL CHECK (importance BETWEEN 1 AND 10),
    confidence       numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    is_inference     boolean NOT NULL DEFAULT false,
    source_ref_type  text,        -- reflection | checkin | onboarding_run | ceo_session | escalation_episode | conversation_message
    source_ref_id    uuid,
    superseded_by    uuid REFERENCES memory_store_entries(id) ON DELETE SET NULL,
    last_featured_at timestamptz,                       -- principle-of-the-day rotation (19.2)
    embedding        vector(1536),
    embedding_model  text,
    embedding_status text NOT NULL DEFAULT 'pending' CHECK (embedding_status IN ('pending','ready','failed')),
    generation_status text NOT NULL DEFAULT 'final' CHECK (generation_status IN ('final','template')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (source <> 'AI-inferred' OR is_inference),
    CHECK (embedding_status <> 'ready' OR embedding IS NOT NULL)
);

CREATE TABLE memory_proposals (                        -- Lesson proposals (19.6)
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    proposed_type    text NOT NULL CHECK (proposed_type IN ('Lesson')),
    content          text NOT NULL,
    evidence_refs    jsonb NOT NULL,                     -- [{reflection_id, similarity}]
    theme_embedding  vector(1536) NOT NULL,
    status           text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','rejected','expired')),
    memory_entry_id  uuid REFERENCES memory_store_entries(id) ON DELETE SET NULL,
    decided_at       timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reflections (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date                  date NOT NULL,                  -- local date
    type                  text NOT NULL CHECK (type IN ('daily','weekly_ceo','accountability')),
    answers               jsonb NOT NULL DEFAULT '{}',    -- daily: {accomplished, held_back, tomorrow}; ceo: q1..q4; accountability: 3 answers
    content               text NOT NULL,                  -- user text (daily) or summary (ceo/accountability)
    transcript            text,                           -- weekly_ceo only
    mood                  smallint CHECK (mood BETWEEN 1 AND 5),
    energy                smallint CHECK (energy BETWEEN 1 AND 5),
    stress                smallint CHECK (stress BETWEEN 1 AND 5),
    goal_categories       text[] NOT NULL DEFAULT '{}',
    escalation_episode_id uuid,                           -- accountability reflections
    search_tsv            tsvector GENERATED ALWAYS AS
                          (to_tsvector('simple', coalesce(content,'') || ' ' || coalesce(answers::text,''))) STORED,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz
);
CREATE UNIQUE INDEX uq_reflections_daily ON reflections(user_id, date)
    WHERE type = 'daily' AND deleted_at IS NULL;          -- one daily reflection per day; edits update it
ALTER TABLE accountability_escalation_states
    ADD FOREIGN KEY (reflection_id) REFERENCES reflections(id) ON DELETE SET NULL;

CREATE TABLE daily_briefings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date      date NOT NULL,
    content         jsonb NOT NULL,                       -- deterministic sections (19.2)
    narrative       text,
    narrative_status text NOT NULL CHECK (narrative_status IN ('generated','template')),
    generated_at    timestamptz NOT NULL,
    notification_id uuid,
    UNIQUE (user_id, local_date)
);

CREATE TABLE weekly_ceo_sessions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start_date      date NOT NULL,                   -- Monday of the reviewed week
    scheduled_at         timestamptz NOT NULL,
    grace_ends_at        timestamptz NOT NULL,
    opened_at            timestamptz,
    completed_at         timestamptz,
    status               text NOT NULL CHECK (status IN ('scheduled','opened','completed','skipped')),
    pre_session_briefing jsonb NOT NULL,
    summary              text,
    reflection_id        uuid REFERENCES reflections(id) ON DELETE SET NULL,
    focus_plan_status    text CHECK (focus_plan_status IN ('proposed','accepted','rejected')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, week_start_date)
);

CREATE TABLE weekly_focus_plans (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ceo_session_id  uuid NOT NULL UNIQUE REFERENCES weekly_ceo_sessions(id) ON DELETE CASCADE,
    week_start_date date NOT NULL,                        -- the upcoming week
    content         jsonb NOT NULL,                       -- [{goal_id, focus, planned_actions[]}]
    accepted_at     timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_insights (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent       text NOT NULL CHECK (agent IN ('personal_assistant','mentor','accountability')),
    content     text NOT NULL,
    citations   jsonb NOT NULL DEFAULT '[]',
    trace_id    uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analytics_results (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('energy_completion_corr','mood_completion_corr','weekly_summary')),
    computed_for date NOT NULL,
    result       jsonb NOT NULL,       -- {method, n, coefficient, p_value, status: none|preliminary|not_significant|significant}
    computed_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, kind, computed_for)
);

CREATE TABLE proactive_flags (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag_type    text NOT NULL CHECK (flag_type IN ('escalation_level_3plus','integrity_below_threshold',
                                                    'ceo_skipped_consecutive','lesson_proposal','schedule_suggestion')),
    owner_agent  text NOT NULL CHECK (owner_agent IN ('personal_assistant','mentor','accountability')),
    ref_type     text,
    ref_id       uuid,
    dedupe_key   text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    surfaced_at  timestamptz,
    resolved_at  timestamptz,
    UNIQUE (user_id, dedupe_key)
);

CREATE TABLE conversation_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode            text NOT NULL DEFAULT 'chat' CHECK (mode IN ('chat','onboarding','ceo_meeting')),
    flow_ref_id     uuid,                                -- onboarding_runs.id or weekly_ceo_sessions.id
    origin          text NOT NULL CHECK (origin IN ('user','notification','dashboard_now','why','briefing')),
    title           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    deleted_at      timestamptz
);

CREATE TABLE conversation_messages (
    id          uuid PRIMARY KEY,                          -- = message_id (idempotent upsert)
    session_id  uuid NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('user','assistant')),
    content     text NOT NULL,
    agent       text CHECK (agent IN ('personal_assistant','mentor','accountability')),
    citations   jsonb NOT NULL DEFAULT '[]',
    synthetic   boolean NOT NULL DEFAULT false,            -- opener placeholders are hidden in UI
    trace_id    uuid,
    search_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pending_confirmations (
    action_id     uuid PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id    uuid NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    message_id    uuid NOT NULL,
    thread_id     text NOT NULL,
    tool_name     text NOT NULL,
    tier          text NOT NULL CHECK (tier IN ('mutating','destructive')),
    preview       jsonb NOT NULL,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','expired')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz,
    expires_at    timestamptz NOT NULL
);

CREATE TABLE notifications (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type           text NOT NULL CHECK (type IN ('briefing','daily_reflection','ceo_meeting','commitment_breach',
                                                 'commitment_explanation_due','accountability_l1','accountability_l2',
                                                 'accountability_l3','accountability_l4','accountability_l5',
                                                 'schedule_suggestion','lesson_proposal','account_security')),
    level          smallint CHECK (level BETWEEN 1 AND 5),
    title          text NOT NULL,
    body           text NOT NULL,
    deep_link      jsonb NOT NULL,                  -- {screen, context_ref (signed)}; no raw sensitive content
    delivery_state text NOT NULL DEFAULT 'Scheduled'
                   CHECK (delivery_state IN ('Scheduled','Delivered','Opened','Acted Upon')),
    scheduled_at   timestamptz NOT NULL,
    not_before     timestamptz,                     -- DND / rate-limit deferral
    stale_after    timestamptz,                     -- Level 1–2: scheduled_at + 4 h
    delivered_at   timestamptz,
    opened_at      timestamptz,
    acted_at       timestamptz,
    retry_count    smallint NOT NULL DEFAULT 0,
    final_failure  boolean NOT NULL DEFAULT false,
    dedupe_key     text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, dedupe_key)
);

CREATE TABLE notification_attempts (
    id              bigserial PRIMARY KEY,
    notification_id uuid NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    attempt_no      smallint NOT NULL,
    channel         text NOT NULL CHECK (channel IN ('push','in_app')),
    status          text NOT NULL CHECK (status IN ('success','failed','dropped_stale','suppressed_rate_limit')),
    error_code      text,
    attempted_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE push_devices (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform         text NOT NULL CHECK (platform IN ('ios','android','web')),
    token_ciphertext bytea NOT NULL,
    token_hash       bytea NOT NULL UNIQUE,
    enabled          boolean NOT NULL DEFAULT true,
    last_seen_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_traces (
    id                  uuid PRIMARY KEY,                    -- = trace_id
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id          uuid REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    message_id          uuid,
    graph               text NOT NULL CHECK (graph IN ('supervisor','onboarding','ceo_meeting','background')),
    mode                text,
    selected_agent      text,
    routing_confidence  numeric(3,2),
    ambiguity_flag      boolean NOT NULL DEFAULT false,
    classifier_fallback boolean NOT NULL DEFAULT false,
    tool_calls          jsonb NOT NULL DEFAULT '[]',   -- [{name, tier, status, latency_ms, error_code, sanitized_input}]
    memory_queries      jsonb NOT NULL DEFAULT '[]',   -- [{query_hash, k, threshold, result_ids[]}]
    proposed_actions    jsonb NOT NULL DEFAULT '[]',   -- [{action_id, tool_name, tier, outcome: approved|edited|rejected|expired}]
    outcome             text CHECK (outcome IN ('accepted','rejected','mixed','none')),
    has_tool_error      boolean NOT NULL DEFAULT false, -- flagged for developer review (R23.4)
    grounding_flags     text[] NOT NULL DEFAULT '{}',   -- grounding_violation, missing_longitudinal_citation, ...
    review_status       text NOT NULL DEFAULT 'none' CHECK (review_status IN ('none','flagged','reviewed')),
    model_calls         smallint NOT NULL DEFAULT 0,
    input_tokens        int,
    output_tokens       int,
    first_token_ms      int,
    total_ms            int,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_trace_payloads (
    trace_id           uuid PRIMARY KEY REFERENCES agent_traces(id) ON DELETE CASCADE,
    capture_mode       text NOT NULL CHECK (capture_mode IN ('metadata','full_redacted','full')),
    encrypted_payload  bytea NOT NULL,
    key_version        text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE background_job_runs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name       text NOT NULL,
    logical_run_at timestamptz NOT NULL,
    user_id        uuid REFERENCES users(id) ON DELETE CASCADE,  -- NULL for whole-sweep rows
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz,
    attempt_no     smallint NOT NULL DEFAULT 1,
    status         text NOT NULL CHECK (status IN ('running','succeeded','failed')),
    error_code     text,
    error_details  text                                  -- sanitized
);

CREATE TABLE developer_alerts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source       text NOT NULL,                          -- job name / subsystem
    severity     text NOT NULL CHECK (severity IN ('warning','error','critical')),
    user_id      uuid REFERENCES users(id) ON DELETE CASCADE,
    details      jsonb NOT NULL,                         -- sanitized
    created_at   timestamptz NOT NULL DEFAULT now(),
    acknowledged_at timestamptz
);

CREATE TABLE idempotency_records (
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idempotency_key     text NOT NULL,
    request_fingerprint bytea NOT NULL,                  -- sha256(method + route + canonical body)
    status              text NOT NULL CHECK (status IN ('processing','completed','failed')),
    result              jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    PRIMARY KEY (user_id, idempotency_key)
);

CREATE TABLE domain_events (                          -- transactional outbox (Section 4.3)
    id           bigserial PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type   text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

CREATE TABLE realtime_events (                        -- SSE replay buffer (Section 28.3)
    id          bigserial PRIMARY KEY,                  -- monotonic sequence
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type  text NOT NULL,
    payload     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE data_exports (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       text NOT NULL CHECK (status IN ('queued','running','ready','failed','expired')),
    object_key   text,                                   -- encrypted object storage location
    requested_at timestamptz NOT NULL DEFAULT now(),
    ready_at     timestamptz,
    expires_at   timestamptz                             -- ready_at + 24 h
);

CREATE TABLE account_deletion_requests (               -- survives the purge for audit (no FK, no PII)
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL,
    confirmed_at     timestamptz NOT NULL,
    purge_due_at     timestamptz NOT NULL,               -- confirmed_at + 30 days (purge runs earlier by default: + 7 days)
    purged_at        timestamptz,
    status           text NOT NULL CHECK (status IN ('pending_purge','purged','failed'))
);
