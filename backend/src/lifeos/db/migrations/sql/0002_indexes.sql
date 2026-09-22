-- GENERATED from design.md §25 by backend/scripts/extract_schema.py (T1.2). Edit design.md, then re-extract.

-- Goal hierarchy
CREATE INDEX idx_goals_user_priority ON goals(user_id, priority DESC, target_date, created_at)
    WHERE status = 'active' AND deleted_at IS NULL;
CREATE INDEX idx_objectives_goal ON objectives(goal_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_projects_objective ON projects(objective_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_tasks_user_status_due ON tasks(user_id, status, due_date) WHERE deleted_at IS NULL;
CREATE INDEX idx_objective_history ON objective_value_history(objective_id, changed_at);

-- Habits and routines
CREATE INDEX idx_habits_user_active ON habits(user_id) WHERE status = 'active' AND deleted_at IS NULL;
CREATE INDEX idx_habit_pauses ON habit_pause_periods(habit_id, starts_on);
CREATE INDEX idx_routine_entries_template ON routine_entries(routine_template_id, sort_order) WHERE deleted_at IS NULL;
CREATE INDEX idx_routine_exceptions ON routine_exceptions(user_id, date);

-- Daily Actions and check-ins
CREATE INDEX idx_daily_actions_user_date ON daily_actions(user_id, date) WHERE lifecycle_state = 'active';
CREATE INDEX idx_daily_actions_user_start ON daily_actions(user_id, scheduled_start);
CREATE INDEX idx_daily_actions_source ON daily_actions(user_id, source_type, source_id, date);
CREATE INDEX idx_daily_actions_due_eval ON daily_actions(scheduled_start, scheduled_end)
    WHERE status IN ('Planned','Started') AND lifecycle_state = 'active';      -- Level 1/2 sweeps
CREATE INDEX idx_checkin_action_created ON checkin_records(daily_action_id, created_at);
CREATE INDEX idx_checkin_user_created ON checkin_records(user_id, created_at);
CREATE INDEX idx_schedule_history_action ON daily_action_schedule_history(daily_action_id, changed_at);

-- Commitments and accountability
CREATE INDEX idx_commitments_user_status_due ON commitments(user_id, status, current_due_date);
CREATE INDEX idx_commitments_eval ON commitments(current_due_date) WHERE status IN ('Open','Deferred');
CREATE INDEX idx_commitment_links_entity ON commitment_links(entity_type, entity_id) WHERE removed_at IS NULL;
CREATE INDEX idx_commitment_events ON commitment_events(commitment_id, created_at);
CREATE INDEX idx_escalation_user_level ON accountability_escalation_states(user_id, level);

-- Memory
CREATE INDEX idx_memory_user_type ON memory_store_entries(user_id, type, created_at DESC);
CREATE INDEX idx_memory_user_ready ON memory_store_entries(user_id)
    WHERE embedding_status = 'ready' AND superseded_by IS NULL;
CREATE INDEX idx_memory_embedding_hnsw ON memory_store_entries USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_memory_pending ON memory_store_entries(created_at) WHERE embedding_status = 'pending';
CREATE INDEX idx_memory_content_trgm ON memory_store_entries USING gin (content gin_trgm_ops);

-- Reflections, conversations
CREATE INDEX idx_reflections_user_date ON reflections(user_id, date DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_reflections_tsv ON reflections USING gin (search_tsv);
CREATE INDEX idx_messages_session_created ON conversation_messages(session_id, created_at);
CREATE INDEX idx_messages_tsv ON conversation_messages USING gin (search_tsv);
CREATE INDEX idx_sessions_user_activity ON conversation_sessions(user_id, last_activity_at DESC) WHERE deleted_at IS NULL;

-- Notifications
CREATE INDEX idx_notifications_due ON notifications(scheduled_at)
    WHERE delivery_state = 'Scheduled' AND final_failure = false;
CREATE INDEX idx_notifications_user_created ON notifications(user_id, created_at DESC);

-- Traces (R23.2)
CREATE INDEX idx_traces_user_created ON agent_traces(user_id, created_at);
CREATE INDEX idx_traces_session ON agent_traces(session_id);
CREATE INDEX idx_traces_agent_created ON agent_traces(selected_agent, created_at);
CREATE INDEX idx_traces_flagged ON agent_traces(created_at) WHERE review_status = 'flagged';

-- Platform
CREATE INDEX idx_idempotency_expires ON idempotency_records(expires_at);
CREATE INDEX idx_domain_events_unprocessed ON domain_events(id) WHERE processed_at IS NULL;
CREATE INDEX idx_realtime_events_user ON realtime_events(user_id, id);
CREATE INDEX idx_proactive_open ON proactive_flags(user_id) WHERE resolved_at IS NULL;
CREATE INDEX idx_auth_sessions_user_active ON auth_sessions(user_id) WHERE invalidated_at IS NULL;
