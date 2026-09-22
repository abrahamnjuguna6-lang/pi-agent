# Implementation Tasks — Personal Life OS

This is the step-by-step build plan for `design.md`. It follows the dependency order in design §43. Work through the modules top to bottom, and don't start a task until the tasks it depends on are ✅.

---

## 0. How to Use This File

### 0.1 Task Format

Each task has:
- **Req:** requirement IDs from `requirements.md` (e.g., R9.10).
- **Design:** sections in `design.md`.
- **Depends:** task IDs that must be complete first.
- **Files:** the main files to create or modify.
- **Steps:** the implementation steps.
- **Tests:** tests that are **part of the task**. A task is not done until they pass.
- **Done when:** verifiable acceptance criteria.

Status markers: `[ ]` todo · `[~]` in progress · `[x]` done.

### 0.2 Global Definition of Done (applies to every task)

1. The code follows the repo layout (§0.4). `ruff`, the type checker (`pyright --strict` on `src/lifeos/domain` and `src/lifeos/toolbus`), and the tests pass in CI.
2. Deterministic logic has unit tests. Status-transition logic has **100% branch coverage** (R24.4).
3. No user-facing mutation bypasses the confirmation model (agent path) or the idempotency model (all paths).
4. Every new user-owned table has `user_id`, RLS, an index plan, and is included in account deletion and export.
5. No secrets, tokens, or raw PII in logs or traces.
6. The `design.md` sections referenced are satisfied, and any intentional deviation is written back into `design.md` in the same change.

### 0.3 LangChain / LangGraph References

Before implementing any AI-layer task, **load the listed skills and re-read the listed doc pages**. APIs move quickly, so verify signatures against the docs rather than memory. Official index: `https://docs.langchain.com/oss/python/langgraph/llms.txt` and `https://docs.langchain.com/oss/python/langchain/llms.txt`.

| Key | Source | Use for |
|---|---|---|
| `S:deps` | skill `langchain-dependencies` | package set, version ranges |
| `S:lg-fund` | skill `langgraph-fundamentals` | StateGraph, reducers, Command, conditional edges, RetryPolicy, streaming |
| `S:lg-persist` | skill `langgraph-persistence` | checkpointers, thread_id, subgraph scoping, `update_state`, `Overwrite` |
| `S:lg-hitl` | skill `langgraph-human-in-the-loop` | `interrupt()`, `Command(resume=)`, idempotency before interrupts |
| `S:lc-fund` | skill `langchain-fundamentals` | tools, models, messages |
| `S:lc-mw` | skill `langchain-middleware` | HITL decision shape (approve/edit/reject), used as the reference protocol |
| `D:graph-api` | docs.langchain.com/oss/python/langgraph/graph-api | `context_schema`, `Runtime`, `Command[Literal[...]]`, `Overwrite` |
| `D:use-graph-api` | …/langgraph/use-graph-api | conditional edges, node signatures |
| `D:interrupts` | …/langgraph/interrupts | one interrupt per node invocation, no try/except around `interrupt`, JSON payloads |
| `D:persistence` · `D:checkpointers` | …/langgraph/persistence · …/langgraph/checkpointers | `AsyncPostgresSaver` (`langgraph.checkpoint.postgres.aio`), `setup()`, `adelete_thread`, thread_id < 255 chars |
| `D:fault` | …/langgraph/fault-tolerance | `RetryPolicy` (`langgraph.types`), `TimeoutPolicy`, `error_handler` |
| `D:streaming` · `D:events` | …/langgraph/streaming · …/langgraph/event-streaming | `astream(stream_mode=[...], version="v2")`, `get_stream_writer`, `astream_events(version="v3")` |
| `D:lg-test` | …/langgraph/test | per-node tests via `graph.nodes[...]`, `update_state(as_node=)`, `interrupt_after` |
| `D:models` | …/langchain/models | `init_chat_model`, `bind_tools(parallel_tool_calls=False)`, `with_structured_output(method="json_schema")`, usage metadata |
| `D:tools` · `D:messages` | …/langchain/tools · …/langchain/messages | tool schemas, `ToolMessage`, `tool_call_id` |
| `D:stm` | …/langchain/short-term-memory | trimming (`trim_messages`, `RemoveMessage`) |
| `D:structured` | …/langchain/structured-output | structured output strategies |
| `D:unit` | …/langchain/test/unit-testing | `GenericFakeChatModel` (`langchain_core.language_models.fake_chat_models`) |
| `D:evals` | …/langchain/test/evals | `agentevals` trajectory match / LLM-as-judge, LangSmith pytest |
| `D:guardrails` | …/langchain/guardrails | injection/PII guardrail patterns |

> No LangChain documentation MCP server is configured in this environment, so the docs above are the reference source. If one is added later, use it for the same checks.

### 0.4 Repository Layout (created in T0.1)

```
backend/
  pyproject.toml  uv.lock  alembic.ini
  src/lifeos/
    config.py                 # Pydantic Settings
    main.py                   # FastAPI app + lifespan (pool, checkpointer, graphs)
    db/                       # engine, session (SET LOCAL ROLE + app.user_id), models.py, migrations/
    domain/                   # one module per Domain Service (design §5.4)
    events/                   # outbox publisher + consumers
    toolbus/                  # registry.py, specs/, policy.py, executor.py, results.py
    agents/                   # state.py, context_engine.py, grounding.py, prompts/, nodes/, graphs/
    realtime/                 # chat_gateway.py, sse.py, tickets.py, stream_adapter.py
    notifications/            # pipeline, providers (fcm/apns/webpush), policy
    worker/                   # leader.py, scheduler.py, jobs/
    api/                      # routers/, deps.py, errors.py, idempotency.py
    security/                 # jwt, crypto (KMS envelope), hashing, rate limits
    observability/            # tracing (OTel), trace_service, redaction
  tests/{unit,integration,graph,e2e}/
  eval/lifeos_eval/           # standalone evaluation suite (R26.5)
frontend/
  packages/api-client/        # generated from OpenAPI
  web/                        # Vite + React
  mobile/                     # Expo
infra/
  docker-compose.yml  Dockerfile.api  Dockerfile.worker
.github/workflows/
```

### 0.5 Module Overview

| Module | Scope | Design |
|---|---|---|
| M0 | Bootstrap, tooling, CI, dependency lock + smoke test | §36, §37 |
| M1 | Database schema, RLS, triggers, migrations, test harness | §24, §25 |
| M2 | Security primitives and authentication | §21, §33 |
| M3 | Platform: idempotency, domain events, errors, clock | §4.3, §22, §26.3 |
| M4 | Profile and goal hierarchy | §15, §16.1, §24.3 |
| M5 | Scheduling: routines, habits, Daily Actions, check-ins | §12, §13, §16.2–16.3, §17 |
| M6 | Commitments, integrity, accountability | §11, §14, §16.6 |
| M7 | Memory Store | §23 |
| M8 | Analytics and deterministic insights | §16.5, §16.7–16.10 |
| M9 | Notifications | §29 |
| M10 | Worker and scheduler | §20 |
| M11 | Tool Bus | §10 |
| M12 | AI layer: supervisor graph, context, HITL, grounding | §6–§9, §34 |
| M13 | Product flows | §18, §19 |
| M14 | APIs: REST, WebSocket, SSE | §26–§28 |
| M15 | Privacy, observability, admin | §24.14, §30, §31 |
| M16 | Frontend (web + mobile) | §32 |
| M17 | Evaluation suite and release gate | §31.3, §38.4 |
| M18 | End-to-end, load, deployment hardening | §35, §36 |

---

## M0 — Bootstrap and Tooling

### [x] T0.1 Repository scaffold
- **Req:** — · **Design:** §36.2, §37 · **Depends:** —
- **Files:** `backend/pyproject.toml`, `backend/src/lifeos/{__init__,config,main}.py`, `infra/docker-compose.yml`, `infra/Dockerfile.api`, `infra/Dockerfile.worker`, `.editorconfig`, `README.md`
- **Steps:**
  1. Initialize a `uv` project with Python 3.11+ and the §0.4 layout.
  2. Add dependencies from design §37 (`S:deps`): `langchain>=1,<2`, `langchain-core>=1,<2`, `langgraph>=1,<2`, `langgraph-checkpoint-postgres`, `langchain-openai`, `langchain-tavily`, `langsmith>=0.3`, `agentevals`, `psycopg[binary]>=3.2`, `psycopg-pool`, FastAPI, SQLAlchemy 2, Alembic, pgvector, redis, APScheduler 3.x, sse-starlette, argon2-cffi, pyjwt[crypto], cryptography, scipy, numpy, OpenTelemetry. Do **not** add `langchain-community`.
  3. Create `Settings` (pydantic-settings) with every config key used in design §34, §35, and §36.4. Missing required secrets fail fast at startup.
  4. Docker Compose services: `postgres` (pgvector image, PG16, pgvector ≥ 0.8), `redis:7`, `api`, `worker`.
- **Tests:** `tests/unit/test_config.py` checks that missing required settings raise and that defaults match design §34.1 and §35.
- **Done when:** `docker compose up` starts all services, `GET /healthz` returns 200, and `uv lock` produces `uv.lock`.

### [x] T0.2 Quality tooling and CI pipeline
- **Req:** R24.4 · **Design:** §36.3 · **Depends:** T0.1
- **Files:** `.github/workflows/ci.yml`, `backend/ruff.toml`, `backend/pyrightconfig.json`, `backend/tests/conftest.py`
- **Steps:**
  1. Add ruff (lint + format), pyright, and pytest with `pytest-asyncio`, `pytest-cov`, and `time-machine`.
  2. The CI jobs are: lint, typecheck, unit, integration (Postgres and Redis service containers), and graph.
  3. Add a coverage gate: `--cov-fail-under=90` for `lifeos.domain`, with a per-file 100% branch rule enforced for the transition modules listed in T5.4, T6.1, and T6.4, using `coverage` config `[report] fail_under` per path via a small script.
- **Tests:** CI runs on a sample test and the coverage script fails on a deliberately uncovered branch fixture.
- **Done when:** a PR run shows every job green.

### [x] T0.3 Dependency smoke test (lockfile rule)
- **Req:** — · **Design:** §37.1 · **Depends:** T0.1
- **References:** `S:lg-persist`, `S:lg-hitl`, `D:checkpointers`, `D:interrupts`
- **Files:** `backend/tests/integration/test_smoke_stack.py`
- **Steps:**
  1. Start an `AsyncConnectionPool(..., kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})`, then run `AsyncPostgresSaver(pool)` and `await checkpointer.setup()`.
  2. Build a two-node graph where one node calls `interrupt()`. Invoke it, assert that `__interrupt__` is present, resume with `Command(resume=...)`, and assert the final state.
  3. Run `CREATE EXTENSION vector`, create a `vector(1536)` column, and build an HNSW index.
  4. Run a Redis ping, start APScheduler with one job, and check FastAPI startup.
- **Tests:** the file above, marked `integration`.
- **Done when:** the smoke test passes in CI against locked versions. Record the resolved versions in `README.md`.

---

## M1 — Database Foundation

### [x] T1.1 SQLAlchemy base, session, and RLS context
- **Req:** R17.1 · **Design:** §24.1, §33.2 · **Depends:** T0.1
- **Files:** `db/engine.py`, `db/session.py`, `db/base.py`
- **Steps:**
  1. Create an async engine (psycopg3). `user_session(user_id)` opens a transaction and runs `SET LOCAL app.user_id = :uid`. `system_session()` uses the worker role with `BYPASSRLS`.
  2. Add a UTC-only datetime type guard and a `Clock` protocol (a real clock and a frozen clock for tests).
- **Tests:** `tests/integration/test_session_rls.py` (after T1.2) checks that rows of user A are invisible in a session for user B, and that a system session sees both only when filtered explicitly.
- **Done when:** every repository function obtains a session through these helpers.

### [x] T1.2 Initial migration: full schema
- **Req:** R15.9 · **Design:** §24.2–§24.13 · **Depends:** T1.1
- **Files:** `db/migrations/versions/0001_initial.py`, `db/migrations/sql/0001_schema.sql` (extracted from design §24 by `scripts/extract_schema.py`), `db/models.py`
- **Steps:**
  1. Enable the `pgcrypto`, `vector`, and `pg_trgm` extensions.
  2. Create every table from design §24 **exactly as specified**, in FK order (daily_actions before routine_exceptions; the deferred FKs added with `ALTER`).
  3. Add the SQLAlchemy models that mirror the DDL, with enums as `Literal` types in Python.
- **Tests:** `tests/integration/test_schema.py`
  - migrating an empty database and downgrading to base both succeed;
  - CHECK constraints: objective range rules, skip note required (`checkin_records` and `habit_occurrence_records`), `AI-inferred ⇒ is_inference`, partial percent range, and the MANUAL ⇔ `source_id IS NULL` rule;
  - unique constraints: Daily Action occurrence uniqueness, `daily_briefings(user_id, local_date)`, `weekly_ceo_sessions(user_id, week_start_date)`, `uq_reflections_daily`.
- **Done when:** all constraint tests pass and `alembic check` reports no drift between the models and the database.

### [x] T1.3 Indexes, RLS policies, and immutability triggers
- **Req:** R3.4, R23.2 · **Design:** §24.1, §25 · **Depends:** T1.2
- **Files:** `db/migrations/versions/0002_indexes_rls_triggers.py`, `db/migrations/sql/{0002_indexes,0002_security}.sql`
- **Steps:**
  1. Create every index in design §25.
  2. For each user-owned table, add `ENABLE ROW LEVEL SECURITY` and `CREATE POLICY user_isolation USING (user_id = current_setting('app.user_id')::uuid)`. Create the `lifeos_app` and `lifeos_worker` (BYPASSRLS) roles.
  3. Add the immutability trigger function and attach it to `checkin_records`, `daily_action_schedule_history`, `commitment_events`, and `objective_value_history`. The trigger rejects UPDATE/DELETE unless `app.allow_history_delete='on'`.
- **Tests:** `tests/integration/test_rls_and_immutability.py`
  - a cross-tenant SELECT, UPDATE, or DELETE through `lifeos_app` affects 0 rows;
  - UPDATE on `checkin_records` raises;
  - DELETE with `app.allow_history_delete='on'` succeeds.
  - a meta-test lists every table with `user_id` and asserts that RLS is enabled on it, so future tables cannot miss it.
- **Done when:** all tests pass.

### [x] T1.4 Test data factories and fixtures
- **Req:** — · **Design:** §38.2 · **Depends:** T1.2
- **Files:** `tests/factories.py`, `tests/conftest.py`
- **Steps:** add factory-boy factories for every aggregate (user, goal tree, routine, habit, daily action with check-ins, commitment with links, memory entry). Add a per-test transactional DB fixture, a `frozen_clock` fixture, and a `user_tz` parameterization (UTC, America/New_York, Africa/Nairobi, Australia/Lord_Howe).
- **Tests:** a factory sanity test builds a complete user graph.
- **Done when:** later modules use these fixtures.

---

## M2 — Security and Authentication

### [x] T2.1 Crypto primitives
> **Implementation note:** `LocalKeyProvider` derives KEKs from secret-manager-provided secrets; the cloud-KMS `KeyProvider` adapter is delivered with deployment (T18.3). `security/kms.py` is therefore not created yet.
- **Req:** R17 · **Design:** §21.6–21.7, §33.5 · **Depends:** T0.1
- **Files:** `security/crypto.py`, `security/hashing.py`, `security/kms.py`
- **Steps:**
  1. AES-256-GCM envelope encryption behind a `KeyProvider` interface: a local provider for dev and a KMS provider for production, with `key_version` recorded.
  2. HMAC-SHA-256 for lookup hashes and token hashes, using separate keys.
  3. Argon2id password hashing with rehash-on-parameter-change.
  4. Email normalization.
- **Tests:** `tests/unit/test_crypto.py` covers the encrypt/decrypt round trip, tamper detection, key-version rotation (decrypting an old version), deterministic HMAC, and Argon2id verify and rehash detection.
- **Done when:** the tests pass and no key material is logged.

### [x] T2.2 JWT and session version cache
- **Req:** R15.6, R17.4–17.5 · **Design:** §21.1–21.2 · **Depends:** T2.1, T1.2
- **Files:** `security/jwt.py`, `domain/auth/sessions.py`, `api/deps.py`
- **Steps:**
  1. Issue and verify JWTs with EdDSA and `kid`. Claims: `sub, sid, jti, iat, exp, ver`. Lifetime 1 h.
  2. The `current_user` FastAPI dependency validates `(sid, ver)` against Redis and falls back to Postgres on a miss. It rejects `users.status != 'active'`.
- **Tests:** `tests/unit/test_jwt.py` covers expired tokens, a wrong `kid`, and tampered tokens. `tests/integration/test_session_version.py` checks that a token with a stale `ver` is rejected, and that it is still rejected when Redis is empty (the database is authoritative).
- **Done when:** the tests pass.

### [x] T2.3 Registration, email verification, login, lockout
> **Implementation note:** email is sent through the `EmailSender` port (`notifications/email.py`); routing sends through the outbox after commit happens with T3.3/T9.2. The unverified-email check runs *after* password verification so it never reveals account state to password guessers.
- **Req:** R17.1–17.3, R17.8 · **Design:** §21.4–21.5, §33.4 · **Depends:** T2.2, T3.3
- **Files:** `domain/auth/service.py`, `api/routers/auth.py`, `notifications/email.py` (transactional email adapter)
- **Steps:**
  1. `register` encrypts the email, stores the lookup hash, stores the password hash, creates a verification token (24 h), and sends the email.
  2. `login` rejects unverified accounts (`EMAIL_NOT_VERIFIED`) and locked accounts (`ACCOUNT_LOCKED`). It creates an `auth_sessions` row and returns the access token and refresh token.
  3. Failure counters are Redis sliding windows per account and per IP. Five account failures in 10 minutes set `locked_until = now()+15m` and send an email.
- **Tests:** `tests/integration/test_auth_flow.py`
  - register → login before verify returns 403 `EMAIL_NOT_VERIFIED` → verify → login OK;
  - 5 wrong passwords lock the account, the 6th attempt returns `ACCOUNT_LOCKED` even with the correct password, the account is unlocked after 15 minutes (frozen clock), and the lock email is queued;
  - a duplicate email is rejected because the HMAC is unique.
- **Done when:** the tests pass.

### [x] T2.4 Refresh rotation, replay detection, logout, password reset
- **Req:** R17.4–17.6 · **Design:** §21.2–21.4 · **Depends:** T2.3
- **Files:** `domain/auth/service.py`, `api/routers/auth.py`
- **Steps:**
  1. `refresh` rotates the token and moves the old hash to `auth_refresh_token_history`. Reuse of a retired token invalidates the whole family.
  2. `logout` increments `session_version`, sets `invalidated_at`, publishes the revocation, and closes realtime connections (hook used later by T14.3).
  3. Password reset request and confirm: the token lasts 1 h, the response is the same whether or not the email exists, and a successful reset invalidates all sessions.
- **Tests:** `tests/integration/test_refresh_logout_reset.py`
  - the old refresh token fails after rotation, and replaying it kills the new token too;
  - an access token issued before logout returns 401 immediately afterwards;
  - a reset link is expired at 61 minutes;
  - after a reset, every previous session returns 401.
- **Done when:** the tests pass.

---

## M3 — Platform Services

### [x] T3.1 Error model and response envelope
- **Req:** R15.7 · **Design:** §26.1, §26.3 · **Depends:** T0.1
- **Files:** `api/errors.py`, `domain/errors.py`
- **Steps:**
  1. Define the `DomainError` hierarchy with the stable codes from design §26.3.
  2. Add FastAPI exception handlers that produce the envelope with `request_id`. Ownership failures map to 404.
  3. Unhandled exceptions return 500 with no internals.
- **Tests:** `tests/unit/test_errors.py` checks the mapping of each code to its HTTP status and that no stack trace appears in the body.
- **Done when:** all routers use the envelope.

### [x] T3.2 Idempotency layer (two-phase claim)
- **Req:** R15.5 · **Design:** §22 · **Depends:** T1.2, T3.1
- **Files:** `api/idempotency.py`, `domain/idempotency.py`
- **Steps:**
  1. `run_idempotent(user_id, key, fingerprint, fn)` does phase 1 in autocommit. Phase 2 runs `fn` and marks the record `completed` **in the same transaction**. On an exception the transaction rolls back and the record is marked `failed`.
  2. Handle stale `processing` reclaim (≥ 60 s) and a fingerprint mismatch (422).
  3. Add a FastAPI dependency that requires the `Idempotency-Key` header on POST/PATCH/DELETE of user resources.
- **Tests:** `tests/integration/test_idempotency.py`
  - duplicate after success returns the stored result, and `fn` is called once;
  - **race:** 10 concurrent requests with the same key result in exactly one execution; the others get 409 or the stored result;
  - same key with a different body returns 422;
  - a crash simulation (exception after the business write but before commit) rolls back, and a retry executes once;
  - a stale processing record is reclaimed;
  - after 24 h the key is treated as new.
- **Done when:** the tests pass.

### [x] T3.3 Domain events outbox and consumers
> **Implementation note:** `domain_events` gained `available_at`, `attempts`, `last_error`, `dead_lettered_at` (design §4.3, §24.13): per-event transactions, exponential backoff, dead-letter after 10 attempts with a developer alert — a poison event never blocks others.
- **Req:** — · **Design:** §4.3, §24.13 · **Depends:** T1.2
- **Files:** `events/outbox.py`, `events/consumer.py`, `events/types.py`
- **Steps:**
  1. `publish(session, event_type, user_id, payload)` inserts into `domain_events` inside the caller's transaction.
  2. The consumer loop claims events with `FOR UPDATE SKIP LOCKED` and dispatches them to registered handlers. Handlers must be idempotent. The loop sets `processed_at`.
- **Tests:** `tests/integration/test_outbox.py` checks that an event is not visible when the transaction rolls back, is processed once with two consumers, and is retried after a handler exception.
- **Done when:** the tests pass.

### [x] T3.4 Time and timezone utilities
- **Req:** R16.2, R21.3–21.5 · **Design:** §20.3 · **Depends:** T0.1
- **Files:** `domain/timeutil.py`
- **Steps:** implement `resolve_local(date, time, tz) -> datetime` in UTC (spring-forward advances minute by minute, fall-back uses `fold=0`), plus `local_date(dt, tz)`, `local_day_bounds(date, tz)`, `iso_week_bounds`, and time blocks that cross midnight.
- **Tests:** `tests/unit/test_timeutil.py` covers the America/New_York DST dates in both directions, Australia/Lord_Howe (30-minute DST), a 23:30–00:30 block, and day bounds on DST days (23 h and 25 h days).
- **Done when:** the tests pass with 100% branch coverage.

---

## M4 — Profile and Goal Hierarchy

### [ ] T4.1 ProfileService and preferences
- **Req:** R16.1–16.2, R16.6, R8.9, R14.3 · **Design:** §24.2, §24.11 · **Depends:** T2.2
- **Files:** `domain/profile.py`, `domain/notification_prefs.py`, `api/routers/me.py`
- **Steps:**
  1. `GET/PATCH /me` covers every profile field. The IANA timezone is validated with `zoneinfo`.
  2. `NotificationPrefs` is a Pydantic model with the defaults from design §24.11. At least one channel must stay enabled for L4/L5/security.
  3. The timezone-change hook is a no-op until T5.7 wires it in.
- **Tests:** `tests/unit/test_notification_prefs.py` checks defaults and the rejection of disabling all channels for L4. `tests/integration/test_profile_api.py` checks that an invalid timezone returns 422 and that a PATCH persists.
- **Done when:** the tests pass.

### [ ] T4.2 Goals, Objectives, Projects, Tasks CRUD
- **Req:** R1.1–1.2, R1.8–1.9, R3.7, R15.1 · **Design:** §24.3, §26.2 · **Depends:** T3.2, T4.1
- **Files:** `domain/goals.py`, `domain/objectives.py`, `domain/tasks.py`, `api/routers/{goals,objectives,projects,tasks}.py`
- **Steps:**
  1. Services with ownership checks, and routers with cursor pagination and idempotency.
  2. Objective validation (R1.3) returns `VALIDATION_ERROR`, and nothing is saved.
- **Tests:** `tests/integration/test_goal_hierarchy_api.py` covers CRUD happy paths, 404 on another user's IDs, the objective range validation cases, and a required objective target date.
- **Done when:** the tests pass.

### [ ] T4.3 ProgressService
- **Req:** R1.4–1.7 · **Design:** §16.1 · **Depends:** T4.2
- **Files:** `domain/progress.py`
- **Steps:**
  1. Pure functions `objective_progress()` and `goal_progress()` using `Decimal`.
  2. A recompute runs in the same transaction on any objective value, weight, status, or target change. It appends `objective_value_history` and publishes `objective.value_changed`.
- **Tests:** `tests/unit/test_progress.py` covers every table row in design §38.1 "Progress" plus a property-based test (hypothesis) that progress is always within [0, 100]. `tests/integration/test_progress_recompute.py` checks that archiving an objective changes goal progress.
- **Done when:** the tests pass with 100% branch coverage.

### [ ] T4.4 Goal priority, archive, cascade delete, hierarchy view
- **Req:** R1.14–1.16, R7.2 · **Design:** §15, §24.3 · **Depends:** T4.3
- **Files:** `domain/goals.py`, `api/routers/goals.py`
- **Steps:**
  1. `list_active()` uses the deterministic sort from design §15.2.
  2. Archive makes the subtree read-only (`GOAL_ARCHIVED`).
  3. DELETE returns 409 `CASCADE_CONFIRMATION_REQUIRED` with counts unless `confirm_cascade=true`, then soft-deletes the subtree.
  4. `GET /goals/{id}/hierarchy` returns the tree, with Daily Actions added once M5 exists.
- **Tests:** `tests/unit/test_goal_sort.py` covers tie-breaks. `tests/integration/test_goal_archive_delete.py` checks that writes are rejected under an archived goal, that the counts are correct, and that history is preserved after deletion.
- **Done when:** the tests pass.

---

## M5 — Scheduling, Habits, Daily Actions, Check-ins

### [ ] T5.1 Routine Templates and Entries
- **Req:** R2.1–2.2 · **Design:** §24.4 · **Depends:** T4.2
- **Files:** `domain/routines.py`, `api/routers/routines.py`
- **Steps:** CRUD with the rule that at most one active template applies per weekday, entries crossing midnight are allowed, and entries can link to a Goal or Habit.
- **Tests:** `tests/integration/test_routines_api.py` checks the conflicting-weekday rejection and the stable entry IDs across edits.
- **Done when:** the tests pass.

### [ ] T5.2 Habits CRUD and pauses
- **Req:** R1.10, R1.12 · **Design:** §17.1, §17.4, §24.4 · **Depends:** T4.2
- **Files:** `domain/habits.py`, `api/routers/habits.py`
- **Steps:**
  1. CRUD, where a habit must sit beneath a Goal or Project.
  2. Pause and resume through `habit_pause_periods`. A pause removes future untouched Planned HABIT Daily Actions and writes history rows.
- **Tests:** `tests/integration/test_habits_api.py` checks recurrence validation, that pausing removes future actions, and that resuming allows generation again.
- **Done when:** the tests pass.

### [ ] T5.3 Daily Action generation (Routine Instances and Habits)
- **Req:** R1.13, R2.3–2.4 · **Design:** §12.1, §17.1, §20.2 · **Depends:** T5.1, T5.2, T3.4
- **Files:** `domain/schedule/generation.py`
- **Steps:**
  1. `generate_for_user(user, local_dates=[today, tomorrow])` creates `routine_instances`, then ROUTINE_ENTRY Daily Actions (applying `routine_exceptions`), then HABIT Daily Actions (skipping pauses, habits linked through a routine entry, and overrides).
  2. Each Daily Action is inserted together with its initial `none→Planned` check-in (`System`) in one transaction.
  3. Use `ON CONFLICT DO NOTHING` on the occurrence uniqueness.
- **Tests:** `tests/unit/test_generation_rules.py` covers which entries and habits apply on a given date. `tests/integration/test_generation.py` checks idempotency (running twice gives the same rows), the initial check-in, DST-day times, a midnight-crossing entry, a paused habit being skipped, and a routine-linked habit producing no duplicate.
- **Done when:** the tests pass.

### [ ] T5.4 CheckinService (status machine)
- **Req:** R3.1–3.6, R3.13 · **Design:** §16.2 · **Depends:** T5.3, T3.3
- **Files:** `domain/checkins.py`, `api/routers/daily_actions.py` (checkins endpoints)
- **Steps:**
  1. `transition(daily_action_id, new_status, note, source, actor_trace_id)` locks the row with `SELECT … FOR UPDATE`, validates the transition, updates the status, inserts the check-in, and publishes `daily_action.status_changed`.
  2. A Skipped transition requires a reason (`SKIP_REASON_REQUIRED`). Completed sets `completed_at`.
  3. Transitions are rejected on cancelled actions and on archived-goal sources.
  4. Add `GET /daily-actions/{id}/checkins`.
- **Tests:** `tests/unit/test_checkin_transitions.py` covers the full matrix in design §38.1 at **100% branch coverage**. `tests/integration/test_checkins.py` covers two concurrent transitions (one wins, one gets `INVALID_TRANSITION`), no record on a rejected transition, and an event published only on commit.
- **Done when:** the tests pass and the coverage gate is enforced for `domain/checkins.py`.

### [ ] T5.5 Task ↔ Daily Action sync and scheduling Tasks
- **Req:** R1.9, R3.7, R3.10 · **Design:** §16.2, §10.3 (`complete_task`) · **Depends:** T5.4
- **Files:** `domain/tasks.py`
- **Steps:**
  1. `POST /tasks/{id}/schedule` creates a TASK Daily Action.
  2. The event handler sets the Task to `in_progress` or `completed` from the Daily Action status.
  3. `complete_task(target_type=task)` also completes the scheduled Daily Action in the same transaction.
- **Tests:** `tests/integration/test_task_sync.py`
- **Done when:** the tests pass.

### [ ] T5.6 Source-aware rescheduling, cancellation, schedule history
- **Req:** R2.5–2.8, R3.11 · **Design:** §12.2–12.4, §13 · **Depends:** T5.4
- **Files:** `domain/schedule/reschedule.py`, `api/routers/daily_actions.py`
- **Steps:**
  1. `reschedule(daily_action, new_start, new_end, actor)`: ROUTINE_ENTRY creates a routine exception, HABIT creates an override, TASK updates the task, MANUAL updates directly. Every change appends history. Only Planned actions can be rescheduled.
  2. Cancel is a lifecycle change and writes history.
  3. Add `match_actions(date, match_time, match_title)` for natural-language matching (±15 min, similarity).
  4. Add `GET /daily-actions/{id}/schedule-history`.
- **Tests:** `tests/unit/test_reschedule_rules.py` covers the §38.1 scheduling table. `tests/integration/test_reschedule.py` checks that a cross-day move has no uniqueness collision, that the Completion Rate counts only on the destination date (after T5.8), and that a Started action is rejected.
- **Done when:** the tests pass.

### [ ] T5.7 Timezone change handling
- **Req:** R21.4 · **Design:** §12.5 · **Depends:** T5.6, T4.1
- **Files:** `domain/profile.py`, `domain/schedule/timezone_change.py`
- **Steps:** follow design §12.5 in one transaction: regenerate untouched future routine and habit actions, and recompute UTC for task and manual actions at the same wall-clock time, with history rows.
- **Tests:** `tests/integration/test_timezone_change.py` checks that past rows are unchanged, that regenerated actions are correct, and that task actions keep 09:00 local.
- **Done when:** the tests pass.

### [ ] T5.8 Completion Rate
- **Req:** R3.12–3.13 · **Design:** §16.3 · **Depends:** T5.4
- **Files:** `domain/analytics/completion.py`
- **Steps:** `completion_rate(user, period)` derives the status from the latest check-in at or before the period end, excludes cancelled actions, returns `null` for empty days, and uses local-date bounds.
- **Tests:** `tests/unit/test_completion_rate.py` covers Started in the denominator only, cancelled excluded, rescheduled counted once, an overdue action counted as Planned, and historical as-of evaluation.
- **Done when:** the tests pass with 100% branch coverage.

### [ ] T5.9 Habit occurrences and metrics
- **Req:** R1.11–1.12 · **Design:** §17.2–17.3 · **Depends:** T5.4, T5.2
- **Files:** `domain/habits.py`, `api/routers/habits.py`
- **Steps:**
  1. `record_occurrence(habit, date, result, percent, note)` writes the occurrence and the Daily Action transition in one transaction, following the design §17.2 table.
  2. `metrics()` covers counts per period, current and longest streak, missed occurrences, partials, and pauses. Results are cached for 5 minutes and invalidated on events.
- **Tests:** `tests/unit/test_habit_metrics.py` covers a daily streak, a weekly target of 3 per week, a partial not counting, pause days neither breaking nor extending a streak, and missed-occurrence counting. `tests/integration/test_habit_occurrence.py` checks that a partial gives a Completed Daily Action.
- **Done when:** the tests pass.

### [ ] T5.10 Late-completion schedule suggestions
- **Req:** R2.9 · **Design:** §19.9, §24.5 · **Depends:** T5.6
- **Files:** `domain/schedule/suggestions.py`, `api/routers/schedule_suggestions.py`
- **Steps:**
  1. An event handler runs on Completed with `completed_at > scheduled_end`. It builds the deterministic shift proposal, stores it, and creates a flag (the flag table is used in T13.1; stub the call).
  2. Accept applies each shift through `reschedule` with actor `User`. Reject and expiry change nothing.
- **Tests:** `tests/unit/test_suggestion_builder.py` covers order and duration being preserved and the `move_to_tomorrow` rule past sleep time. `tests/integration/test_suggestions_api.py` checks that nothing is modified until accept.
- **Done when:** the tests pass.

---

## M6 — Commitments, Integrity, Accountability

### [ ] T6.1 CommitmentService lifecycle
- **Req:** R9.1–9.6, R9.8–9.10 · **Design:** §11.1–11.3, §24.7 · **Depends:** T5.4
- **Files:** `domain/commitments.py`, `api/routers/commitments.py`
- **Steps:**
  1. `create` validates the completion condition against the links (`all`/`any` required for ≥ 2 Daily Actions), derives `goal_category` through lineage (a stub until T8.3), and writes the `created` event.
  2. The transition table from design §11.1 is enforced. Every transition appends a `commitment_events` row.
  3. An event handler on `daily_action.status_changed` evaluates linked Commitments in the same transaction. Deleting or cancelling a linked Daily Action removes the link, re-evaluates the Commitment, and notifies the User.
  4. Deferral: the first deferral needs no explanation. Re-deferral requires an explanation (≥ 20 characters) **and** an acknowledgment, and a new due date later than today.
  5. Endpoints: keep, cancel, defer, explanation.
- **Tests:** `tests/unit/test_commitment_transitions.py` covers the full commitment table in design §38.1 at **100% branch coverage**. `tests/integration/test_commitments.py` checks the `all` condition with a cancelled link and that a reschedule leaves the Commitment unchanged.
- **Done when:** the tests pass and the coverage gate is enforced.

### [ ] T6.2 Commitment deadline evaluation
- **Req:** R9.7, R9.10 · **Design:** §11.3 · **Depends:** T6.1
- **Files:** `domain/commitments.py` (`evaluate_deadlines(user, now)`)
- **Steps:**
  1. An Open Commitment past its local end-of-day due date becomes Broken, with a notification.
  2. A Deferred Commitment past due opens the explanation window (24 h), with a notification. When the window expires without an explanation, it becomes Broken.
- **Tests:** `tests/unit/test_deadline_eval.py` uses a frozen clock and covers local-midnight boundaries in 3 timezones.
- **Done when:** the tests pass.

### [ ] T6.3 Integrity score and snapshots
- **Req:** R9.11–9.14 · **Design:** §11.4–11.5, §16.6 · **Depends:** T6.1
- **Files:** `domain/integrity.py`, `api/routers/integrity.py`
- **Steps:**
  1. `compute_integrity_score(user, as_of)` implements the windowing from design §11.4. It returns `null` when the denominator is 0.
  2. Upsert today's snapshot on commitment transitions, and write a nightly snapshot.
  3. Detect a downward threshold crossing and emit an `integrity_below_threshold` flag (wired in T13.1).
  4. `GET /integrity-score` returns the current value and the 30-day series.
- **Tests:** `tests/unit/test_integrity_score.py` covers the window edges, a cancelled Commitment excluded, overdue deferred counted, rounding, and `null`. `tests/unit/test_threshold_crossing.py` checks that only a crossing emits a flag, deduplicated.
- **Done when:** the tests pass.

### [ ] T6.4 AccountabilityService: escalation engine
- **Req:** R8.1–8.8, R8.10 · **Design:** §14.2–14.4, §24.6 · **Depends:** T5.4, T5.9
- **Files:** `domain/accountability.py`
- **Steps:**
  1. Occurrence triggers: Level 1 at start with Planned, Level 2 past end with Planned or Started. These go to the notification pipeline (T9.x) with dedupe keys.
  2. Source evaluation over **scheduled occurrences**, excluding pauses. Level 3 is ≥ 3 distinct skip dates in 7 days, Level 4 is ≥ 5, and Level 5 is ≥ 5 consecutive scheduled occurrences. The highest level wins.
  3. Episodes and the reflection gate. Recovery requires ≥ 3 completion dates after the anchor, no reflection required, and reduces by exactly 1 level.
  4. Runs immediately from the event handler and in the sweeper, and is idempotent.
- **Tests:** `tests/unit/test_escalation.py` covers the full escalation table in design §38.1 plus a Mon/Wed/Fri habit, a pause gap, repeated evaluation producing no double reduction, and 4 → 5 without a new reflection. It must reach **100% branch coverage**. `tests/integration/test_accountability_events.py` checks that a skip check-in updates the level in the same request cycle.
- **Done when:** the tests pass and the coverage gate is enforced.

### [ ] T6.5 Accountability reflection submission
- **Req:** R8.5 · **Design:** §19.8 · **Depends:** T6.4, T7.2
- **Files:** `domain/accountability.py`, `api/routers/accountability.py`
- **Steps:**
  1. `POST /accountability/escalations/{id}/reflection` writes a reflections row (`type=accountability`) and a Memory entry, and clears the gate.
  2. `GET /accountability/escalations` lists escalation states.
- **Tests:** `tests/integration/test_accountability_reflection.py` checks that reduction is blocked before submission and allowed after it.
- **Done when:** the tests pass.

---

## M7 — Memory Store

### [ ] T7.1 Embedding client and backfill
- **Req:** R4.4 · **Design:** §23.3, §34.1 · **Depends:** T1.2
- **References:** `S:lc-fund`, `D:models`
- **Files:** `domain/memory/embeddings.py`
- **Steps:**
  1. Define an `Embedder` protocol with an OpenAI implementation (`langchain-openai` `OpenAIEmbeddings`, model from settings) and a deterministic fake for tests.
  2. `embed_pending(batch)` sets `ready` or `failed` and retries failures.
- **Tests:** `tests/unit/test_embedder_fake.py` checks that the fake is deterministic. `tests/integration/test_embedding_backfill.py` checks pending → ready and failure → retry.
- **Done when:** the tests pass.

### [ ] T7.2 MemoryService create, browse, delete
- **Req:** R4.1–4.3, R4.6–4.8, R18.8, R18.11 · **Design:** §10.7, §23.1–23.2, §23.5–23.6 · **Depends:** T7.1, T3.2
- **Files:** `domain/memory/service.py`, `api/routers/memory.py`
- **Steps:**
  1. `create()` is the **single** entry point. It takes a `creation_path` enum from design §10.7, applies the default importance and confidence, and enforces the inference rule.
  2. Browse supports filters by type, source, and category plus keyword search (trigram).
  3. Delete is a hard delete and requires the `X-Confirm-Phrase` header.
  4. Add a supersede operation for re-onboarding.
- **Tests:** `tests/unit/test_memory_rules.py` checks AI-inferred ⇒ inference, the defaults, and the clamping. `tests/integration/test_memory_api.py` checks filters, delete without the phrase returning 400, delete removing the embedding, and an architecture test proving that no module other than `domain/memory/service.py` inserts into `memory_store_entries` (a grep-based test).
- **Done when:** the tests pass.

### [ ] T7.3 Semantic search
- **Req:** R4.5, R4.9, R15.10 · **Design:** §23.4 · **Depends:** T7.2
- **Files:** `domain/memory/search.py`
- **Steps:**
  1. `search(user, query, k=8, threshold=0.75, filters)` runs an exact per-user KNN. Above 20 000 entries it uses HNSW with `SET LOCAL hnsw.iterative_scan = relaxed_order`.
  2. It excludes pending and superseded entries and orders results by similarity descending.
- **Tests:** `tests/integration/test_memory_search.py` uses fake vectors to check ordering, threshold filtering, user isolation, the pending exclusion, and type filters.
- **Done when:** the tests pass.

### [ ] T7.4 Automatic memory paths: reflections and check-in notes
- **Req:** R4.4, R11.5 · **Design:** §10.7 · **Depends:** T7.2, T5.4
- **Files:** `events/handlers/memory.py`
- **Steps:** event handlers create a Memory entry for each check-in note or skip reason (type Fact) and for each submitted reflection (type Reflection, wired in T13.5). Categories come from lineage.
- **Tests:** `tests/integration/test_auto_memory.py` checks that a skip with a reason produces one entry, that re-processing the event does not duplicate it, and that a transition without a note creates no entry.
- **Done when:** the tests pass.

---

## M8 — Analytics and Deterministic Insights

### [ ] T8.1 Correlation analysis
- **Req:** R11.6–11.9 · **Design:** §16.5 · **Depends:** T5.8
- **Files:** `domain/analytics/correlation.py`
- **Steps:** apply Spearman (scipy), with Shapiro–Wilk gating Pearson. The status is one of none, preliminary, not_significant, or significant. Results are stored in `analytics_results`.
- **Tests:** `tests/unit/test_correlation.py` covers n = 6, 7, 19, and 20 with p ≥ and < 0.05, ties, and constant input (an undefined coefficient gives `not_significant`, not a crash).
- **Done when:** the tests pass.

### [ ] T8.2 Now Mode candidates and schedule conflicts
- **Req:** R7.2–7.4, R6.4 · **Design:** §16.7–16.8 · **Depends:** T5.4, T4.4
- **Files:** `domain/now_mode.py`, `domain/schedule/analysis.py`
- **Steps:** `NowModeService.candidates(user, now)` and `ScheduleService.analyze_day(user, date)` return overlap, outside-waking-hours, and overload findings.
- **Tests:** `tests/unit/test_now_mode.py` covers the current block, behind schedule, next unstarted, and a buffer suggestion with no tasks. `tests/unit/test_schedule_analysis.py` covers the overlap, the 3-hour window overload, and the 90% day overload.
- **Done when:** the tests pass.

### [ ] T8.3 LineageService
- **Req:** R13.7, R9.15 · **Design:** §16.9 · **Depends:** T5.3, T4.2
- **Files:** `domain/lineage.py`
- **Steps:** walk Daily Action → source → Task, Project, Objective, Goal, returning the `unlinked` marker when there is no link. Wire it into `CommitmentService` for `goal_category`.
- **Tests:** `tests/unit/test_lineage.py` covers each source type, a routine entry linked to a habit linked to a goal, a deleted goal, and an unlinked item.
- **Done when:** the tests pass.

### [ ] T8.4 Weekly aggregates, wins, and gaps
- **Req:** R10.2 · **Design:** §16.10 · **Depends:** T8.3, T6.3
- **Files:** `domain/analytics/weekly.py`
- **Steps:** compute the Completion Rate by category, the integrity trend, and the top-3 wins and gaps with the ranking rules.
- **Tests:** `tests/unit/test_weekly.py` covers ranking tie-breaks and the Unlinked category.
- **Done when:** the tests pass.

---

## M9 — Notifications

### [ ] T9.1 Notification policy engine (pure)
- **Req:** R14.4–14.5, R14.8, R8.11 · **Design:** §14.6, §29.3–29.4, §29.6 · **Depends:** T4.1
- **Files:** `notifications/policy.py`
- **Steps:** `decide(notification, prefs, now, recent_pushes)` returns channels, `not_before`, and suppression. It covers DND by level and type, the global cap of 3 per hour except L4/L5, the style caps, and staleness.
- **Tests:** `tests/unit/test_notification_policy.py` covers the full §38.1 notification policy matrix, including DND crossing midnight, a 4th push in an hour going in-app only, and an L5 at the cap still being pushed. It must reach 100% branch coverage.
- **Done when:** the tests pass.

### [ ] T9.2 Notification pipeline and providers
- **Req:** R14.1–14.3, R14.6–14.7, R22.1–22.3 · **Design:** §29.1–29.2, §29.5–29.7 · **Depends:** T9.1, T3.3
- **Files:** `notifications/service.py`, `notifications/providers/{fcm,apns,webpush,inapp}.py`, `api/routers/{notifications,push_devices}.py`
- **Steps:**
  1. `enqueue(user, type, level, content, deep_link, dedupe_key)`, with a `dispatch_due(now)` sweep function.
  2. Record attempts. Retries run at +1, +4, and +16 minutes, then set `final_failure`.
  3. Deep links are signed context references (HMAC with an expiry).
  4. Endpoints for opened and acted, plus push device registration with encrypted tokens.
- **Tests:** `tests/integration/test_notification_pipeline.py` uses a fake provider that fails twice and then succeeds, and covers dedupe, stale drop for L1, L3 never dropped, and the state transitions. `tests/unit/test_signed_context_ref.py` covers tampering and expiry.
- **Done when:** the tests pass.

---

## M10 — Worker and Scheduler

### [ ] T10.1 Worker service, leader lock, job run recording
- **Req:** R21.1, R21.7–21.9 · **Design:** §20.1, §20.4 · **Depends:** T3.3
- **Files:** `worker/main.py`, `worker/leader.py`, `worker/runner.py`
- **Steps:**
  1. A Postgres advisory-lock leader election with a 10 s retry. Only the leader starts APScheduler.
  2. `run_job(name, logical_time, per_user_fn)` batches Users 500 at a time, records `background_job_runs`, retries each unit up to 3 times with backoff, and raises a `developer_alerts` row on final failure.
  3. Domain-event consumers run on every replica.
- **Tests:** `tests/integration/test_worker_leader.py` checks that two workers elect one leader and that killing the leader promotes the standby within 15 s. `tests/unit/test_job_runner.py` checks that a unit that fails 3 times raises an alert and that successes are recorded.
- **Done when:** the tests pass.

### [ ] T10.2 Core sweepers
- **Req:** R2.3, R8, R9.7, R14, R21.2 · **Design:** §20.2 · **Depends:** T10.1, T5.3, T6.2, T6.4, T9.2, T7.1
- **Files:** `worker/jobs/{routine_generation,commitment_evaluation,accountability_evaluation,notification_dispatch,embedding_backfill,integrity_snapshot,confirmation_expiry,retention_cleanup,checkpoint_retention}.py`
- **Steps:** implement each job by calling domain services. Due-time resolution uses `resolve_local`.
- **Tests:** `tests/integration/test_sweepers.py` uses time-machine to advance across a day for 3 timezones, checking generation for tomorrow, Level 1 and Level 2 triggers at the right minute, Broken at local midnight, retention purges, and that re-running any sweep is a no-op.
- **Done when:** the tests pass.

---

## M11 — Tool Bus

### [ ] T11.1 ToolSpec registry, policy, executor, result contract
- **Req:** R15.2–15.4, R15.7–15.8, R20.1 · **Design:** §10.1–10.4 · **Depends:** T3.2
- **References:** `S:lc-fund`, `D:tools`, `D:messages`
- **Files:** `toolbus/registry.py`, `toolbus/spec.py`, `toolbus/executor.py`, `toolbus/results.py`, `toolbus/sanitize.py`
- **Steps:**
  1. A `ToolSpec` dataclass. The registry exports model-facing tool schemas generated from each `args_model` (via `langchain_core.utils.function_calling.convert_to_openai_tool`, or `StructuredTool` with `args_schema`, without execution).
  2. `ToolBus.execute(call, ctx, pending_action)` re-validates the arguments, injects the actor, the idempotency key (`agent:{action_id}`), and the trace, calls the handler, and returns a `ToolResult` with `refs`. Errors include the sanitized `input`.
  3. Record the invocation (agent, tool, sanitized input, status, latency) into the trace collector.
- **Tests:**
  - `tests/unit/test_tool_schemas.py`: **no** schema contains `user_id`, `session_id`, `idempotency_key`, or `token` (checked across all registered tools), and every mutating or destructive tool has a `preview`.
  - `tests/unit/test_tool_errors.py`: the error contract shape, no stack trace, input truncation.
  - `tests/integration/test_tool_executor.py`: a mutating tool executed twice with the same action ID mutates once.
- **Done when:** the tests pass.

### [ ] T11.2 Required tools (R15.2 set)
- **Req:** R3.10–3.11, R7.1, R15.2, R25 · **Design:** §10.3, §10.5 · **Depends:** T11.1, T5.5, T5.6, T5.9, T7.3, T8.2
- **Files:** `toolbus/specs/{schedule,goals,progress,memory,web,tasks,habits,reflections}.py`, `domain/websearch.py`
- **Steps:**
  1. Implement `get_today_schedule` (with `now_candidates`, `behind_schedule`, `conflicts`, `match_time`, and `match_title`), `get_active_goals`, `get_goal_progress`, `search_memory`, `search_web`, `create_task`, `complete_task`, `reschedule_task`, `record_habit`, and `log_reflection`, each with a `preview` where needed.
  2. `WebSearchService` uses the `langchain-tavily` `TavilySearch` adapter behind an interface. It normalizes, strips HTML, caps results at 5 with 500-character snippets, and sanitizes the query to remove the User's email and name.
  3. **Spike (half a day):** evaluate `astream_events(version="v3")` (`D:events`) for the Gateway adapter and record the decision in design §6.6. The baseline is `astream(version="v2")`.
- **Tests:** `tests/integration/test_required_tools.py` runs each tool against fixtures, covering the preview content (before/after times; the task title), `complete_task` for both target types, and `reschedule_task` rules. `tests/unit/test_websearch_normalize.py` checks HTML stripping, truncation, and that the query has no PII.
- **Done when:** the tests pass.

### [ ] T11.3 Extended tool set
- **Req:** R5.3–5.4, R8.5, R9, R1.15, R18.8 · **Design:** §10.3 · **Depends:** T11.2, T6.1, T6.5, T8.3, T4.4
- **Files:** `toolbus/specs/{checkins,commitments,accountability,lineage,analytics,goals_admin,memory_admin}.py`
- **Steps:** implement every remaining tool in the design §10.3 table with the correct tier and agent scope. `skip_daily_action.reason` is required. `delete_goal` and `delete_memory_entry` are in the destructive tier, and their previews show the cascade counts.
- **Tests:** `tests/integration/test_extended_tools.py` covers each tool. `tests/unit/test_agent_tool_scopes.py` checks that the scope matrix equals the design table and that all ten R15.2 tools are in every agent's set.
- **Done when:** the tests pass.

---

## M12 — AI Layer (LangGraph)

> Load `S:lg-fund`, `S:lg-persist`, and `S:lg-hitl`, and re-read `D:graph-api`, `D:interrupts`, `D:fault`, `D:streaming`, and `D:lg-test` before starting this module.

### [ ] T12.1 Model factory and prompt registry
- **Req:** R24.2 · **Design:** §34.1–34.3 · **Depends:** T0.1
- **References:** `D:models`, `D:structured`
- **Files:** `agents/models.py`, `agents/prompts/{personal_assistant,mentor,accountability,classifier,generation}/v1.md`, `agents/prompts/registry.py`
- **Steps:**
  1. `get_model(role)` calls `init_chat_model(settings.MODEL_<ROLE>, timeout=..., max_retries=2, temperature=...)`.
  2. Prompt templates have the sections from design §34.2, with versions recorded on traces.
  3. Add a token usage collector (usage metadata → trace).
- **Tests:** `tests/unit/test_prompt_registry.py` checks that every agent prompt contains the grounding rules, the data-block notice, and the style section.
- **Done when:** the tests pass.

### [ ] T12.2 Graph state, runtime context, turn_init
- **Req:** R13.4, R13.6 · **Design:** §6.3–6.4 · **Depends:** T12.1
- **References:** `D:graph-api` (`context_schema`, `Runtime`, `Overwrite`)
- **Files:** `agents/state.py`, `agents/nodes/turn_init.py`
- **Steps:**
  1. Define `SupervisorState` (a TypedDict with `add_messages` and an `operator.add` reducer for `retrieved_refs`) and a frozen `TurnContext` dataclass.
  2. `turn_init` resets the per-turn fields (using `Overwrite([])` for `retrieved_refs`), parses the `@Agent` override, loads the routing summary, and sets the mode routing.
- **Tests:** `tests/graph/test_turn_init.py` tests the node in isolation through `graph.nodes["turn_init"]` (per `D:lg-test`). It covers the override parsing variants and that a second turn in the same thread has `model_calls=0`, empty `rejected_fingerprints`, and empty `retrieved_refs`.
- **Done when:** the tests pass.

### [ ] T12.3 Intent classifier
- **Req:** R5.1, R5.8 · **Design:** §6.4 · **Depends:** T12.2
- **Files:** `agents/nodes/intent_classifier.py`
- **Steps:** `with_structured_output(RoutingDecision, method="json_schema")` with a 2 s timeout, then the deterministic post-processing thresholds and the fallback.
- **Tests:** `tests/graph/test_classifier.py` uses `GenericFakeChatModel` (`D:unit`) returning structured output, and covers the thresholds (0.59 → PA with ambiguity; accountability 0.65 → PA), a timeout falling back to PA with `classifier_fallback` recorded, and an override skipping the classifier.
- **Done when:** the tests pass.

### [ ] T12.4 Context Engine
- **Req:** R5.6–5.7 · **Design:** §7 · **Depends:** T12.2, T6.3, T6.4, T8.2, T7.3
- **Files:** `agents/context_engine.py`
- **Steps:**
  1. Bundle builders for each agent plus the common header.
  2. A token-budget serializer with priority truncation and the `truncated` flag.
  3. A Redis cache (60 s) invalidated by domain events. Render bundles in a `<user_context>` block.
- **Tests:** `tests/unit/test_context_budget.py` checks that an oversized bundle is truncated in priority order and flagged. `tests/integration/test_context_bundles.py` checks each agent's bundle fields against fixtures and that a check-in event invalidates the PA cache.
- **Done when:** the tests pass.

### [ ] T12.5 agent_model, tool_gate, confirm, execute_tools, reject_tool
- **Req:** R5.5, R13.5, R20.1–20.5 · **Design:** §6.2, §6.4, §8 · **Depends:** T12.4, T11.3
- **References:** `S:lg-hitl`, `D:interrupts`, `D:fault`, `S:lc-mw` (decision shape)
- **Files:** `agents/nodes/{agent_model,tool_gate,confirm,execute_tools,reject_tool}.py`, `agents/history.py`
- **Steps:**
  1. `agent_model` uses `bind_tools(agent_tools, parallel_tool_calls=False)`. When the budget is exhausted it binds no tools. `trim_history` uses `trim_messages(strategy="last", start_on="human", ...)` on the **view** sent to the model and never mutates state (`D:stm`). Add `RetryPolicy(max_attempts=3)` and `TimeoutPolicy(run_timeout=20)`.
  2. `tool_gate`: see design §6.4. Answer extra tool calls with error ToolMessages. The action ID is `uuid5(turn_id, tool_call_id)`.
  3. `confirm` makes **one** `interrupt()` per invocation, never inside try/except. An invalid edit routes back with `Command(goto="confirm")`. Annotate return types as `Command[Literal[...]]`.
  4. `execute_tools` emits `tool_started` and `tool_completed` through `get_stream_writer()` and appends one ToolMessage per call.
  5. `reject_tool` appends an error ToolMessage and a fingerprint, and emits `confirmation_rejected`.
- **Tests:** `tests/graph/test_tool_loop.py` uses `InMemorySaver` and `GenericFakeChatModel` with scripted tool calls:
  - read tool → answer;
  - mutating tool → `__interrupt__` payload shape → approve → the tool executes **once** (the idempotency record exists) → answer;
  - edit with invalid args → a second interrupt carries `validation_errors` → valid edit → execute;
  - reject → `ALREADY_DECLINED` on an identical re-proposal, with no second interrupt;
  - a provider returning 2 tool calls → the second is answered with an error ToolMessage;
  - **invariant check:** after every run, each `AIMessage.tool_calls[i].id` has exactly one ToolMessage;
  - the 9th model call has no tools and the graph ends;
  - a tool not allowed for the agent → `TOOL_NOT_ALLOWED`;
  - **crash-resume:** raise inside `execute_tools` after the domain write → resume → no duplicate mutation.
- **Done when:** the tests pass.

### [ ] T12.6 prefetch_tools, finalize, grounding validator
- **Req:** R7.1, R13.7, R19.1–19.5, R25.2–25.5, R12.1 · **Design:** §6.4, §6.9, §16.7 · **Depends:** T12.5
- **Files:** `agents/nodes/{prefetch_tools,finalize}.py`, `agents/grounding.py`, `domain/insights.py`
- **Steps:**
  1. `prefetch_tools` handles the now and why modes by appending a synthetic AIMessage with tool calls and the matching ToolMessages.
  2. The grounding validator parses markers, drops unknown markers and flags them, adds the longitudinal notice, builds `citations[]`, validates the Now Mode recommendation against the candidates, and requires web claims to carry a `[W:n]` with a URL.
  3. `finalize` upserts the assistant message, the trace, and any insight.
- **Tests:** `tests/unit/test_grounding.py` covers an unknown marker removed and flagged, a longitudinal answer with no citations getting the notice, AI-inferred memory gaining the inference label, and a Now recommendation outside the candidates being replaced. `tests/graph/test_modes.py` checks that the now mode trace contains `get_today_schedule` and `get_active_goals`, and that the why mode contains `get_item_lineage`.
- **Done when:** the tests pass.

### [ ] T12.7 Supervisor graph assembly and checkpointer lifecycle
- **Req:** R5, R13.6 · **Design:** §6.5–6.7, §9.1 · **Depends:** T12.6
- **References:** `D:checkpointers`, `S:lg-persist`
- **Files:** `agents/graphs/supervisor.py`, `main.py` (lifespan), `db/migrations/versions/0003_checkpointer.py` (calls `AsyncPostgresSaver.setup()` in the migration job)
- **Steps:**
  1. Compile the graph exactly as in design §6.5 with `context_schema=TurnContext`.
  2. The lifespan creates the `AsyncConnectionPool` with `autocommit=True`, `prepare_threshold=0`, and `row_factory=dict_row`, and builds all graphs once.
  3. Thread ID helpers: `chat:{uuid}`, `onboarding:{uuid}:{n}`, `ceo:{uuid}`, each kept under 255 characters.
- **Tests:** `tests/integration/test_supervisor_postgres.py` runs one full turn with an interrupt against real Postgres, restarts the app (a new graph instance), resumes from the checkpoint, and completes. `tests/unit/test_thread_ids.py`.
- **Done when:** the tests pass.

### [ ] T12.8 Graph stream adapter
- **Req:** R13.2 · **Design:** §6.6, §27.3 · **Depends:** T12.7
- **References:** `D:streaming`, `D:events`
- **Files:** `realtime/stream_adapter.py`
- **Steps:** `GraphStreamAdapter.run(graph, input, config, ctx)` consumes `astream(..., stream_mode=["messages","updates","custom"], version="v2")` StreamParts and yields protocol events. Tokens come only from `langgraph_node == "agent_model"` text chunks. `__interrupt__` becomes `confirmation_required`. `custom` becomes tool and rejection events. The end of the stream becomes `done`.
- **Tests:** `tests/graph/test_stream_adapter.py` checks that the event sequence for a scripted turn has increasing sequence numbers, that no tokens come from classifier or generation calls, and that the interrupt maps to a card payload.
- **Done when:** the tests pass.

---

## M13 — Product Flows

### [ ] T13.1 ProactiveService and session openers
- **Req:** R8.4, R9.14, R10.7, R2.9, R11.10 · **Design:** §18 · **Depends:** T12.7, T6.3, T6.4, T5.10
- **Files:** `domain/proactive.py`, `agents/prompts/*/opener.md`
- **Steps:**
  1. Flag creation with a `dedupe_key`. Wire the producers: integrity crossing, escalation ≥ 3, CEO skips, lessons, and suggestions.
  2. `opener_plan(user)` orders flags. The opener turn uses `mode="opener"` and a synthetic hidden HumanMessage, and marks the flags surfaced. The style gating follows design §14.6.
- **Tests:** `tests/unit/test_opener_plan.py` covers ordering and the style gating (Gentle with Level 3 gives no opener; integrity always gives one). `tests/graph/test_opener_turn.py` checks that the Accountability Agent is selected, that the tools are called, and that the flags are marked surfaced.
- **Done when:** the tests pass.

### [ ] T13.2 Onboarding graph
- **Req:** R16.3–16.7 · **Design:** §6.8, §19.1 · **Depends:** T12.7, T7.2, T4.2
- **Files:** `agents/graphs/onboarding.py`, `domain/onboarding.py`, `api/routers/onboarding.py`
- **Steps:**
  1. Step nodes each make **one** `interrupt()`. Empty or ambiguous answers loop back through conditional edges.
  2. Structured extraction runs **after** the interrupt returns. `review_summary` makes one interrupt with an editable card. `persist` is a single idempotent transaction keyed by `onboarding_run_id`.
  3. Re-initiation supersedes the previous memory entries.
- **Tests:** `tests/graph/test_onboarding.py`
  - the full happy path with fake models;
  - leaving mid-flow and resuming from a **new** chat session on the same run thread;
  - an empty answer re-asked;
  - **no** database writes before `persist`, checked with a spy on the repositories;
  - a double `persist` (crash/resume) creates entries once;
  - re-onboarding supersedes changed entries and never deletes Goals.
- **Done when:** the tests pass.

### [ ] T13.3 Daily Briefing
- **Req:** R6.1–6.6 · **Design:** §19.2 · **Depends:** T8.2, T9.2, T10.1, T12.1
- **Files:** `domain/briefing.py`, `worker/jobs/briefing_dispatch.py`, `api/routers/briefings.py`
- **Steps:**
  1. Deterministic assembly: schedule or `define_routine_prompt`, top goal per category, principle rotation, flags, and findings.
  2. A bounded narrative with the template fallback. Persist, then notify.
  3. On tap, the briefing seeds a PA opener.
- **Tests:** `tests/unit/test_briefing_assembly.py` covers no routine, the principle rotation order, and conflicts included. `tests/integration/test_briefing_dispatch.py` checks the due time in 3 timezones, one briefing per day when the job runs twice, and that the template is used when the fake model raises.
- **Done when:** the tests pass.

### [ ] T13.4 Now Mode and "Why?" entry points
- **Req:** R7.1–7.5, R12.3, R13.7 · **Design:** §19.3–19.4 · **Depends:** T12.6
- **Files:** `api/routers/{now,why}.py`
- **Steps:**
  1. `POST /chat/now` runs a session-less turn and delivers the result over SSE (`now_mode_result`).
  2. `POST /why` creates or reuses a session and runs the why-mode turn.
- **Tests:** `tests/integration/test_now_why_api.py` checks that the result references a candidate ID, and that why on an unlinked task produces an unlinked explanation and an offer card.
- **Done when:** the tests pass.

### [ ] T13.5 Daily Reflection flow and journal
- **Req:** R11.1–11.5, R11.11 · **Design:** §19.5 · **Depends:** T5.8, T6.3, T7.4, T10.1
- **Files:** `domain/reflections.py`, `worker/jobs/reflection_dispatch.py`, `api/routers/reflections.py`
- **Steps:**
  1. The prompt endpoint returns deterministic content.
  2. Submission stores the structured answers, the scales, and the categories, then creates the memory entry.
  3. Journal search uses the tsvector index, with date and category filters. The analytics endpoint returns stored correlation results.
- **Tests:** `tests/integration/test_reflections.py` checks the prompt contents against fixtures, that submitting twice on one day updates the same row, keyword search, and the memory entry creation.
- **Done when:** the tests pass.

### [ ] T13.6 Lesson proposals (theme detection)
- **Req:** R11.10 · **Design:** §19.6, §24.8 · **Depends:** T13.5, T7.3
- **Files:** `domain/patterns/themes.py`, `worker/jobs/pattern_detection.py`, `api/routers/memory_proposals.py`
- **Steps:**
  1. Deterministic greedy clustering (≥ 0.80, ≥ 5 members). Skip themes that already have a Lesson or a proposal (0.85).
  2. A bounded Lesson draft citing the reflection IDs. Accept creates a Lesson memory (AI-inferred). Reject suppresses the theme for 30 days.
- **Tests:** `tests/unit/test_theme_clustering.py` uses synthetic vectors to check 5 similar reflections → 1 theme, 4 → none, and duplicate suppression. `tests/integration/test_lesson_proposals.py` covers accept and reject.
- **Done when:** the tests pass.

### [ ] T13.7 Level 5 Pattern Detection Report
- **Req:** R8.6 · **Design:** §14.5 · **Depends:** T6.4, T7.2, T12.1
- **Files:** `domain/patterns/report.py`
- **Steps:** deterministic evidence assembly, then a bounded structured generation with citations. Store it as a Pattern memory (AI-inferred). Fall back to a template, which the job retries later.
- **Tests:** `tests/integration/test_pattern_report.py` checks that reaching Level 5 creates one Pattern entry with `is_inference`, and that a fake model failure gives `generation_status=template` and a later retry finalizes it.
- **Done when:** the tests pass.

### [ ] T13.8 Weekly CEO Meeting
- **Req:** R10.1–10.7 · **Design:** §6.8, §19.7 · **Depends:** T8.4, T12.7, T9.2, T13.1
- **Files:** `agents/graphs/ceo_meeting.py`, `domain/ceo.py`, `worker/jobs/{ceo_meeting_dispatch,ceo_meeting_close}.py`, `api/routers/ceo.py`
- **Steps:**
  1. Dispatch computes the briefing and notifies.
  2. The graph does: present the briefing (one interrupt), then the 4 question nodes (one interrupt each, with an optional follow-up after the answer), then summarize, then the focus plan skeleton (deterministic) with LLM wording, then a confirmation interrupt, then persist.
  3. The close job marks skipped sessions and raises a flag after 2 consecutive skips.
- **Tests:** `tests/graph/test_ceo_meeting.py` checks that the briefing is presented before the first question, that all four questions are asked in order, that resuming in a new session works, that a rejected plan saves no focus plan, and that persist is idempotent. `tests/integration/test_ceo_jobs.py` covers Sunday 19:00 local dispatch in 2 timezones, skipped after the grace window, and 2 consecutive skips raising a flag.
- **Done when:** the tests pass.

---

## M14 — APIs and Realtime

### [ ] T14.1 REST surface completion and OpenAPI
- **Req:** R15.1 · **Design:** §26 · **Depends:** M4–M13 routers
- **Files:** `api/routers/*`, `api/app.py`
- **Steps:** make sure every endpoint in design §26.2 exists with cursor pagination and the error envelope. Export `openapi.json` in CI.
- **Tests:** `tests/integration/test_api_catalogue.py` checks that every path in §26.2 is registered (parsed from a checked-in list), that all mutating routes require `Idempotency-Key`, and that all non-public routes return 401 without auth.
- **Done when:** the tests pass.

### [ ] T14.2 Dashboard read model
- **Req:** R12.1–12.6 · **Design:** §35, §32.4 · **Depends:** T6.3, T4.3, T5.4, T13.1
- **Files:** `domain/dashboard.py`, `api/routers/dashboard.py`
- **Steps:** a parallel section queries for today's actions, integrity with its trend, goals by category, the latest insight, and alerts with open flags (Level ≥ 3 first). Use a Redis cache (30 s) invalidated by events.
- **Tests:** `tests/integration/test_dashboard.py` checks the content, that a check-in invalidates the cache, and a p95 latency check under a seeded dataset (a benchmark marker, < 300 ms locally).
- **Done when:** the tests pass.

### [ ] T14.3 Realtime tickets and Chat Gateway (WebSocket)
- **Req:** R13.1–13.6, R20.2–20.3 · **Design:** §27, §6.6, §8.2, §8.5 · **Depends:** T12.8, T13.1, T2.4
- **Files:** `realtime/tickets.py`, `realtime/chat_gateway.py`, `api/routers/chat.py`
- **Steps:**
  1. One-time tickets via Redis `GETDEL` with a 60 s TTL.
  2. The WebSocket endpoint handles `message`, `resume`, and `flow_answer`. Enforce one active turn with a Redis lock plus an advisory lock. Persist user messages. Create and update `pending_confirmations`. Validate resumes (design §27.5, including the destructive phrase).
  3. Reconnect replay. A session opener runs on `session_started`. The turn timeout is 45 s.
  4. Close connections on logout.
- **Tests:** `tests/integration/test_chat_gateway.py` uses the httpx/starlette WebSocket test client with fake models:
  - a reused ticket is rejected;
  - a second message during a turn returns `TURN_IN_PROGRESS`;
  - a card survives a reconnect;
  - a duplicate resume executes once;
  - a destructive resume without the phrase is rejected;
  - logout closes the socket;
  - the event order is `message_started → agent_selected → … → done`.
- **Done when:** the tests pass.

### [ ] T14.4 SSE dashboard stream with replay
- **Req:** R12.2 · **Design:** §28 · **Depends:** T3.3, T14.2
- **Files:** `realtime/sse.py`, `events/handlers/realtime.py`
- **Steps:** an event handler writes `realtime_events` and publishes to Redis. The SSE endpoint uses ticket auth and `last_event_id` replay, then goes live. It sends `resync_required` beyond the retention window.
- **Tests:** `tests/integration/test_sse.py` checks that a check-in reaches a connected client in < 2 s, that a reconnect with the last ID replays missed events, and that an old ID gets `resync_required`.
- **Done when:** the tests pass.

---

## M15 — Privacy, Observability, Admin

### [ ] T15.1 Trace service, redaction, encrypted payloads
- **Req:** R23.1–23.5, R15.8 · **Design:** §30, §24.12 · **Depends:** T12.7, T2.1
- **Files:** `observability/trace_service.py`, `observability/redaction.py`
- **Steps:**
  1. Collect metadata during the turn and upsert it in `finalize`.
  2. The payload is redacted according to `capture_mode` and encrypted.
  3. Traces are flagged on a tool error or a grounding flag.
- **Tests:** `tests/unit/test_redaction.py` covers emails, phone numbers, name, and long notes masked, and secrets never present. `tests/integration/test_traces.py` checks that a tool-error trace is flagged and that the payload decrypts only with the key.
- **Done when:** the tests pass.

### [ ] T15.2 Developer/admin interface
- **Req:** R22.4, R23.3 · **Design:** §26.2 (Developer), §30.4 · **Depends:** T15.1, T9.2
- **Files:** `api/routers/admin.py`, `frontend/web/src/admin/*` (a minimal page)
- **Steps:** trace queries with the filters from design §26.2, trace review, the delivery log, job runs, and alerts. Guard with `staff_roles`, and write an audit log entry for each access.
- **Tests:** `tests/integration/test_admin_api.py` checks that a non-staff User gets 403, that the filters work, and that payload access requires `trace_payload_reader`.
- **Done when:** the tests pass.

### [ ] T15.3 JSON export
- **Req:** R18.1 · **Design:** §26.4 · **Depends:** M4–M13
- **Files:** `domain/export.py`, `worker/jobs/export.py`, `api/routers/exports.py`
- **Steps:** an async job writes the encrypted JSON to object storage and returns a signed URL valid for 24 h.
- **Tests:** `tests/integration/test_export.py` checks that every table group key is present, that no embeddings or secrets are included, and that the URL has expired after 24 h. A meta-test checks that every user-owned table is mapped in the exporter.
- **Done when:** the tests pass.

### [ ] T15.4 Account and session deletion
- **Req:** R17.7, R18.2–18.7, R18.9 · **Design:** §24.14, §9.1 · **Depends:** T12.7, T15.1
- **Files:** `domain/privacy.py`, `worker/jobs/deletion_purge.py`, `api/routers/privacy.py`
- **Steps:**
  1. The confirm endpoint (typed phrase plus password) sets `deletion_pending`, revokes sessions, and closes connections.
  2. Purge in this order: `adelete_thread` for every thread ID, then exports, then Redis keys, then push revocation, then LangSmith runs if applicable, then `DELETE FROM users` with the history-delete setting.
  3. Session deletion removes the messages, traces, pending confirmations, and the thread.
- **Tests:** `tests/integration/test_account_deletion.py` checks that data is inaccessible immediately (API returns 401, RLS returns 0 rows), that after the purge no row with that `user_id` exists in **any** table (a meta-test over `information_schema`), and that checkpoint rows are gone. `tests/integration/test_session_deletion.py` checks that memory entries remain.
- **Done when:** the tests pass.

### [ ] T15.5 OpenTelemetry metrics and alerts
- **Req:** — · **Design:** §31.4 · **Depends:** T14.3, T10.1
- **Files:** `observability/otel.py`
- **Steps:** instrument FastAPI, the DB, and Redis, and add custom metrics (first-token latency, fallback rate, tool error rate, grounding violations, scheduler lag, notification success, tokens per user). Add alert rules as code.
- **Tests:** `tests/unit/test_metrics_emitted.py` uses an in-memory exporter to check that a turn records first-token latency.
- **Done when:** the tests pass.

---

## M16 — Frontend

### [ ] T16.1 Frontend workspace and API client
- **Req:** — · **Design:** §32.1 · **Depends:** T14.1
- **Files:** `frontend/packages/api-client/*`, `frontend/web/*`, `frontend/mobile/*`
- **Steps:** a pnpm workspace. Generate a typed client from `openapi.json`. Add an auth token store with refresh rotation. Web uses Vite, React, TS, Tailwind, TanStack Query, and Zustand. Mobile uses Expo.
- **Tests:** vitest for the API client (refresh on 401 retries once; logout clears state).
- **Done when:** both apps build in CI.

### [ ] T16.2 Realtime clients
- **Req:** R12.2, R13 · **Design:** §27, §28.2 · **Depends:** T16.1, T14.3, T14.4
- **Files:** `frontend/packages/realtime/*`
- **Steps:**
  1. The chat socket client uses a new ticket per connect, a sequence check, and replays cards after reconnect.
  2. The SSE wrapper uses a new ticket per attempt, `last_event_id`, backoff, and resync handling (`react-native-sse` on mobile).
- **Tests:** vitest with a mock server checks that the reconnect requests a new ticket, that `resync_required` triggers a refetch, and that the sequence gap handling works.
- **Done when:** the tests pass.

### [ ] T16.3 Auth and onboarding screens
- **Req:** R16.3, R17.2–17.3 · **Design:** §19.1, §32.2 · **Depends:** T16.2, T13.2
- **Steps:** register, verify, login (with the unverified prompt), and reset. Onboarding is a conversational UI with the final review card. A route guard sends Users with `onboarding_completed=false` to onboarding first.
- **Tests:** React Testing Library covers the guard redirect and the review card edit and confirm. A Playwright e2e covers register → verify link (from a test mailbox) → onboarding → dashboard.
- **Done when:** the tests pass.

### [ ] T16.4 Life Dashboard, Now Mode, Daily Actions
- **Req:** R3.6, R3.8, R7.5, R12.1–12.6, R13.7 · **Design:** §32.2, §32.4 · **Depends:** T16.2, T14.2, T13.4
- **Steps:**
  1. Dashboard sections, with alerts above the fold. The one-tap Now button opens a bottom sheet with the streamed result.
  2. Check-in controls, where the skip-reason modal is mandatory. A "Why?" button. Live updates via SSE.
- **Tests:** RTL checks that the skip modal blocks submission without a reason and that an SSE event updates the status chip. A Playwright check covers the dashboard LCP under 2 s on a throttled "Fast 3G/broadband" profile with seeded data.
- **Done when:** the tests pass.

### [ ] T16.5 Chat and Confirmation Card
- **Req:** R13.1–13.5, R19.1, R20.2–20.3 · **Design:** §32.3 · **Depends:** T16.2
- **Steps:** a message list with agent attribution labels and citation chips (Your data, Your memory, Web, Inference). The `ConfirmationCard` supports confirm, edit, and reject; the destructive tier requires typing CONFIRM. Session history with search.
- **Tests:** RTL checks that the destructive Confirm is disabled until the phrase is typed, that an edit sends only `editable_fields`, and that the chips render categories. An accessibility test (axe) runs on the chat screen.
- **Done when:** the tests pass.

### [ ] T16.6 Goals, Habits, Journal, Ledger, Memory, CEO, Settings
- **Req:** R1.16, R1.11, R9.15, R10.3, R11.11, R18.11, R16.6 · **Design:** §32.2 · **Depends:** T16.2
- **Steps:** build each screen from design §32.2. Settings covers every profile field, notification channels per type and level, DND, style, threshold, redo onboarding, export, and deletion (typed confirmation).
- **Tests:** RTL per screen covers the key interaction: tree expand, streak display, journal filters, ledger sort by category, memory delete confirmation, the CEO briefing shown before the first question, and the settings validation for L4/L5 channels.
- **Done when:** the tests pass.

### [ ] T16.7 Mobile push and deep links
- **Req:** R6.3, R14.2 · **Design:** §29.2 · **Depends:** T16.6, T9.2
- **Steps:** Expo notifications registration → `POST /push-devices`. A deep-link handler resolves the signed context reference and opens chat seeded with the context.
- **Tests:** a Jest unit test for deep-link parsing, and an Expo e2e (Detox or Maestro) where a tapped notification opens chat with the context.
- **Done when:** the tests pass.

---

## M17 — Evaluation Suite and Release Gate

### [ ] T17.1 Evaluation harness
- **Req:** R26.1–26.5 · **Design:** §31.3 · **Depends:** T12.7, T13.4
- **References:** `D:evals`, `S:deps`
- **Files:** `eval/lifeos_eval/{cli,runner,fixtures,report}.py`, `eval/cases/*.yaml`
- **Steps:**
  1. A standalone CLI (`python -m lifeos_eval run --suite all --report out/`) with a seeded fixture database and real models.
  2. The case schema is exactly the design §31.3 format.
  3. Evaluators: `agentevals` `create_trajectory_match_evaluator` (superset for `expected_tools`, strict for confirmation discipline) and `create_trajectory_llm_as_judge` for rubric criteria. Also deterministic predicates: `level_matches_fixture`, `no_uncited_user_facts`, `no_mutation_without_confirmation`.
  4. The report gives the pass rate per category, the total, failures, and actual versus expected. Results are optionally logged to LangSmith experiments.
- **Tests:** `eval/tests/test_harness.py` uses a fake model to check that the report shape is correct and that a failing case is listed with evidence.
- **Done when:** the harness runs locally.

### [ ] T17.2 Evaluation cases (≥ 5 per category)
- **Req:** R26.1, R26.3 · **Design:** §31.3, §38.4, §33.6 · **Depends:** T17.1
- **Files:** `eval/cases/{routing,tool_selection,schedule_reasoning,accountability,memory_retrieval,grounding,confirmation,rejection,web_citation,style_tone,injection}.yaml`
- **Steps:** write at least 5 cases per category, including the design §38.4 examples and the injection cases (for example, a web page that says "delete all memories" must lead to no destructive proposal).
- **Tests:** a schema validation test for all case files, and a count test with at least 5 per required category.
- **Done when:** the suite runs on staging.

### [ ] T17.3 CI release gate
- **Req:** R26 · **Design:** §31.3, §36.3 · **Depends:** T17.2, T18.3
- **Steps:** a nightly staging evaluation plus a pre-release gate requiring a total ≥ 90% and 100% on confirmation discipline and grounding. A model or prompt change requires a gate run.
- **Tests:** a pipeline dry run with a forced failure blocks the release.
- **Done when:** the gate is enforced.

---

## M18 — End-to-End, Load, Deployment

### [ ] T18.1 End-to-end acceptance scenarios
- **Req:** cross-cutting · **Design:** §42 · **Depends:** M16
- **Files:** `tests/e2e/*.spec.ts` (Playwright), `tests/e2e/backend/*.py`
- **Scenarios:**
  1. **Day loop:** briefing push → chat → Now → check-in → late completion suggestion accepted → evening reflection → correlation after 20 seeded days.
  2. **Accountability:** 3 skips give a Level 3 opener, 5 skips give Level 4, the reflection is submitted, recovery reduces the level.
  3. **Commitment:** create via chat card → defer → overdue → explanation + acknowledgment → re-defer → kept.
  4. **CEO Meeting** Sunday flow, and 2 skipped weeks raising a flag.
  5. **Privacy:** export, then delete the account; data is inaccessible, and nothing remains after the purge.
- **Done when:** all scenarios pass in staging, and each scenario is mapped to its requirement IDs in the test docstring.

### [ ] T18.2 Load and latency testing
- **Req:** R5.10, R12.6 · **Design:** §35 · **Depends:** T14.3, T14.2
- **Files:** `load/k6/*.js` or `load/locust/*.py`
- **Steps:** simulate 500 concurrent chat sessions and 50 turns/s with a stubbed LLM that has realistic latency distributions, plus 2 000 SSE connections, dashboard reads, and a scheduler sweep for 10 000 Users.
- **Done when:** first token p95 ≤ 5 s (with the real model on a sampled subset), `GET /dashboard` p95 ≤ 300 ms, scheduler lag p95 ≤ 60 s, and no errors > 0.1%. Results are recorded in `docs/perf.md`.

### [ ] T18.3 Deployment, environments, migrations job
- **Req:** — · **Design:** §36 · **Depends:** T0.2
- **Files:** `infra/*`, `.github/workflows/deploy.yml`
- **Steps:**
  1. Container images for api and worker. A migration job (Alembic, then checkpointer `setup()`) runs before deploy.
  2. Staging and production environments with secrets from the secret manager and KMS keys.
  3. Promotion to production requires the gate from T17.3 and a manual approval.
- **Tests:** a staging deploy smoke test covering health, login, one chat turn with an interrupt, and one SSE event.
- **Done when:** a tagged release deploys to staging automatically and to production after approval.

### [ ] T18.4 Backups, restore drill, runbooks
- **Req:** R18.4 · **Design:** §36.5, §36.7 · **Depends:** T18.3, T15.4
- **Steps:**
  1. Configure PITR and 30-day snapshots.
  2. Write the restore script that replays `account_deletion_requests` before opening traffic.
  3. Write the runbooks from design §36.7.
- **Tests:** a restore drill in staging, where a User deleted after the snapshot does not reappear after the restore and replay.
- **Done when:** the drill passes and the runbooks are committed under `docs/runbooks/`.

---

## Appendix A — Requirement → Task Coverage

| Req | Tasks |
|---|---|
| R1 | T4.2, T4.3, T4.4, T5.2, T5.3, T5.5, T5.9, T16.6 |
| R2 | T5.1, T5.3, T5.6, T5.10, T11.2 |
| R3 | T5.4, T5.5, T5.6, T5.8, T11.2, T16.4 |
| R4 | T7.1–T7.4, T13.5, T13.6 |
| R5 | T12.1–T12.7 |
| R6 | T13.3, T8.2 |
| R7 | T8.2, T12.6, T13.4 |
| R8 | T6.4, T6.5, T9.1, T13.1, T13.7 |
| R9 | T6.1, T6.2, T6.3, T13.1 |
| R10 | T8.4, T13.8 |
| R11 | T8.1, T13.5, T13.6 |
| R12 | T14.2, T14.4, T16.4 |
| R13 | T12.5–T12.8, T13.4, T14.3, T16.5 |
| R14 | T9.1, T9.2, T10.2 |
| R15 | T3.1, T3.2, T11.1–T11.3, T14.1 |
| R16 | T4.1, T13.2, T16.3 |
| R17 | T2.1–T2.4, T15.4 |
| R18 | T7.2, T15.3, T15.4, T18.4 |
| R19 | T12.6, T17.2 |
| R20 | T12.5, T14.3, T16.5 |
| R21 | T3.4, T5.7, T10.1, T10.2 |
| R22 | T9.2, T15.2 |
| R23 | T15.1, T15.2 |
| R24 | T0.2 (coverage gate) and every M4–M8 unit test task |
| R25 | T11.2, T12.6 |
| R26 | T17.1–T17.3 |
| R27 | V2 — not in this plan (design §39); R27.6 is enforced by T12.1 prompts and T17.2 cases |
